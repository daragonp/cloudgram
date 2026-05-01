# main.py
import os
import json
import logging
import warnings
import platform
import sys
import time
import shutil 
import numpy as np
from datetime import datetime
from dotenv import load_dotenv
from src.handlers.message_handlers import voice_options_callback


load_dotenv()
warnings.filterwarnings("ignore", category=FutureWarning)

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent, BotCommand
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    InlineQueryHandler,
    filters,
    ContextTypes,
    TypeHandler
)
from telegram.constants import ParseMode
from telegram.error import NetworkError

# 2. IMPORTACIÓN DE SERVICIOS INICIALIZADOS
from src.init_services import db, dropbox_svc, drive_svc, onedrive_svc, openai_client 

# 3. IMPORTACIÓN DE HANDLERS
from src.handlers.message_handlers import start, handle_any_file, show_cloud_menu, get_file_category, FILE_CATEGORIES
from src.handlers.auth_handler import auth_middleware
from src.utils.ai_handler import AIHandler, QuotaExceededError

# ================================================================================================
# CACHE GLOBAL DE CARPETAS
# ================================================================================================
CATEGORY_FOLDER_CACHE = {
    'dropbox': {},   # category_name -> path (ej. '/Documentos')
    'drive': {},     # category_name -> folder_id
    'onedrive': {}   # category_name -> folder_id (OneDrive)
}

# ================================================================================================
# RATE LIMITING DE COMANDOS TELEGRAM
# ================================================================================================
RATE_LIMIT_WINDOW_SECONDS = 5
RATE_LIMIT_STATE = {}

def is_rate_limited(user_id: int, command_name: str):
    if not user_id or not command_name:
        return False, 0
    key = f"{user_id}:{command_name}"
    now = time.time()
    last_ts = RATE_LIMIT_STATE.get(key, 0)
    if now - last_ts < RATE_LIMIT_WINDOW_SECONDS:
        return True, int(RATE_LIMIT_WINDOW_SECONDS - (now - last_ts))
    RATE_LIMIT_STATE[key] = now
    return False, 0

# ================================================================================================
# INICIALIZACIÓN DE CARPETAS POR CATEGORÍA
# ================================================================================================
async def ensure_category_folders():
    """
    Crea automáticamente las carpetas de categoría en Dropbox y Google Drive
    si no existen. Se ejecuta al startup del bot y rellena la cache desde BD.
    """
    global CATEGORY_FOLDER_CACHE
    
    print("\n📁 Inicializando estructura de carpetas por categoría...")
    
    # Cargar caché desde BD
    CATEGORY_FOLDER_CACHE = db.load_category_cache()
    print(f"   [BDD] Caché cargada: {len(CATEGORY_FOLDER_CACHE['dropbox'])} Dropbox, {len(CATEGORY_FOLDER_CACHE['drive'])} Drive, {len(CATEGORY_FOLDER_CACHE['onedrive'])} OneDrive")
    
    categories = list(FILE_CATEGORIES.keys()) + ["Otros"]
    
    # ========== DROPBOX ==========
    if dropbox_svc.dbx:
        try:
            result = dropbox_svc.dbx.files_list_folder("", recursive=False)
            existing_folders = {entry.name for entry in result.entries if hasattr(entry, 'name')}
            print(f"   [DROPBOX] Carpetas existentes: {existing_folders}")
        except Exception as e:
            print(f"   [⚠️  DROPBOX] Error listando: {e}")
            existing_folders = set()
        
        for category_name in categories:
            if category_name not in existing_folders:
                try:
                    result = await dropbox_svc.create_folder(category_name, parent_path="")
                    if result:
                        CATEGORY_FOLDER_CACHE['dropbox'][category_name] = result
                        db.save_category_folder(category_name, 'dropbox', result)
                        print(f"   [✅ DROPBOX] {category_name} -> {result} (CREADA)")
                except Exception as e:
                    print(f"   [⚠️  DROPBOX] {category_name}: {e}")
            else:
                CATEGORY_FOLDER_CACHE['dropbox'][category_name] = f"/{category_name}"
                print(f"   [✅ DROPBOX] {category_name} -> /{category_name} (EXISTENTE)")
        
    # ========== GOOGLE DRIVE ==========
    if drive_svc and drive_svc.service:
        try:
            query = "mimeType='application/vnd.google-apps.folder' and trashed=false and 'root' in parents"
            results = drive_svc.service.files().list(
                q=query, spaces='drive', fields='files(id, name)', pageSize=100
            ).execute()
            existing_drives = {f['name']: f['id'] for f in results.get('files', [])}
            print(f"   [GOOGLE DRIVE] Carpetas existentes: {set(existing_drives.keys())}")
        except Exception as e:
            print(f"   [⚠️  GOOGLE DRIVE] Error listando: {e}")
            existing_drives = {}
        
        for category_name in categories:
            if category_name in existing_drives:
                CATEGORY_FOLDER_CACHE['drive'][category_name] = existing_drives[category_name]
                db.save_category_folder(category_name, 'drive', existing_drives[category_name])
                print(f"   [✅ GOOGLE DRIVE] {category_name} -> {existing_drives[category_name]} (EXISTENTE)")
            else:
                try:
                    result = await drive_svc.create_folder(category_name, parent_id=None)
                    if result:
                        CATEGORY_FOLDER_CACHE['drive'][category_name] = result
                        db.save_category_folder(category_name, 'drive', result)
                        print(f"   [✅ GOOGLE DRIVE] {category_name} -> {result} (CREADA)")
                except Exception as e:
                    print(f"   [⚠️  GOOGLE DRIVE] {category_name}: {e}")

    # ========== ONEDRIVE ==========
    if onedrive_svc and onedrive_svc.app:
        try:
            # Listar carpetas raíz en OneDrive
            results = await onedrive_svc.list_files("root")
            logger.info(f"   [ONEDRIVE] Carpetas/archivos existentes: {results}")
        except Exception as e:
            logger.error(f"   [⚠️  ONEDRIVE] Error listando: {e}")
            results = []
        
        for category_name in categories:
            # Buscamos si ya tenemos el ID en caché
            existing_id = CATEGORY_FOLDER_CACHE['onedrive'].get(category_name)
            if existing_id:
                logger.info(f"   [✅ ONEDRIVE] {category_name} -> {existing_id} (EN CACHÉ)")
            else:
                try:
                    result = await onedrive_svc.create_folder(category_name, parent_id=None)
                    if result:
                        CATEGORY_FOLDER_CACHE['onedrive'][category_name] = result
                        db.save_category_folder(category_name, 'onedrive', result)
                        logger.info(f"   [✅ ONEDRIVE] {category_name} -> {result} (CREADA/RECUPERADA)")
                except Exception as e:
                    logger.error(f"   [⚠️  ONEDRIVE] {category_name}: {e}")


