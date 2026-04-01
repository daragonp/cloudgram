# main.py
import os
import json
import warnings
import platform
import sys
import shutil  # NUEVO: Para detectar ancho de pantalla
import numpy as np
from datetime import datetime
from dotenv import load_dotenv
from src.handlers.message_handlers import voice_options_callback


load_dotenv()
warnings.filterwarnings("ignore", category=FutureWarning)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    filters,
    ContextTypes,
    TypeHandler
)
from telegram.constants import ParseMode
from telegram.error import NetworkError

# 2. IMPORTACIÓN DE SERVICIOS INICIALIZADOS
from src.init_services import db, dropbox_svc, drive_svc, openai_client 

# 3. IMPORTACIÓN DE HANDLERS
from src.handlers.message_handlers import start, handle_any_file, show_cloud_menu, get_file_category, FILE_CATEGORIES
from src.handlers.auth_handler import auth_middleware
from src.utils.ai_handler import AIHandler

# ================================================================================================
# CACHE GLOBAL DE CARPETAS
# ================================================================================================
CATEGORY_FOLDER_CACHE = {
    'dropbox': {},   # category_name -> path (ej. '/Documentos')
    'drive': {}      # category_name -> folder_id
}

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
    print(f"   [BDD] Caché cargada: {len(CATEGORY_FOLDER_CACHE['dropbox'])} Dropbox, {len(CATEGORY_FOLDER_CACHE['drive'])} Drive")
    
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
        text += f"{i}. [{f[1]}]({f[2]}) ({f[3].upper()})\n"
    
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
        "• /eliminar <texto> - Eliminar archivos por nombre\n"
        "• /cancelar - Cancelar acciones en curso\n\n"
        "También puedes enviar archivos (documentos, fotos, audio, voz).\n"
        "Al enviar una nota de voz puedes elegir transcribir o subirla y seleccionar la/s nubes donde guardarla."
    )
    await update.message.reply_text(help_text)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /stats para mostrar métricas directamente en Telegram."""
    try:
        with db._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM files")
                total_db = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM files WHERE embedding IS NOT NULL AND embedding NOT IN ('', '[]', 'error_limit')")
                count_ia = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM files WHERE type IN ('🖼️ Foto', '🎥 Video', 'jpg', 'png', 'jpeg') OR name ILIKE '%.jpg' OR name ILIKE '%.png' OR name ILIKE '%.jpeg'")
                count_fotos = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM files WHERE embedding IS NULL OR embedding IN ('', '[]', 'error_limit')")
                count_pending = cur.fetchone()[0]
                
        db_status = db.check_connection()
        
        texto = f"📊 *Estadísticas de CloudGram*\n\n"
        texto += f"📁 *Archivos Totales:* {total_db}\n"
        texto += f"🧠 *Indexados IA:* {count_ia}\n"
        texto += f"📸 *Multimedia:* {count_fotos}\n"
        texto += f"⚠️ *Pendientes/Errores:* {count_pending}\n\n"
        texto += f"🔌 *Base de datos:* {'ONLINE ✅' if db_status else 'OFFLINE ❌'}\n"
        
        db.log_event("INFO", "BOT", "Comando /stats consultado desde Telegram.")
        await update.message.reply_text(texto, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
         db.log_event("ERROR", "BOT", f"Error en /stats: {e}")
         await update.message.reply_text(f"❌ Error al consultar estadísticas: {e}")

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
    for fid, name, url, service, summary, tech in raw:
        if name in seen:
            continue
        seen.add(name)
        normalized.append({
            'id': fid,
            'name': name,
            'url': url,
            'service': service,
            'summary': summary or (tech or 'Archivo')
        })

    user_data['name_search_results'] = normalized
    user_data['name_search_page'] = 0
    user_data['name_items_per_page'] = 3 if len(normalized) > 3 else 1

    await send_name_search_page(update, context)

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

        category = get_file_category(file_name) or "Otros"
        
        cloud_links = []
        for cloud in selected_clouds:
            try:
                if cloud == 'dropbox':
                    folder_arg = CATEGORY_FOLDER_CACHE['dropbox'].get(category, category)
                    url = await dropbox_svc.upload(local_path, file_name, folder=folder_arg)
                else:  # drive
                    folder_id = CATEGORY_FOLDER_CACHE['drive'].get(category)
                    if not folder_id:
                        folder_id = await drive_svc.create_folder(category, parent_id=None)
                        if folder_id:
                            CATEGORY_FOLDER_CACHE['drive'][category] = folder_id
                    url = await drive_svc.upload(local_path, file_name, folder_id=folder_id) if folder_id else None
                
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

        final_report.append(f"📄 `{file_name}`\n" + " | ".join(cloud_links))
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
    
# 5. BÚSQUEDA IA Y ELIMINAR

async def search_ia_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    if not context.args:
        return await update.message.reply_text("🔎 *Uso:* `/buscar_ia concepto`", parse_mode=ParseMode.MARKDOWN)
    
    query_text = " ".join(context.args)
    msg = await update.message.reply_text("🤖 Consultando mi base neuronal con Gemini...")
    
    try:
        # 1. Extraer intención de búsqueda (Texto semántico vs Tipo de archivo)
        intent = await AIHandler.analyze_search_intent(query_text)
        semantic_query = intent.get("semantic_query", query_text)
        file_types = intent.get("file_types", [])
        
        # Si el LLM extrajo tipos, se lo decimos al usuario para darle feedback
        if file_types:
            await msg.edit_text(f"🤖 Entendido. Buscando `{semantic_query}` en archivos tipo: {', '.join(file_types).upper()}...")

        query_vector = await AIHandler.get_embedding(semantic_query)
        
        if not query_vector:
            return await msg.edit_text("❌ Error generando embedding. Verifica tu API key de Gemini.")

        raw_results = db.search_semantic(query_vector, limit=20, file_types=file_types)

        normalized = []
        seen_names = set()
        
        # Umbral MUCHO MÁS ESTRICTO (0.60) para evitar basura en los resultados.
        semantic = [r for r in raw_results if r.get('similarity', 0) >= 0.60]
        
        if not semantic:
            await msg.edit_text("🔄 No hay coincidencias perfectas por concepto. Buscando por nombre de forma tradicional...")
            tradicional = db.search_by_name(query_text)
            if not tradicional:
                return await msg.edit_text(f"😔 No encontré archivos relevantes para: `{query_text}`.")

            for fid, name, url, service, summary, tech in tradicional[:20]:
                if name in seen_names:
                    continue
                seen_names.add(name)
                normalized.append({
                    'id': fid,
                    'name': name,
                    'url': url,
                    'service': service,
                    'summary': summary or (tech or 'Archivo'),
                    'score': None
                })
        else:
            for res in semantic:
                name = res.get('name')
                if name in seen_names:
                    continue
                seen_names.add(name)
                normalized.append({
                    'id': res.get('id'),
                    'name': name,
                    'url': res.get('url'),
                    'service': res.get('service'),
                    'summary': res.get('summary') or 'Sin resumen disponible.',
                    'score': res.get('similarity', None)
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
        await msg.edit_text(f"⚠️ Hubo un error procesando la búsqueda: {str(e)}")

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
    
    if not current_items:
        return await update.effective_chat.send_message("❌ No hay más archivos para mostrar.")

    text = f"🗑️ *Panel de Eliminación* (Pág. {page + 1})\n"
    text += "Haz clic en el nombre para previsualizar.\n"
    text += "Escribe el **número** para borrar permanentemente:\n\n"
    
    for i, item in enumerate(current_items, start_idx + 1):
        text += f"{i}. [{item[1]}]({item[2]}) | _{item[3].capitalize()}_\n"
    
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
    """Muestra resultados paginados con línea dinámica adaptable."""
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

    text = f"🎯 *Resultados de búsqueda* (Página {page+1}/{total_pages})\n\n"
    
    # ANCHO DE LÍNEA DINÁMICO PARA TELEGRAM
    # Telegram no notifica el ancho, usamos un estándar estético (ej. 30 caracteres).
    # Se ajustará según el largo del emoji numérico.
    BASE_WIDTH = 30 
    
    for idx, item in enumerate(current_items, start_idx + 1):
        score = f" ({int(item['score']*100)}%)" if item.get('score') else ""
        
        # Lógica para convertir número a emoji y calcular ancho de línea
        num_emoji = f"{idx}️⃣"
        
        # Calcular cuántos '=' necesitamos para llenar la línea
        # len(num_emoji) suele ser 3 para <10 (ej 1️⃣) y 4 para >=10 (ej 10️⃣)
        # Restamos también el espacio después del emoji (+1)
        line_fill_len = BASE_WIDTH - len(num_emoji) - 1
        separator = "=" * line_fill_len
        
        text += f"{num_emoji} {separator}\n"
        text += f"📄 *{item['name']}*{score}\n"
        text += f"📝 _{item.get('summary','')}_\n"
        if item.get('url'):
            text += f"🔗 *Enlace:* [Ver en la nube]({item['url']})\n"
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
        num_emoji = f"{idx}️⃣"
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
        BotCommand("listar", "📋 Recientes"),
        BotCommand("buscar", "🔎 Buscar por nombre"),
        BotCommand("eliminar", "🗑️ Borrar archivos"),
        BotCommand("ayuda", "🆘 Ayuda"),
        BotCommand("help", "🆘 Help")
    ])
    await ensure_category_folders()
    db.log_event("INFO", "SISTEMA", "Bot iniciado correctamente y menús registrados.")

async def error_handler(update, context):
    if isinstance(context.error, NetworkError):
        print(f"⚠️ Error de red temporal en Telegram: {context.error}")
    else:
        print(f"❌ Error crítico: {context.error}")

if __name__ == '__main__':
    print_server_welcome()
    if not os.path.exists("descargas"):
        os.makedirs("descargas")
    
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).post_init(post_init).build()
    
    app.add_handler(TypeHandler(Update, auth_middleware), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("listar", list_files_command))
    app.add_handler(CommandHandler("buscar", search_command))
    app.add_handler(CommandHandler("buscar_ia", search_ia_command))
    app.add_handler(CommandHandler("eliminar", delete_command))
    app.add_handler(CommandHandler("ayuda", help_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(voice_options_callback, pattern="^voice_"))
    app.add_handler(CommandHandler(["cancelar", "salir", "stop"], cancelar_handler))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command_handler))
        
    app.add_handler(MessageHandler(
        (filters.Document.ALL | filters.PHOTO | filters.VIDEO | 
         filters.VIDEO_NOTE | filters.AUDIO | filters.VOICE | filters.LOCATION), 
        handle_any_file
    ))
    
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_input))
    
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("🚀 CloudGram PRO v1.0 ONLINE")
    app.run_polling()