import os
import io
import requests
from googleapiclient.http import MediaFileUpload
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError
from openai import OpenAI
from mimetypes import MimeTypes

# --- Configuration for Document Export ---
# MimeTypes that Google Drive can convert to plain text for summarization
EXPORTABLE_MIMETYPES = [
    'application/vnd.google-apps.document',  # Google Docs
    'application/vnd.google-apps.spreadsheet', # Google Sheets (will export table data as text)
    'application/vnd.google-apps.presentation', # Google Slides
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # DOCX
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # XLSX
    'application/pdf',  # PDF
]

# --- Helper Functions ---

def get_folder_id(drive, folder_path):
    """
    Traverses the Drive structure to find the ID of the target folder path.
    Example: 'Reports/2024/Q1'
    """
    current_parent_id = 'root'  # Start from the root of the user's Drive

    # Ensure folder_path is clean and split into segments
    segments = [s.strip() for s in folder_path.strip('/').split('/') if s.strip()]

    if not segments:
        return 'root', None

    for segment in segments:
        try:
            # Search for the folder by name and parent ID
            # q: name='folder_name' and mimeType='folder' and 'parent_id' in parents and trashed=false
            query = (
                f"name='{segment}' and mimeType='application/vnd.google-apps.folder' "
                f"and '{current_parent_id}' in parents and trashed=false"
            )
            
            # Using the native Google API Client to list files
            response = drive.files().list(
                q=query,
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageSize=1
            ).execute()

            files = response.get('files', [])

            if not files:
                return None, f"Folder segment not found: '{segment}'"

            # Found the folder, update the current parent ID for the next segment
            current_parent_id = files[0]['id']

        except HttpError as error:
            return None, f"An error occurred while searching for folder '{segment}': {error}"
        except Exception as e:
            return None, f"An unknown error occurred: {e}"

    # After the loop, current_parent_id holds the ID of the last segment (the target folder)
    return current_parent_id, None


def get_file_by_name(drive, folder_path, file_name):
    """
    Finds a single file within a specific folder path.
    Returns file_id or None.
    """
    folder_id, error = get_folder_id(drive, folder_path)
    if error:
        return None, error

    if not folder_id:
        return None, f"Folder '{folder_path}' not found."

    try:
        # q: name='file_name' and mimeType!='folder' and 'folder_id' in parents and trashed=false
        query = (
            f"name='{file_name}' and mimeType!='application/vnd.google-apps.folder' "
            f"and '{folder_id}' in parents and trashed=false"
        )
        
        response = drive.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)',
            pageSize=1
        ).execute()

        files = response.get('files', [])

        if files:
            return files[0]['id'], None
        else:
            return None, f"File '{file_name}' not found in folder '{folder_path}'."

    except HttpError as error:
        return None, f"An error occurred while searching for file: {error}"
    except Exception as e:
        return None, f"An unknown error occurred: {e}"


def get_file_by_name_anywhere(drive, file_name):
    """
    Finds a single file anywhere in the user's drive (used primarily by RENAME).
    Returns file_id or None.
    """
    try:
        # q: name='file_name' and mimeType!='folder' and trashed=false
        query = (
            f"name='{file_name}' and mimeType!='application/vnd.google-apps.folder' "
            f"and trashed=false"
        )
        
        response = drive.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)',
            pageSize=1
        ).execute()

        files = response.get('files', [])

        if files:
            return files[0]['id'], None
        else:
            return None, f"File '{file_name}' not found anywhere in your Drive."

    except HttpError as error:
        return None, f"An error occurred while searching for file: {error}"
    except Exception as e:
        return None, f"An unknown error occurred: {e}"


# --- Core Drive Commands ---

def list_files(drive, folder_path):
    """Lists files and folders in a specific Drive path."""
    folder_id, error = get_folder_id(drive, folder_path)
    if error:
        return f"❌ Error: {error}"

    if not folder_id:
        return f"❌ Folder '{folder_path}' not found."

    try:
        query = f"'{folder_id}' in parents and trashed=false"
        
        response = drive.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name, mimeType)',
            orderBy='folder, name', # FIXED: Removed 'mimeType desc' to resolve API error
            pageSize=50 # Max items to show
        ).execute()

        items = response.get('files', [])
        
        if not items:
            return f"📂 Folder /{folder_path} is empty."

        output = f"📂 *Contents of /{folder_path}:*\n"
        for item in items:
            name = item['name']
            mime_type = item['mimeType']
            
            if mime_type == 'application/vnd.google-apps.folder':
                output += f"  > *{name}/*\n"
            else:
                output += f"  - {name}\n"
        
        return output

    except HttpError as error:
        return f"❌ An error occurred while listing files: {error}"
    except Exception as e:
        return f"❌ An unknown error occurred: {e}"


