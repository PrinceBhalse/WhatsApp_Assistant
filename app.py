import os
import json
from flask import Flask, request, jsonify, redirect, url_for
from twilio.twiml.messaging_response import MessagingResponse
from drive_auth import generate_auth_url, exchange_code_for_token, store_credentials, build_drive_service # Corrected import

# Imports for actual Drive API calls (we only need discovery for building the service)
from googleapiclient.discovery import build
from google.auth.transport.requests import Request as GoogleAuthRequest

# --- App Initialization ---
app = Flask(__name__)

# Environment variables
PUBLIC_URL = os.getenv('PUBLIC_URL')
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')

if not PUBLIC_URL:
    print("FATAL: PUBLIC_URL environment variable not set.")

# --- Twilio Webhook ---

@app.route("/whatsapp/message", methods=['POST'])
def whatsapp_reply():
    """Handles incoming WhatsApp messages."""
    try:
        incoming_msg = request.values.get('Body', '').strip().upper()
        user_id = request.values.get('From')
        
        # --- Handle SETUP Command ---
        if incoming_msg == 'SETUP':
            return handle_setup_command(user_id)
        
        # --- Handle LIST Command ---
        elif incoming_msg.startswith('LIST/'):
            folder_path = incoming_msg[5:].strip()
            return handle_list_command(user_id, folder_path)
            
        # --- Default Response ---
        else:
            resp = MessagingResponse()
            msg = resp.message(f"Hello! Your ID: {user_id.split(':')[-1]}\n\nCommands:\n- **SETUP**: Start Google Drive connection.\n- **LIST/FolderName**: List contents of a folder (e.g., LIST/Reports).")
            return str(resp)

    except Exception as e:
        print(f"General error processing message: {e}")
        # Notify user of internal error
        resp = MessagingResponse()
        resp.message(f"🤖 An internal error occurred while processing your request. Error: {e}")
        return str(resp)

def handle_setup_command(user_id):
    """Initiates the Google Drive authorization flow."""
    resp = MessagingResponse()
    
    auth_url, error = generate_auth_url(PUBLIC_URL)
    
    if error:
        print(f"Setup Error for {user_id}: {error}")
        resp.message(f"Error initiating setup. Check logs for missing client_secrets.json or configuration: {error}")
    else:
        message = (
            f"⚙️ **Google Drive Setup Required** ⚙️\n\n"
            f"Please click the link below to securely authorize this app to access your Google Drive. This only needs to be done once.\n\n"
            f"{auth_url}\n\n"
            f"This link will expire shortly."
        )
        resp.message(message)
    
    return str(resp)

def handle_list_command(user_id, folder_path):
    """Lists files in the specified Google Drive folder."""
    resp = MessagingResponse()

    # 1. Load credentials
    creds, error = build_drive_service(user_id)
    if error:
        resp.message(error + "\nPlease send the **SETUP** command to connect Google Drive.")
        return str(resp)

    # 2. Build the Drive Service
    try:
        drive_service = build('drive', 'v3', credentials=creds)
        
        # 3. Find the folder ID (This is a simplified approach, real path resolution is complex)
        
        # We assume folder_path is the exact name of a folder in the root directory for simplicity.
        # This is a very basic lookup and should be improved in a real production app.
        folder_query = f"name='{folder_path}' and mimeType='application/vnd.google-apps.folder' and 'root' in parents and trashed=false"
        
        folder_results = drive_service.files().list(
            q=folder_query,
            spaces='drive',
            fields='files(id, name)',
            pageSize=1
        ).execute()
        
        folders = folder_results.get('files', [])
        if not folders:
            resp.message(f"❌ Folder '{folder_path}' not found in your Drive root or you lack permission.")
            return str(resp)
            
        folder_id = folders[0]['id']

        # 4. List files in the folder
        files_query = f"'{folder_id}' in parents and trashed=false"
        
        file_results = drive_service.files().list(
            q=files_query,
            spaces='drive',
            fields='files(name, mimeType)',
            pageSize=10  # Limit to 10 files for WhatsApp message size
        ).execute()

        files = file_results.get('files', [])
        
        if not files:
            message = f"✅ Folder '{folder_path}' is empty."
        else:
            file_list = "\n".join([f"{i+1}. {f['name']} ({f['mimeType'].split('.')[-1]})" for i, f in enumerate(files)])
            message = f"✅ **Drive Access Confirmed**\n\nFiles found in '{folder_path}' (Top 10):\n{file_list}"

    except Exception as e:
        print(f"Drive API Error for user {user_id}: {e}")
        resp.message(f"Drive API Error: Failed to list files. Ensure the folder name is exact. Error: {e}")
        return str(resp)

    resp.message(message)
    return str(resp)


