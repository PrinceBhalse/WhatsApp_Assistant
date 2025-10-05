from flask import Flask, request, redirect, url_for
from dotenv import load_dotenv
import os
import re
from twilio.rest import Client
import requests

# Local imports
from drive_assistant import (
    list_files, delete_file, move_file, rename_file,
    summarize_folder, upload_file
)
from drive_auth import get_user_drive_service, generate_auth_url
from db_manager import init_db, get_user_token, save_user_token

load_dotenv()
app = Flask(__name__)

# Global instances
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
AI_MODEL = os.getenv('AI_MODEL', 'gpt-4o-mini')

# Twilio Globals
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER')
TWILIO_CLIENT = None

# Global instance to hold the GoogleAuth flow object during setup
# NOTE: This global state is simple but sufficient for a single-instance Flask app
AUTH_FLOW_CONTEXT = {}


def initialize_twilio():
    global TWILIO_CLIENT
    if TWILIO_CLIENT is None and TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        try:
            TWILIO_CLIENT = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            app.logger.info("Twilio client initialized.")
        except Exception as e:
            app.logger.error(f"Failed to initialize Twilio: {e}")
            pass


# Initialize Twilio client and Database on startup
with app.app_context():
    initialize_twilio()
    init_db()
    app.logger.info("Database initialized.")


@app.route('/whatsapp/message', methods=['POST'])
def whatsapp_webhook():
    """Handles incoming messages from the Twilio WhatsApp webhook."""

    # 1. Parse Twilio Form Data
    message_body = request.form.get('Body')
    media_url = request.form.get('MediaUrl0')
    sender_number = request.form.get('From')  # User's WhatsApp number

    if not message_body:
        send_whatsapp_response(sender_number, "Please send a command (e.g., LIST/Reports or SETUP).")
        return 'OK'

    app.logger.info(f"Received message from {sender_number}: {message_body}")

    # 2. Process Command
    response_message = process_command(message_body, media_url, sender_number)

    # 3. Send Response via Twilio API
    send_whatsapp_response(sender_number, response_message)

    return 'OK'


@app.route('/oauth/callback', methods=['GET'])
def oauth_callback():
    """
    Handles the redirect from Google after the user grants permission.
    This is where we get the final token and save it to the DB.
    """
    global AUTH_FLOW_CONTEXT
    code = request.args.get('code')
    state = request.args.get('state')  # This should be the user's WhatsApp number

    if not code or not state:
        return "Authentication Failed: Missing authorization code or user state.", 400

    whatsapp_number = state

    # Retrieve the flow context associated with this user
    auth_flow = AUTH_FLOW_CONTEXT.pop(whatsapp_number, None)

    if auth_flow is None:
        return "Authentication Failed: Setup timed out or flow context lost. Please restart SETUP.", 500

    try:
        # Exchange the authorization code for an access token
        auth_flow.Auth(code)
        refresh_token = auth_flow.credentials['refresh_token']

        if refresh_token:
            save_user_token(whatsapp_number, refresh_token)
            return "Google Drive connected successfully! You can now use commands like LIST/Reports."
        else:
            return "Authentication Failed: Did not receive a refresh token. Check Google Cloud Console setup.", 500

    except Exception as e:
        app.logger.error(f"OAuth Callback Error for {whatsapp_number}: {e}")
        return f"Authentication Failed during token exchange: {e}", 500


def send_whatsapp_response(to_number, message_text):
    """Sends a message back to the user via the Twilio API."""
    if TWILIO_CLIENT:
        try:
            TWILIO_CLIENT.messages.create(
                from_=TWILIO_WHATSAPP_NUMBER,
                to=to_number,
                body=message_text
            )
            app.logger.info(f"Successfully sent response to {to_number}")
        except Exception as e:
            app.logger.error(f"Failed to send Twilio message to {to_number}: {e}")
    else:
        app.logger.info(f"[MOCK RESPONSE] To: {to_number}, Body: {message_text}")


