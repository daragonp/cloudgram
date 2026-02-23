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
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))