import os
import json
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from drive_auth import generate_auth_url, exchange_code_for_token, build_drive_service, store_credentials
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
import urllib.parse
import base64

app = Flask(__name__)

# --- Configuration ---
# Your Render public URL (e.g., https://my-app.onrender.com)
PUBLIC_URL = os.getenv('RENDER_EXTERNAL_URL') or 'http://localhost:5000'

# --- Utility Functions ---

def get_user_id(request_form):
    """Extracts the unique user ID (WhatsApp number) from the Twilio request."""
    # Twilio sends the number in the format: "whatsapp:+1234567890"
    from_number = request_form.get('From', '')
    user_id = from_number.split(':')[-1]
    
    if not user_id:
        print("Warning: Could not extract user_id from Twilio payload.")
        return from_number
        
    print(f"Extracted User ID: {user_id}")
    return user_id

def send_whatsapp_message(text):
    """Creates a Twilio TwiML response for WhatsApp."""
    resp = MessagingResponse()
    resp.message(text)
    return str(resp)

def format_drive_response(file_list, folder_name):
    """Formats the list of Google Drive files into a concise WhatsApp message."""
    if not file_list:
        return f"No files found in folder: '{folder_name}'"
    
    message = f"Files in '{folder_name}':\n"
    for i, item in enumerate(file_list[:10]): 
        name = item.get('name', 'Untitled')
        link = item.get('webViewLink')
        
        line = f"({i+1}) {name}"
        if link:
            # We don't want to use the long link URL in the message body, 
            # we just confirm the link exists.
            line += " (Link available)"
            
        message += line + "\n"
        
    if len(file_list) > 10:
        message += f"\n...and {len(file_list) - 10} more."
        
    return message.strip()

# --- Flask Routes ---

@app.route("/whatsapp/message", methods=['POST'])
def whatsapp_message():
    """Handles incoming WhatsApp messages from Twilio."""
    user_id = get_user_id(request.form)
    command = request.form.get('Body', '').strip()

    print(f"Received command: '{command}' from user: {user_id}")

    if command.upper() == 'SETUP':
        
        # CRITICAL FIX: Pass the user_id in the state parameter
        # We URL-safe base64 encode the user_id to ensure it survives the trip
        encoded_user_id = base64.urlsafe_b64encode(user_id.encode()).decode()
        
        auth_url, error = generate_auth_url(PUBLIC_URL)
        if error:
            print(f"Setup Error for {user_id}: {error}")
            return send_whatsapp_message(f"Error initiating setup. Check logs for missing client_secrets.json or configuration: {error}")
        
        # Append the encoded user_id to the authorization URL state parameter
        # The library generates a state, we append our custom info to it
        # We assume the library's generated state is already in auth_url, so we append our parameter cleanly.
        auth_url_with_state = f"{auth_url}&custom_user_id_state={encoded_user_id}"
        
        message = f"*Google Drive Setup Required*\n\nPlease click the link below to securely authorize this app to access your Google Drive. This only needs to be done once.\n\n{auth_url_with_state}\n\nThis link will expire shortly."
        return send_whatsapp_message(message)

    elif command.upper().startswith('LIST/'):
        # 1. Extract folder name
        try:
            folder_name = command.split('/', 1)[1].strip()
        except IndexError:
            return send_whatsapp_message("Invalid LIST command. Format must be LIST/<Folder Name> (e.g., LIST/Reports)")
        
        # 2. Get Drive service
        drive_service, auth_error = build_drive_service(user_id)
        
        if auth_error:
            # auth_error is 'Drive not connected. Send 'SETUP' first.'
            return send_whatsapp_message(auth_error + "\n\nPlease send the *SETUP* command to connect Google Drive.")

        # 3. Search Drive
        try:
            # Search for the folder ID first
            folder_q = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false"
            folder_results = drive_service.files().list(
                q=folder_q,
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageSize=1
            ).execute()
            
            folders = folder_results.get('files', [])
            if not folders:
                return send_whatsapp_message(f"Folder '{folder_name}' not found in your Drive root.")
            
            folder_id = folders[0]['id']
            
            # Search for files within that folder
            file_q = f"'{folder_id}' in parents and trashed=false"
            results = drive_service.files().list(
                q=file_q,
                spaces='drive',
                fields='nextPageToken, files(id, name, webViewLink)',
                pageSize=10 
            ).execute()
            
            items = results.get('files', [])
            
            return send_whatsapp_message(format_drive_response(items, folder_name))

        except HttpError as e:
            print(f"Drive API Error: {e}")
            return send_whatsapp_message(f"Error accessing Google Drive API. Code: {e.resp.status}. Please try *SETUP* again if the issue persists.")
        except Exception as e:
            print(f"Unexpected error during LIST command: {e}")
            return send_whatsapp_message(f"An unexpected error occurred: {e}")

    else:
        return send_whatsapp_message("Unknown command. Supported commands are *SETUP* and *LIST/<Folder Name>* (e.g., LIST/Reports).")

@app.route("/oauth/callback", methods=['GET'])
def oauth_callback():
    """Handles the redirect from Google after user authorization."""
    auth_code = request.args.get('code')
    
    # CRITICAL FIX: Extract the custom user_id from the query parameters
    encoded_user_id = request.args.get('custom_user_id_state')

    if not auth_code:
        return "Authorization Failed. No code received.", 400

    # 1. Decode user_id from the custom state parameter
    user_id = None
    try:
        if encoded_user_id:
            user_id = base64.urlsafe_b64decode(encoded_user_id).decode()
        
        if not user_id:
             print("Error: Could not decode user_id from state parameter.")
             return "Authorization Failed. Internal error: User identifier missing.", 400
        
    except Exception as e:
        print(f"Error decoding user_id from custom state: {e}")
        return "Authorization Failed. Internal error: User identifier decoding failed.", 400

    print(f"Callback received for user: {user_id}. Attempting token exchange.")
    
    # 2. Exchange the code for the token
    credentials, error = exchange_code_for_token(auth_code, PUBLIC_URL)

    if error:
        return f"Authorization Failed\nError: {error}", 400

    # 3. Store the refresh token against the correct user ID
    if credentials:
        store_credentials(user_id, credentials)
        
    return "Success! Google Drive authorization was successful. You may now return to WhatsApp and test with the **LIST/Documents** command.", 200

# Ensure Drive service initialization runs once on app load
# (This is handled by drive_auth.py's global call)

if __name__ == '__main__':
    app.run(debug=True, port=int(os.environ.get('PORT', 5000)))
