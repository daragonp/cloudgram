# src/services/google_drive_service.py
import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from .base_service import CloudService


class GoogleDriveService(CloudService):
    def __init__(self):
        self.scopes = ['https://www.googleapis.com/auth/drive.file']
        self.service = None # No autenticamos en el constructor

    def _get_service(self):
        """Autentica solo cuando sea estrictamente necesario"""
        if self.service:
            return self.service
            
        creds = None
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', self.scopes)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', self.scopes)
                creds = flow.run_local_server(port=0)
            with open('token.json', 'w') as token:
                token.write(creds.to_json())
        
        self.service = build('drive', 'v3', credentials=creds)
        return self.service

    async def upload(self, local_path, file_name):
        service = self._get_service()
        file_metadata = {'name': file_name}
        media = MediaFileUpload(local_path, resumable=True)
        
        # 1. Crear el archivo en Drive
        file = service.files().create(
            body=file_metadata, 
            media_body=media, 
            fields='id, webViewLink' 
        ).execute()
        
        # 2. Cambiar rol a 'reader' (el valor correcto en v3)
        try:
            permission = {
                'type': 'anyone', 
                'role': 'reader' 
            }
            service.permissions().create(
                fileId=file.get('id'), 
                body=permission
            ).execute()
        except Exception as e:
            print(f"⚠️ No se pudo establecer permiso público: {e}")
        
        return file.get('webViewLink')
    
    async def list_files(self, path="/"):

        results = self.service.files().list(pageSize=10, fields="nextPageToken, files(id, name)").execute()
        items = results.get('files', [])
        return [item['name'] for item in items]
    
    async def delete_file(self, file_name):
        """Busca un archivo por nombre y lo elimina de Google Drive"""
        if not self.service: return False
        try:
            # 1. Buscar el ID del archivo por su nombre
            query = f"name = '{file_name}' and trashed = false"
            response = self.service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
            files = response.get('files', [])

            if not files:
                print(f"⚠️ No se encontró el archivo '{file_name}' en Google Drive.")
                return False

            # 2. Eliminar el archivo usando su ID
            file_id = files[0].get('id')
            self.service.files().delete(fileId=file_id).execute()
            print(f"✅ Archivo '{file_name}' (ID: {file_id}) eliminado de Google Drive.")
            return True
        except Exception as e:
            print(f"❌ Error borrando en Google Drive: {e}")
            return False