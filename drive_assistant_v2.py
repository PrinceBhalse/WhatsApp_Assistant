import os
import io
import requests
from googleapiclient.http import MediaFileUpload, MediaFileUpload
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

def get_folder_id(drive_service, folder_path):
    """
    Finds the ID of the folder based on its path (e.g., 'Reports/Q3/2025').
    Returns the folder ID (string) or None if any segment of the path is not found.
    """
    current_parent_id = 'root'  # Start from the root of Google Drive

    # Split the path, removing any leading/trailing slashes
    folder_names = [name.strip() for name in folder_path.split('/') if name.strip()]

    if not folder_names:
        # If folder_path is empty or '/', the intent is likely the root.
        # But for 'parents' field in upload, we should usually use a specific folder ID, 
        # so returning 'root' here is only if the path is truly empty.
        return 'root'

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
                # CRITICAL: Folder not found at this level, stop and return None
                return None
                
            # Update parent ID for the next segment of the path
            current_parent_id = items[0]['id']
        except HttpError as e:
            print(f"Drive API Error during folder search for '{folder_name}': {e}")
            return None
        except Exception as e:
            print(f"Unexpected Error during folder search: {e}")
            return None

    # After iterating through all path segments, current_parent_id is the final folder ID
    return current_parent_id


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

def get_file_id_by_name_and_path(drive_service, parent_folder_path, file_name):
    """
    Finds a file ID given its parent folder path and exact file name.
    Returns the file ID (string) or None if not found.
    """
    parent_id = get_folder_id(drive_service, parent_folder_path)
    if not parent_id:
        # Parent folder not found
        return None, f"Parent folder '{parent_folder_path}' not found."

    query = (
        f"'{parent_id}' in parents and "
        f"name = '{file_name}' and "
        "trashed = false and "
        # Exclude folders, we are looking for a file
        "mimeType != 'application/vnd.google-apps.folder'" 
    )
    
    try:
        results = drive_service.files().list(
            q=query,
            fields="files(id, name)",
            spaces='drive'
        ).execute()

        items = results.get('files', [])
        if not items:
            return None, f"File '{file_name}' not found in folder '{parent_folder_path}'."
            
        return items[0]['id'], None

    except HttpError as e:
        return None, f"Drive API Error during file search: {e}"
    except Exception as e:
        return None, f"Unexpected Error during file search: {e}"


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

def list_files_in_folder(drive_service, folder_path):
    """
    Lists the contents (files and folders) of a specified Google Drive path.
    """
    folder_id = get_folder_id(drive_service, folder_path)
    
    if not folder_id:
        return f"❌ Folder not found: {folder_path}"

    try:
        # Query for all files and folders that are children of the folder_id
        query = f"'{folder_id}' in parents and trashed = false"

        results = drive_service.files().list(
            q=query,
            fields="files(id, name, mimeType, size)",
            spaces='drive'
        ).execute()

        items = results.get('files', [])

        if not items:
            return f"📂 Folder /{folder_path} is empty."

        output = [f"📂 Contents of /{folder_path}:"]
        
        for item in items:
            is_folder = item['mimeType'] == 'application/vnd.google-apps.folder'
            
            if is_folder:
                output.append(f"  [DIR] {item['name']} (ID: {item['id']})")
            else:
                # Format file size for readability
                size_bytes = int(item.get('size', 0) or 0)
                size_str = f"{size_bytes / (1024 * 1024):.2f} MB" if size_bytes > 0 else "N/A"
                output.append(f"  [FILE] {item['name']} ({size_str}) (ID: {item['id']})")
                
        return "\n".join(output)

    except HttpError as error:
        return f"❌ An error occurred during file listing: {error}"
    except Exception as e:
        return f"❌ An unexpected error occurred: {e}"



def upload_file(drive_service, folder_path, temp_file_path_full, drive_file_name):
    """
    Uploads a file from a temporary local path to the specified Google Drive folder.
    
    FIX: Uses MediaFileUpload for robust handling of file content and metadata.
    """
    # 1. Get the ID of the destination folder
    folder_id = get_folder_id(drive_service, folder_path)

    if not folder_id:
        return f"❌ Upload failed: Destination folder '{folder_path}' not found."
    
    # 2. Determine the MIME type
    mime = MimeTypes()
    guessed_mime_type = mime.guess_type(drive_file_name)[0] or 'application/octet-stream'

    # 3. Define the file's metadata, including the critical 'parents' field
    file_metadata = {
        'name': drive_file_name,
        # This is the essential field to place the file in the correct folder
        'parents': [folder_id] 
    }

    # 4. Create the MediaFileUpload object
    try:
        # Use MediaFileUpload to link the local file path and its MIME type
        media = MediaFileUpload(temp_file_path_full, mimetype=guessed_mime_type, resumable=True)
    except FileNotFoundError:
        return f"❌ Upload failed: Local file not found at path: {temp_file_path_full}"
    
    # 5. Execute the upload request
    try:
        uploaded_file = drive_service.files().create(
            body=file_metadata,      # The metadata (name, parents)
            media_body=media,        # The file content wrapped in MediaFileUpload
            fields='id'              # Only request the file ID back
        ).execute()

        # The ID is returned, confirming success
        return f"✅ Successfully uploaded '{drive_file_name}' to /{folder_path} (ID: {uploaded_file['id']})."

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


def delete_item(drive_service, parent_folder_path, item_name):
    """
    Deletes a file from Google Drive using its parent folder path and name.
    """
    # Use the helper to find the ID of the file to delete
    item_id, error = get_file_id_by_name_and_path(drive_service, parent_folder_path, item_name)
    
    if not item_id:
        # If the file wasn't found, the error message from the helper is returned
        return f"❌ Deletion failed: {error}"

    try:
        # Simply calling delete with the file ID trashes the file
        drive_service.files().delete(fileId=item_id).execute()
        return f"✅ Successfully deleted (trashed) item '{item_name}' (ID: {item_id})."

    except HttpError as error:
        return f"❌ Deletion failed due to a Drive API error: {error}"
    except Exception as e:
        return f"❌ An unexpected error occurred during deletion: {e}"


def move_file(drive_service, parent_folder_path, file_name, destination_folder_path):
    """
    Moves a file between two folders using its parent path, name, and the new destination path.
    """
    # 1. Get the ID of the file to move
    file_id, error = get_file_id_by_name_and_path(drive_service, parent_folder_path, file_name)
    
    if not file_id:
        return f"❌ Move failed: {error}"

    # 2. Get the IDs of the current parent and the destination parent
    current_parent_id = get_folder_id(drive_service, parent_folder_path)
    destination_id = get_folder_id(drive_service, destination_folder_path)

    if not destination_id:
        return f"❌ Move failed: Destination folder '{destination_folder_path}' not found."

    try:
        # Retrieve the file's current parents to remove them
        file = drive_service.files().get(fileId=file_id, fields='parents').execute()
        
        # Drive API update requires removing the old parents and adding the new ones
        updated_file = drive_service.files().update(
            fileId=file_id,
            # String of IDs to remove (old parents)
            removeParents=','.join(file['parents']),
            # String of IDs to add (new parent)
            addParents=destination_id,
            fields='id, parents'
        ).execute()

        return f"✅ Successfully moved '{file_name}' from /{parent_folder_path} to /{destination_folder_path}."

    except HttpError as error:
        return f"❌ Move failed due to a Drive API error: {error}"
    except Exception as e:
        return f"❌ An unexpected error occurred during move: {e}"


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






