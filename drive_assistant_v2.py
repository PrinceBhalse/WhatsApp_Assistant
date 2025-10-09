import os
import io
import requests
import json
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from googleapiclient.errors import HttpError
from openai import OpenAI
from google.oauth2.credentials import Credentials
from mimetypes import MimeTypes


# --- Configuration for Summarization ---
# MimeTypes that Google Drive can convert to plain text for summarization
EXPORTABLE_MIMETYPES = [
    'application/vnd.google-apps.document',  # Google Docs
    'application/vnd.google-apps.spreadsheet',  # Google Sheets
    'application/vnd.google-apps.presentation',  # Google Slides
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # DOCX
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # XLSX
    'application/pdf',  # PDF
    'text/plain', # TXT
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
                # Folder not found at this level
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


def get_file_id_by_name_and_path(drive_service, parent_folder_path, file_name):
    """
    Finds a file ID given its parent folder path and exact file name.
    Returns the file ID (string) and None for error, or None and an error message.
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
            fields="files(id, name, mimeType)",
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


# --- Core Drive Operations ---

def list_files(drive_service, folder_path):
    """
    Lists the contents (files and folders) of a specified Google Drive path.
    (Matches the expected 'list_files' function name in app.py)
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
                output.append(f"   {item['name']} ")
            else:
                # Format file size for readability
                size_bytes = int(item.get('size', 0) or 0)
                # Convert bytes to MB, ensuring clean division
                size_str = f"{size_bytes / (1024 * 1024):.2f} MB" if size_bytes > 0 else "N/A"
                output.append(f"  [FILE] {item['name']} ({size_str}) (ID: {item['id']})")
                
        return "\n".join(output)

    except HttpError as error:
        return f"❌ An error occurred during file listing: {error}"
    except Exception as e:
        return f"❌ An unexpected error occurred: {e}"


def delete_file(drive_service, parent_folder_path, item_name):
    """
    Deletes a file (or non-empty folder, if found by the helper) from Google Drive using its path and name.
    """
    # Use the helper to find the ID of the file to delete
    # Note: get_file_id_by_name_and_path explicitly excludes folders, so this primarily deletes files.
    item_id, error = get_file_id_by_name_and_path(drive_service, parent_folder_path, item_name)
    
    # If file not found, try to check if it's a folder
    if not item_id:
        folder_id = get_folder_id(drive_service, os.path.join(parent_folder_path, item_name).replace('\\', '/'))
        if folder_id:
            item_id = folder_id # Found a folder
        else:
            return f"❌ Deletion failed: {error} (or folder not found)."

    try:
        # Simply calling delete with the item ID trashes the file/folder
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
    # The current parent ID is not strictly needed for the API call but is good for verification
    # current_parent_id = get_folder_id(drive_service, parent_folder_path) 
    destination_id = get_folder_id(drive_service, destination_folder_path)

    if not destination_id:
        return f"❌ Move failed: Destination folder '{destination_folder_path}' not found."

    try:
        # Retrieve the file's current parents to know which ones to remove
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



def upload_file(drive_service, folder_path, temp_file_path_full, drive_file_name):
    """
    Uploads a file from a temporary local path to the specified Google Drive folder.
    (Confirmed to be working)
    """
    folder_id = get_folder_id(drive_service, folder_path)
    
    if not folder_id or folder_id == 'root':
        if folder_path and folder_path != '/':
             return f"❌ Upload failed: Destination folder '{folder_path}' not found or is root."
        
        target_parents = []
        upload_location_msg = "My Drive (Root)"
    else:
        target_parents = [folder_id]
        upload_location_msg = folder_path

    if not os.path.exists(temp_file_path_full):
        return f"❌ Upload failed: Local file not found at path: {temp_file_path_full}"
    
    mime = MimeTypes()
    guessed_mime_type = mime.guess_type(drive_file_name)[0] or 'application/octet-stream'

    file_metadata = {
        'name': drive_file_name,
        'parents': target_parents
    }

    try:
        media = MediaFileUpload(temp_file_path_full, mimetype=guessed_mime_type, resumable=True)
    except FileNotFoundError:
        return f"❌ Upload failed: Local file not found at path: {temp_file_path_full}"
    
    try:
        uploaded_file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

        return f"✅ Successfully uploaded '{drive_file_name}' to /{upload_location_msg} (ID: {uploaded_file['id']})."

    except HttpError as error:
        print(f"Drive API upload failed: {error}")
        return f"❌ Upload failed due to a Drive API error. Details: {error}"
    except Exception as e:
        print(f"Unexpected upload error: {e}")
        return f"❌ An unexpected error occurred during upload: {e}"


def download_file(drive_service, file_id, download_path):
    """
    Downloads a file from Google Drive using its ID to a specified local path.
    """
    try:
        # Use MediaIoBaseDownload for the actual content
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.FileIO(download_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        
        return f"✅ Successfully downloaded file ID {file_id} to local path: {download_path}"
    
    except HttpError as error:
        return f"❌ Download failed due to a Drive API error: {error}"
    except Exception as e:
        return f"❌ An unexpected error occurred during download: {e}"


def summarize_folder_contents(drive_service, folder_path, client, openai_model_name="gpt-4o"):
    """
    Finds all text-extractable files in a folder, concatenates their content, and generates a summary using OpenAI.
    """
    folder_id = get_folder_id(drive_service, folder_path)
    
    if not folder_id:
        return f"❌ Folder not found: {folder_path}"

    try:
        # Query for exportable files in the folder
        mime_query = ' or '.join([f"mimeType = '{m}'" for m in EXPORTABLE_MIMETYPES])
        query = f"'{folder_id}' in parents and ({mime_query}) and trashed = false"

        results = drive_service.files().list(
            q=query,
            fields="files(id, name, mimeType)",
            spaces='drive'
        ).execute()
        
        items = results.get('files', [])
        
        if not items:
            return f"⚠️ No extractable files (Docs, PDF, Sheets, etc.) found in /{folder_path} to summarize."
        
        full_text = ""
        file_list = []
        
        for item in items:
            file_list.append(item['name'])
            try:
                # Use export_media to convert to plain text for supported file types
                if item['mimeType'].startswith('application/vnd.google-apps'):
                    # Google native documents require the export method
                    request = drive_service.files().export_media(fileId=item['id'], mimeType='text/plain')
                else:
                    # Non-native files (PDF, DOCX) use get_media and are hoped to be simple enough to decode
                    request = drive_service.files().get_media(fileId=item['id'])

                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while done is False:
                    status, done = downloader.next_chunk()

                # Attempt to decode content, ignoring errors for robustness
                content = fh.getvalue().decode('utf-8', errors='ignore')

                if content.strip():
                    full_text += f"\n\n--- FILE: {item['name']} ---\n"
                    full_text += content
                
            except HttpError as e:
                 print(f"Error during Drive export/download for {item['name']} (may not be exportable): {e}")
                 continue # Skip to the next file
            except Exception as e:
                print(f"Unexpected error processing file {item['name']}: {e}")
                continue

        if not full_text.strip():
            return f"⚠️ Could not extract any readable text from {len(file_list)} documents in /{folder_path}."

        # Truncate text to fit within typical model limits
        max_chars = 20000
        truncated_text = full_text[:max_chars]

        # Call OpenAI API to summarize
        prompt = (
            "Analyze the following document texts and provide a concise, professional summary "
            "highlighting the key themes, main findings, or important takeaways. "
            "Do not exceed 300 words. Source Files: "
            f"{', '.join(file_list)}\n\n--- Content (Truncated if > {max_chars} chars) ---\n{truncated_text}"
        )

        chat_completion = client.chat.completions.create(
            model=openai_model_name,
            messages=[{"role": "user", "content": prompt}]
        )

        summary = chat_completion.choices[0].message.content

        return f"🤖 *AI Summary for /{folder_path}* ({len(file_list)} documents analyzed): \n\n{summary}"

    except HttpError as error:
        return f"❌ An error occurred during Drive API call: {error}"
    except Exception as e:
        # Catch network or OpenAI API errors
        return f"❌ An unexpected error occurred during summarization: {e}"




