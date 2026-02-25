import os
import dropbox
from dotenv import load_dotenv

# 1. CARGA DE ENTORNO
load_dotenv()

APP_KEY = os.getenv("DROPBOX_APP_KEY")
APP_SECRET = os.getenv("DROPBOX_APP_SECRET")

auth_flow = dropbox.DropboxOAuth2FlowNoRedirect(APP_KEY, APP_SECRET, token_access_type='offline')

authorize_url = auth_flow.start()
print("1. Ve a esta URL:")
print(authorize_url)
print("2. Autoriza la aplicación.")
print("3. Copia el código de autorización que te da Dropbox.")

auth_code = input("Pega el código de autorización aquí: ").strip()

try:
    oauth_result = auth_flow.finish(auth_code)
    print("\n--- ¡ÉXITO! ---")
    print("Guarda estos datos en tu script original:")
    print(f"REFRESH_TOKEN: {oauth_result.refresh_token}")
except Exception as e:
    print(f"Error: {e}")