import os
import io
import json
import pdfplumber

# Fixes the "Server Error: Scope has changed from..." exception
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Scopes needed: Read classroom coursework, and read Drive files (for downloading attachments)
SCOPES = [
    'https://www.googleapis.com/auth/classroom.coursework.me.readonly',
    'https://www.googleapis.com/auth/classroom.coursework.students.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

def authenticate_google():
    """Authenticates the user and returns the Classroom and Drive service client objects."""
    creds = None
    # We will store the user's access and refresh tokens in token.json
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # If there are no valid credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                raise FileNotFoundError("Missing 'credentials.json' in the current directory.")
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            # Run local server to catch the callback
            creds = flow.run_local_server(port=0)
        
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            
    classroom_service = build('classroom', 'v1', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)
    return classroom_service, drive_service

def extract_pdf_text(file_stream):
    """Extract text from a downloaded PDF byte stream."""
    text = ""
    try:
        with pdfplumber.open(file_stream) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        print(f"Error extracting PDF text: {e}")
    return text

def download_attachment(drive_service, file_id, mime_type):
    """Downloads a file from Google Drive and extracts text."""
    try:
        # If it's a Google Workspace document (Google Doc)
        if mime_type == 'application/vnd.google-apps.document':
            request = drive_service.files().export_media(fileId=file_id, mimeType='text/plain')
            file_stream = io.BytesIO(request.execute())
            return file_stream.read().decode('utf-8')
        
        # If it's a regular file like a PDF
        request = drive_service.files().get_media(fileId=file_id)
        file_stream = io.BytesIO()
        downloader = MediaIoBaseDownload(file_stream, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        
        file_stream.seek(0)
        
        if mime_type == 'application/pdf':
            return extract_pdf_text(file_stream)
        else:
            # Try decoding as plain text for other random types
            return file_stream.read().decode('utf-8', errors='ignore')
            
    except Exception as e:
        print(f"Failed to download/parse attachment {file_id}: {e}")
        return ""

def get_assignment_details(course_id, coursework_id):
    """
    Given an assignment, returns the title, standard description, 
    and the combined text of all attached instructional files.
    """
    classroom_service, drive_service = authenticate_google()
    
    # Get coursework from Google Classroom
    coursework = classroom_service.courses().courseWork().get(
        courseId=course_id, id=coursework_id
    ).execute()
    
    title = coursework.get('title', f"Assignment_{coursework_id}")
    description = coursework.get('description', '')
    
    materials_text = []
    materials = coursework.get('materials', [])
    
    for mat in materials:
        if 'driveFile' in mat:
            drive_file = mat['driveFile']['driveFile']
            file_id = drive_file['id']
            # We must get metadata first to know the mimeType if not strictly defined
            try:
                meta = drive_service.files().get(fileId=file_id, fields='mimeType, name').execute()
                mime = meta.get('mimeType')
                print(f"Found attachment: {meta.get('name')}")
                extracted = download_attachment(drive_service, file_id, mime)
                if extracted:
                    materials_text.append(f"--- CONTENT FROM ATTACHED FILE ({meta.get('name')}) ---\n{extracted}\n")
            except Exception as e:
                print(f"Skipping drive file {file_id}: {e}")
                
    combined_instructions = description + "\n\n" + "\n".join(materials_text)
    return title, combined_instructions.strip()

if __name__ == "__main__":
    # Test authentication directly if run
    authenticate_google()
    print("Authentication successful!")
