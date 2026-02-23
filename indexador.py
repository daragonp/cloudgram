import asyncio
import os
import json
from src.database.db_handler import DatabaseHandler  # <--- CORREGIDO
from src.utils.ai_handler import AIHandler
from src.services.dropbox_service import DropboxService
from src.services.google_drive_service import GoogleDriveService
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()
from src.init_services import db, dropbox_svc, drive_svc

# Inicialización de servicios
dropbox_svc = DropboxService(
    app_key=os.getenv("DROPBOX_APP_KEY"),
    app_secret=os.getenv("DROPBOX_APP_SECRET"),
    refresh_token=os.getenv("DROPBOX_REFRESH_TOKEN")
)
drive_svc = GoogleDriveService()

async def procesar_archivos_viejos():
    """
    Escanea Dropbox y Drive, compara con Supabase e indexa lo faltante.
    """
    print("🔍 Iniciando escaneo global de nubes...")
    reporte = {"nuevos": 0, "errores": 0}
    
    # 1. Escaneo de Dropbox
    print("📦 Escaneando Dropbox...")
    dbx_files = await dropbox_svc.list_files("")
    for name in dbx_files:
        await _indexar_si_falta(name, 'dropbox', reporte)

    # 2. Escaneo de Google Drive
    print("📂 Escaneando Google Drive...")
    try:
        drive_files = await drive_svc.list_files(limit=50)
        for name in drive_files:
            await _indexar_si_falta(name, 'drive', reporte)
    except Exception as e:
        print(f"❌ Error en Drive Indexer: {e}")

    return f"Procesados: {reporte['nuevos']} nuevos archivos. Errores: {reporte['errores']}."

async def _indexar_si_falta(name, servicio, reporte):
    """Lógica interna para procesar un archivo individual si no está en DB"""
    # Verificamos si ya existe en Supabase
    existente = db.get_file_by_name_and_service(name, servicio)
    
    # Si existe y ya tiene embedding, saltamos
    if existente and existente.get('embedding'):
        return

    print(f"⚙️ Procesando: {name} ({servicio})...")
    local_path = os.path.join("descargas", name)
    
    try:
        # 1. Descarga según el servicio
        success = False
        url = None
        if servicio == 'dropbox':
            success = await dropbox_svc.download_file(f"/{name}", local_path)
            url = await dropbox_svc.get_link(f"/{name}")
        elif servicio == 'drive':
            success = await drive_svc.download_file_by_name(name, local_path)
            url = await drive_svc.get_link_by_name(name)

        if not success or not os.path.exists(local_path):
            raise Exception("No se pudo descargar el archivo.")

        # 2. Análisis IA
        texto = await AIHandler.extract_text(local_path)
        # Recortamos texto para evitar errores de tokens (usando tu función)
        texto_limpio = limpiar_y_recortar_texto(texto)
        vector = await AIHandler.get_embedding(texto_limpio) if texto_limpio else None

        # 3. Registro en Supabase
        # Si ya existe pero no tenía embedding, lo actualizamos, si no, creamos.
        db.register_file(
            telegram_id="INDEXER_SYNC",
            name=name,
            f_type=name.split('.')[-1] if '.' in name else 'file',
            cloud_url=url or "link_no_disponible",
            service=servicio,
            content_text=texto_limpio,
            embedding=vector
        )
        
        reporte['nuevos'] += 1
        print(f"✅ Indexado: {name}")

    except Exception as e:
        print(f"❌ Error indexando {name}: {e}")
        reporte['errores'] += 1
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

def limpiar_y_recortar_texto(texto, max_chars=15000):
    if not texto: return ""
    return texto[:max_chars] if len(texto) > max_chars else texto

# --- COMPATIBILIDAD CON WEB_ADMIN (FLASK) ---

def ejecutar_indexacion_completa():
    """Llamada por el botón del Dashboard. Usa un loop nuevo para no chocar con Flask."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        resultado = loop.run_until_complete(procesar_archivos_viejos())
        loop.close()
        return resultado
    except Exception as e:
        return f"Error: {str(e)}"

def ejecutar_indexacion_paso_a_paso():
    """Generador para la barra de progreso"""
    yield "data: 20\n\n"
    res = ejecutar_indexacion_completa()
    yield f"data: 100\n\n"