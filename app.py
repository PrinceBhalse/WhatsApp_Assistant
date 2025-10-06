import os
import json
import base64
import drive_auth  # Authentication and service builder
import drive_assistant_v2 as drive_assistant  # New logic using native API
import requests
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import re  # For better command parsing

app = Flask(__name__)

# --- Configuration ---
PUBLIC_URL = os.getenv('PUBLIC_URL', 'http://localhost:5000')
TEMP_DIR = os.getenv('TEMP_DIR', '/tmp')
TEMP_FILE_PATH = os.path.join(TEMP_DIR, 'upload_temp')

# Get your OpenAI API Key and Model Name from environment variables
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', 'default-key')
OPENAI_MODEL_NAME = os.getenv('OPENAI_MODEL_NAME', 'gpt-3.5-turbo')


# --- Utility Functions for WhatsApp Response ---

def send_whatsapp_response(msg=""):
    """
    Helper function to create a TwiML response.
    Uses explicit TwiML construction with CDATA to handle raw, messy AI text safely.
    """
    # Force convert to ASCII to strip any remaining non-standard characters, 
    # then wrap the clean text in a CDATA block for maximum TwiML compatibility.
    safe_msg = msg.encode('ascii', 'ignore').decode('ascii')
    
    twiml = (
        f'<Response><Message><Body><![CDATA[{safe_msg}]]></Body></Message></Response>'
    )
    return twiml


# --- Drive Service Builder (Assuming this is already working and returns a native API service) ---

def get_drive_service(user_id):
    """Retrieves the authenticated Google API Service object for the user."""
    service, auth_error = drive_auth.build_drive_service(user_id)
    return service, auth_error


# --- Flask Routes ---

@app.route("/oauth/callback", methods=["GET"])
def oauth_callback():
    """Handles the redirect from Google after authorization."""
    try:
        code = request.args.get('code')
        encoded_user_id = request.args.get('state')

        if not code or not encoded_user_id:
            return "Authorization failed. Missing code or state.", 400

        user_id = base64.b64decode(encoded_user_id).decode('utf-8')
        print(f"Callback received for user: {user_id}. Attempting token exchange.")

        credentials, error = drive_auth.exchange_code_for_token(code, PUBLIC_URL)

        if error:
            print(f"Error during token exchange: {error}")
            return f"Authorization failed: {error}", 500

        drive_auth.store_credentials(user_id, credentials)

        # Success page
        return """
            <html><body>
            <h1>Success!</h1>
            <p>Google Drive authorization was successful. You may now return to WhatsApp and use all the commands!</p>
            </body></html>
        """
    except Exception as e:
        print(f"General error in OAuth callback: {e}")
        return "An unexpected error occurred during authorization.", 500


