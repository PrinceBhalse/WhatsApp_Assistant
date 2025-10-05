import sqlite3
import os

# Determine the database path: use /tmp/ in production (like Render),
# and the current directory (.) locally.
DATABASE_PATH = os.path.join(os.getenv('TEMP_DIR', '.'), 'user_creds.db') 


def init_db():
    """Initializes the SQLite database and the users table."""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                whatsapp_number TEXT PRIMARY KEY,
                refresh_token TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        # Important for debugging deployment
        print(f"Error initializing database at {DATABASE_PATH}: {e}")


def save_user_token(whatsapp_number, refresh_token):
    """Saves or updates a user's Google Drive refresh token."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    # INSERT OR REPLACE handles both new users and updating tokens
    cursor.execute(
        "INSERT OR REPLACE INTO users (whatsapp_number, refresh_token) VALUES (?, ?)",
        (whatsapp_number, refresh_token)
    )
    conn.commit()
    conn.close()


def get_user_token(whatsapp_number):
    """Retrieves a user's Google Drive refresh token."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT refresh_token FROM users WHERE whatsapp_number = ?", 
        (whatsapp_number,)
    )
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None
