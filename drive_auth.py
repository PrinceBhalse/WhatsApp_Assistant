import os
import json
import firebase_admin
from firebase_admin import firestore
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from urllib.parse import urlparse

# --- Configuration ---
DRIVE_SCOPE = ['https://www.googleapis.com/auth/drive.metadata.readonly']

# Global variables (Initialized later)
db = None
auth = None
client_secrets_json_data = {}

# --- Firestore Paths and Secrets ---
# App ID is globally provided by the environment
app_id = os.getenv('__app_id', 'default-app-id') 

# Directory where the client_secrets.json file will be temporarily written
TEMP_DIR = os.getenv('TEMP_DIR', '/tmp') 
SECRETS_FILE_PATH = os.path.join(TEMP_DIR, 'client_secrets.json')

# --- Helper Functions ---

def initialize_firestore_client():
    """Initializes the Firestore client and attempts to authenticate."""
    global db
    try:
        if not firebase_admin._apps:
            # We assume firebaseConfig is passed as a string or available in the env
            firebase_config_str = os.getenv('__firebase_config')
            if not firebase_config_str:
                print("FATAL: Firebase config not found in __firebase_config environment variable.")
                return False
            
            firebase_config = json.loads(firebase_config_str)
            
            # Initialize the Firebase App
            cred = firebase_admin.credentials.Certificate(firebase_config)
            firebase_admin.initialize_app(cred)

        db = firestore.client()
        print("Firestore client initialized successfully.")
        return True
    except Exception as e:
        print(f"Error initializing Firestore: {e}")
        return False

def get_db():
    """Returns the initialized Firestore client."""
    if db is None:
        initialize_firestore_client()
    return db

def get_token_doc_ref(user_id):
    """Gets the Firestore Document Reference for a user's token."""
    # Private data path: /artifacts/{appId}/users/{userId}/tokens/{tokenDoc}
    return get_db().document(f'artifacts/{app_id}/users/{user_id}/tokens/drive_token')

def store_credentials(user_id, credentials):
    """Stores the Google Drive credentials (refresh token) for a user."""
    try:
        doc_ref = get_token_doc_ref(user_id)
        token_data = {
            'refresh_token': credentials.refresh_token,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'token_uri': credentials.token_uri,
            'scopes': credentials.scopes
        }
        doc_ref.set(token_data)
        print(f"Credentials successfully stored for user: {user_id}")
    except Exception as e:
        print(f"Error storing credentials for {user_id}: {e}")

def load_credentials(user_id):
    """Loads and rebuilds Google Drive credentials for a user."""
    try:
        doc_ref = get_token_doc_ref(user_id)
        doc = doc_ref.get()
        if doc.exists:
            token_data = doc.to_dict()
            # The token data is not a complete Credentials object, but contains the necessary parts
            # to be used by the google-auth library.
            # We don't need to rebuild Credentials object unless making API call.
            return token_data
        return None
    except Exception as e:
        print(f"Error loading credentials for {user_id}: {e}")
        return None

def write_secrets_to_file():
    """
    Reads secrets from the environment variable and stores them globally,
    then writes them to the file the library expects.
    """
    global client_secrets_json_data
    secrets_content = os.getenv('GOOGLE_DRIVE_SECRETS_CONTENT')
    if secrets_content:
        try:
            # 1. Store the JSON data globally for inspection
            client_secrets_json_data = json.loads(secrets_content)
            
            # 2. WRITE the content to the file the library expects
            os.makedirs(TEMP_DIR, exist_ok=True)
            with open(SECRETS_FILE_PATH, 'w') as f:
                f.write(secrets_content)
            
            print(f"Successfully wrote secrets content to {SECRETS_FILE_PATH}")
            return True
        except json.JSONDecodeError as e:
            # This handles the case where the JSON is malformed
            print(f"FATAL Error parsing GOOGLE_DRIVE_SECRETS_CONTENT: {e}")
            return False
    else:
        print("FATAL: GOOGLE_DRIVE_SECRETS_CONTENT environment variable is missing.")
        return False

# --- Core OAuth Functions ---

