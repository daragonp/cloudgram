import dropbox
import os
from dropbox.files import WriteMode
from .base_service import CloudService

class DropboxService(CloudService):
    # CAMBIO PRINCIPAL: Ahora recibimos 3 argumentos en lugar de 1
    def __init__(self, app_key, app_secret, refresh_token):
        if not all([app_key, app_secret, refresh_token]):
            self.dbx = None
            return

        try:
            # Añadimos check_invalid_token=True para que valide al arrancar
            self.dbx = dropbox.Dropbox(
                app_key=app_key,
                app_secret=app_secret,
                oauth2_refresh_token=refresh_token
            )
            # Prueba de fuego: Validar el token inmediatamente
            self.dbx.users_get_current_account()
            print("✅ DropboxService: Conexión verificada con éxito.")
        except Exception as e:
            print(f"❌ Error de autenticación en Dropbox: {e}")
            self.dbx = None 
    
    async def delete_file(self, path):
        if not self.dbx: return False
        try:
            # path debe ser algo como "/foto.jpg"
            self.dbx.files_delete_v2(path)
            print(f"✅ Archivo {path} borrado de Dropbox.")
            return True
        except Exception as e:
            print(f"❌ Error borrando en Dropbox: {e}")
            return False

    async def download_file(self, cloud_path, local_path):
        if not self.dbx: return False
        try:
            # cloud_path debe iniciar con "/" (ej: /foto.jpg)
            self.dbx.files_download_to_file(local_path, cloud_path)
            return True
        except Exception as e:
            print(f"Error descargando de Dropbox: {e}")
            return False

    async def get_link(self, cloud_path):
        """Recupera un link existente o crea uno nuevo"""
        try:
            res = self.dbx.sharing_list_shared_links(path=cloud_path, direct_only=True)
            if res.links:
                return res.links[0].url.replace('?dl=0', '?dl=1')
            link = self.dbx.sharing_create_shared_link_with_settings(cloud_path)
            return link.url.replace('?dl=0', '?dl=1')
        except:
            return None
            
    async def list_files(self, path=""):
        if not self.dbx: return []
        try:
            res = self.dbx.files_list_folder(path)
            return [item.name for item in res.entries]
        except Exception as e:
            print(f"Error listando Dropbox: {e}")
            return []
        
    
    async def create_folder(self, folder_name, parent_path=""):
        if not self.dbx: return None
        # Normalizamos el path: evitar "//" y asegurar que empiece con "/"
        path = f"/{parent_path}/{folder_name}".replace("//", "/")
        try:
            res = self.dbx.files_create_folder_v2(path)
            return res.metadata.path_display
        except dropbox.exceptions.ApiError as e:
            # Si el error es por conflicto (ya existe), devolvemos el path sin morir
            if e.error.is_path() and e.error.get_path().is_conflict():
                return path
            print(f"❌ Error Dropbox mkdir: {e}")
            return None

    async def upload(self, local_path, file_name, folder="General"):
        if not self.dbx: return None
        # Aseguramos un path limpio
        cloud_path = f"/{folder}/{file_name}".replace("//", "/")
        try:
            with open(local_path, "rb") as f:
                self.dbx.files_upload(f.read(), cloud_path, mode=WriteMode('overwrite'))
            
            # Intentar obtener o crear link
            try:
                link_metadata = self.dbx.sharing_create_shared_link_with_settings(cloud_path)
                url = link_metadata.url
            except dropbox.exceptions.ApiError as e:
                if "shared_link_already_exists" in str(e):
                    links = self.dbx.sharing_list_shared_links(path=cloud_path, direct_only=True).links
                    url = links[0].url if links else None
                else: raise e
            
            # IMPORTANTE: Retornar solo el string transformado para descarga directa
            return url.replace('?dl=0', '?dl=1') if url else None
        except Exception as e:
            print(f"❌ Error Dropbox Upload: {e}")
            return None