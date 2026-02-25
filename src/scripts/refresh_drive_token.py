import os
import json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

def refresh_google_token():
    try:
        # 1. Leemos los "paquetes" JSON desde el .env
        creds_json = os.getenv("GOOGLE_DRIVE_CREDENTIALS")
        token_json = os.getenv("GOOGLE_DRIVE_TOKEN_JSON")

        if not creds_json or not token_json:
            print("❌ Faltan las variables GOOGLE_DRIVE_CREDENTIALS o GOOGLE_DRIVE_TOKEN_JSON")
            return False

        # 2. Convertimos el texto JSON a diccionarios de Python
        creds_data = json.loads(creds_json)
        token_data = json.loads(token_json)

        # 3. Extraemos las piezas desde adentro del JSON
        # En tu .env, el client_id está dentro de "installed"
        client_id = creds_data['installed']['client_id']
        client_secret = creds_data['installed']['client_secret']
        
        # El refresh_token está en el otro paquete (TOKEN_JSON)
        refresh_token = token_data.get('refresh_token')

        if not refresh_token:
            print("❌ No se encontró refresh_token dentro del JSON")
            return False

        # 4. Configuramos las credenciales para Google
        creds = Credentials(
            token=token_data.get('token'),
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret
        )

        # 5. Pedimos a Google un nuevo token de acceso
        creds.refresh(Request())
        
        print("✅ Conexión con Google Drive actualizada correctamente.")
        return True

    except Exception as e:
        print(f"❌ Error al procesar los JSON de Google: {e}")
        return False