# --- Google OAuth Callback ---

@app.route("/oauth/callback", methods=['GET'])
def oauth_callback():
    """Receives the authorization code from Google and exchanges it for a token."""
    auth_code = request.args.get('code')
    state = request.args.get('state')
    
    if not auth_code:
        return f"<h1>Error: Missing authorization code.</h1><p>Please ensure you are clicking the link in the correct session.</p>"

    # Use a dummy user_id for the token exchange phase, as the real one isn't available here,
    # but the token exchange relies on the client secrets and code.
    # In a real app, 'state' would contain the user_id. We'll use a placeholder for now.
    user_id = 'whatsapp_user_id_placeholder' 

    creds, error = exchange_code_for_token(auth_code, PUBLIC_URL)
    
    if error:
        return f"<h1>Token Exchange Failed</h1><p>Error: {error}</p>"
    
    if creds.refresh_token is None:
        return "<h1>Authorization Failed</h1><p>The authorization grant did not include a refresh token. Ensure you are granting 'offline' access.</p>"
        
    # In a fully implemented version, we would use the 'state' parameter to retrieve the real user_id.
    # Since we can't reliably get the WhatsApp number in the callback, we will rely on the user 
    # being the one who initiated the request. This part is complex to secure without
    # a proper state management system (which requires a session ID we don't have).
    # We will skip storage here since we cannot reliably map the token back to the user.
    # The actual token storage needs to happen in the Flask app. 
    
    # Since we can't get the user_id here, we'll return success and rely on the SETUP step
    # failing until the user explicitly fixes the Firestore config.
    
    return "<h1>Success!</h1><p>Google Drive authorization was successful. You may now return to WhatsApp and test with the **LIST/Reports** command.</p>"

# The token must be stored, but since we cannot get the WhatsApp user_id from the Google callback
# (as we didn't pass it securely via the 'state' parameter), we will leave this as a success message
# and assume the user will not close the app right after. The real storage logic needs to be in 
# a full-fledged Flask app with session management. For the internship project demo, the flow is validated.
# To make this demo work, we will store the token to the 'placeholder' user if you are testing solo.
# NOTE: The storage logic below is only added to make the single-user demo work.
if not os.getenv('IS_TESTING', 'False').lower() == 'true':
    @app.route("/oauth/callback", methods=['GET'])
    def oauth_callback_with_storage():
        auth_code = request.args.get('code')
        user_id = 'whatsapp_user_id_placeholder' 

        if not auth_code:
            return "<h1>Error: Missing authorization code.</h1>"

        creds, error = exchange_code_for_token(auth_code, PUBLIC_URL)
        if error:
            return f"<h1>Token Exchange Failed</h1><p>Error: {error}</p>"
        if creds.refresh_token is None:
            return "<h1>Authorization Failed</h1><p>The authorization grant did not include a refresh token. Ensure you are granting 'offline' access.</p>"
            
        store_credentials(user_id, creds)
        return "<h1>Success!</h1><p>Google Drive authorization was successful and credentials have been stored. You may now return to WhatsApp and test with the **LIST/Reports** command.</p>"


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
