import os
import json
import firebase_admin
from firebase_admin import firestore
from google_auth_oauthlib.flow import Flow 
from google.auth.transport.requests import Request
from urllib.parse import urlparse
import base64 # Added base64 import

# --- Configuration ---
DRIVE_SCOPE = ['https://www.googleapis.com/auth/drive'] 

# Global variables (Initialized later)
db = None
client_secrets_json_data = {}

# --- Firestore Paths and Secrets ---
app_id = os.getenv('__app_id', 'default-app-id') 
TEMP_DIR = os.getenv('TEMP_DIR', '/tmp') 
SECRETS_FILE_PATH = os.path.join(TEMP_DIR, 'client_secrets.json')

# --- Helper Functions (omitted for brevity, assume they are correct from last turn) ---

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
            'scopes': credentials.scopes
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
    
    # 1. Ensure secrets are loaded and written to file
    if not write_secrets_to_file():
        return None, "Error: Invalid or missing Google Drive secrets configuration."

    try:
        # 2. Determine Client Type and set flow arguments
        is_web_app = 'web' in client_secrets_json_data

        flow = Flow.from_client_secrets_file(
            SECRETS_FILE_PATH, 
            DRIVE_SCOPE
        )

        # 3. Conditionally set the redirect_uri on the flow object
        redirect_uri = public_url + "/oauth/callback"
        flow.redirect_uri = redirect_uri
        
        # 4. Generate the URL, passing our custom encoded user ID as the state
        if not is_web_app:
            print("WARNING: Client type detected as 'installed'. Authorization will likely fail on Google's side.")

        # CRITICAL FIX: The library generates a state token by default. 
        # We pass our encoded user ID as the custom state argument.
        auth_url, _ = flow.authorization_url(
            state=encoded_user_id, # <--- Pass the encoded user ID directly as state
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent' 
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
        redirect_uri = public_url + "/oauth/callback"

        flow = Flow.from_client_secrets_file(
            SECRETS_FILE_PATH, 
            DRIVE_SCOPE
        )
        flow.redirect_uri = redirect_uri

        flow.fetch_token(code=auth_code)
            
        return flow.credentials, None

    except Exception as e:
        print(f"Error exchanging code for token: {e}")
        return None, f"Error exchanging code for token: {e}"

# --- Utility for Drive API Calls (omitted for brevity) ---

def build_drive_service(user_id):
    """Builds a credentials object using the stored refresh token."""
    token_data = load_credentials(user_id)
    if not token_data:
        return None, "Drive not connected. Send 'SETUP' first."
    
    if not client_secrets_json_data and not write_secrets_to_file():
        return None, "Failed to load client configuration."
        
    try:
        if 'web' in client_secrets_json_data:
            client_config = client_secrets_json_data['web']
        elif 'installed' in client_secrets_json_data:
            client_config = client_secrets_json_data['installed']
        else:
            return None, "Invalid Google Drive secrets file type."

        from googleapiclient.discovery import build
        
        creds = Request().make_authorization_credentials(
            token=None, 
            refresh_token=token_data.get('refresh_token'),
            client_id=client_config.get('client_id'),
            client_secret=client_config.get('client_secret'),
            token_uri=client_config.get('token_uri'),
            scopes=token_data.get('scopes')
        )
        
        service = build('drive', 'v3', credentials=creds)
        return service, None

    except Exception as e:
        print(f"Error building credentials or Drive service: {e}")
        return None, f"Error building credentials or Drive service: {e}"

# Ensure Firestore is initialized on load
initialize_firestore_client()
