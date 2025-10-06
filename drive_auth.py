import os
import json
import firebase_admin
from firebase_admin import firestore
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
# IMPORTANT: Need to import the Credentials object directly
from google.oauth2.credentials import Credentials
from urllib.parse import urlparse
import base64
from googleapiclient.discovery import build  # Using native Google API Client

# --- Configuration ---
DRIVE_SCOPE = ['https://www.googleapis.com/auth/drive']

# Global variables (Initialized later)
db = None
client_secrets_json_data = {}

# --- Firestore Paths and Secrets ---
# Note: Using 'default-app-id' as __app_id is not available in local env
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
            # The Admin SDK expects service account credentials directly, not the Firebase config object.
            # Assuming __firebase_config contains service account credentials JSON.
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
            # Storing the actual token data fields needed for reconstruction
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
        print(
            f"Error: Could not get Firestore document reference for loading credentials for user: {user_id}. DB may be uninitialized.")
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
        flow = Flow.from_client_secrets_file(
            SECRETS_FILE_PATH,
            DRIVE_SCOPE
        )
        redirect_uri = public_url + "/oauth/callback"
        flow.redirect_uri = redirect_uri # Set redirect_uri on the flow object

        auth_url, _ = flow.authorization_url(
            state=encoded_user_id,
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
            # FIX: Removed the redundant 'redirect_uri' keyword argument
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


# --- Utility for Drive API Calls ---

def build_drive_service(user_id):
    """
    Builds the Google Drive API service object (native API).
    This function is unchanged from your working version.
    """
    token_data = load_credentials(user_id)
    if not token_data:
        return None, "Drive not connected. Send 'SETUP' first."

    # 1. Ensure secrets are loaded to get client_id/secret for reconstruction
    if not client_secrets_json_data and not write_secrets_to_file():
        return None, "Failed to load client configuration."

    try:

        # 2. Reconstruct Credentials object using the data loaded from Firestore
        creds = Credentials(
            token=None,  # Token is dynamic, we use the refresh token
            refresh_token=token_data.get('refresh_token'),
            # The following required fields come from the stored token data
            client_id=token_data.get('client_id'),
            client_secret=token_data.get('client_secret'),
            token_uri=token_data.get('token_uri'),
            scopes=token_data.get('scopes')
        )

        # 3. Request a fresh access token using the refresh token
        # This uses the Request object correctly to refresh the token
        creds.refresh(Request())

        # 4. Build the Drive Service (native googleapiclient)
        service = build('drive', 'v3', credentials=creds)
        return service, None

    except Exception as e:
        print(f"Error building credentials or Drive service: {e}")
        return None, f"Error building credentials or Drive service: '{e}'"


# Ensure Firestore is initialized on load
initialize_firestore_client()
