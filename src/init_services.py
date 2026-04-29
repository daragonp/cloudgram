# src/init_services.py
"""
Inicialización de servicios para CloudGram Pro.
Todos los servicios se inicializan aquí para ser compartidos entre módulos.
"""
import os
import logging
from dotenv import load_dotenv
from openai import OpenAI

from src.database.db_handler import DatabaseHandler
from src.services.dropbox_service import DropboxService
from src.services.google_drive_service import GoogleDriveService
from src.services.onedrive_service import OneDriveService

load_dotenv()

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# INSTANCIAS COMPARTIDAS
# ============================================================================

# Base de datos
db = DatabaseHandler()
logger.info("✅ DatabaseHandler inicializado")

# Dropbox Service
dropbox_svc = DropboxService(
    app_key=os.getenv("DROPBOX_APP_KEY"),
    app_secret=os.getenv("DROPBOX_APP_SECRET"),
    refresh_token=os.getenv("DROPBOX_REFRESH_TOKEN")
)
if dropbox_svc.dbx:
    logger.info("✅ DropboxService conectado")
else:
    logger.warning("⚠️ DropboxService no disponible (credenciales faltantes)")

# Google Drive Service
drive_svc = GoogleDriveService()
if drive_svc.service:
    logger.info("✅ GoogleDriveService conectado")
else:
    logger.warning("⚠️ GoogleDriveService no disponible")

# OneDrive Service
onedrive_svc = OneDriveService(
    client_id=os.getenv("ONEDRIVE_CLIENT_ID"),
    client_secret=os.getenv("ONEDRIVE_CLIENT_SECRET"),
    refresh_token=os.getenv("ONEDRIVE_REFRESH_TOKEN")
)
if onedrive_svc.app:
    logger.info("✅ OneDriveService conectado")
else:
    logger.warning("⚠️ OneDriveService no disponible (credenciales faltantes)")

# ============================================================================
# CLIENTE DE IA - ARQUITECTURA ACTUAL
# ============================================================================
"""
Todo el pipeline de IA usa OpenAI:
  • Embeddings     → text-embedding-3-small  (AIHandler.get_embedding)
  • Visión/Imágenes → gpt-4o-mini            (AIHandler.analyze_image_vision)
  • Audio/Voz      → whisper-1               (AIHandler.transcribe_audio)
  • Resúmenes      → gpt-4o-mini            (AIHandler.generate_summary)
  • Intención /buscar_ia → gpt-4o           (AIHandler.analyze_search_intent)

Gemini (GEMINI_API_KEY) ya no se usa en el pipeline principal.
Se mantiene en .env por compatibilidad pero no es requerida.
"""

openai_client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)
logger.info("✅ Cliente OpenAI inicializado (embeddings, visión, audio, resúmenes)")

# ============================================================================
# FUNCIONES DE UTILIDAD
# ============================================================================

def test_all_connections():
    """
    Prueba todas las conexiones de servicios.
    Útil para diagnóstico al iniciar la aplicación.
    """
    results = {
        "database": False,
        "dropbox": False,
        "google_drive": False,
        "onedrive": False,
        "gemini_chat": False,
        "openai_embedding": False
    }
    
    # Test Database
    try:
        results["database"] = db.check_connection()
    except Exception as e:
        logger.error(f"Error DB: {e}")
    
    # Test Dropbox
    try:
        if dropbox_svc.dbx:
            dropbox_svc.dbx.users_get_current_account()
            results["dropbox"] = True
    except Exception as e:
        logger.error(f"Error Dropbox: {e}")
    
    # Test Google Drive
    try:
        if drive_svc.service:
            drive_svc.service.about().get(fields="user").execute()
            results["google_drive"] = True
    except Exception as e:
        logger.error(f"Error Drive: {e}")
    
    # Test OneDrive
    try:
        if onedrive_svc.app:
            token = onedrive_svc._get_access_token()
            if token:
                results["onedrive"] = True
    except Exception as e:
        logger.error(f"Error OneDrive: {e}")
    
    # Test Gemini Chat (via OpenAI-compatible)
    try:
        response = openai_client.chat.completions.create(
            model="gemini-2.0-flash",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5
        )
        results["gemini_chat"] = True
    except Exception as e:
        logger.error(f"Error Gemini Chat: {e}")
    
    # Test OpenAI Embedding
    try:
        from openai import OpenAI as _OAI
        _oa = _OAI(api_key=os.getenv("OPENAI_API_KEY"))
        _oa.embeddings.create(model="text-embedding-3-small", input="test")
        results["openai_embedding"] = True
    except Exception as e:
        logger.error(f"Error OpenAI Embedding: {e}")
    
    return results


def print_status():
    """Imprime el estado de todos los servicios."""
    print("\n" + "="*50)
    print("📊 ESTADO DE SERVICIOS - CLOUDGRAM PRO")
    print("="*50)
    
    status = test_all_connections()
    
    icons = {
        True: "✅",
        False: "❌"
    }
    
    for service, is_ok in status.items():
        logger.info(f"  {icons[is_ok]} {service.replace('_', ' ').title()}")
    
    print("="*50 + "\n")
    
    return all(status.values())


if __name__ == "__main__":
    print_status()