def upload_file(drive_service, folder_path, temp_file_path_full, drive_file_name):
    """
    Uploads a file from a temporary local path to the specified Google Drive folder.
    
    Args:
        drive_service: The initialized Google Drive API service object.
        folder_path: The name or path of the destination folder (used by get_folder_id).
        temp_file_path_full: The full local path to the file to upload.
        drive_file_name: The name the file should have on Drive.
        
    Returns:
        A success or failure message.
    """
    # 1. Get Folder ID (The most critical part)
    # The get_folder_id function MUST return a valid string ID or None.
    # If it returns a bad ID, the file goes to root.
    try:
        # Assuming get_folder_id is defined elsewhere and is correct
        folder_id = get_folder_id(drive_service, folder_path) 
    except Exception as e:
        return f"❌ Upload failed: Error retrieving folder ID for '{folder_path}'. Details: {e}"

    if not folder_id:
        return f"❌ Upload failed: Destination folder '{folder_path}' not found."

    # 2. Determine MIME Type
    mime = MimeTypes()
    guessed_mime_type = mime.guess_type(drive_file_name)[0] or 'application/octet-stream'

    # 3. Create File Metadata (Including the correct 'parents' property)
    file_metadata = {
        'name': drive_file_name,
        # 'parents' is an array of folder IDs the file should belong to.
        'parents': [folder_id] 
    }

    # 4. Create the Media Upload Object (The standard, robust way)
    try:
        media = MediaFileUpload(temp_file_path_full, mimetype=guessed_mime_type, resumable=True)
    except FileNotFoundError:
        return f"❌ Upload failed: Local file not found at path: {temp_file_path_full}"
    
    # 5. Execute the Upload
    try:
        uploaded_file = drive_service.files().create(
            body=file_metadata,
            media_body=media, # Pass the MediaFileUpload object
            fields='id, parents' # Request parents back for verification
        ).execute()

        # Optional: Double-check the parents returned from the API response
        if folder_id in uploaded_file.get('parents', []):
            return f"✅ Successfully uploaded '{drive_file_name}' to /{folder_path} (ID: {uploaded_file['id']})."
        else:
            # This should only happen if the API silently failed to move the file
            return f"⚠️ Warning: Uploaded to Drive root. Folder ID was likely invalid or permissions failed. File ID: {uploaded_file['id']}."

    except HttpError as error:
        print(f"Drive API upload failed: {error}")
        return f"❌ Upload failed due to a Drive API error. Details: {error}"
    except Exception as e:
        print(f"Unexpected upload error: {e}")
        return f"❌ An unexpected error occurred during upload: {e}"


def rename_file(drive, old_file_name, new_file_name):
    """Renames a file found anywhere in the user's drive."""
    file_id, error = get_file_by_name_anywhere(drive, old_file_name)
    if error:
        return f"❌ Error: {error}"

    try:
        file_metadata = {'name': new_file_name}
        drive.files().update(
            fileId=file_id, 
            body=file_metadata, 
            fields='id'
        ).execute()

        return f"✏️ Successfully renamed '{old_file_name}' to '{new_file_name}'."

    except HttpError as error:
        return f"❌ An error occurred during rename: {error}"
    except Exception as e:
        return f"❌ An unknown error occurred during rename: {e}"


def delete_file(drive, folder_path, file_name):
    """Moves a file in a specified folder to the trash."""
    file_id, error = get_file_by_name(drive, folder_path, file_name)
    if error:
        return f"❌ Error: {error}"

    try:
        # Setting 'trashed' to true moves the file to the trash
        file_metadata = {'trashed': True}
        drive.files().update(
            fileId=file_id, 
            body=file_metadata, 
            fields='id, trashed'
        ).execute()

        return f"🗑️ Successfully moved file '{file_name}' to trash."

    except HttpError as error:
        return f"❌ An error occurred during deletion: {error}"
    except Exception as e:
        return f"❌ An unknown error occurred during deletion: {e}"


