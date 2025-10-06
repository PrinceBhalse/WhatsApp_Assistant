import os
import json
import firebase_admin
from firebase_admin import firestore
from google_auth_oauthlib.flow import Flow 
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials 
from urllib.parse import urlparse
import base64 

# REQUIRED IMPORTS FOR PYDRIVE2 FIX
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

# --- Configuration ---
DRIVE_SCOPE = ['https://www.googleapis.com/auth/drive'] 

# Global variables (Initialized later)
db = None
client_secrets_json_data = {}

# --- Firestore Paths and Secrets ---
app_id = os.getenv('__app_id', 'default-app-id') 
TEMP_DIR = os.getenv('TEMP_DIR', '/tmp') 
SECRETS_FILE_PATH = os.path.join(TEMP_DIR, 'client_secrets.json')

# --- Helper Functions (Firestore Initialization) ---

def initialize_firestore_client():
    """Initializes the Firestore client and attempts to authenticate."""
    global db
    if db is not None:
        return True
        
    try:
        if not firebase_admin._apps:
            firebase_config_str = os.getenv('__firebase_config')
            if not firebase_config_str:
                print("FATAL: Firebase config not found in __firebase_config environment variable.")
                return False
            
            firebase_config = json.loads(firebase_config_str)
            cred = firebase_admin.credentials.Certificate(firebase_config)
            firebase_admin.initialize_app(cred)

        db = firestore.client()
        print("Firestore client initialized successfully.")
        return True
    except Exception as e:
        print(f"Error initializing Firestore: {e}")
        return False

def get_db():
    """Returns the initialized Firestore client, ensuring initialization first."""
    if db is None:
        initialize_firestore_client()
    return db

def get_token_doc_ref(user_id):
    """Gets the Firestore Document Reference for a user's token."""
    if get_db():
        return db.document(f'artifacts/{app_id}/users/{user_id}/tokens/drive_token')
    return None

def store_credentials(user_id, credentials):
    """Stores the Google Drive credentials (refresh token) for a user."""
    doc_ref = get_token_doc_ref(user_id)
    if not doc_ref:
        print(f"Error: Could not get Firestore document reference for storing credentials for user: {user_id}")
        return

    try:
        if not credentials.refresh_token:
            print(f"Skipping credential storage for {user_id}: No refresh token received.")
            return 
            
        token_data = {
            'refresh_token': credentials.refresh_token,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'token_uri': credentials.token_uri,
            'scopes': credentials.scopes,
            # Also store the current access token for immediate use by pydrive2
            'access_token': credentials.token, 
            'token_expiry': credentials.expiry.isoformat() if credentials.expiry else None
        }
        doc_ref.set(token_data)
        print(f"Credentials successfully stored for user: {user_id}")
    except Exception as e:
        print(f"Error storing credentials for {user_id}: {e}")

def load_credentials(user_id):
    """Loads and rebuilds Google Drive credentials for a user."""
    doc_ref = get_token_doc_ref(user_id)
    if not doc_ref:
        print(f"Error: Could not get Firestore document reference for loading credentials for user: {user_id}. DB may be uninitialized.")
        return None

    try:
        print(f"Attempting to load token from path: {doc_ref.path}")
        doc = doc_ref.get()
        if doc.exists:
            token_data = doc.to_dict()
            print(f"Token loaded successfully for user: {user_id}. Scopes: {token_data.get('scopes')}")
            return token_data
        
        print(f"No token found for user: {user_id} at path: {doc_ref.path}")
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
            client_secrets_json_data = json.loads(secrets_content)
            os.makedirs(TEMP_DIR, exist_ok=True)
            with open(SECRETS_FILE_PATH, 'w') as f:
                f.write(secrets_content)
            
            print(f"Successfully wrote secrets content to {SECRETS_FILE_PATH}")
            return True
        except json.JSONDecodeError as e:
            print(f"FATAL Error parsing GOOGLE_DRIVE_SECRETS_CONTENT: {e}")
            return False
    else:
        print("FATAL: GOOGLE_DRIVE_SECRETS_CONTENT environment variable is missing.")
        return False

# --- Core OAuth Functions ---

def generate_auth_url(public_url, encoded_user_id):
    """Generates the Google authorization URL, using encoded_user_id as state."""
    if not write_secrets_to_file():
        return None, "Error: Invalid or missing Google Drive secrets configuration."
    try:
        flow = Flow.from_client_secrets_file(SECRETS_FILE_PATH, DRIVE_SCOPE)
        redirect_uri = public_url + "/oauth/callback"
        flow.redirect_uri = redirect_uri
        
        auth_url, _ = flow.authorization_url(
            state=encoded_user_id,
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent' 
        )
        return auth_url, None
    except Exception as e:
        print(f"Error generating auth URL: {e}")
        return None, f"Error generating auth URL: {e}"


def exchange_code_for_token(auth_code, public_url):
    """Exchanges the authorization code for an access/refresh token."""
    if not write_secrets_to_file():
        return None, "Error: Invalid or missing Google Drive secrets configuration."
    try:
        redirect_uri = public_url + "/oauth/callback"
        flow = Flow.from_client_secrets_file(SECRETS_FILE_PATH, DRIVE_SCOPE)
        flow.redirect_uri = redirect_uri
        flow.fetch_token(code=auth_code)
        return flow.credentials, None
    except Exception as e:
        print(f"Error exchanging code for token: {e}")
        return None, f"Error exchanging code for token: {e}"

# --- PYDRIVE2 COMPATIBILITY FIX ---

def build_drive_credentials(user_id):
    """
    FIX: Now returns a configured pydrive2.auth.GoogleAuth object, 
    which correctly handles token expiration and refreshing required by pydrive2.
    """
    token_data = load_credentials(user_id)
    if not token_data:
        return None, "Drive not connected. Send 'SETUP' first."
    
    # 1. Initialize GoogleAuth
    # The 'settings' argument points pydrive2 to the client secrets file
    gauth = GoogleAuth(settings={'client_config_file': SECRETS_FILE_PATH})
    
    # 2. Load the token data into pydrive2's format
    try:
        # pydrive2 needs the token saved in its internal format (a dictionary)
        pydrive2_token = {
            'refresh_token': token_data.get('refresh_token'),
            'access_token': token_data.get('access_token'), # Use the last known token
            'token_expiry': token_data.get('token_expiry'),
            'client_id': token_data.get('client_id'),
            'client_secret': token_data.get('client_secret'),
            'token_uri': token_data.get('token_uri'),
            'scope': token_data.get('scopes')
        }
        
        # 3. Set the loaded credentials. This creates the necessary internal attributes.
        gauth.LoadCredentials(pydrive2_token)
        
        # 4. Check if we need to refresh (pydrive2's internal mechanism)
        if gauth.access_token_expired:
            gauth.Refresh()
            
        return gauth, None

    except Exception as e:
        print(f"Error building or refreshing pydrive2 credentials: {e}")
        return None, f"Error building or refreshing pydrive2 credentials: '{e}'"


def build_drive_service(user_id):
    """Gets the PyDrive2 GoogleDrive object using the compatible GoogleAuth object."""
    gauth, auth_error = build_drive_credentials(user_id)
    if auth_error:
        return None, auth_error
    
    try:
        # Initialize GoogleDrive using the configured GoogleAuth object
        drive = GoogleDrive(gauth)
        return drive, None
    except Exception as e:
        return None, f"Error initializing GoogleDrive (PyDrive2): {e}"


# Ensure Firestore is initialized on load
initialize_firestore_client()