def process_command(message: str, file_url: str | None, sender_number: str) -> str:
    """Parses the message and executes the Drive command for the specific user."""
    global AUTH_FLOW_CONTEXT

    parts = message.strip().split('/', 1)
    command = parts[0].upper()
    arguments = parts[1].strip() if len(parts) > 1 else ''

    # --- SETUP COMMAND: Start OAuth flow ---
    if command == 'SETUP':
        try:
            # 1. Generate the unique OAuth URL
            auth_url, flow = generate_auth_url(sender_number)

            # 2. Store the flow object in the global context, keyed by user number
            AUTH_FLOW_CONTEXT[sender_number] = flow

            # 3. Return setup link to the user
            return (
                f"Please click the link below to securely connect your Google Drive.\n\n"
                f"1. Click: {auth_url}\n"
                f"2. Log in and Grant Permissions.\n\n"
                f"After authorization, you can use all commands."
            )
        except Exception as e:
            return f"Error initiating setup. Check logs for missing client_secrets.json or configuration: {e}"

    # --- CHECK FOR USER CREDENTIALS ---
    refresh_token = get_user_token(sender_number)
    if not refresh_token:
        return "Your Drive is not connected. Please send the command 'SETUP' to link your Google Drive first."

    try:
        # Create a drive service instance specific to this user
        USER_DRIVE = get_user_drive_service(refresh_token)
    except Exception as e:
        app.logger.error(f"Token error for user {sender_number}: {e}")
        # If the token is invalid/revoked, force the user to set up again
        return f"Error connecting to your Drive: Your token may be expired or revoked. Please send 'SETUP' again to reconnect your Drive."

    # --- RENAME ---
    if command == 'RENAME':
        rename_args = arguments.split(None, 1)
        if len(rename_args) != 2:
            return "Invalid RENAME format. Use: RENAME OldFileName.ext NewFileName.ext"
        old_file_name, new_file_name = rename_args
        return rename_file(USER_DRIVE, old_file_name, new_file_name)

    # --- LIST ---
    elif command == 'LIST':
        if not arguments:
            return "Invalid LIST format. Use: LIST/FolderName"
        return list_files(USER_DRIVE, arguments)

    # --- SUMMARY ---
    elif command == 'SUMMARY':
        if not arguments:
            return "Invalid SUMMARY format. Use: SUMMARY/FolderName"
        if not OPENAI_API_KEY:
            return "AI summarization service is not configured (OPENAI_API_KEY missing)."
        return summarize_folder(USER_DRIVE, arguments, OPENAI_API_KEY, AI_MODEL)

    # --- DELETE ---
    elif command == 'DELETE':
        delete_parts = arguments.split('/', 1)
        if len(delete_parts) != 2:
            return "Invalid DELETE format. Use: DELETE/FolderName/file.pdf"
        folder_name, file_name = delete_parts
        return delete_file(USER_DRIVE, folder_name, file_name)

    # --- MOVE ---
    elif command == 'MOVE':
        move_parts = arguments.split('/', 2)
        if len(move_parts) != 3:
            return "Invalid MOVE format. Use: MOVE/SourceFolder/file.pdf/DestFolder"
        source_folder, file_name, dest_folder = move_parts
        return move_file(USER_DRIVE, source_folder, file_name, dest_folder)

    # --- UPLOAD ---
    elif command == 'UPLOAD':
        if not file_url:
            return "UPLOAD command requires a file attachment in WhatsApp (MediaUrl0 in webhook)."

        upload_match = re.search(r'(?P<folder>[^/]+)\s+(?P<filename>.*)', arguments.strip())
        if upload_match:
            folder_name = upload_match.group('folder').strip()
            new_file_name = upload_match.group('filename').strip()

            temp_file_path = f"uploaded_file_{os.getpid()}.tmp"

            try:
                # 1. Download file content from Twilio URL
                file_response = requests.get(file_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
                file_response.raise_for_status()  # Raise exception for bad status codes

                # 2. Write content to a temporary file
                with open(temp_file_path, 'wb') as f:
                    f.write(file_response.content)

                # 3. Use USER_DRIVE instance for upload
                result = upload_file(USER_DRIVE, folder_name, temp_file_path, new_file_name)
                return result

            except requests.exceptions.RequestException as e:
                return f"Error downloading file from Twilio: {e}"
            except Exception as e:
                return f"Error during Drive upload: {e}"
            finally:
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
        else:
            return "Invalid UPLOAD format. Use: UPLOAD/FolderName NewFileName.ext"

    # --- UNKNOWN COMMAND ---
    else:
        return f"Unknown command: {command}. Available commands: LIST, DELETE, MOVE, SUMMARY, RENAME, UPLOAD, SETUP."


if __name__ == '__main__':
    print("WhatsApp Assistant starting (Multi-User Ready)...")
    app.run(debug=True, port=5001)
