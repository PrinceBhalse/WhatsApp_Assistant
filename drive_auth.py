import os
import json
import firebase_admin
from firebase_admin import firestore
# Changed import name to reflect the need for the base Flow class
from google_auth_oauthlib.flow import Flow 
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
                # We return False but allow the app to start so the user sees the message
                return False
            
            # The Admin SDK expects a dict, which the env var provides as a JSON string
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
        # Using Flow base class instead of InstalledAppFlow to be explicit, though functionality is similar
        flow = Flow.from_client_secrets_file(
            SECRETS_FILE_PATH, 
            DRIVE_SCOPE
        )

        # 3. Conditionally set the redirect_uri on the flow object
        redirect_uri = public_url + "/oauth/callback"
        flow.redirect_uri = redirect_uri
        
        # 4. Generate the URL
        # For Web Applications, the library automatically looks up redirect_uri 
        # from the flow object AND the client secrets file. 
        # Passing it as a keyword argument causes the "multiple values" error.
        
        # Check if the client type is 'installed' based on the JSON content
        if not is_web_app:
            # If it's not a web app, the library expects http://localhost, and our redirect_uri 
            # will likely cause errors on Google's side, but we should generate the URL anyway.
            print("WARNING: Client type detected as 'installed'. Authorization will likely fail on Google's side.")

        auth_url, _ = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true'
        )

        # 5. Success
        return auth_url, None

    except Exception as e:
        print(f"Error generating auth URL: {e}")
        return None, f"Error generating auth URL: {e}"


def exchange_code_for_token(auth_code, public_url):
    """Exchanges the authorization code for an access/refresh token."""
    
    if not write_secrets_to_file():
        return None, "Error: Invalid or missing Google Drive secrets configuration."

    try:
        is_web_app = 'web' in client_secrets_json_data
        redirect_uri = public_url + "/oauth/callback"

        flow = Flow.from_client_secrets_file(
            SECRETS_FILE_PATH, 
            DRIVE_SCOPE
        )
        flow.redirect_uri = redirect_uri # Set redirect_uri on flow object

        # Exchange the code. No need to pass redirect_uri as keyword argument here either.
        flow.fetch_token(code=auth_code)
            
        return flow.credentials, None

    except Exception as e:
        print(f"Error exchanging code for token: {e}")
        return None, f"Error exchanging code for token: {e}"

# --- Utility for Drive API Calls (unchanged) ---

def build_drive_service(user_id):
    """Builds a credentials object using the stored refresh token."""
    token_data = load_credentials(user_id)
    if not token_data:
        return None, "Drive not connected. Send 'SETUP' first."
    
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
        
        return creds, None

    except Exception as e:
        print(f"Error building credentials: {e}")
        return None, f"Error building credentials: {e}"

# Ensure Firestore is initialized on load
# This is wrapped to prevent app crash if config is missing
if initialize_firestore_client():
    print("Firestore initialization confirmed.")
else:
    print("Firestore initialization skipped due to missing config.")
