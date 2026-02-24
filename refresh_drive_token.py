import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Si modificas estos SCOPES, elimina el archivo token.json.
SCOPES = ['https://www.googleapis.com/auth/drive.file']

def refresh_google_token():
    creds = None
    # El archivo token.json almacena los tokens de acceso y actualización del usuario
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    try:
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                print("✅ Token refrescado exitosamente.")
            else:
                # Si no hay refresh token, hay que re-autenticar
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            
            # Guardar las credenciales para la próxima ejecución
            with open('token.json', 'w') as token:
                token.write(creds.to_json())
        return True
    except Exception as e:
        print(f"❌ Error al renovar token: {e}")
        return False

if __name__ == "__main__":
    refresh_google_token()