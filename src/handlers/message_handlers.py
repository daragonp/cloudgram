import time
import os
import asyncio
import ssl
import certifi
import random
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CallbackQueryHandler
from geopy.geocoders import Nominatim
import geopy.geocoders

from src.init_services import db, dropbox_svc, drive_svc, openai_client
from src.utils.ai_handler import AIHandler, QuotaExceededError

# Configuración SSL para mi MacBook
ctx = ssl.create_default_context(cafile=certifi.where())
geopy.geocoders.options.default_ssl_context = ctx
geolocator = Nominatim(user_agent="cloudgram_bot")

# ============================================================================
# MAPEO DE CATEGORÍAS: extensión -> carpeta de destino automática
# ============================================================================
FILE_CATEGORIES = {
    'Documentos': {
        'icon': '📄',
        'extensions': ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'odt', 'pptx', 'ppt', 'txt', 'rtf', 'ods', 'odp', 'csv', 'md', 'epub', 'pages']
    },
    'Imágenes': {
        'icon': '🖼️',
        'extensions': ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp', 'tiff', 'ico', 'heic', 'raw', 'cr2', 'nef']
    },
    'Vídeos': {
        'icon': '🎥',
        'extensions': ['mp4', 'avi', 'mov', 'mkv', 'wmv', 'flv', 'webm', 'mpg', 'mpeg', 'm4v']
    },
    'Audio': {
        'icon': '🎵',
        'extensions': ['mp3', 'wav', 'aac', 'flac', 'ogg', 'm4a', 'opus', 'aiff', 'wma', 'm3u']
    },
    'Comprimidos': {
        'icon': '📦',
        'extensions': ['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'iso', 'dmg', 'tgz']
    },
    'Programas': {
        'icon': '⚙️',
        'extensions': ['exe', 'msi', 'app', 'deb', 'rpm', 'apk', 'pkg', 'jar']
    },
    'Código': {
        'icon': '💻',
        'extensions': ['py', 'js', 'ts', 'html', 'css', 'json', 'c', 'cpp', 'java', 'go', 'rs', 'sh', 'php', 'sql', 'yaml', 'yml', 'xml']
    }
}

def get_file_category(file_name: str) -> str:
    """
    Determina la categoría de carpeta para un archivo según su extensión.
    Retorna el nombre de la carpeta ('Documentos', 'Imágenes', etc.)
    o None si no encaja en ninguna categoría.
    """
    if not file_name:
        return None
    ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''
    for category, data in FILE_CATEGORIES.items():
        if ext in data['extensions']:
            return category
    return None

if not os.path.exists("descargas"):
    os.makedirs("descargas")
    
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✨ *CloudGram Pro Activo*", parse_mode=ParseMode.MARKDOWN)