def print_server_welcome():
    """
    Realiza un chequeo exhaustivo del entorno y muestra un reporte 
    de bienvenida en la consola al iniciar el servidor.
    ANCHO DINÁMICO: Se adapta al tamaño de la ventana de la terminal.
    """
    load_dotenv()
    
    # 1. Obtener ancho de la terminal (default 80 si no se detecta)
    try:
        cols = shutil.get_terminal_size((80, 20)).columns
    except:
        cols = 80
    
    # Mínimo de seguridad para que no se rompa el diseño
    if cols < 40: cols = 40
    inner_width = cols - 2

    # 2. Cabecera Dinámica
    print("\n╔" + "═" * inner_width + "╗")
    
    title1 = "☁️  CLOUDGRAM PRO v1.0"
    title2 = "SISTEMA DE GESTIÓN CLOUD"
    
    # Centrar texto
    pad1 = (inner_width - len(title1)) // 2
    print("║" + " " * pad1 + title1 + " " * (inner_width - len(title1) - pad1) + "║")
    
    pad2 = (inner_width - len(title2)) // 2
    print("║" + " " * pad2 + title2 + " " * (inner_width - len(title2) - pad2) + "║")
    
    print("╚" + "═" * inner_width + "╝")

    # 3. Información del Sistema
    print(f"📅 Fecha de arranque: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"💻 Sistema Operativo: {platform.system()} {platform.release()}")
    print(f"🐍 Python Versión:  {sys.version.split()[0]}")
    print("-" * cols)

    # 4. Directorios
    print("📁 VERIFICACIÓN DE DIRECTORIOS:")
    required_dirs = ['descargas', 'data']
    for folder in required_dirs:
        if not os.path.exists(folder):
            os.makedirs(folder)
            print(f"   [+] Creado: /{folder}")
        else:
            print(f"   [OK] Detectado: /{folder}")

    db_url = os.getenv("DATABASE_URL")
    if db_url and "supabase" in db_url.lower():
        print(f"🗄️  Base de Datos:  CONECTADA A SUPABASE (Nube)")
    else:
        print(f"🗄️  Base de Datos:  LOCAL (SQLite)")

    # 5. Credenciales
    print("-" * cols)
    print("🔑 CHEQUEO DE CREDENCIALES (.env):")
    critical_keys = [
        'TELEGRAM_BOT_TOKEN', 
        'GEMINI_API_KEY', 
        'DROPBOX_APP_KEY',
        'DROPBOX_REFRESH_TOKEN'
    ]
    
    all_ok = True
    for key in critical_keys:
        val = os.getenv(key)
        if not val or val == "tu_token_aqui":
            print(f"   [❌] Faltante: {key}")
            all_ok = False
        else:
            print(f"   [✅] Configurada: {key} ({val[:4]}***)")

    print("-" * cols)
    if all_ok:
        print("🚀 ¡SERVIDOR LISTO! Conectando con la API de Telegram...")
    else:
        print("⚠️  ADVERTENCIA: Faltan llaves. El bot podría no funcionar.")
    print("═" * cols + "\n")

# 3. FUNCIONES DE COMANDO
async def list_files_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    files = db.get_last_files(20)
    if not files:
        return await update.message.reply_text("La lista está vacía.")
    
    text = "📋 *Últimos 20 archivos:*\n\n"
    for i, f in enumerate(files, 1): # El '1' inicia el conteo en 1
        num_emoji = "".join(f"{d}️⃣" for d in str(i))
        text += f"{num_emoji} [{f[1]}]({f[2]}) ({f[3].upper()})\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 *Ayuda de CloudGram Pro*\n\n"
        "Comandos principales:\n"
        "• /start - Menú principal\n"
        "• /stats - Ver estadísticas en tiempo real\n"
        "• /listar - Mostrar archivos recientes\n"
        "• /buscar <texto> - Buscar por nombre\n"
        "• /buscar_ia <consulta> - Búsqueda semántica (IA)\n"
        "• /preguntar <ID> <pregunta> - Pregunta sobre un documento ya indexado\n"
        "• /indexar - Generar embeddings pendientes\n"
        "• /eliminar <texto> - Eliminar archivos por nombre\n"
        "• /cancelar - Cancelar acciones en curso\n\n"
        "También puedes enviar archivos (documentos, fotos, audio, voz).\n"
        "Al enviar una nota de voz puedes elegir transcribir o subirla y seleccionar la/s nubes donde guardarla."
    )
    await update.message.reply_text(help_text)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /stats para mostrar métricas directamente en Telegram."""
    try:
        from src.init_services import onedrive_svc
        
        with db._connect() as conn:
            with conn.cursor() as cur:
                # Totales generales
                cur.execute("SELECT COUNT(*) FROM files")
                total_db = cur.fetchone()[0]

                # Desglose por nube
                cur.execute("SELECT service, COUNT(*) FROM files GROUP BY service")
                rows = cur.fetchall()
                services_count = {row[0]: row[1] for row in rows}

                # IA
                cur.execute("SELECT COUNT(*) FROM files WHERE embedding IS NOT NULL")
                count_ia = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM files WHERE type IN ('🖼️ Foto', '🎥 Video', 'jpg', 'png', 'jpeg') OR name ILIKE '%.jpg' OR name ILIKE '%.png' OR name ILIKE '%.jpeg'")
                count_fotos = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM files WHERE embedding IS NULL")
                count_pending = cur.fetchone()[0]
                
        db_status = db.check_connection()
        
        # Barra de progreso IA
        if total_db > 0:
            porcentaje = (count_ia / total_db) * 100
            bloques = int(porcentaje / 10)
            barra = "■" * bloques + "□" * (10 - bloques)
        else:
            porcentaje = 0
            barra = "□" * 10

        texto = f"📊 *ESTADÍSTICAS CLOUDGRAM*\n"
        texto += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        
        texto += f"📁 *Archivos Totales:* `{total_db}`\n"
        texto += f"🧠 *Indexación IA:* `{porcentaje:.1f}%`\n"
        texto += f"`[{barra}]`\n\n"
        
        texto += f"☁️ *Almacenamiento:*\n"
        texto += f"🔹 Dropbox: `{services_count.get('dropbox', 0)}` \n"
        texto += f"🔹 Google Drive: `{services_count.get('drive', 0)}` \n"
        texto += f"🔹 OneDrive: `{services_count.get('onedrive', 0)}` \n\n"
        
        texto += f"📸 *Multimedia:* `{count_fotos}`\n"
        texto += f"⚠️ *Pendientes:* `{count_pending}`\n\n"
        
        texto += f"🔌 *Estado de Servicios:*\n"
        texto += f"🗄️ Base de datos: {'`ONLINE ✅`' if db_status else '`OFFLINE ❌`'}\n"
        
        # Dropbox Status check
        from src.init_services import dropbox_svc, onedrive_svc
        try:
            dbx_status = '`ONLINE ✅`' if (dropbox_svc and dropbox_svc.dbx) else '`OFFLINE ❌`'
        except:
            dbx_status = '`OFFLINE ❌`'
        texto += f"☁️ Dropbox API: {dbx_status}\n"

        # Drive Status check
        try:
            from src.scripts.refresh_drive_token import refresh_google_token
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):
                drv_status = '`ONLINE ✅`' if refresh_google_token() else '`OFFLINE ❌`'
        except:
            drv_status = '`OFFLINE ❌`'
        texto += f"☁️ Google Drive API: {drv_status}\n"

        # OneDrive Status check
        od_status = '`ONLINE ✅`' if (onedrive_svc and onedrive_svc.app and onedrive_svc._get_access_token()) else '`OFFLINE ❌`'
        texto += f"☁️ OneDrive API: {od_status}\n"

        # OpenAI Status check
        from src.utils.ai_handler import AIHandler
        try:
            ai_health = await AIHandler.test_connection()
            ai_status_str = ", ".join([f"{k}: {'✅' if v else '❌'}" for k, v in ai_health.items()])
            texto += f"🧠 OpenAI: `{ai_status_str}`\n"
        except:
            texto += f"🧠 OpenAI: `OFFLINE ❌`\n"
        
        db.log_event("INFO", "BOT", "Comando /stats consultado con éxito.")
        await update.message.reply_text(texto, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
         db.log_event("ERROR", "BOT", f"Error en /stats: {e}")
         await update.message.reply_text(f"❌ Error al consultar estadísticas: {e}")

async def ask_document_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        return await update.message.reply_text(
            "🔎 Uso: /preguntar <ID> <pregunta>\nEjemplo: /preguntar 42 ¿Cuál es la fecha de este documento?"
        )

    try:
        file_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ El ID debe ser un número válido.")

    is_limited, wait = is_rate_limited(update.effective_user.id, 'preguntar')
    if is_limited:
        return await update.message.reply_text(f"⏳ Por favor espera {wait}s antes de volver a usar /preguntar.")

    question = " ".join(context.args[1:]).strip()
    if not question:
        return await update.message.reply_text("❌ Escribe una pregunta después del ID.")

    archivo = db.get_file_by_id(file_id)
    if not archivo:
        return await update.message.reply_text(f"❌ No encontré el archivo con ID {file_id}.")

    content_text = archivo.get('content_text') if isinstance(archivo, dict) else None
    if not content_text or not content_text.strip():
        return await update.message.reply_text(
            "❌ Este archivo no tiene texto indexado aún. Usa /indexar o sube el archivo de nuevo para generar el texto y embeddings."
        )

    await update.message.reply_text("🤖 Consultando el documento en la base de datos... esto puede tardar unos segundos.")
    try:
        document_name = archivo.get('name') if isinstance(archivo, dict) else archivo[3]
        answer = await AIHandler.answer_document_question(document_name, question, content_text)
        await update.message.reply_text(answer, parse_mode=ParseMode.MARKDOWN)
    except QuotaExceededError:
        await update.message.reply_text(f"🚨 Cuota de IA agotada. Intenta de nuevo en unos instantes.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error procesando la pregunta: {e}")

async def unknown_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja comandos no reconocidos y muestra la ayuda al usuario."""
    try:
        cmd = update.message.text.split()[0]
    except Exception:
        cmd = update.message.text or "(desconocido)"

    await update.message.reply_text(f"❌ Comando desconocido: {cmd}\nUsa /ayuda para ver la lista de comandos disponibles.")
    await help_command(update, context)
    
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    query = " ".join(context.args)
    if not query:
        return await update.message.reply_text("🔎 Indica el nombre del archivo.")

    raw = db.search_by_name(query)
    if not raw:
        return await update.message.reply_text("❌ No encontré archivos con ese nombre.")

    normalized = []
    seen = set()
    for row in raw:
        fid = row[0]
        name = row[1]
        url = row[2]
        service = row[3]
        summary = row[4]
        tech = row[5]
        tags = row[6] if len(row) > 6 else None
        if name in seen:
            continue
        seen.add(name)
        summary_text = summary or (tech or 'Archivo')
        if tags:
            summary_text = f"{summary_text} | Tags: {tags}"
        normalized.append({
            'id': fid,
            'name': name,
            'url': url,
            'service': service,
            'summary': summary_text
        })

    user_data['name_search_results'] = normalized
    user_data['name_search_page'] = 0
    user_data['name_items_per_page'] = 3 if len(normalized) > 3 else 1

    await send_name_search_page(update, context)

