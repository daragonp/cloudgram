import os
from dotenv import load_dotenv
from openai import OpenAI
from src.database.db_handler import DatabaseHandler
from src.services.dropbox_service import DropboxService
from src.services.google_drive_service import GoogleDriveService

load_dotenv()

# Instancias compartidas
db = DatabaseHandler()
dropbox_svc = DropboxService(
    app_key=os.getenv("DROPBOX_APP_KEY"),
    app_secret=os.getenv("DROPBOX_APP_SECRET"),
    refresh_token=os.getenv("DROPBOX_REFRESH_TOKEN")
)
drive_svc = GoogleDriveService()

# Cliente de IA: usa la API de Google Gemini (gratis) con interfaz compatible con OpenAI
openai_client = OpenAI(
    api_key=os.getenv("GEMINI_API_KEY"),
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)