def generate_auth_url(public_url):
    """Generates the Google authorization URL."""
    
    # 1. Ensure secrets are loaded and written to file
    if not write_secrets_to_file():
        return None, "Error: Invalid or missing Google Drive secrets configuration."

    try:
        # 2. Determine Client Type and set flow arguments
        
        # Check if the JSON contains the 'web' key for Web Application flow
        is_web_app = 'web' in client_secrets_json_data

        # We must read the configuration from the file we just wrote
        flow = InstalledAppFlow.from_client_secrets_file(
            SECRETS_FILE_PATH, 
            DRIVE_SCOPE
        )

        # 3. Conditionally add redirect_uri based on client type
        # The 'redirect_uri' parameter is ONLY expected for Web Applications
        # If we pass it for an 'installed' (Desktop) app, it throws the error:
        # "got an unexpected keyword argument 'redirect_uri'"
        
        redirect_uri = public_url + "/oauth/callback"

        if is_web_app:
            # For Web App, we pass the custom redirect_uri
            auth_url, _ = flow.authorization_url(
                redirect_uri=redirect_uri,
                access_type='offline',
                include_granted_scopes='true'
            )
        else:
            # For Installed App, the library expects no redirect_uri argument
            # This is the fix for the persistent error.
            auth_url, _ = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true'
            )
            # IMPORTANT: If this runs, the callback will go to http://localhost, which is wrong for Render.
            # This confirms the configuration MUST be a Web App.
            print("WARNING: Client type detected as 'installed'. Authorization will fail on Render.")


        # 4. Success
        return auth_url, None

    except Exception as e:
        print(f"Error generating auth URL: {e}")
        return None, f"Error generating auth URL: {e}"


def exchange_code_for_token(auth_code, public_url):
    """Exchanges the authorization code for an access/refresh token."""
    
    # 1. Ensure secrets are loaded and written to file
    if not write_secrets_to_file():
        return None, "Error: Invalid or missing Google Drive secrets configuration."

    try:
        # 2. Determine Client Type and set flow arguments
        is_web_app = 'web' in client_secrets_json_data
        redirect_uri = public_url + "/oauth/callback"

        flow = InstalledAppFlow.from_client_secrets_file(
            SECRETS_FILE_PATH, 
            DRIVE_SCOPE
        )

        # 3. Conditionally fetch token based on client type
        if is_web_app:
            # For Web App, we must supply the redirect_uri used in the authorization step
            flow.fetch_token(code=auth_code, redirect_uri=redirect_uri)
        else:
            # For Installed App, we do not supply redirect_uri
            flow.fetch_token(code=auth_code)
            print("WARNING: Client type detected as 'installed'. Token exchange may fail.")
            
        # 4. Success
        return flow.credentials, None

    except Exception as e:
        # This catches errors during the token exchange (e.g., invalid code, client ID)
        print(f"Error exchanging code for token: {e}")
        return None, f"Error exchanging code for token: {e}"

# --- Utility for Drive API Calls ---

def build_drive_service(user_id):
    """Builds a credentials object using the stored refresh token."""
    token_data = load_credentials(user_id)
    if not token_data:
        return None, "Drive not connected. Send 'SETUP' first."
    
    # Check if secrets were loaded (necessary for rebuilding credentials)
    if not client_secrets_json_data and not write_secrets_to_file():
        return None, "Failed to load client configuration."
        
    try:
        # We need to extract the client configuration based on the type
        if 'web' in client_secrets_json_data:
            client_config = client_secrets_json_data['web']
        elif 'installed' in client_secrets_json_data:
            client_config = client_secrets_json_data['installed']
        else:
            return None, "Invalid Google Drive secrets file type."

        # Rebuild Credentials object from stored data and client config
        creds = Request().make_authorization_credentials(
            token=None, 
            refresh_token=token_data.get('refresh_token'),
            client_id=client_config.get('client_id'),
            client_secret=client_config.get('client_secret'),
            token_uri=client_config.get('token_uri'),
            scopes=token_data.get('scopes')
        )
        
        # NOTE: Drive Service object is usually built using googleapiclient.discovery.build
        # We only return the Credentials object here, as the Flask app handles the API call.
        return creds, None

    except Exception as e:
        print(f"Error building credentials: {e}")
        return None, f"Error building credentials: {e}"

# Ensure Firestore is initialized on load
initialize_firestore_client()