async def inline_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.inline_query.query.strip() if update.inline_query else ""
    if not query_text:
        return await update.inline_query.answer([], cache_time=1, is_personal=True)

    try:
        query_vector = await AIHandler.get_embedding(query_text)
        if not query_vector:
            return await update.inline_query.answer([], cache_time=1, is_personal=True)

        raw_results = db.search_semantic(query_vector, limit=5)
        if not raw_results:
            raw_results = db.search_by_name(query_text)[:5]

        results = []
        for item in raw_results[:8]:
            title = item['name'] if isinstance(item, dict) else item[1]
            description = item.get('summary') if isinstance(item, dict) else (item[4] if len(item) > 4 else '')
            tags = item.get('tags') if isinstance(item, dict) else None
            if tags:
                description = f"{description or ''} | Tags: {tags}"
            url = item['url'] if isinstance(item, dict) else item[2]
            text = f"*{title}*\n{description}\n{url}"
            results.append(
                InlineQueryResultArticle(
                    id=str(item['id']) if isinstance(item, dict) else str(item[0]),
                    title=title,
                    input_message_content=InputTextMessageContent(text, parse_mode=ParseMode.MARKDOWN),
                    description=(description or '').strip()[:95]
                )
            )

        await update.inline_query.answer(results, cache_time=1, is_personal=True)
    except QuotaExceededError:
        await update.inline_query.answer([], cache_time=1, is_personal=True)
    except Exception as e:
        print(f"❌ Error en inline_search_handler: {e}")
        await update.inline_query.answer([], cache_time=1, is_personal=True)

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    text = update.message.text.strip()
    state = user_data.get('state')

    if state == 'waiting_delete_selection':
        if text.lower() in ['cancelar', 'terminar', 'salir']:
            user_data['state'] = None
            user_data.pop('search_results', None)
            return await update.message.reply_text("🚫 Sesión de limpieza finalizada.")

        if text.isdigit():
            idx = int(text) - 1
            results = user_data.get('search_results', [])
            
            if 0 <= idx < len(results):
                selected = results[idx]
                fid = selected[0]
                name = selected[1]
                service = selected[3]
                
                msg = await update.message.reply_text(f"⏳ Eliminando `{name}` de {service.upper()}...")
                
                cloud_deleted = False
                try:
                    if service == 'dropbox':
                        cloud_deleted = await dropbox_svc.delete_file(f"/{name}")
                    elif service == 'drive':
                        cloud_deleted = await drive_svc.delete_file(name)
                except Exception as e:
                    print(f"Error borrado cloud: {e}")

                db.delete_file_by_id(fid)
                
                results.pop(idx)
                user_data['search_results'] = results

                status = "✅ Eliminado por completo." if cloud_deleted else "⚠️ Eliminado de la DB (No se pudo borrar de la nube)."
                await msg.edit_text(f"{status}\nArchivo: `{name}`")

                if not results:
                    user_data['state'] = None
                    return await update.message.reply_text("📭 Ya no quedan archivos en esta búsqueda.")
                
                items_per_page = 10
                if user_data.get('current_page', 0) * items_per_page >= len(results):
                    user_data['current_page'] = max(0, user_data.get('current_page', 0) - 1)

                await update.message.reply_text("🔄 Actualizando lista...")
                return await send_delete_page(update, context, edit=False)
            else:
                return await update.message.reply_text(f"❌ Número fuera de rango. Elige entre 1 y {len(results)}.")
    
    if state == 'renaming' and user_data.get('file_queue'):
        file_info = user_data['file_queue'][-1]
        old_name = file_info['name']
        
        new_name = text
        if "." not in new_name and "." in old_name:
            new_name = f"{new_name}.{old_name.split('.')[-1]}"
            
        user_data['file_queue'][-1]['name'] = new_name
        user_data['state'] = None
        
        await update.message.reply_text(f"✅ Renombrado a: `{new_name}`")
        return await show_cloud_menu(update, context, edit=False)

    if state == 'waiting_folder_name':
        folder_name = text
        parent_id = user_data.get('parent_folder_id')
        
        if any(c in folder_name for c in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']):
            return await update.message.reply_text("❌ El nombre contiene caracteres no permitidos.")

        status_msg = await update.message.reply_text(f"🛠️ Creando carpeta `{folder_name}` en la nube...")
        
        try:
            parent_path = ""
            if parent_id:
                p_folder = db.get_folder_by_id(parent_id)
                parent_path = p_folder['cloud_folder_id'] if p_folder else ""

            cloud_id = await dropbox_svc.create_folder(folder_name, parent_path)
            
            db.create_folder(
                name=folder_name, 
                service='dropbox', 
                cloud_folder_id=cloud_id, 
                parent_id=parent_id
            )
            
            user_data['state'] = None
            user_data.pop('parent_folder_id', None)
            
            await status_msg.edit_text(f"✅ Carpeta `{folder_name}` creada y registrada correctamente.")
            return
            
        except Exception as e:
            print(f"Error creando carpeta: {e}")
            await status_msg.edit_text(f"❌ Error al crear la carpeta: {str(e)}")
            return

    await update.message.reply_text("❌ No reconozco esa entrada. Aquí tienes la ayuda:")
    await help_command(update, context)
    return

# 4. PROCESO DE SUBIDA Y CALLBACKS
async def upload_process(update, context, target_files_info: list, predefined_embedding=None):
    user_data = context.user_data
    selected_clouds = user_data.get('selected_clouds', set())
    if not selected_clouds:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Selecciona al menos una nube.")
        return

    final_report = []
    for local_path, file_name, original_info in target_files_info:
        if not os.path.exists(local_path):
            try:
                tg_f = await context.bot.get_file(original_info['id'])
                await tg_f.download_to_drive(local_path)
            except: continue

        try:
            texto = await AIHandler.extract_text(local_path)
            vector = predefined_embedding
            resumen = None
            ext = file_name.split('.')[-1].lower()
            desc_tec = f"Archivo {ext.upper()}"

            if texto and texto.strip():
                print(f"🧠 IA: Texto extraído de '{file_name}' ({len(texto)} chars). Generando embedding...")
                if not vector:
                    vector = await AIHandler.get_embedding(texto)
                    print(f"🔢 Embedding: {'✅ OK (' + str(len(vector)) + ' dims)' if vector else '❌ FALLÓ (None)'}")
                resumen = await AIHandler.generate_summary(texto)
            else:
                print(f"⚠️ IA: No se extrajo texto de '{file_name}' (ext={ext}). Sin embedding.")
                resumen = f"Documento binario/comprimido ({ext}). No se extrajo texto."
        except QuotaExceededError as qe:
            print(f"⚠️ Cuota de IA agotada enviando desde bot: {qe}")
            texto = None
            vector = None
            resumen = f"IA temporalmente saturada (429). {qe}"
            ext = file_name.split('.')[-1].lower()
            desc_tec = f"Archivo {ext.upper()}"
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"⏳ *IA Saturada:* `{file_name}` se subirá a la nube, pero el análisis inteligente se hará más tarde.",
                parse_mode=ParseMode.MARKDOWN
            )

        category = get_file_category(file_name) or "Otros"
        
        cloud_links = []
        for cloud in selected_clouds:
            try:
                if cloud == 'dropbox':
                    folder_arg = CATEGORY_FOLDER_CACHE['dropbox'].get(category, category)
                    url = await dropbox_svc.upload(local_path, file_name, folder=folder_arg)
                elif cloud == 'drive':
                    folder_id = CATEGORY_FOLDER_CACHE['drive'].get(category)
                    if not folder_id:
                        folder_id = await drive_svc.create_folder(category, parent_id=None)
                        if folder_id:
                            CATEGORY_FOLDER_CACHE['drive'][category] = folder_id
                    url = await drive_svc.upload(local_path, file_name, folder_id=folder_id) if folder_id else None
                elif cloud == 'onedrive':
                    folder_id = CATEGORY_FOLDER_CACHE['onedrive'].get(category)
                    if not folder_id:
                        folder_id = await onedrive_svc.create_folder(category, parent_id=None)
                        if folder_id:
                            CATEGORY_FOLDER_CACHE['onedrive'][category] = folder_id
                        else:
                            logger.warning(f"⚠️ OneDrive: No se pudo obtener folder_id para '{category}', se subirá a raíz.")
                    
                    logger.info(f"🚀 OneDrive: Iniciando subida de '{file_name}'...")
                    url = await onedrive_svc.upload(local_path, file_name, folder_id=folder_id)
                    if not url:
                        logger.error(f"❌ OneDrive: La subida de '{file_name}' no devolvió URL.")
                
                if url:
                    cloud_links.append(f"[✅ {cloud.capitalize()}]({url})")
                    
                    db.register_file(
                        telegram_id=update.effective_user.id,  # ID real del usuario
                        name=file_name,
                        f_type=ext,
                        cloud_url=url,
                        service=cloud,
                        content_text=texto,
                        embedding=vector,
                        summary=resumen,
                        technical_description=desc_tec,
                        folder_id=original_info.get('folder_id', user_data.get('current_folder_id'))
                    )
            except Exception as e:
                print(f"Error subiendo a {cloud}: {e}")
                cloud_links.append(f"❌ {cloud.capitalize()}")

        report_item = f"📄 `{file_name}`\n" + " | ".join(cloud_links)
        if resumen and "No se extrajo texto" not in resumen and "IA temporalmente saturada" not in resumen:
            report_item += f"\n💡 *Resumen:* _{resumen}_"
        final_report.append(report_item)
        
        if os.path.exists(local_path): os.remove(local_path)
        db.log_event("SUCCESS", "BOT", f"Archivo subido y registrado: {file_name}", {"clouds": list(selected_clouds), "links": len(cloud_links)})

    # Enviar reporte final con reintentos ante posibles timeouts de red
    import asyncio as _asyncio
    from telegram.error import TimedOut as _TimedOut, NetworkError as _NetErr
    for attempt in range(3):
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🚀 *Subida finalizada:*\n\n" + "\n".join(final_report),
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            break
        except (_TimedOut, _NetErr) as net_err:
            if attempt < 2:
                await _asyncio.sleep(3 * (attempt + 1))
            else:
                print(f"⚠️ No se pudo enviar el reporte final tras 3 intentos: {net_err}")
        except Exception as e:
            print(f"❌ Error inesperado enviando reporte: {e}")
            break


