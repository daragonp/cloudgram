import os
import msal
import requests
import json
import logging
from .base_service import CloudService

logger = logging.getLogger(__name__)

class OneDriveService(CloudService):
    def __init__(self, client_id, client_secret, refresh_token):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.authority = "https://login.microsoftonline.com/common"
        self.scopes = ["Files.ReadWrite.All"]
        self.base_url = "https://graph.microsoft.com/v1.0"
        
        if not all([client_id, client_secret, refresh_token]):
            self.app = None
            logger.warning("⚠️ OneDriveService: Faltan credenciales (CLIENT_ID, SECRET o REFRESH_TOKEN)")
            return

        try:
            self.app = msal.ConfidentialClientApplication(
                client_id,
                authority=self.authority,
                client_credential=client_secret
            )
            # Validar que podemos obtener un token inicial
            token = self._get_access_token()
            if token:
                logger.info("✅ OneDriveService: Conexión verificada con éxito.")
            else:
                logger.error("❌ OneDriveService: No se pudo obtener el token de acceso.")
                self.app = None
        except Exception as e:
            logger.error(f"❌ Error de inicialización en OneDrive: {e}")
            self.app = None

    def _get_access_token(self):
        if not self.app:
            return None
        try:
            result = self.app.acquire_token_by_refresh_token(
                self.refresh_token,
                scopes=self.scopes
            )
            if "access_token" in result:
                return result["access_token"]
            else:
                logger.error(f"❌ Error MSAL: {result.get('error_description', result.get('error'))}")
                return None
        except Exception as e:
            logger.error(f"❌ Error recuperando token OneDrive: {e}")
            return None

    def _get_headers(self):
        token = self._get_access_token()
        if not token:
            return None
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    async def list_files(self, path="root"):
        """Lista archivos en una carpeta específica id o 'root'."""
        headers = self._get_headers()
        if not headers: return []
        
        endpoint = f"{self.base_url}/me/drive/{path}/children"
        try:
            response = requests.get(endpoint, headers=headers)
            if response.status_code == 200:
                items = response.json().get('value', [])
                return [item['name'] for item in items]
            return []
        except Exception as e:
            print(f"❌ Error listando OneDrive: {e}")
            return []

    async def create_folder(self, folder_name, parent_id=None):
        """Crea una carpeta y devuelve su ID."""
        headers = self._get_headers()
        if not headers: return None

        # Si parent_id es None o vacío, usamos la raíz
        parent = f"items/{parent_id}" if parent_id else "root"
        endpoint = f"{self.base_url}/me/drive/{parent}/children"
        
        # Primero verificar si existe para evitar duplicados
        try:
            check_resp = requests.get(endpoint, headers=headers)
            if check_resp.status_code == 200:
                existing = [i for i in check_resp.json().get('value', []) if i['name'] == folder_name and 'folder' in i]
                if existing:
                    return existing[0]['id']
        except: pass

        body = {
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail"
        }
        
        try:
            response = requests.post(endpoint, headers=headers, json=body)
            if response.status_code in [201, 200]:
                return response.json().get('id')
            elif response.status_code == 409: # Conflict
                logger.info(f"ℹ️ OneDrive: Carpeta '{folder_name}' ya existe (409). Buscando ID...")
                check_resp = requests.get(endpoint, headers=headers)
                existing = [i for i in check_resp.json().get('value', []) if i['name'] == folder_name]
                return existing[0]['id'] if existing else None
            else:
                logger.error(f"❌ OneDrive create_folder failed Status: {response.status_code} Resp: {response.text}")
            return None
        except Exception as e:
            logger.error(f"❌ Error OneDrive mkdir: {e}")
            return None

    async def upload(self, local_path, file_name, folder_id=None):
        """Sube un archivo y devuelve el enlace de visualización."""
        headers = self._get_headers()
        if not headers: return None

        file_size = os.path.getsize(local_path)
        # Para archivos > 4MB usamos sesión de subida (recomendado para estabilidad)
        if file_size > 4000000:
            return await self._upload_large_file(local_path, file_name, folder_id)
        
        # Subida simple (PUT)
        parent = f"items/{folder_id}" if folder_id else "root"
        endpoint = f"{self.base_url}/me/drive/{parent}:/{file_name}:/content"
        
        try:
            with open(local_path, "rb") as f:
                response = requests.put(endpoint, headers={"Authorization": headers["Authorization"]}, data=f)
                if response.status_code in [200, 201]:
                    item_id = response.json().get('id')
                    return await self.get_link(item_id)
                else:
                    logger.error(f"❌ OneDrive upload failed Status: {response.status_code} Resp: {response.text}")
            return None
        except Exception as e:
            logger.error(f"❌ Error subida simple OneDrive: {e}")
            return None

    async def _upload_large_file(self, local_path, file_name, folder_id=None):
        headers = self._get_headers()
        parent = f"items/{folder_id}" if folder_id else "root"
        endpoint = f"{self.base_url}/me/drive/{parent}:/{file_name}:/createUploadSession"
        
        try:
            session_resp = requests.post(endpoint, headers=headers, json={"item": {"@microsoft.graph.conflictBehavior": "replace"}})
            if session_resp.status_code != 200: return None
            
            upload_url = session_resp.json().get('uploadUrl')
            file_size = os.path.getsize(local_path)
            chunk_size = 327680 * 10 # ~3MB por trozo (debe ser múltiplo de 327680)
            
            with open(local_path, "rb") as f:
                start = 0
                while start < file_size:
                    chunk = f.read(chunk_size)
                    end = start + len(chunk) - 1
                    headers_chunk = {
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                        "Content-Length": str(len(chunk))
                    }
                    resp = requests.put(upload_url, headers=headers_chunk, data=chunk)
                    start = end + 1
                    if resp.status_code in [201, 200]:
                        item_id = resp.json().get('id')
                        return await self.get_link(item_id)
            return None
        except Exception as e:
            print(f"❌ Error subida pesada OneDrive: {e}")
            return None

    async def get_link(self, item_id):
        """Genera un link de visualización compartido."""
        headers = self._get_headers()
        if not headers: return None
        
        endpoint = f"{self.base_url}/me/drive/items/{item_id}/createLink"
        body = {"type": "view", "scope": "anonymous"}
        
        try:
            response = requests.post(endpoint, headers=headers, json=body)
            if response.status_code in [200, 201]:
                return response.json().get('link', {}).get('webUrl')
            else:
                print(f"❌ OneDrive get_link failed Status: {response.status_code} Resp: {response.text}")
            return None
        except Exception as e:
            print(f"❌ Error obteniendo link OneDrive: {e}")
            return None

    async def delete_file(self, item_id):
        headers = self._get_headers()
        if not headers: return False
        
        endpoint = f"{self.base_url}/me/drive/items/{item_id}"
        try:
            response = requests.delete(endpoint, headers=headers)
            return response.status_code == 204
        except Exception as e:
            print(f"❌ Error borrando OneDrive: {e}")
            return False

    async def download_file(self, item_id, local_path):
        headers = self._get_headers()
        if not headers: return False
        
        endpoint = f"{self.base_url}/me/drive/items/{item_id}/content"
        try:
            response = requests.get(endpoint, headers=headers, stream=True)
            if response.status_code == 200:
                with open(local_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                return True
            return False
        except Exception as e:
            print(f"❌ Error descargando OneDrive: {e}")
            return False

    async def download_file_by_name(self, file_name, local_path):
        """Busca por nombre y descarga."""
        headers = self._get_headers()
        if not headers: return False
        
        # Búsqueda usando query parameters
        endpoint = f"{self.base_url}/me/drive/root/search(q='{file_name}')"
        try:
            response = requests.get(endpoint, headers=headers)
            if response.status_code == 200:
                items = response.json().get('value', [])
                # Filtrar coincidencia exacta
                matches = [i for i in items if i['name'] == file_name]
                if matches:
                    return await self.download_file(matches[0]['id'], local_path)
            return False
        except Exception as e:
            print(f"❌ Error buscando/descargando OneDrive: {e}")
            return False

    async def move_file(self, item_id, new_parent_id):
        headers = self._get_headers()
        if not headers: return False
        
        endpoint = f"{self.base_url}/me/drive/items/{item_id}"
        body = {
            "parentReference": {"id": new_parent_id}
        }
        try:
            response = requests.patch(endpoint, headers=headers, json=body)
            return response.status_code == 200
        except Exception as e:
            print(f"❌ Error moviendo OneDrive: {e}")
            return False
