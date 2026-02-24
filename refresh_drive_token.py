import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

def refresh_google_token():
    try:
        # 1. Cargamos los datos desde las variables de entorno (no archivos)
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")

        if not all([client_id, client_secret, refresh_token]):
            print("❌ Faltan variables de entorno de Google")
            return False

        # 2. Creamos el objeto de credenciales sin buscar archivos
        creds = Credentials(
            token=None, # El access_token se generará al refrescar
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret
        )

        # 3. Refrescamos el token
        creds.refresh(Request())
        
        # Opcional: Aquí podrías guardar el nuevo creds.token en memoria o DB
        print("✅ Token de Google Drive renovado exitosamente")
        return True

    except Exception as e:
        print(f"❌ Error al renovar token: {e}")
        return False