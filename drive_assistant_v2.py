import os
import io
import requests
import json
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError
from openai import OpenAI  # Used for SUMMARY command


# --- Utility: Drive API Helper Functions ---

def get_folder_id(service, folder_path):
    """
    Finds the ID of a folder given its path (e.g., 'Reports/Q3').
    Assumes path is relative to the user's root Drive.
    Returns folder ID or None.
    """
    folder_names = folder_path.strip('/').split('/')
    parent_id = 'root'

    for folder_name in folder_names:
        if not folder_name: continue  # Skip empty strings from split/strip

        query = (
            f"name='{folder_name}' and "
            f"mimeType='application/vnd.google-apps.folder' and "
            f"'{parent_id}' in parents and "
            "trashed=false"
        )

        # Use native API: drive.files().list()
        response = service.files().list(
            q=query,
            spaces='drive',
            fields='nextPageToken, files(id, name)',
            pageSize=1
        ).execute()

        files = response.get('files', [])
        if files:
            parent_id = files[0]['id']
        else:
            return None  # Folder not found at this level

    return parent_id


def get_file_by_name(service, name, parent_id='root'):
    """
    Finds a file by name in a specific folder (parent_id).
    Returns file metadata or None.
    """
    query = (
        f"name='{name}' and "
        f"'{parent_id}' in parents and "
        "trashed=false"
    )

    response = service.files().list(
        q=query,
        spaces='drive',
        fields='nextPageToken, files(id, name, mimeType)',
        pageSize=1
    ).execute()

    files = response.get('files', [])
    return files[0] if files else None


def get_file_by_name_anywhere(service, name):
    """
    Finds a file by name anywhere in the Drive.
    Returns file metadata or None. (Used for RENAME).
    """
    query = (
        f"name='{name}' and "
        "trashed=false"
    )

    # Use pageSize=1 for efficiency as we only need the first match
    response = service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name, parents, mimeType)',
        pageSize=1
    ).execute()

    files = response.get('files', [])
    return files[0] if files else None


# --- Command Implementations ---

def list_files(service, folder_path):
    """
    Lists files in a given folder path.
    Command: LIST /FolderName
    """
    folder_id = get_folder_id(service, folder_path)
    if not folder_id:
        return f"❌ Folder not found: /{folder_path}"

    query = (
        f"'{folder_id}' in parents and "
        "trashed=false"
    )

    try:
        response = service.files().list(
            q=query,
            spaces='drive',
            fields='files(name, mimeType, size)',
            pageSize=20  # Limit to 20 files for a clean WhatsApp list
        ).execute()

        files = response.get('files', [])

        if not files:
            return f"✅ Folder /{folder_path} is empty."

        output = [f"📁 *Files in /{folder_path}* 📁"]

        for file in files:
            name = file.get('name')
            mime_type = file.get('mimeType')

            icon = '📄'
            if mime_type == 'application/vnd.google-apps.folder':
                icon = '🗂️'
            elif mime_type.startswith('image/'):
                icon = '🖼️'
            elif mime_type == 'application/pdf':
                icon = '📕'

            output.append(f"{icon} {name}")

        return "\n".join(output)

    except HttpError as error:
        return f"❌ An error occurred while listing files: {error}"


def upload_file(service, folder_path, local_file_path, drive_file_name):
    """
    Uploads a local file to the specified Drive folder.
    Command: UPLOAD /FolderName NewFileName.ext
    """
    folder_id = get_folder_id(service, folder_path)
    if not folder_id:
        return f"❌ Upload failed: Target folder not found: /{folder_path}"

    try:
        # Determine MIME type based on file extension
        mime_type = 'application/octet-stream'  # Default
        if drive_file_name.endswith('.jpg') or drive_file_name.endswith('.jpeg'):
            mime_type = 'image/jpeg'
        elif drive_file_name.endswith('.png'):
            mime_type = 'image/png'
        elif drive_file_name.endswith('.pdf'):
            mime_type = 'application/pdf'

        file_metadata = {
            'name': drive_file_name,
            'parents': [folder_id]
        }

        media = MediaFileUpload(local_file_path, mimetype=mime_type, resumable=True)

        # Use native API: drive.files().create()
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name'
        ).execute()

        return f"✅ Successfully uploaded '{file.get('name')}' to /{folder_path}."

    except HttpError as error:
        return f"❌ Upload error: {error}"
    except Exception as e:
        return f"❌ Upload error: {e}"


