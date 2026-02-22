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
            
    async def upload(self, local_path, file_name):
        if not self.dbx: return None
        try:
            with open(local_path, "rb") as f:
                # Sube el archivo
                self.dbx.files_upload(f.read(), f"/{file_name}", mode=WriteMode('overwrite'))
                
                # Crea o recupera el link compartido
                link = self.dbx.sharing_create_shared_link_with_settings(f"/{file_name}")
                # Forzar descarga/vista directa cambiando dl=0 por dl=1
                return link.url.replace('?dl=0', '?dl=1') 
        except Exception as e:
            print(f"Error subiendo a Dropbox: {e}")
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