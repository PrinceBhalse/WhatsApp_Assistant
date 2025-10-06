import os
import io
import requests
import json
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError
from openai import OpenAI
from google.oauth2.credentials import Credentials

# --- Helper Functions ---

def get_folder_id(drive_service, folder_path):
    """
    Finds the ID of the folder based on its path (e.g., 'Reports/Q3/2025').
    Returns the folder ID or None if not found.
    """
    current_parent_id = 'root'  # Start from the root of Google Drive

    # Split the path, removing any leading/trailing slashes
    folder_names = [name.strip() for name in folder_path.split('/') if name.strip()]

    if not folder_names:
        return 'root' # If path is empty, return root

    for folder_name in folder_names:
        # Search for the current folder name within the current parent ID
        query = (
            f"'{current_parent_id}' in parents and "
            f"name = '{folder_name}' and "
            f"mimeType = 'application/vnd.google-apps.folder' and "
            "trashed = false"
        )
        try:
            results = drive_service.files().list(
                q=query,
                fields="files(id, name)",
                spaces='drive'
            ).execute()
            
            items = results.get('files', [])
            if not items:
                print(f"Folder not found: {folder_name} inside parent ID: {current_parent_id}")
                return None  # Path component not found

            # Found the folder, update the parent ID for the next iteration
            current_parent_id = items[0]['id']

        except HttpError as error:
            print(f"An error occurred while searching for folder: {error}")
            return None
    
    # After iterating through all parts, the last current_parent_id is the target folder ID
    return current_parent_id

def get_file_by_name(drive_service, folder_name, file_name):
    """
    Searches for a specific file name inside a specific folder.
    Returns the file ID or None.
    """
    folder_id = get_folder_id(drive_service, folder_name)

    if not folder_id:
        return None, f"Folder '{folder_name}' not found."

    query = (
        f"'{folder_id}' in parents and "
        f"name = '{file_name}' and "
        "trashed = false"
    )
    
    try:
        results = drive_service.files().list(
            q=query,
            fields="files(id, name)",
            spaces='drive'
        ).execute()

        items = results.get('files', [])
        if not items:
            return None, f"File '{file_name}' not found in folder '{folder_name}'."
        
        return items[0]['id'], None # Return the file ID and no error

    except HttpError as error:
        return None, f"An error occurred while searching for file: {error}"


def get_file_by_name_anywhere(drive_service, file_name):
    """
    Searches for a specific file name anywhere in the user's drive (used for RENAME).
    Returns the file ID or None.
    """
    # Note: Using the name field, not in a specific folder
    query = (
        f"name = '{file_name}' and "
        "trashed = false"
    )
    
    try:
        results = drive_service.files().list(
            q=query,
            fields="files(id, name)",
            spaces='drive'
        ).execute()

        items = results.get('files', [])
        if not items:
            return None, f"File '{file_name}' not found in your Drive."
        
        # Return the first matching file ID
        return items[0]['id'], None

    except HttpError as error:
        return None, f"An error occurred while searching for file: {error}"


# --- Command Implementations ---

def list_files(drive_service, folder_path):
    """
    Lists the files in the specified folder path.
    Returns a formatted string message.
    """
    folder_id = get_folder_id(drive_service, folder_path)

    if not folder_id:
        return f"❌ Folder '{folder_path}' not found."

    query = (
        f"'{folder_id}' in parents and "
        "trashed = false"
    )
    
    try:
        results = drive_service.files().list(
            q=query,
            fields="files(id, name, mimeType, modifiedTime)",
            spaces='drive',
            pageSize=50 # Limit to 50 items for readability in WhatsApp
        ).execute()

        items = results.get('files', [])

        if not items:
            return f"📁 Folder '/{folder_path}' is empty."

        output = f"📂 *Contents of /{folder_path}* (up to 50 items):\n"
        
        for item in items:
            is_folder = item['mimeType'] == 'application/vnd.google-apps.folder'
            icon = '📁' if is_folder else '📄'
            
            # Extract and format the modification date (Drive API returns ISO 8601)
            mod_time = item['modifiedTime'][:10] 
            
            output += f"{icon} {item['name']} ({mod_time})\n"
            
        return output

    except HttpError as error:
        return f"❌ An error occurred: {error}"


def upload_file(drive_service, folder_path, temp_file_path_full, drive_file_name):
    """
    Uploads a file from a temporary local path to the specified Google Drive folder.
    """
    folder_id = get_folder_id(drive_service, folder_path)
    
    if not folder_id:
        return f"❌ Upload failed: Destination folder '{folder_path}' not found."

    file_metadata = {
        'name': drive_file_name,
        'parents': [folder_id]
    }
    
    # Determine the MIME type (Drive often guesses correctly, but we need the path)
    
    from mimetypes import MimeTypes
    mime = MimeTypes()
    guessed_mime_type = mime.guess_type(drive_file_name)[0] or 'application/octet-stream'

    media = None
    try:
        media = drive_service.files().create(
            body=file_metadata,
            media_body=temp_file_path_full,
            media_mime_type=guessed_mime_type,
            fields='id'
        ).execute()

        return f"✅ Successfully uploaded '{drive_file_name}' to /{folder_path}."

    except HttpError as error:
        print(f"Upload failed: {error}")
        return f"❌ Upload failed due to a Drive API error. Details: {error}"
    except Exception as e:
        print(f"Unexpected upload error: {e}")
        return f"❌ An unexpected error occurred during upload: {e}"