# Tamaño de página para el comando /indexar
INDEX_PAGE_SIZE = 10

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_data = context.user_data
    
    await query.answer()

    if data.startswith('toggle_'):
        cloud = data.replace('toggle_', '')
        if 'selected_clouds' not in user_data:
            user_data['selected_clouds'] = set()
        
        if cloud in user_data['selected_clouds']:
            user_data['selected_clouds'].remove(cloud)
        else:
            user_data['selected_clouds'].add(cloud)
        
        try:
            await show_cloud_menu(update, context, edit=True)
        except Exception as e:
            if "Message is not modified" not in str(e):
                print(f"Error al refrescar menú: {e}")

    elif data == 'confirm_upload':
        queue = user_data.get('file_queue', [])
        selected_clouds = user_data.get('selected_clouds', set())

        if not selected_clouds:
            await query.message.reply_text("⚠️ Selecciona al menos una nube antes de subir.")
            return

        if not queue:
            await query.message.reply_text("📭 La cola está vacía.")
            return

        await query.edit_message_text(f"🚀 Procesando y subiendo {len(queue)} archivos...")
        
        prepared = []
        for f in queue:
            path = os.path.join("descargas", f['name'])
            if not str(f['id']).startswith("LOC_") and not os.path.exists(path):
                try:
                    tg_file = await context.bot.get_file(f['id'])
                    await tg_file.download_to_drive(path)
                except Exception as e:
                    print(f"Error descargando {f['name']}: {e}")
                    continue
            prepared.append((path, f['name'], f))

        await upload_process(update, context, prepared)
        user_data['file_queue'] = []
        user_data['selected_clouds'] = set()

    elif data.startswith('del_') and not data.startswith('del_page_'):
        db_id = data.replace('del_', '')
        try:
            db.delete_file_by_id(db_id)
            await query.edit_message_text("🗑️ Registro eliminado de la base de datos.")
        except Exception as e:
            await query.message.reply_text(f"❌ Error al eliminar: {e}")
    
    if data == 'del_page_next':
        user_data['current_page'] += 1
        return await send_delete_page(update, context, edit=True)
        
    elif data == 'del_page_prev':
        user_data['current_page'] -= 1
        return await send_delete_page(update, context, edit=True)

    elif data == 'search_page_next':
        user_data['ia_current_page'] = user_data.get('ia_current_page', 0) + 1
        return await send_search_page(update, context, edit=True)
    elif data == 'search_page_prev':
        user_data['ia_current_page'] = max(0, user_data.get('ia_current_page', 0) - 1)
        return await send_search_page(update, context, edit=True)
    elif data == 'search_cancel':
        user_data.pop('search_results_ia', None)
        user_data.pop('ia_current_page', None)
        user_data.pop('ia_items_per_page', None)
        return await query.edit_message_text("🚫 Búsqueda cancelada.")

    elif data == 'name_search_next':
        user_data['name_search_page'] = user_data.get('name_search_page', 0) + 1
        return await send_name_search_page(update, context, edit=True)
    elif data == 'name_search_prev':
        user_data['name_search_page'] = max(0, user_data.get('name_search_page', 0) - 1)
        return await send_name_search_page(update, context, edit=True)
    elif data == 'name_search_cancel':
        user_data.pop('name_search_results', None)
        user_data.pop('name_search_page', None)
        user_data.pop('name_items_per_page', None)
        return await query.edit_message_text("🚫 Búsqueda cancelada.")

    elif data == 'cancel_deletion':
        user_data['state'] = None
        user_data.pop('search_results', None)
        return await query.edit_message_text("🚫 Acción de eliminación cancelada.")

    elif data.startswith('mkdir_'):
        parent_id = data.split('_')[1]
        context.user_data['parent_folder_id'] = None if parent_id == 'root' else parent_id
        context.user_data['state'] = 'waiting_folder_name'
        
        await query.message.reply_text(
            "📁 *Nueva Carpeta*\nEscribe el nombre que deseas ponerle:",
            parse_mode=ParseMode.MARKDOWN
        )

    # =========================================================
    # CALLBACKS DE INDEXACIÓN (/indexar)
    # =========================================================

    elif data == 'embed_close':
        await query.edit_message_text("✅ Panel de indexación cerrado.")

    elif data == 'embed_page_next':
        context.user_data['index_page'] = context.user_data.get('index_page', 0) + 1
        return await send_indexar_page(update, context, edit=True)

    elif data == 'embed_page_prev':
        context.user_data['index_page'] = max(0, context.user_data.get('index_page', 0) - 1)
        return await send_indexar_page(update, context, edit=True)

    elif data.startswith('embed_file_'):
        file_id_str = data.replace('embed_file_', '')
        try:
            file_id = int(file_id_str)
        except ValueError:
            return await query.answer("❌ ID inválido", show_alert=True)

        # Obtener nombre del archivo para el mensaje de progreso
        archivo = db.get_file_by_id(file_id)
        nombre = archivo[1] if isinstance(archivo, tuple) else (archivo.get('name', '?') if archivo else '?')

        await query.answer()
        await query.edit_message_text(
            f"⏳ *Indexando* `{nombre}`...\nEsto puede tardar unos segundos.",
            parse_mode=ParseMode.MARKDOWN
        )

        try:
            ok = await _process_single_embed(file_id, update, context)
            if ok:
                db.log_event("INFO", "BOT", f"Embedding manual OK: {nombre}")
                await query.edit_message_text(
                    rf"✅ *¡Indexado!* `{nombre}` ya es buscable con IA.\n\n"
                    "Usa /indexar para continuar con los demás o /buscar_ia para buscar.",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await query.edit_message_text(
                    f"⚠️ `{nombre}` no pudo indexarse\n(sin texto extraible o descarga fallida).\n\n"
                    f"Usa /indexar para ver el resto.",
                    parse_mode=ParseMode.MARKDOWN
                )
        except QuotaExceededError as qe:
            retry_msg = f"\nReintentar en {qe.retry_after}s" if qe.retry_after else ""
            await query.edit_message_text(
                f"🚫 *Cuota de OpenAI agotada.*{retry_msg}\n\n"
                f"Espera un momento y vuelve a intentarlo con /indexar.",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"❌ Error indexando ID={file_id}: {e}")
            await query.edit_message_text(
                f"❌ Error inesperado indexando `{nombre}`.\nDetalles: {str(e)[:100]}",
                parse_mode=ParseMode.MARKDOWN
            )

    elif data == 'embed_all':
        page = context.user_data.get('index_page', 0)
        offset = page * INDEX_PAGE_SIZE
        archivos = db.get_files_without_embedding(limit=INDEX_PAGE_SIZE, offset=offset)

        if not archivos:
            return await query.edit_message_text("✅ No hay archivos pendientes en esta página.")

        await query.answer()
        total = len(archivos)
        ok_count = 0
        fail_count = 0
        quota_hit = False

        for idx, archivo in enumerate(archivos, 1):
            fid = archivo['id'] if isinstance(archivo, dict) else archivo[0]
            fname = archivo['name'] if isinstance(archivo, dict) else archivo[1]

            try:
                await query.edit_message_text(
                    f"🚀 *Indexando en lote...*\n"
                    f"━" * 18 + "\n"
                    f"Archivo {idx}/{total}: `{fname}`\n"
                    f"✅ Éxitos: {ok_count} │ ❌ Fallos: {fail_count}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass  # Telegram puede rechazar ediciones muy frecuentes

            try:
                ok = await _process_single_embed(fid, update, context)
                if ok:
                    ok_count += 1
                    db.log_event("INFO", "BOT", f"Embedding batch OK: {fname}")
                else:
                    fail_count += 1
            except QuotaExceededError as qe:
                quota_hit = True
                retry_msg = f"\u23f0 Reintentar en {qe.retry_after}s" if qe.retry_after else ""
                await query.edit_message_text(
                    f"🚫 *Cuota de OpenAI agotada.*\n{retry_msg}\n\n"
                    f"✅ Indexados: {ok_count}/{total}\n"
                    f"Usa /indexar cuando puedas para continuar.",
                    parse_mode=ParseMode.MARKDOWN
                )
                db.log_event("WARNING", "BOT", f"Quota hit durante batch. OK={ok_count}")
                break
            except Exception as e:
                fail_count += 1
                logger.error(f"❌ Error en batch embed (id={fid}): {e}")

        if not quota_hit:
            pendientes = db.count_files_without_embedding()
            await query.edit_message_text(
                f"✅ *Lote completado*\n"
                f"━" * 18 + "\n"
                f"✅ Indexados: {ok_count}\n"
                f"❌ Sin texto: {fail_count}\n"
                f"⚠️ Pendientes totales: {pendientes}\n\n"
                + ("🎉 ¡Todo indexado!" if pendientes == 0 else "Usa /indexar para continuar."),
                parse_mode=ParseMode.MARKDOWN
            )
            db.log_event("INFO", "BOT", f"Batch indexar: OK={ok_count}, fail={fail_count}")

# 5. BÚSQUEDA IA Y ELIMINAR


async def indexar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler del comando /indexar: muestra archivos sin embedding para indexar."""
    context.user_data['index_page'] = 0
    await send_indexar_page(update, context, edit=False)

async def send_indexar_page(update: ContextTypes.DEFAULT_TYPE, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    """Renderiza la lista paginada de archivos sin embedding."""
    page = context.user_data.get('index_page', 0)
    offset = page * INDEX_PAGE_SIZE

    total = db.count_files_without_embedding()
    archivos = db.get_files_without_embedding(limit=INDEX_PAGE_SIZE, offset=offset)
    total_pages = max(1, (total + INDEX_PAGE_SIZE - 1) // INDEX_PAGE_SIZE)

    if total == 0:
        text = (
            "🧠 *¡Indexación completa!*\n\n"
            "✅ Todos los archivos ya tienen su embedding generado.\n"
            "La búsqueda inteligente `/buscar_ia` funcionará al 100%."
        )
        if edit and update.callback_query:
            return await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        return await update.effective_chat.send_message(text, parse_mode=ParseMode.MARKDOWN)

    # Construir el texto principal
    text = "🧠 *Indexación de Archivos*\n"
    text += f"━" * 20 + "\n"
    text += f"⚠️ *{total}* archivo(s) sin indexar — Pág. {page + 1}/{total_pages}\n"
    text += f"━" * 20 + "\n\n"

    for i, archivo in enumerate(archivos, start=offset + 1):
        num_emoji = "".join(f"{d}️⃣" for d in str(i))
        tiene_texto = bool(archivo.get('content_text') and str(archivo['content_text']).strip())
        estado = "⚡ (rápido)" if tiene_texto else "📥 (requiere descarga)"
        text += f"{num_emoji} `{archivo['name']}`\n"
        text += f"   ☁️ {archivo['service'].upper()} • {estado}\n\n"

    # Teclado: botones individuales + batch + navegación
    keyboard = []

    # Fila de botones individuales (⋯ Indexar por cada archivo)
    for archivo in archivos:
        keyboard.append([
            InlineKeyboardButton(
                f"⚡ {archivo['name'][:30]}",
                callback_data=f"embed_file_{archivo['id']}"
            )
        ])

    # Botones de batch + navegación
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Anterior", callback_data="embed_page_prev"))
    if (offset + INDEX_PAGE_SIZE) < total:
        nav_buttons.append(InlineKeyboardButton("Siguiente ➡️", callback_data="embed_page_next"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton(
            f"🚀 Indexar TODOS estos {len(archivos)}",
            callback_data="embed_all"
        )
    ])
    keyboard.append([InlineKeyboardButton("❌ Cerrar", callback_data="embed_close")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
        )
    else:
        await update.effective_chat.send_message(
            text, reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
        )

async def _process_single_embed(file_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Genera y guarda el embedding de un archivo identificado por su ID en la DB.
    
    Estrategia:
      1. Si el archivo tiene content_text en la DB → usa ese texto directamente (rápido, sin descarga).
      2. Si no tiene texto → descarga el archivo desde cloud_url y extrae texto con AIHandler.
    
    Returns:
        True si se guardó correctamente, False en caso de error.
    Raises:
        QuotaExceededError: Si la API de Gemini agota su cuota.
    """
    import aiohttp
    import tempfile

    # Obtener info del archivo desde la DB
    archivo = db.get_file_by_id(file_id)
    if not archivo:
        logger.warning(f"⚠️ _process_single_embed: archivo ID={file_id} no encontrado en DB")
        return False

    name = archivo[1] if isinstance(archivo, tuple) else archivo.get('name', '')
    service = archivo[2] if isinstance(archivo, tuple) else archivo.get('service', '')
    cloud_url = archivo[3] if isinstance(archivo, tuple) else archivo.get('cloud_url', '')

    # Intentar obtener content_text desde la DB (evita re-descarga)
    with db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT content_text FROM files WHERE id = %s", (file_id,))
            row = cur.fetchone()
            content_text = row[0] if row else None

    texto = content_text if content_text and content_text.strip() else None

    if not texto:
        # Necesitamos descargar el archivo y extraer texto
        if not cloud_url:
            logger.warning(f"⚠️ Sin cloud_url para ID={file_id} ({name})")
            return False
        
        logger.info(f"📥 Descargando '{name}' desde {service} para indexar...")
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else 'bin'
        
        tmp_path = None
        try:
            # Descargar desde la URL de la nube
            async with aiohttp.ClientSession() as session:
                async with session.get(cloud_url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        logger.error(f"❌ Descarga fallida para '{name}': HTTP {resp.status}")
                        return False
                    
                    with tempfile.NamedTemporaryFile(
                        suffix=f'.{ext}', delete=False,
                        dir='descargas', prefix='idx_'
                    ) as tmp:
                        tmp_path = tmp.name
                        async for chunk in resp.content.iter_chunked(8192):
                            tmp.write(chunk)
            
            texto = await AIHandler.extract_text(tmp_path)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    if not texto or not texto.strip():
        logger.warning(f"⚠️ Sin texto extraible de '{name}'. Skipping embedding.")
        return False

    # Generar embedding y resumen
    vector = await AIHandler.get_embedding(texto)  # puede lanzar QuotaExceededError
    if not vector:
        logger.error(f"❌ Embedding nulo para '{name}'")
        return False

    resumen = await AIHandler.generate_summary(texto)

    # Guardar en DB
    return db.update_file_embedding(
        file_id=file_id,
        embedding=vector,
        summary=resumen,
        content_text=texto
    )

async def search_ia_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Búsqueda inteligente tipo Google usando motor híbrido.
    Combina: semántica + full-text + metadata con reranking.
    """
    user_data = context.user_data
    if not context.args:
        return await update.message.reply_text("🔎 *Uso:* `/buscar_ia concepto`", parse_mode=ParseMode.MARKDOWN)
    
    is_limited, wait = is_rate_limited(update.effective_user.id, 'buscar_ia')
    if is_limited:
        return await update.message.reply_text(f"⏳ Por favor espera {wait}s antes de volver a usar /buscar_ia.")

    query_text = " ".join(context.args)
    msg = await update.message.reply_text("🔍 Buscando en mi base de datos con IA...")
    
    try:
        from src.init_services import search_engine
        
        # 🚀 BÚSQUEDA HÍBRIDA: Semántica + Full-Text + Metadata
        results = await search_engine.search(query_text, limit=20)
        
        if not results:
            return await msg.edit_text(f"😔 No encontré archivos relevantes para: `{query_text}`.\n\n💡 Intenta con otros términos o revisa los tags de tus archivos.")
        
        # Preparar resultados para visualización
        normalized = []
        seen_names = set()
        
        for res in results:
            name = res.get('name')
            if name in seen_names:
                continue
            seen_names.add(name)
            
            # Construir descripción con score
            score = res.get('combined_score', res.get('score', 0))
            score_pct = int(score * 100)
            score_emoji = "🔥" if score_pct >= 80 else "⭐" if score_pct >= 60 else "✓"
            
            summary_text = res.get('summary') or 'Archivo sin resumen'
            if res.get('tags'):
                summary_text += f" | Tags: {res.get('tags')}"
            
            normalized.append({
                'id': res.get('id'),
                'name': name,
                'url': res.get('url'),
                'service': res.get('service'),
                'summary': summary_text,
                'tags': res.get('tags'),
                'score': score,
                'score_emoji': score_emoji,
                'score_pct': score_pct
            })
        
        user_data['search_results_ia'] = normalized
        user_data['ia_current_page'] = 0
        
        n = len(normalized)
        if n <= 5:
            user_data['ia_items_per_page'] = 1
        elif n <= 15:
            user_data['ia_items_per_page'] = 2
        else:
            user_data['ia_items_per_page'] = 3

        await send_search_page(update, context)

    except Exception as e:
        print(f"❌ ERROR EN BUSQUEDA IA: {e}")
        import traceback
        traceback.print_exc()
        await msg.edit_text(f"⚠️ Error en la búsqueda: {str(e)[:100]}")

async def cancelar_handler(update, context):
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 ¡Entendido, {user_name}! He detenido cualquier proceso activo.\n"
        "Estoy listo para tu siguiente búsqueda o archivo."
    )

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    if not query:
        return await update.message.reply_text("🗑️ Indica el nombre para eliminar.")
    
    results = db.search_by_name(query)
    if not results:
        return await update.message.reply_text("❌ No hay coincidencias para eliminar.")
    
    context.user_data['search_results'] = results
    context.user_data['current_page'] = 0
    context.user_data['state'] = 'waiting_delete_selection'
    
    await send_delete_page(update, context)

async def send_delete_page(update, context, edit=False):
    user_data = context.user_data
    results = user_data.get('search_results', [])
    page = user_data.get('current_page', 0)
    items_per_page = 10
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    current_items = results[start_idx:end_idx]
    total_pages = (len(results) + items_per_page - 1) // items_per_page
    
    if not current_items:
        return await update.effective_chat.send_message("❌ No hay más archivos para mostrar.")

    text = f"🗑️ *Panel de Eliminación*\n"
    text += f"📖 Página {page+1} de {total_pages}\n"
    text += "⎯" * 15 + "\n\n"
    
    for i, item in enumerate(current_items, start_idx + 1):
        num_emoji = "".join(f"{d}️⃣" for d in str(i))
        text += f"{num_emoji} [{item[1]}]({item[2]}) | _{item[3].capitalize()}_\n\n"
    
    text += "⎯" * 15 + "\n"
    text += "⚠️ *ACCIÓN REQUERIDA:*\n"
    text += "Escribe el **número** del archivo que deseas borrar permanentemente o usa los botones para navegar."
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Anterior", callback_data="del_page_prev"))
    if end_idx < len(results):
        nav_buttons.append(InlineKeyboardButton("Siguiente ➡️", callback_data="del_page_next"))

    keyboard = [nav_buttons] if nav_buttons else []
    keyboard.append([InlineKeyboardButton("❌ CANCELAR Y SALIR", callback_data="cancel_deletion")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, 
            reply_markup=reply_markup, 
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True 
        )
    else:
        await update.effective_chat.send_message(
            text, 
            reply_markup=reply_markup, 
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        
async def send_search_page(update, context, edit=False):
    """Muestra resultados paginados con línea dinámica adaptable y score de relevancia."""
    user_data = context.user_data
    results = user_data.get('search_results_ia', [])
    page = user_data.get('ia_current_page', 0)
    items_per_page = user_data.get('ia_items_per_page', 1)

    if not results:
        return await update.effective_chat.send_message("❌ No hay resultados para mostrar.")

    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    current_items = results[start_idx:end_idx]
    total_pages = (len(results) + items_per_page - 1) // items_per_page

    text = f"🎯 *Resultados de búsqueda IA* (Página {page+1}/{total_pages})\n\n"
    
    BASE_WIDTH = 30 
    
    for idx, item in enumerate(current_items, start_idx + 1):
        # Mostrar relevancia con emoji y porcentaje
        score_pct = item.get('score_pct', int((item.get('score', 0) * 100)))
        score_emoji = item.get('score_emoji', '✓')
        score_str = f" {score_emoji} *{score_pct}%*"
        
        # Emoji para número
        num_emoji = "".join(f"{d}️⃣" for d in str(idx))
        line_fill_len = BASE_WIDTH - len(num_emoji) - 1
        separator = "=" * line_fill_len
        
        text += f"{num_emoji} {separator}\n"
        text += f"📄 *{item['name']}*{score_str}\n"
        text += f"📝 _{item.get('summary','')[:150]}_\n"
        if item.get('tags'):
            text += f"🏷️ {item.get('tags')}\n"
        if item.get('url'):
            text += f"🔗 [Ver en la nube]({item['url']})\n"
        text += "\n"

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Anterior", callback_data="search_page_prev"))
    if end_idx < len(results):
        nav_buttons.append(InlineKeyboardButton("Siguiente ➡️", callback_data="search_page_next"))

    keyboard = [nav_buttons] if nav_buttons else []
    keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="search_cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    else:
        await update.effective_chat.send_message(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


async def send_name_search_page(update, context, edit=False):
    """Muestra resultados paginados para el comando /buscar (por nombre)."""
    user_data = context.user_data
    results = user_data.get('name_search_results', [])
    page = user_data.get('name_search_page', 0)
    items_per_page = user_data.get('name_items_per_page', 1)

    if not results:
        return await update.effective_chat.send_message("❌ No hay resultados para mostrar.")

    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    current_items = results[start_idx:end_idx]
    total_pages = (len(results) + items_per_page - 1) // items_per_page

    text = f"🔎 *Resultados por nombre* (Página {page+1}/{total_pages})\n\n"
    for idx, item in enumerate(current_items, start_idx + 1):
        num_emoji = "".join(f"{d}️⃣" for d in str(idx))
        text += f"{num_emoji} \n"
        text += f"📄 *{item['name']}* — _{item.get('service','').upper()}_\n"
        text += f"📝 {item.get('summary','')}\n"
        if item.get('url'):
            text += f"🔗 [Abrir en la nube]({item['url']})\n"
        text += "\n"

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Anterior", callback_data="name_search_prev"))
    if end_idx < len(results):
        nav_buttons.append(InlineKeyboardButton("Siguiente ➡️", callback_data="name_search_next"))

    keyboard = [nav_buttons] if nav_buttons else []
    keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="name_search_cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    else:
        await update.effective_chat.send_message(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def execute_full_deletion(fid, name, service, update):
    try:
        success = False
        if service == 'dropbox':
            success = await dropbox_svc.delete_file(f"/{name}") 
        elif service == 'drive':
            success = await drive_svc.delete_file(name) 

        db.delete_file_by_id(fid)
        
        status = "y de la nube ✅" if success else "(solo de la DB ⚠️)"
        await update.message.reply_text(f"🗑️ Archivo `{name}` eliminado correctamente {status}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error durante el borrado: {e}")
        
# 6. CONFIGURACIÓN E INICIO

async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "🏠 Menú Principal"),
        BotCommand("stats", "📊 Estadísticas"),
        BotCommand("buscar_ia", "🤖 Búsqueda Inteligente"),
        BotCommand("preguntar", "❓ Consulta un documento por ID"),
        BotCommand("indexar", "⚡ Indexar archivos pendientes"),
        BotCommand("listar", "📋 Recientes"),
        BotCommand("buscar", "🔎 Buscar por nombre"),
        BotCommand("eliminar", "🗑️ Borrar archivos"),
        BotCommand("ayuda", "🆘 Ayuda"),
        BotCommand("help", "🆘 Help")
    ])
    await ensure_category_folders()
    db.log_event("INFO", "SISTEMA", "Bot iniciado correctamente y menús registrados.")

async def post_stop(application):
    """Acciones a realizar al detener el bot"""
    print("\n🛑 Deteniendo CloudGram PRO...")
    await AIHandler.close_async_client()
    db.log_event("INFO", "SISTEMA", "Bot detenido correctamente.")

async def error_handler(update, context):
    if isinstance(context.error, NetworkError):
        print(f"⚠️ Error de red temporal en Telegram: {context.error}")
    else:
        print(f"❌ Error crítico: {context.error}")

if __name__ == '__main__':
    print_server_welcome()
    if not os.path.exists("descargas"):
        os.makedirs("descargas")
    
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).post_init(post_init).post_stop(post_stop).build()
    
    app.add_handler(TypeHandler(Update, auth_middleware), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("listar", list_files_command))
    app.add_handler(CommandHandler("buscar", search_command))
    app.add_handler(CommandHandler("buscar_ia", search_ia_command))
    app.add_handler(CommandHandler("preguntar", ask_document_command))
    app.add_handler(CommandHandler("indexar", indexar_command))
    app.add_handler(CommandHandler("eliminar", delete_command))
    app.add_handler(CommandHandler("ayuda", help_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(voice_options_callback, pattern="^voice_"))
    app.add_handler(InlineQueryHandler(inline_search_handler))
    app.add_handler(CommandHandler(["cancelar", "salir", "stop"], cancelar_handler))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command_handler))
        
    app.add_handler(MessageHandler(
        (filters.Document.ALL | filters.PHOTO | filters.VIDEO | 
         filters.VIDEO_NOTE | filters.AUDIO | filters.VOICE | filters.LOCATION), 
        handle_any_file
    ))
    
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_input))
    
    app.add_handler(CallbackQueryHandler(button_callback))
    
    app.add_error_handler(error_handler)
    
    print("🚀 CloudGram PRO v1.0 ONLINE")
    app.run_polling()