def move_file(drive, source_folder, file_name, dest_folder):
    """Moves a file from the source folder to the destination folder."""
    file_id, error = get_file_by_name(drive, source_folder, file_name)
    if error:
        return f"❌ Error: {error}"
        
    dest_folder_id, error = get_folder_id(drive, dest_folder)
    if error:
        return f"❌ Destination Error: {error}"
    if not dest_folder_id:
        return f"❌ Destination folder '{dest_folder}' not found."

    try:
        # 1. Get the current parents (to find the source folder ID)
        file = drive.files().get(fileId=file_id, fields='parents').execute()
        current_parent_id = file.get('parents')[0] # Assuming one parent for simplicity

        # 2. Update the file: remove current parent, add new parent
        drive.files().update(
            fileId=file_id,
            addParents=dest_folder_id,
            removeParents=current_parent_id,
            fields='id, parents'
        ).execute()

        return f"➡️ Successfully moved '{file_name}' from /{source_folder} to /{dest_folder}."

    except HttpError as error:
        return f"❌ An error occurred during move: {error}"
    except Exception as e:
        return f"❌ An unknown error occurred during move: {e}"


def summarize_folder(drive, folder_path, openai_api_key, openai_model_name):
    """
    Combines text from all files in a folder and generates an AI summary.
    Now handles conversion of Docs, PDFs, etc. to plain text.
    """
    if not openai_api_key or openai_api_key == 'default-key':
        return "⚠️ Error: OPENAI_API_KEY is not configured in environment variables."

    folder_id, error = get_folder_id(drive, folder_path)
    if error:
        return f"❌ Error: {error}"
    if not folder_id:
        return f"❌ Folder '{folder_path}' not found."
    
    # Initialize OpenAI client
    client = OpenAI(api_key=openai_api_key)
    
    full_text = ""
    file_list = []

    try:
        # Get files from the folder
        query = f"'{folder_id}' in parents and trashed=false"
        response = drive.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name, mimeType)',
            pageSize=50
        ).execute()
        items = response.get('files', [])

        if not items:
            return f"📂 Folder /{folder_path} is empty, nothing to summarize."
        
        for item in items:
            if item['mimeType'] == 'application/vnd.google-apps.folder':
                continue # Skip folders
            
            file_list.append(item['name'])
            
            file_id = item['id']
            file_mime_type = item['mimeType']
            
            # Determine if we need to export (convert) or just download
            if file_mime_type in EXPORTABLE_MIMETYPES or file_mime_type.startswith('application/vnd.google-apps.'):
                # Use export_media for convertable document types (PDF, Docs, Sheets, etc.)
                request = drive.files().export_media(fileId=file_id, mimeType='text/plain')
            else:
                # Use get_media for binary files that are already text (e.g., .txt)
                request = drive.files().get_media(fileId=file_id)

            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            
            # Attempt to decode content, ignoring errors for robustness
            full_text += fh.getvalue().decode('utf-8', errors='ignore')
            full_text += "\n---\n" # Separator between file contents
        
        if not full_text.strip():
            return f"⚠️ Warning: Found files {', '.join(file_list)}, but could not extract any readable text."

        # Truncate text to fit within typical model limits (e.g., 20,000 characters)
        max_chars = 20000 
        truncated_text = full_text[:max_chars]

        # Call OpenAI API to summarize
        prompt = (
            "Analyze the following document texts and provide a concise, professional summary "
            "highlighting the key themes, main findings, or important takeaways. "
            "Do not exceed 300 words. Source Files: "
            f"{', '.join(file_list)}\n\n--- Content ---\n{truncated_text}"
        )

        chat_completion = client.chat.completions.create(
            model=openai_model_name,
            messages=[{"role": "user", "content": prompt}]
        )

        summary = chat_completion.choices[0].message.content

        return f"🤖 *AI Summary for /{folder_path}*\n\n{summary}"

    except HttpError as error:
        return f"❌ A Google Drive API error occurred: {error}"
    except Exception as e:
        return f"❌ An unexpected error occurred during summarization: {e}"


