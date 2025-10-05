# drive_auth.py (FINAL MULTI-USER VERSION - PRODUCTION READY)
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
import os
from dotenv import load_dotenv

load_dotenv()

# --- Global Paths (Adjusted for Production) ---
TEMP_DIR = os.getenv('TEMP_DIR', '.')
SECRETS_FILE_PATH = os.path.join(TEMP_DIR, 'client_secrets.json')
DRIVE_SCOPE = ['https://www.googleapis.com/auth/drive']


# ---------------------------------------------


def write_secret_files_from_env():
    """
    Writes client_secrets.json from environment variable (production)
    or copies it from the local file (setup/testing).
    """

    # 1. Ensure the temporary directory exists
    if not os.path.exists(TEMP_DIR):
        os.makedirs(TEMP_DIR)

    # 2. Check ENV variable (used in deployment)
    secrets_content = os.getenv('GOOGLE_DRIVE_SECRETS_CONTENT')
    if secrets_content:
        with open(SECRETS_FILE_PATH, 'w') as f:
            f.write(secrets_content)
    else:
        # Fallback to local file
        local_secrets_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'client_secrets.json')
        if os.path.exists(local_secrets_path):
            import shutil
            shutil.copyfile(local_secrets_path, SECRETS_FILE_PATH)
        else:
            raise RuntimeError(
                "Google Drive Secrets content not found. Set GOOGLE_DRIVE_SECRETS_CONTENT or place client_secrets.json.")


def get_user_drive_service(refresh_token):
    """Initializes GoogleDrive service for a specific user using their refresh token."""

    # Ensure client secrets file is ready
    write_secret_files_from_env()

    gauth = GoogleAuth()
    gauth.LoadClientConfigFile(SECRETS_FILE_PATH)

    # Manually construct credentials using the user's refresh token
    gauth.credentials = {
        'access_token': None,
        'client_id': gauth.client_id,
        'client_secret': gauth.client_secret,
        'refresh_token': refresh_token,
        'token_expiry': None,
        'token_response': {},
        'token_uri': 'https://oauth2.googleapis.com/token',
        'user_agent': gauth.user_agent,
        'revoke_uri': 'https://oauth2.googleapis.com/revoke',
        'scope': DRIVE_SCOPE
    }

    # Force a token refresh to get a valid access token
    gauth.Refresh()

    return GoogleDrive(gauth)


def generate_auth_url(whatsapp_number):
    """Generates the unique Google OAuth URL for a user to start the setup process."""
    write_secret_files_from_env()
    gauth = GoogleAuth()
    gauth.LoadClientConfigFile(SECRETS_FILE_PATH)

    # CRITICAL: Dynamically set the REDIRECT_URI using the PUBLIC_URL environment variable
    # This must match the URL you add to Google Cloud Console
    public_url = os.getenv('PUBLIC_URL')
    if not public_url:
        # Fallback to local for dev, but this must be set in production
        public_url = "http://127.0.0.1:5001"

    REDIRECT_URI = public_url + "/oauth/callback"

    # Set the OAuth flow details
    gauth.settings['auth_uri_param'] = {'access_type': 'offline'}
    gauth.settings['scope'] = DRIVE_SCOPE

    # Create the URL, using the whatsapp_number as the state parameter
    auth_url = gauth.GetAuthUrl(redirect_uri=REDIRECT_URI, state=whatsapp_number)

    # Get the flow object that will be used later to exchange the code
    # We call LocalWebserverAuth() just to initialize the flow object structure needed for gauth.Auth()
    gauth.LocalWebserverAuth()
    flow = gauth

    return auth_url, flow


if __name__ == '__main__':
    print("This file should not be run directly in the final multi-user application.")
    print("Authentication is now handled via the 'SETUP' command and web flow.")
