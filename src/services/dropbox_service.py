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

    async def upload(self, local_path, file_name, folder_path="General"):
        if not self.dbx: return None
        # Aseguramos que el path empiece por / y no tenga dobles //
        cloud_path = f"/{folder_path}/{file_name}".replace("//", "/")
        
        try:
            with open(local_path, "rb") as f:
                self.dbx.files_upload(f.read(), cloud_path, mode=dropbox.files.WriteMode('overwrite'))
            
            try:
                # Intentar crear link nuevo
                link_metadata = self.dbx.sharing_create_shared_link_with_settings(cloud_path)
                return link_metadata.url.replace('?dl=0', '?dl=1')
            except dropbox.exceptions.ApiError as e:
                # Si el link ya existe (Error shared_link_already_exists)
                if e.error.is_shared_link_already_exists():
                    links = self.dbx.sharing_list_shared_links(path=cloud_path, direct_only=True).links
                    return links[0].url.replace('?dl=0', '?dl=1')
                raise e
        except Exception as e:
            print(f"❌ Error crítico Dropbox: {e}")
            return None    
    
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
        full_path = f"{parent_path}/{folder_name}".replace("//", "/")
        try:
            res = self.dbx.files_create_folder_v2(full_path)
            return res.metadata.path_display
        except Exception as e:
            print(f"Error en Dropbox mkdir: {e}")
            raise e