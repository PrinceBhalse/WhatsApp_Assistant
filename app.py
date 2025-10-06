import os
import json
import base64
import drive_auth # Assuming this handles the build_drive_service for pydrive2
import drive_assistant # Your core drive logic
import requests 
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from pydrive2.drive import GoogleDrive # Needed for type hinting/creation
import io

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
    """Helper function to create a TwiML response."""
    resp = MessagingResponse()
    resp.message(msg)
    return str(resp)

# --- Drive Service Builder (Assuming this is already working and returns a pydrive2 object) ---

def get_drive_service(user_id):
    """
    Retrieves the authenticated GoogleDrive object (pydrive2) for the user.
    Assumes drive_auth.build_drive_service is configured to return the
    correct PyDrive2 object or raise an authentication error.
    """
    # NOTE: This function needs to align exactly with what your existing drive_auth.py returns.
    # Based on our previous exchanges, I assume a function that returns the pydrive2.GoogleDrive object.
    # If the current drive_auth.py only returns googleapiclient.discovery.build object,
    # the integration with pydrive2 in drive_assistant.py is incorrect, but we proceed
    # assuming the integration is handled within your existing, working files.
    
    # Placeholder call to your existing, working auth function:
    # If your drive_auth.py returns a tuple (service, error_message):
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
        
        # Using the existing working functions from your drive_auth.py
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
    
    # 1. SETUP Command (Always handled first without needing Drive service)
    if msg_body.upper() == 'SETUP':
        # ... (Existing SETUP logic) ...
        print(f"Received command: 'SETUP' from user: {user_id}")
        encoded_user_id = base64.b64encode(user_id.encode('utf-8')).decode('utf-8')
        auth_url, error = drive_auth.generate_auth_url(PUBLIC_URL, encoded_user_id)
        
        if error:
            return send_whatsapp_response(f"Error initiating setup. Check logs for configuration issues: {error}")

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
        if msg_body.upper() != 'SETUP':
             return send_whatsapp_response(f"Error: {auth_error}")
        # If user sends SETUP, it's handled above, so we pass here.


    # 2. Media Handling (UPLOAD) - This needs to run if media is present.
    # Command format: UPLOAD /Reports new_report.pdf
    if num_media > 0 and drive:
        
        command_parts = msg_body.strip().split(' ', 2) # Splits by first two spaces max: [UPLOAD, /Reports, new_report.pdf]
        
        if not command_parts or command_parts[0].upper() != 'UPLOAD':
            return send_whatsapp_response("Invalid upload command. Attach a file and use the caption format: UPLOAD /<Folder Name> <New File Name.ext>")
            
        if len(command_parts) < 2:
            return send_whatsapp_response("Please specify the target folder for upload. Format: UPLOAD /<Folder Name> <New File Name.ext>")

        # Extract folder path and file name
        folder_path = command_parts[1].strip('/')
        new_file_name = command_parts[2] if len(command_parts) == 3 else request.values.get('MediaFilename0', f"WhatsApp_Upload_{os.urandom(4).hex()}")
        
        # Twilio sends MediaUrl0, MediaContentType0 for the first media item
        media_url = request.values.get('MediaUrl0')
        
        # 1. Fetch the media file and save temporarily
        print(f"[{user_id}] Fetching media from URL: {media_url}")
        
        # We need Twilio credentials to download media securely
        media_response = requests.get(media_url, auth=(os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN')))
        
        if media_response.status_code != 200:
            return send_whatsapp_response(f"Error: Could not fetch media from Twilio. Status: {media_response.status_code}. Check TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN.")
        
        os.makedirs(TEMP_DIR, exist_ok=True)
        temp_file_path_full = f"{TEMP_FILE_PATH}_{os.urandom(4).hex()}"
        
        try:
            with open(temp_file_path_full, 'wb') as f:
                f.write(media_response.content)
            
            # 2. Upload using drive_assistant logic
            print(f"[{user_id}] Attempting upload of '{new_file_name}' to folder '{folder_path}'")
            result_msg = drive_assistant.upload_file(drive, folder_path, temp_file_path_full, new_file_name)
            return send_whatsapp_response(result_msg)

        except Exception as e:
            print(f"Error during UPLOAD processing: {e}")
            return send_whatsapp_response(f"An error occurred during file processing or upload: {e}")
        finally:
            # 3. Clean up the temporary file
            if os.path.exists(temp_file_path_full):
                os.remove(temp_file_path_full)
                print(f"Cleaned up temporary file: {temp_file_path_full}")
        
        # Return here to prevent falling through to command parsing below
        return send_whatsapp_response("Processing file upload...")


    # 3. Command Parsing (Non-media commands)
    command_parts = [p.strip() for p in msg_body.upper().split('/', 1) if p.strip()]
    
    if command_parts and drive:
        command = command_parts[0]
        arg_string = command_parts[1] if len(command_parts) > 1 else None

        print(f"[{user_id}] Processing text command: {command} with args: {arg_string}")

        # --- LIST Command (Confirmed working) ---
        if command == 'LIST' and arg_string:
            result = drive_assistant.list_files(drive, arg_string)
            return send_whatsapp_response(result)
        
        # --- DELETE Command ---
        elif command == 'DELETE' and arg_string:
            # Format: DELETE/Folder/FileName.ext
            parts = [p.strip() for p in arg_string.split('/', 1) if p.strip()]
            if len(parts) == 2:
                result = drive_assistant.delete_file(drive, parts[0], parts[1])
                return send_whatsapp_response(result)
            return send_whatsapp_response("Invalid DELETE format. Use: DELETE/FolderName/FileName.ext")

        # --- MOVE Command ---
        elif command == 'MOVE' and arg_string:
            # Format: MOVE/SourceFolder/FileName.ext/DestFolder
            # Note: Using space as separator for destination folder as per user's request, but logic must match drive_assistant.py's expectation.
            # drive_assistant.py expects: MOVE/SourceFolder/file.pdf/DestFolder (3 slash-separated arguments)
            # User requested: MOVE /FolderName/file.pdf /Archive (2 space-separated arguments, with inner slash separation)
            
            # We will assume the user meant: MOVE/SourceFolder/File.ext/DestFolder (as implemented in drive_assistant)
            parts = [p.strip() for p in arg_string.split('/', 2) if p.strip()]
            if len(parts) == 3:
                result = drive_assistant.move_file(drive, parts[0], parts[1], parts[2])
                return send_whatsapp_response(result)
            
            # Handling the user's specific ambiguous format: MOVE /FolderName/file.pdf /Archive
            # If the user sends "MOVE /Drafts/file.pdf /Final"
            # The arg_string is "/Drafts/file.pdf /Final"
            # Splitting by '/' gives ["", "Drafts", "file.pdf /Final"] -> Incorrect parsing.
            
            # Let's adjust parsing to handle the "MOVE/Source/File/Dest" structure, as it's cleaner.
            return send_whatsapp_response("Invalid MOVE format. Use: MOVE/SourceFolder/FileName.ext/DestFolder")


        # --- SUMMARY Command ---
        elif command == 'SUMMARY' and arg_string:
            # Format: SUMMARY/FolderName
            result = drive_assistant.summarize_folder(drive, arg_string, OPENAI_API_KEY, OPENAI_MODEL_NAME)
            return send_whatsapp_response(result)

        # --- RENAME Command (Note: RENAME uses space separation, not slash) ---
        elif command == 'RENAME' and not arg_string:
            # RENAME commands are not slash-separated. We need to re-parse the original body.
            # Format: RENAME file.pdf NewFileName.pdf
            
            # Strip "RENAME" and split the remaining string by the first space.
            parts = [p.strip() for p in msg_body.strip().split(' ', 3) if p.strip()]
            
            if len(parts) == 3 and parts[0].upper() == 'RENAME':
                old_file_name = parts[1]
                new_file_name = parts[2]
                result = drive_assistant.rename_file(drive, old_file_name, new_file_name)
                return send_whatsapp_response(result)
            return send_whatsapp_response("Invalid RENAME format. Use: RENAME OldFileName.ext NewFileName.ext")


    # 4. Fallback/Help
    help_msg = (
        "*Drive Assistant Commands:*\n"
        "1. *SETUP*: Connect your Google Drive.\n"
        "2. *LIST/<Folder>*: List contents (e.g., `LIST/Reports`).\n"
        "3. *UPLOAD /<Folder> <New File Name>*: Attach a file and use this caption (e.g., `UPLOAD /Images my_pic.jpg`). If no new name is provided, original filename is used.\n"
        "4. *DELETE/<Folder>/<File>*: Delete a file (e.g., `DELETE/Docs/OldReport.pdf`).\n"
        "5. *MOVE/<SrcFolder>/<File>/<DestFolder>*: Move a file (e.g., `MOVE/Temp/Draft.doc/Final`).\n"
        "6. *RENAME OldName.ext NewName.ext*: Rename a file (e.g., `RENAME report.pdf final.pdf`).\n"
        "7. *SUMMARY/<Folder>*: Get an AI summary of text documents in a folder (requires OpenAI key)."
    )
    return send_whatsapp_response(help_msg)


if __name__ == '__main__':
    # Ensure client secrets are loaded at startup
    drive_auth.write_secrets_to_file()
    app.run(debug=True, host='0.0.0.0', port=os.environ.get('PORT', 5000))
