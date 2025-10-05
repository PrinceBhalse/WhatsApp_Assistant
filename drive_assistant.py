# drive_assistant.py
import os
import io
import re
from pydrive2.files import GoogleDriveFile
from pydrive2.drive import GoogleDrive
from openai import OpenAI  # Or similar for Claude/GPT

# The ID of the root folder in Google Drive (usually 'root')
DRIVE_ROOT_FOLDER_ID = 'root'


def get_folder_id(drive: GoogleDrive, folder_path: str, create_if_not_exists=False) -> str | None:
    """Gets the ID for a folder path (e.g., 'Reports/Q3')."""
    current_parent_id = DRIVE_ROOT_FOLDER_ID

    # Clean up path: remove leading/trailing slashes and split by '/'
    path_segments = [p for p in folder_path.strip('/').split('/') if p]

    for segment in path_segments:
        q = f"title='{segment}' and mimeType='application/vnd.google-apps.folder' and '{current_parent_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': q}).GetList()

        if file_list:
            current_parent_id = file_list[0]['id']
        elif create_if_not_exists:
            # Create the missing folder segment
            folder_metadata = {
                'title': segment,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [{'id': current_parent_id}]
            }
            folder = drive.CreateFile(folder_metadata)
            folder.Upload()
            current_parent_id = folder['id']
        else:
            return None  # Path not found

    return current_parent_id


# --- CORE COMMANDS ---

def list_files(drive: GoogleDrive, folder_name: str) -> str:
    """LIST/FolderName -> Lists files in folder."""
    folder_id = get_folder_id(drive, folder_name)
    if not folder_id:
        return f"Error: Folder '{folder_name}' not found."

    q = f"'{folder_id}' in parents and trashed=false"
    file_list = drive.ListFile({'q': q}).GetList()

    if not file_list:
        return f"Folder '{folder_name}' is empty."

    response = [f"Files in /{folder_name}:"]
    for file in file_list:
        file_type = "Folder" if file['mimeType'] == 'application/vnd.google-apps.folder' else "File"
        response.append(f"  - [{file_type}] {file['title']} (ID: {file['id']})")

    return "\n".join(response)


def delete_file(drive: GoogleDrive, folder_name: str, file_name: str) -> str:
    """DELETE/FolderName/file.pdf -> Deletes file."""
    folder_id = get_folder_id(drive, folder_name)
    if not folder_id:
        return f"Error: Folder '{folder_name}' not found."

    q = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
    file_list = drive.ListFile({'q': q}).GetList()

    if not file_list:
        return f"Error: File '{file_name}' not found in '{folder_name}'."

    file = file_list[0]
    file.Delete()  # Moves to trash
    return f"Successfully moved file '{file_name}' to trash."


def move_file(drive: GoogleDrive, source_folder: str, file_name: str, dest_folder: str) -> str:
    """MOVE/SourceFolder/file.pdf/DestFolder -> Moves file."""
    source_id = get_folder_id(drive, source_folder)
    dest_id = get_folder_id(drive, dest_folder, create_if_not_exists=True)  # Ensure destination exists

    if not source_id:
        return f"Error: Source folder '{source_folder}' not found."
    if not dest_id:
        return f"Error: Destination folder '{dest_folder}' could not be found or created."

    q = f"title='{file_name}' and '{source_id}' in parents and trashed=false"
    file_list = drive.ListFile({'q': q}).GetList()

    if not file_list:
        return f"Error: File '{file_name}' not found in '{source_folder}'."

    file: GoogleDriveFile = file_list[0]

    # Remove from source, add to destination
    file['parents'] = [{'id': dest_id}]

    # If file is a folder, need to manually remove source parent ID
    # For simplicity, we assume file here is a document
    file.Upload(param={'supportsAllDrives': True})

    return f"Successfully moved '{file_name}' from /{source_folder} to /{dest_folder}."


def rename_file(drive: GoogleDrive, old_file_name: str, new_file_name: str) -> str:
    """RENAME file.pdf NewFileName.pdf -> Renames a file (searches entire drive)."""
    # Note: Searching entire drive by name is slow. In production, need folder context.
    q = f"title='{old_file_name}' and trashed=false"
    file_list = drive.ListFile({'q': q}).GetList()

    if not file_list:
        return f"Error: File '{old_file_name}' not found in the root of the drive."

    file = file_list[0]
    file['title'] = new_file_name
    file.Upload()

    return f"Successfully renamed '{old_file_name}' to '{new_file_name}'."


def summarize_folder(drive: GoogleDrive, folder_name: str, api_key: str, model: str) -> str:
    """SUMMARY/FolderName -> Summarises files in that folder (AI-powered)."""
    folder_id = get_folder_id(drive, folder_name)
    if not folder_id:
        return f"Error: Folder '{folder_name}' not found."

    q = f"'{folder_id}' in parents and trashed=false"
    file_list = drive.ListFile({'q': q}).GetList()

    if not file_list:
        return f"Folder '{folder_name}' is empty."

    documents_content = []

    for file in file_list:
        # Only process text-readable files
        if file['mimeType'] in ['application/pdf', 'text/plain', 'application/vnd.google-apps.document']:

            # Download file content (simplified for text-based)
            try:
                # For Google Workspace files, use exportLinks
                if 'exportLinks' in file and 'text/plain' in file['exportLinks']:
                    download_url = file['exportLinks']['text/plain']
                    # Need an HTTP request here using the auth token, but pydrive2 simplifies download
                    file.GetContentFile(file['title'], mimetype='text/plain')

                    with open(file['title'], 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    os.remove(file['title'])

                # For non-Google files (PDF/TXT), download as is
                else:
                    temp_file = io.BytesIO()
                    file.GetContentIO(temp_file)
                    # Simple decoding for demo. Real PDF needs text extraction (e.g., PyPDF2)
                    content = temp_file.getvalue().decode('utf-8', errors='ignore')

                documents_content.append(f"--- Document: {file['title']} ---\n{content}\n")

            except Exception as e:
                documents_content.append(f"--- Document: {file['title']} ---\n[ERROR READING CONTENT: {e}]\n")

    if not documents_content:
        return f"No readable text documents found in /{folder_name} for summarization."

    # --- AI CALL ---
    client = OpenAI(api_key=api_key)
    full_text = "\n\n".join(documents_content)

    prompt = (
        "You are an intelligent file assistant. Based on the following documents, "
        "provide a concise, high-level summary of the key information, decisions, "
        "or themes present in the folder. Limit your summary to 3-5 key points."
        f"\n\nDOCUMENTS:\n\n{full_text}"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}]
        )
        return f"✅ AI Summary for /{folder_name}:\n\n{response.choices[0].message.content}"
    except Exception as e:
        return f"Error communicating with AI service: {e}"


def upload_file(drive: GoogleDrive, folder_name: str, file_path: str, new_file_name: str) -> str:
    """UPLOAD/Reports new_report.pdf -> Uploads a file (file_path) to a folder, renaming it."""

    # 1. Ensure destination folder exists
    folder_id = get_folder_id(drive, folder_name, create_if_not_exists=True)
    if not folder_id:
        return f"Error: Destination folder '{folder_name}' could not be created."

    # 2. Upload the file
    file_metadata = {
        'title': new_file_name,
        'parents': [{'id': folder_id}]
    }

    try:
        gfile = drive.CreateFile(file_metadata)
        gfile.SetContentFile(file_path)  # Assumes file_path is the temporary local path
        gfile.Upload()

        return f"✅ Successfully uploaded file as '{new_file_name}' to /{folder_name}."
    except Exception as e:
        return f"Error uploading file: {e}"