def delete_file(drive_service, folder_name, file_name):
    """
    Moves a file to trash.
    """
    file_id, error_msg = get_file_by_name(drive_service, folder_name, file_name)

    if error_msg:
        return f"❌ Delete failed: {error_msg}"
        
    try:
        # Update the file's metadata to set 'trashed' to true
        drive_service.files().update(
            fileId=file_id, 
            body={'trashed': True}
        ).execute()
        
        return f"🗑️ Successfully moved file '{file_name}' to trash."

    except HttpError as error:
        return f"❌ Delete failed due to a Drive API error: {error}"
        
        
def move_file(drive_service, source_folder, file_name, destination_folder):
    """
    Moves a file from the source folder to the destination folder.
    """
    file_id, error_msg = get_file_by_name(drive_service, source_folder, file_name)

    if error_msg:
        return f"❌ Move failed: {error_msg}"
    
    destination_folder_id = get_folder_id(drive_service, destination_folder)

    if not destination_folder_id:
        return f"❌ Move failed: Destination folder '{destination_folder}' not found."
    
    source_folder_id = get_folder_id(drive_service, source_folder)
    
    try:
        # Atomically remove it from the source folder and add it to the destination folder
        drive_service.files().update(
            fileId=file_id,
            addParents=destination_folder_id,
            removeParents=source_folder_id,
            fields='id, parents'
        ).execute()
        
        return f"➡️ Successfully moved '{file_name}' from /{source_folder} to /{destination_folder}."
        
    except HttpError as error:
        return f"❌ Move failed due to a Drive API error: {error}"


def rename_file(drive_service, old_file_name, new_file_name):
    """
    Renames a file found anywhere in the user's Drive.
    """
    file_id, error_msg = get_file_by_name_anywhere(drive_service, old_file_name)

    if error_msg:
        return f"❌ Rename failed: {error_msg}"
        
    try:
        # Update the file's metadata to set the new name
        drive_service.files().update(
            fileId=file_id, 
            body={'name': new_file_name}
        ).execute()
        
        return f"✏️ Successfully renamed '{old_file_name}' to '{new_file_name}'."

    except HttpError as error:
        return f"❌ Rename failed due to a Drive API error: {error}"


def summarize_folder(drive_service, folder_path, openai_api_key, model_name):
    """
    Downloads all text-based files in a folder, concatenates their content, and generates an AI summary.
    """
    if not openai_api_key or openai_api_key == 'default-key':
        return "⚠️ SUMMARY failed: OPENAI_API_KEY is missing or invalid. Set this environment variable to use AI features."
        
    folder_id = get_folder_id(drive_service, folder_path)

    if not folder_id:
        return f"❌ Folder '{folder_path}' not found."

    # Search query: files inside the folder, not trashed, and NOT folders themselves.
    query = (
        f"'{folder_id}' in parents and "
        f"mimeType != 'application/vnd.google-apps.folder' and "
        "trashed = false"
    )
    
    try:
        results = drive_service.files().list(
            q=query,
            fields="files(id, name, mimeType, size)",
            spaces='drive',
            pageSize=10 # Limit file processing to 10 files for performance
        ).execute()

        items = results.get('files', [])

        if not items:
            return f"📂 Folder '/{folder_path}' contains no documents to summarize (or maximum 10 file limit exceeded)."

        full_text = ""
        file_count = 0
        
        # Initialize OpenAI Client
        client = OpenAI(api_key=openai_api_key)

        for item in items:
            # Skip large files (e.g., > 1MB) for summary generation
            if item.get('size') and int(item['size']) > 1048576: # 1MB limit
                continue
                
            # Download file content
            try:
                # Get the file's content using its ID
                request = drive_service.files().get_media(fileId=item['id'])
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while done is False:
                    status, done = downloader.next_chunk()
                
                # IMPORTANT FIX: Decode with errors='ignore' to handle non-UTF8/binary content
                # This prevents the UnicodeDecodeError if non-text files are present.
                content = fh.getvalue().decode('utf-8', errors='ignore')

                if content.strip():
                    full_text += f"\n\n--- FILE: {item['name']} ---\n"
                    full_text += content
                    file_count += 1

            except Exception as e:
                print(f"Error downloading or processing file {item['name']}: {e}")
                continue # Skip to the next file if an error occurs

        if not full_text:
            return f"⚠️ Could not extract any readable text from the documents in /{folder_path}."

        # Send combined text to OpenAI for summary
        prompt = (
            "Summarize the following concatenated text from multiple documents. "
            "Provide a concise, single-paragraph overview of the key themes and information. "
            "Do not exceed 150 words. The content is:\n\n"
            f"{full_text}"
        )

        chat_completion = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}]
        )

        summary = chat_completion.choices[0].message.content.strip()
        
        return f"🤖 *AI Summary for /{folder_path}* ({file_count} documents analyzed):\n\n{summary}"

    except HttpError as error:
        return f"❌ An error occurred during Drive API call: {error}"
    except Exception as e:
        # Catch network or OpenAI API errors
        return f"❌ An external error occurred during SUMMARY (AI/Network): {e}"