def delete_file(service, folder_path, file_name):
    """
    Moves a file to the trash.
    Command: DELETE/FolderName/FileName.ext
    """
    folder_id = get_folder_id(service, folder_path)
    if not folder_id:
        return f"❌ Delete failed: Folder not found: /{folder_path}"

    file_info = get_file_by_name(service, file_name, folder_id)

    if not file_info:
        return f"❌ Delete failed: File '{file_name}' not found in /{folder_path}."

    try:
        # To delete (move to trash), update the 'trashed' property to True
        service.files().update(
            fileId=file_info['id'],
            body={'trashed': True},
            fields='id'
        ).execute()

        return f"🗑️ Successfully moved file '{file_name}' to trash."
    except HttpError as error:
        return f"❌ Delete error: {error}"


def move_file(service, src_folder, file_name, dest_folder):
    """
    Moves a file from the source folder to the destination folder.
    Command: MOVE/SrcFolder/FileName.ext/DestFolder
    """
    src_id = get_folder_id(service, src_folder)
    dest_id = get_folder_id(service, dest_folder)

    if not src_id:
        return f"❌ Move failed: Source folder not found: /{src_folder}"
    if not dest_id:
        return f"❌ Move failed: Destination folder not found: /{dest_folder}"

    file_info = get_file_by_name(service, file_name, src_id)
    if not file_info:
        return f"❌ Move failed: File '{file_name}' not found in /{src_folder}."

    try:
        # Use the native API update method to remove the parent and add a new one
        service.files().update(
            fileId=file_info['id'],
            addParents=dest_id,
            removeParents=src_id,
            fields='id, parents'
        ).execute()

        return f"➡️ Successfully moved '{file_name}' from /{src_folder} to /{dest_folder}."
    except HttpError as error:
        return f"❌ Move error: {error}"


def rename_file(service, old_name, new_name):
    """
    Renames a file. Searches entire Drive for the old file name.
    Command: RENAME OldName.ext NewName.ext
    """
    file_info = get_file_by_name_anywhere(service, old_name)

    if not file_info:
        return f"❌ Rename failed: File '{old_name}' not found in your Drive."

    try:
        # Update file metadata with the new name
        service.files().update(
            fileId=file_info['id'],
            body={'name': new_name},
            fields='id, name'
        ).execute()

        return f"✏️ Successfully renamed '{old_name}' to '{new_name}'."
    except HttpError as error:
        return f"❌ Rename error: {error}"


def summarize_folder(service, folder_path, openai_api_key, openai_model_name):
    """
    Summarizes content of text/document files in a folder using OpenAI.
    Command: SUMMARY/FolderName
    """
    folder_id = get_folder_id(service, folder_path)
    if not folder_id:
        return f"❌ Summary failed: Folder not found: /{folder_path}"

    if not openai_api_key or openai_api_key == 'default-key':
        return "⚠️ Summary failed: OPENAI_API_KEY environment variable is not set."

    query = (
        f"'{folder_id}' in parents and "
        "mimeType contains 'text/' and "  # Target text documents
        "trashed=false"
    )

    # List files to summarize
    response = service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name, mimeType)',
        pageSize=10  # Limit the number of documents to read
    ).execute()

    files = response.get('files', [])
    if not files:
        return f"✅ No text documents found in /{folder_path} to summarize."

    full_text = ""

    # Download content of each text document
    for file in files:
        file_id = file['id']
        file_name = file['name']
        mime_type = file['mimeType']

        try:
            # For non-Google Docs files, use files().get(alt='media')
            if mime_type.startswith('text/'):
                request = service.files().get_media(fileId=file_id)
            # You would need to handle Google Docs export (application/vnd.google-apps.document) differently
            # For simplicity, we only download plain text files here.
            else:
                continue

            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()

            # Append content to the full text
            full_text += f"\n\n--- Start of {file_name} ---\n"
            full_text += fh.getvalue().decode('utf-8')
            full_text += f"\n--- End of {file_name} ---\n"

        except HttpError as error:
            print(f"Warning: Could not download {file_name}. Error: {error}")
            continue

    if not full_text:
        return "⚠️ Summary failed: Could not read content from any text file."

    # Use OpenAI to summarize
    try:
        client = OpenAI(api_key=openai_api_key)

        prompt = (
            "You are a document summarizer. Read the following concatenated documents "
            f"from the folder '{folder_path}'. Provide a concise, bulleted summary of the key findings, topics, or decisions, "
            f"formatted strictly using Markdown bullet points (*)."
            f"\n\n--- DOCUMENTS START ---\n{full_text}\n--- DOCUMENTS END ---\n"
        )

        chat_completion = client.chat.completions.create(
            model=openai_model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )

        summary = chat_completion.choices[0].message.content.strip()

        return f"🤖 *AI Summary for /{folder_path}* 🤖\n\n{summary}"

    except Exception as e:
        return f"❌ AI summary error. Check OPENAI_API_KEY and service status: {e}"