@app.route("/whatsapp/message", methods=["POST"])
def whatsapp_message():
    """Handles incoming WhatsApp messages and commands, including media/file uploads."""

    msg_body = request.values.get('Body', '').strip()
    user_id = request.values.get('WaId')
    num_media = int(request.values.get('NumMedia', 0))

    if not user_id:
        return send_whatsapp_response("Error: Could not identify sender ID.")

    print(f"Extracted User ID: {user_id}")

    # 1. SETUP Command (Always handled first)
    if msg_body.upper() == 'SETUP':
        print(f"Received command: 'SETUP' from user: {user_id}")
        encoded_user_id = base64.b64encode(user_id.encode('utf-8')).decode('utf-8')
        auth_url, error = drive_auth.generate_auth_url(PUBLIC_URL, encoded_user_id)

        if error:
            return send_whatsapp_response(f"Error initiating setup: {error}")

        response_msg = (
            "*Google Drive Setup Required*\n\n"
            "Please click the link below to securely authorize this app to access your Google Drive. This only needs to be done once.\n\n"
            f"{auth_url}\n\n"
            "This link will expire shortly."
        )
        return send_whatsapp_response(response_msg)

    # --- Initialize Drive Service for All Other Commands/Media ---
    drive, auth_error = get_drive_service(user_id)
    if auth_error:
        # If user sends a command but is not authenticated
        return send_whatsapp_response(f"Error: {auth_error}. Please send 'SETUP' to connect your Drive.")

    # 2. Media Handling (UPLOAD) - Runs if media is present AND command starts with UPLOAD
    if num_media > 0 and drive:

        command_match = re.match(r'UPLOAD\s+(/[^\s]+)(?:\s+(.+))?', msg_body.strip(), re.IGNORECASE)

        if not command_match:
            return send_whatsapp_response(
                "File attached, but missing or invalid UPLOAD command. Use: UPLOAD /<Folder Name> <New File Name.ext>")

        folder_path = command_match.group(1).strip('/')
        new_file_name_input = command_match.group(2)
        default_file_name = request.values.get('MediaFilename0', f"WhatsApp_Upload_{os.urandom(4).hex()}")
        drive_file_name = new_file_name_input or default_file_name

        media_url = request.values.get('MediaUrl0')

        print(f"[{user_id}] Fetching media from URL: {media_url}")

        media_response = requests.get(media_url, auth=(os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN')))

        if media_response.status_code != 200:
            return send_whatsapp_response(
                f"Error: Could not fetch media from Twilio. Status: {media_response.status_code}. Check TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN.")

        os.makedirs(TEMP_DIR, exist_ok=True)
        temp_file_path_full = f"{TEMP_FILE_PATH}_{os.urandom(4).hex()}"
        result_msg = "Processing file upload..." 

        try:
            with open(temp_file_path_full, 'wb') as f:
                f.write(media_response.content)

            print(f"[{user_id}] Attempting upload of '{drive_file_name}' to folder '{folder_path}'")
            result_msg = drive_assistant.upload_file(drive, folder_path, temp_file_path_full, drive_file_name)

        except Exception as e:
            print(f"Error during UPLOAD processing: {e}")
            result_msg = f"An error occurred during file processing or upload: {e}"
        finally:
            if os.path.exists(temp_file_path_full):
                os.remove(temp_file_path_full)
                print(f"Cleaned up temporary file: {temp_file_path_full}")

        return send_whatsapp_response(result_msg)

    # 3. Command Parsing (Non-media commands)

    # Check for RENAME first, as it uses spaces and breaks the slash logic
    if msg_body.upper().startswith('RENAME '):
        parts = [p.strip() for p in msg_body.strip().split(' ', 3) if p.strip()]
        result_msg = "Invalid RENAME format. Use: RENAME OldFileName.ext NewFileName.ext"

        if len(parts) == 3:
            old_file_name = parts[1]
            new_file_name = parts[2]
            print(f"[{user_id}] Processing RENAME command: from '{old_file_name}' to '{new_file_name}'")
            
            try:
                result_msg = drive_assistant.rename_file(drive, old_file_name, new_file_name)
            except Exception as e:
                print(f"Error during RENAME execution: {e}")
                result_msg = f"❌ An error occurred during rename: {e}"
            
        return send_whatsapp_response(result_msg)


    # Standard slash parsing for all remaining commands (LIST, DELETE, MOVE, SUMMARY)
    command_parts = msg_body.strip().upper().split('/', 1)
    command = command_parts[0]
    arg_string = command_parts[1] if len(command_parts) > 1 else None

    if drive:
        print(f"[{user_id}] Processing text command: {command} with args: {arg_string}")
        result_msg = ""

        # --- LIST Command ---
        if command == 'LIST' and arg_string:
            result_msg = drive_assistant.list_files(drive, arg_string)

        # --- DELETE Command ---
        elif command == 'DELETE' and arg_string:
            parts = [p.strip() for p in arg_string.split('/', 1) if p.strip()]
            if len(parts) == 2:
                result_msg = drive_assistant.delete_file(drive, parts[0], parts[1])
            else:
                result_msg = "Invalid DELETE format. Use: DELETE/FolderName/FileName.ext"

        # --- MOVE Command ---
        elif command == 'MOVE' and arg_string:
            # Format: MOVE/SourceFolder/FileName.ext/DestFolder
            parts = [p.strip() for p in arg_string.split('/', 2) if p.strip()]
            
            if len(parts) == 3:
                try:
                    result_msg = drive_assistant.move_file(drive, parts[0], parts[1], parts[2])
                except Exception as e:
                    print(f"Error during MOVE execution: {e}")
                    result_msg = f"❌ An error occurred during move: {e}"
            else:
                result_msg = "Invalid MOVE format. Use: MOVE/SourceFolder/FileName.ext/DestFolder"


        # --- SUMMARY Command ---
        elif command == 'SUMMARY' and arg_string:
            # Format: SUMMARY/FolderName
            try:
                # Call the summary logic
                summary_text = drive_assistant.summarize_folder(drive, arg_string, OPENAI_API_KEY, OPENAI_MODEL_NAME)
                result_msg = summary_text

            except Exception as e:
                # Catch any error during summary generation or API call
                print(f"Error during SUMMARY execution: {e}")
                result_msg = f"❌ An error occurred during summary generation: {e}"

        # If any slash command was executed, return the result
        if result_msg:
            return send_whatsapp_response(result_msg)


    # 4. Fallback/Help
    help_msg = (
        "*Drive Assistant Commands:*\n"
        "1. *SETUP*: Connect your Google Drive.\n"
        "2. *LIST/<Folder>*: List contents (e.g., `LIST/Reports`).\n"
        "3. *UPLOAD /<Folder> <New File Name>*: Attach a file and use this caption (e.g., `UPLOAD /Images my_pic.jpg`). If no new name is provided, original filename is used.\n"
        "4. *DELETE/<Folder>/<File>*: Delete a file (e.g., `DELETE/Docs/OldReport.pdf`).\n"
        "5. *MOVE/<SrcFolder>/<File>/<DestFolder>*: Move a file (e.g., `MOVE/Temp/Draft.doc/Final`).\n"
        "6. *RENAME OldName.ext NewName.ext*: Rename a file (e.g., `RENAME report.pdf final.pdf`).\n"
        "7. *SUMMARY/<Folder>*: Get an AI summary of text documents in a folder (requires OPENAI_API_KEY)."
    )
    return send_whatsapp_response(help_msg)


if __name__ == '__main__':
    drive_auth.write_secrets_to_file()
    app.run(debug=True, host='0.0.0.0', port=os.environ.get('PORT', 5000))
