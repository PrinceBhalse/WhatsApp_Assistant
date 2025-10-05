from db_manager import init_db, save_user_token
import json
import os

# Ensure the database is initialized
init_db()

# 1. Define the user and the path to your locally generated credentials
TEST_WHATSAPP_NUMBER = 'whatsapp:+15551234567' # Use the number from your cURL tests
CREDS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mycreds.json')

try:
    with open(CREDS_FILE_PATH, 'r') as f:
        data = json.load(f)
        user_refresh_token = data.get('refresh_token')

    if user_refresh_token:
        save_user_token(TEST_WHATSAPP_NUMBER, user_refresh_token)
        print(f"✅ Successfully inserted your Drive token for user: {TEST_WHATSAPP_NUMBER}")
        print("You can now test commands from this user.")
    else:
        print("❌ Error: 'refresh_token' not found in mycreds.json.")

except FileNotFoundError:
    print(f"❌ Error: {CREDS_FILE_PATH} not found. Please run drive_auth.py first.")
except Exception as e:
    print(f"An unexpected error occurred: {e}")
