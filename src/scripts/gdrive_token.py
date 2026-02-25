from google_auth_oauthlib.flow import InstalledAppFlow
import json
import os

# Copia aquí el contenido de tu GOOGLE_DRIVE_CREDENTIALS (el JSON de "installed")
client_config = {
    "installed": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "project_id": "whatsappdrivebot-486000",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "redirect_uris": ["http://localhost"]
    }
}

scopes = ["https://www.googleapis.com/auth/drive.file"]
flow = InstalledAppFlow.from_client_config(client_config, scopes)
creds = flow.run_local_server(port=0)

print("\n--- NUEVO TOKEN JSON PARA RAILWAY ---")
print(creds.to_json())