async def buscar_ia_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🔎 *Uso:* `/buscar_ia ¿dónde está el contrato?`", parse_mode=ParseMode.MARKDOWN)
        return

    query = " ".join(context.args)
    espera_msg = await update.message.reply_text("🤖 Consultando a mi memoria neuronal...")

    try:
        response = openai_client.embeddings.create(input=[query], model="text-embedding-3-small")
        query_vector = response.data[0].embedding
        resultados = db.search_semantic(query_vector, limit=3)

        if resultados and resultados[0][3] > 0.3:
            texto_respuesta = "🎯 *He encontrado estos archivos:*\n\n"
            for res in resultados:
                porcentaje = int(res[3] * 100)
                texto_respuesta += f"📄 *{res[1]}* ({porcentaje}% coincidencia)\n🔗 [Ver archivo]({res[2]})\n\n"
            await espera_msg.edit_text(texto_respuesta, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        else:
            await espera_msg.edit_text("😔 No encontré nada con ese contexto.")
    except Exception as e:
        await espera_msg.edit_text("❌ Error al procesar la búsqueda con IA.")

async def handle_any_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from main import db, dropbox_svc, drive_svc
    from src.utils.ai_handler import AIHandler
    from datetime import datetime
    import os
    import time
    import asyncio
    import random

    user_data = context.user_data
    file_id, file_name, file_type = None, None, "documento"
    is_location, is_voice = False, False
    texto_extraido = "" 
    
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    ts_unix = int(time.time())
    rand_suffix = random.randint(1000, 9999)

    # 1. DETECCIÓN DE TIPO DE ARCHIVO
    if update.message.document:
        file_id = update.message.document.file_id
        file_name = update.message.document.file_name
        file_type = "📦 Documento"
    elif update.message.voice:
        is_voice = True
        file_id = update.message.voice.file_id
        file_name = f"nota_voz_{ts_str}.ogg"
        file_type = "🎙️ Nota de voz"
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_name = f"foto_{ts_str}.jpg"
        file_type = "🖼️ Foto"
    elif update.message.audio:
        file_id = update.message.audio.file_id
        file_name = update.message.audio.file_name or f"audio_{ts_str}.mp3"
        file_type = "🎵 Audio"
    elif update.message.video or update.message.video_note:
        target = update.message.video or update.message.video_note
        file_id = target.file_id
        file_name = f"video_{ts_str}.mp4"
        file_type = "🎥 Video"
    elif update.message.location:
        is_location = True
        lat, lon = update.message.location.latitude, update.message.location.longitude
        try:
            from src.handlers.message_handlers import geolocator
            location = geolocator.reverse(f"{lat}, {lon}", timeout=10)
            direccion = location.address if location else f"{lat}, {lon}"
        except: 
            direccion = f"{lat}, {lon}"
        
        texto_extraido = (f"📍 Ubicación enviada.\n"
                         f"Dirección: {direccion}\n"
                         f"Coordenadas: {lat}, {lon}\n"
                         f"Maps: https://www.google.com/maps?q={lat},{lon}")
        
        file_name = f"Ubicacion_{ts_str}.txt"
        local_path = os.path.join("descargas", file_name)
        if not os.path.exists("descargas"): os.makedirs("descargas")
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(texto_extraido)
        file_id = f"LOC_{ts_unix}"
        file_type = "📍 Ubicación"

    if not file_id and not is_location:
        await update.message.reply_text("❌ No pude procesar este archivo.")
        return

    # 2. CASO ESPECIAL: NOTA DE VOZ (Menú de 4 opciones)
    if is_voice:
        user_data['temp_voice'] = {
            'file_id': file_id, 
            'file_name': file_name,
            'folder_id': user_data.get('current_folder_id'),
            'cloud_id': user_data.get('current_cloud_id')
        }
        keyboard = [
            [InlineKeyboardButton("📝 Solo Transcribir (Ver aquí)", callback_data="voice_only_view")],
            [InlineKeyboardButton("🎙️ Subir Audio y Transcripción", callback_data="voice_upload_both")],
            [InlineKeyboardButton("☁️ Subir Solo Audio", callback_data="voice_upload_audio")],
            [InlineKeyboardButton("📄 Subir Solo Transcripción", callback_data="voice_upload_txt")]
        ]
        await update.message.reply_text("🎙️ *Nota de voz detectada.*\n¿Qué deseas hacer?", 
                                      reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        return

    # 3. ¿HAY CARPETA ACTIVA? (Subida directa y proceso IA)
    folder_id = user_data.get('current_folder_id')
    cloud_parent = user_data.get('current_cloud_id')

    if folder_id:
        msg = await update.message.reply_text(f"📥 Procesando para *{user_data.get('current_path_name', 'Nube')}*...", parse_mode=ParseMode.MARKDOWN)
        local_path = os.path.join("descargas", file_name)
        if not os.path.exists("descargas"): os.makedirs("descargas")
        
        try:
            # A. Descarga
            if not is_location:
                tg_file = await context.bot.get_file(file_id)
                await tg_file.download_to_drive(local_path)
            
            # B. IA (Aislada para que errores de fitz/OpenAI no detengan la subida)
            vector = None
            try:
                if not is_location:
                    texto_extraido = await AIHandler.extract_text(local_path)
                
                if texto_extraido and texto_extraido.strip():
                    vector = await AIHandler.get_embedding(texto_extraido)
            except QuotaExceededError:
                print("⚠️ Cuota de IA agotada detectada en bot.")
                if msg: await msg.edit_text("⏳ *IA temporalmente saturada:* El archivo se subirá pero la búsqueda inteligente tardará un poco más en activarse.", parse_mode=ParseMode.MARKDOWN)
            except Exception as ai_err:
                print(f"⚠️ Error en IA: {ai_err}")

            # C. Subida
            svc = drive_svc if folder_id and not str(folder_id).startswith('/') else dropbox_svc
            svc_name = "drive" if svc == drive_svc else "dropbox"
            
            url = await svc.upload(local_path, file_name, folder_id if svc_name == "drive" else (cloud_parent or "General"))
            
            if url:
                if isinstance(url, tuple): url = url[0]
                
                # D. Registro Database
                db.register_file(
                    telegram_id=update.effective_user.id,
                    name=file_name,
                    f_type=file_name.split('.')[-1],
                    cloud_url=url,
                    service=svc_name,
                    content_text=texto_extraido,
                    embedding=vector,
                    folder_id=folder_id
                )
                await msg.edit_text(f"✅ *Guardado:* `{file_name}`\n🔗 [Ver en la nube]({url})", parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            else:
                await msg.edit_text("❌ Error al subir a la nube.")

        except Exception as e:
            print(f"Error crítico: {e}")
            await msg.edit_text(f"❌ Error crítico: {str(e)}")
        finally:
            if os.path.exists(local_path): 
                os.remove(local_path)
    else:
        # 4. MODO MANUAL
        if 'file_queue' not in user_data: user_data['file_queue'] = []
        user_data['file_queue'].append({'id': file_id, 'name': file_name, 'type': file_type})

        if 'menu_timer' in user_data: user_data['menu_timer'].cancel()

        async def _wait():
            await asyncio.sleep(1.2)
            await show_cloud_menu(update, context)
        
        user_data['menu_timer'] = asyncio.create_task(_wait())

async def show_cloud_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    user_data = context.user_data
    queue = user_data.get('file_queue', [])
    if not queue: return
    selected = user_data.get('selected_clouds', set())
    
    display_name = queue[-1]['name'] if len(queue) == 1 else f"{len(queue)} archivos"
    
    # NUEVO: Mostrar la categoría automática asignada
    first_file = queue[0]['name'] if queue else ""
    category = get_file_category(first_file) or "Otros"
    
    dbx_check = "✅" if "dropbox" in selected else "📦"
    drive_check = "✅" if "drive" in selected else "📁"

    keyboard = [
        [InlineKeyboardButton(f"{dbx_check} Dropbox", callback_data='toggle_dropbox')],
        [InlineKeyboardButton(f"{drive_check} Google Drive", callback_data='toggle_drive')],
        [InlineKeyboardButton("🚀 CONFIRMAR SUBIDA", callback_data='confirm_upload')]
    ]
    
    text = f"📄 *Archivo:* `{display_name.replace('_', ' ')}`\n📁 *Carpeta:* {category} (automático)\n\n¿A qué nube(s)?"
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.effective_chat.send_message(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def voice_options_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from main import db, dropbox_svc, drive_svc
    from src.utils.ai_handler import AIHandler
    import os

    query = update.callback_query
    await query.answer()
    user_data = context.user_data
    voice_data = user_data.get('temp_voice')
    
    if not voice_data:
        await query.edit_message_text("❌ Error: Datos expirados.")
        return

    progress_msg = await query.edit_message_text("⏳ Conectando con el cerebro de IA... (Gemini 2.0)")
    local_audio = os.path.join("descargas", voice_data['file_name'])
    local_txt = local_audio.replace(".ogg", ".txt")
    
    if not os.path.exists("descargas"): os.makedirs("descargas")

    # Acción la extraemos antes del try
    action = query.data
    try:
        # 1. Descargar el audio
        tg_file = await context.bot.get_file(voice_data['file_id'])
        await tg_file.download_to_drive(local_audio)
        
        transcripcion = ""

        # 2. Transcribir si es necesario
        if action in ["voice_only_view", "voice_upload_both", "voice_upload_txt"]:
            try:
                await progress_msg.edit_text("🎙️ Transcribiendo audio... por favor espera.")
                transcripcion = await AIHandler.transcribe_audio(local_audio)

                # 3. Si el usuario solo quería ver la transcripción
                if action == "voice_only_view":
                    if "[Error" in transcripcion:
                        await progress_msg.edit_text(f"❌ *Error en la transcripción:*\n\n{transcripcion}", parse_mode="Markdown")
                    else:
                        await progress_msg.edit_text(f"📝 *Transcripción:* \n\n{transcripcion}", parse_mode="Markdown")
                    
                    # Limpiar
                    user_data.pop('temp_voice', None)
                    if os.path.exists(local_audio): os.remove(local_audio)
                    return
            except QuotaExceededError:
                await progress_msg.edit_text("⚠️ *Cuota de IA agotada:* Lo siento, Gemini no puede procesar más audios por este minuto. Inténtalo de nuevo en 60 segundos.", parse_mode=ParseMode.MARKDOWN)
                if os.path.exists(local_audio): os.remove(local_audio)
                return
            except Exception as e:
                await progress_msg.edit_text(f"❌ Error crítico: {str(e)}")
                return

        # 4. Para cualquier acción de subida, agregamos los elementos al menú de subida
        if action in ["voice_upload_audio", "voice_upload_txt", "voice_upload_both"]:
            # Recuuperar folder_id (necesario si se usa)
            f_id_from_data = voice_data.get('folder_id')
            
            # preparar la cola de archivos
            if 'file_queue' not in user_data:
                user_data['file_queue'] = []

            if action in ["voice_upload_audio", "voice_upload_both"]:
                user_data['file_queue'].append({
                    'id': voice_data['file_id'],
                    'name': voice_data['file_name'],
                    'type': 'audio',
                    'folder_id': f_id_from_data
                })

            if action in ["voice_upload_txt", "voice_upload_both"]:
                if not transcripcion or not transcripcion.strip():
                    transcripcion = "[No se pudo generar texto para este audio]"
                
                with open(local_txt, "w", encoding="utf-8") as f:
                    f.write(transcripcion)
                
                txt_name = voice_data['file_name'].replace(".ogg", ".txt")
                user_data['file_queue'].append({
                    'id': voice_data['file_id'],
                    'name': txt_name,
                    'type': 'text',
                    'folder_id': f_id_from_data
                })

            # liberamos el objeto temporal de la voz y mostramos el menú de nubes
            user_data.pop('temp_voice', None)
            # mostramos el menú de selección de nubes reemplazando el mensaje anterior
            await show_cloud_menu(update, context, edit=True)
            return

    except Exception as e:
        await progress_msg.edit_text(f"❌ Error crítico: {str(e)}")
    finally:
        # sólo borramos los ficheros locales si no estamos esperando subirlos
        if action == "voice_only_view":
            if os.path.exists(local_audio): os.remove(local_audio)
        user_data.pop('temp_voice', None)
        
async def send_explorer(update: Update, context: ContextTypes.DEFAULT_TYPE, folder_id=None, page=0):
    from main import db
    service = context.user_data.get('explore_service')
    items = db.get_folder_contents(folder_id, service=service)
    
    if folder_id and folder_id != 'root':
        folder_data = db.get_folder_by_id(folder_id)
        nombre_ruta = folder_data['name'] if folder_data else "Desconocida"
    else:
        nombre_ruta = f"Raíz ({service.capitalize() if service else ''})"

    ITEMS_PER_PAGE = 10
    total_pages = (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    
    page_items = items[start_idx:end_idx]

    keyboard = []
    if folder_id and folder_id != 'root':
        parent = db.get_parent_folder(folder_id)
        keyboard.append([InlineKeyboardButton("⬆️ Volver", callback_data=f"cd_{parent['id'] if parent else 'root'}")])
    else:
        # At root, providing a way to go back to cloud selection
        keyboard.append([InlineKeyboardButton("⬆️ Cambiar Nube", callback_data="exp_svc_menu")])

    for item in page_items:
        icon = "📁" if item['type'] == 'folder' else "📄"
        keyboard.append([InlineKeyboardButton(f"{icon} {item['name']}", callback_data=f"{'cd' if item['type']=='folder' else 'info'}_{item['id']}")])
    
    # "Crear Carpeta" button removed for pure visualization
    
    # Pagination buttons
    nav_buttons = []
    nav_folder_id = folder_id if folder_id else 'root'
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"exp_page_{nav_folder_id}_{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"exp_page_{nav_folder_id}_{page + 1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    text = f"📂 *Explorador:* `{nombre_ruta}`"
    if total_pages > 1:
        text += f" (Pág. {page + 1}/{total_pages})"
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

# Removed generar_teclado_explorador as it is merged into send_explorer
