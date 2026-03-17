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

# ============================================================================
# CLIENTE DE IA - IMPORTANTE
# ============================================================================
"""
NOTA: El cliente OpenAI-compatible de Gemini SOLO soporta:
- Chat Completions (gemini-2.0-flash, gemini-1.5-flash)
- Vision (imágenes)

NO soporta:
- Embeddings (usar google.generativeai nativo via AIHandler)
- Audio (usar google.generativeai nativo via AIHandler)

Para embeddings y transcripción de audio, usar src.utils.ai_handler.AIHandler
"""

openai_client = OpenAI(
    api_key=os.getenv("GEMINI_API_KEY"),
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)
logger.info("✅ Cliente OpenAI-compatible (Gemini) inicializado para chat/vision")

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
        "gemini_chat": False,
        "gemini_embedding": False
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
    
    # Test Gemini Embedding (via native API)
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        genai.embed_content(model="models/text-embedding-004", content="test")
        results["gemini_embedding"] = True
    except Exception as e:
        logger.error(f"Error Gemini Embedding: {e}")
    
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
        print(f"  {icons[is_ok]} {service.replace('_', ' ').title()}")
    
    print("="*50 + "\n")
    
    return all(status.values())


if __name__ == "__main__":
    print_status()
