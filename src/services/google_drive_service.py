import os
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from .base_service import CloudService

class GoogleDriveService(CloudService):
    def __init__(self):
        self.scopes = ['https://www.googleapis.com/auth/drive.file']
        self.service = None

    def _get_service(self):
        if self.service:
            return self.service
            
        creds = None
        # Intentamos leer el token desde la variable de entorno (Railway)
        token_env = os.getenv('GOOGLE_DRIVE_TOKEN_JSON')
        
        if token_env:
            # Cargamos las credenciales desde el texto de la variable
            token_data = json.loads(token_env)
            creds = Credentials.from_authorized_user_info(token_data, self.scopes)
        
        # Si el token existe pero expiró, lo renovamos automáticamente
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Opcional: Podrías imprimir el nuevo token si quieres actualizarlo, 
            # pero Google suele manejar la renovación en memoria bien.
        
        if not creds:
            raise Exception("❌ No se encontró GOOGLE_DRIVE_TOKEN_JSON en Railway. Configúralo en Variables.")

        self.service = build('drive', 'v3', credentials=creds)
        return self.service

    async def list_files(self, limit=10000):
            service = self._get_service()
            # Aumentamos el pageSize para no dejar archivos fuera
            results = service.files().list(
                pageSize=limit, 
                fields="files(id, name)",
                q="trashed = false" # No indexar la papelera
            ).execute()
            items = results.get('files', [])
            return [item['name'] for item in items]
    
    async def download_file_by_name(self, file_name, local_path):
        service = self._get_service()
        try:
            # 1. Buscar el ID por nombre
            query = f"name = '{file_name}' and trashed = false"
            res = service.files().list(q=query, fields="files(id)").execute()
            files = res.get('files', [])
            if not files: return False
            
            file_id = files[0]['id']
            # 2. Descargar el contenido
            from googleapiclient.http import MediaIoBaseDownload
            import io
            
            request = service.files().get_media(fileId=file_id)
            fh = io.FileIO(local_path, 'wb')
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            fh.close()
            return True
        except Exception as e:
            print(f"Error descargando de Drive: {e}")
            return False

    async def get_link_by_name(self, file_name):
        service = self._get_service()
        query = f"name = '{file_name}' and trashed = false"
        res = service.files().list(q=query, fields="files(webViewLink)").execute()
        files = res.get('files', [])
        return files[0].get('webViewLink') if files else None
    
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
        
    async def upload(self, local_path, file_name, folder_id=None):
            service = self._get_service()
            try:
                # Si folder_id es "root" o vacío, lo tratamos como None
                p_id = folder_id if (folder_id and folder_id != "root") else None
                
                file_metadata = {'name': file_name}
                if p_id:
                    file_metadata['parents'] = [p_id]
                    
                media = MediaFileUpload(local_path, resumable=True)
                file = service.files().create(
                    body=file_metadata, 
                    media_body=media, 
                    fields='id, webViewLink'
                ).execute()

                # Permisos públicos para que el link funcione
                try:
                    service.permissions().create(
                        fileId=file.get('id'),
                        body={'type': 'anyone', 'role': 'reader'}
                    ).execute()
                except: pass

                # RETORNO ÚNICO: Solo el string del link
                return file.get('webViewLink')
            except Exception as e:
                print(f"❌ Error Drive Upload: {e}")
                return None

    async def create_folder(self, folder_name, parent_id=None):
        service = self._get_service()
        try:
            p_id = parent_id if (parent_id and parent_id != "root") else None
            
            metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            if p_id:
                metadata['parents'] = [p_id]
                
            folder = service.files().create(body=metadata, fields='id').execute()
            return folder.get('id')
        except Exception as e:
            print(f"❌ Error Drive mkdir: {e}")
            return None