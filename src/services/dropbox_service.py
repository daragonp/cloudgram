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
        """Recupera un link privado directamente al visor web de Dropbox"""
        try:
            return f"https://www.dropbox.com/preview{cloud_path}"
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
        cloud_path = f"/{folder}/{file_name}".replace("//", "/")
        try:
            with open(local_path, "rb") as f:
                self.dbx.files_upload(f.read(), cloud_path, mode=WriteMode('overwrite'))
            
            # Devolvemos directamente el enlace a la vista privada
            return f"https://www.dropbox.com/preview{cloud_path}"
        except Exception as e:
            print(f"❌ Error real en Dropbox: {e}")
            return None

    # --- operaciones adicionales ------------------------------------------------
    async def move_file(self, source_path: str, dest_path: str):
        """Mueve o renombra un archivo/carpeta dentro de Dropbox.
        Devuelve la ruta nueva o False en caso de fallo.
        """
        if not self.dbx:
            return False
        try:
            res = self.dbx.files_move_v2(source_path, dest_path, autorename=True)
            return res.metadata.path_display
        except Exception as e:
            print(f"❌ Error moviendo en Dropbox: {e}")
            return False