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
from src.utils.ai_handler import AIHandler

# Configuración SSL para mi MacBook
ctx = ssl.create_default_context(cafile=certifi.where())
geopy.geocoders.options.default_ssl_context = ctx
geolocator = Nominatim(user_agent="cloudgram_bot")

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
            await espera_msg.edit_text(texto_respuesta, parse_mode=ParseMode.MARKDOWN)
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
                await msg.edit_text(f"✅ *Guardado:* `{file_name}`\n🔗 [Ver en la nube]({url})", parse_mode=ParseMode.MARKDOWN)
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
    dbx_check = "✅" if "dropbox" in selected else "📦"
    drive_check = "✅" if "drive" in selected else "📁"

    keyboard = [
        [InlineKeyboardButton(f"{dbx_check} Dropbox", callback_data='toggle_dropbox')],
        [InlineKeyboardButton(f"{drive_check} Google Drive", callback_data='toggle_drive')],
        [InlineKeyboardButton("🚀 CONFIRMAR SUBIDA", callback_data='confirm_upload')]
    ]
    
    text = f"📄 *Archivo:* `{display_name.replace('_', ' ')}` \nSelecciona destino:"
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

    await query.edit_message_text("⏳ Procesando nota de voz con IA...")
    local_audio = os.path.join("descargas", voice_data['file_name'])
    local_txt = local_audio.replace(".ogg", ".txt")
    
    # Determinar Nube
    folder_id = voice_data.get('folder_id')
    svc = drive_svc if folder_id and not str(folder_id).startswith('/') else dropbox_svc
    svc_name = "drive" if svc == drive_svc else "dropbox"
    dest_id = folder_id if svc_name == "drive" else voice_data.get('cloud_id', 'General')

    try:
        # 1. Descargar
        tg_file = await context.bot.get_file(voice_data['file_id'])
        await tg_file.download_to_drive(local_audio)
        
        action = query.data 
        transcripcion = ""

        # 2. Transcribir si es necesario
        if action in ["voice_only_view", "voice_upload_both", "voice_upload_txt"]:
            transcripcion = await AIHandler.transcribe_audio(local_audio)

        # 3. Ejecutar Acción
        if action == "voice_only_view":
            await query.edit_message_text(f"📝 *Transcripción:* \n\n{transcripcion}", parse_mode="Markdown")

        elif action == "voice_upload_audio":
            url = await svc.upload(local_audio, voice_data['file_name'], dest_id)
            if url:
                if isinstance(url, tuple): url = url[0]
                db.register_file(update.effective_user.id, voice_data['file_name'], "ogg", url, svc_name, folder_id=folder_id)
                await query.edit_message_text(f"✅ Audio subido a {svc_name.capitalize()}")

        elif action == "voice_upload_txt":
            with open(local_txt, "w", encoding="utf-8") as f: f.write(transcripcion)
            txt_name = voice_data['file_name'].replace(".ogg", ".txt")
            url = await svc.upload(local_txt, txt_name, dest_id)
            if url:
                if isinstance(url, tuple): url = url[0]
                vector = await AIHandler.get_embedding(transcripcion)
                db.register_file(update.effective_user.id, txt_name, "txt", url, svc_name, transcripcion, vector, folder_id)
                await query.edit_message_text(f"✅ Transcripción guardada en {svc_name.capitalize()}")

        elif action == "voice_upload_both":
            # Subir Audio
            url_audio = await svc.upload(local_audio, voice_data['file_name'], dest_id)
            # Subir TXT
            with open(local_txt, "w", encoding="utf-8") as f: f.write(transcripcion)
            txt_name = voice_data['file_name'].replace(".ogg", ".txt")
            url_txt = await svc.upload(local_txt, txt_name, dest_id)
            
            if url_audio and url_txt:
                if isinstance(url_audio, tuple): url_audio = url_audio[0]
                if isinstance(url_txt, tuple): url_txt = url_txt[0]
                vector = await AIHandler.get_embedding(transcripcion)
                db.register_file(update.effective_user.id, voice_data['file_name'], "ogg", url_audio, svc_name, folder_id=folder_id)
                db.register_file(update.effective_user.id, txt_name, "txt", url_txt, svc_name, transcripcion, vector, folder_id)
                await query.edit_message_text(f"✅ Audio y Texto guardados en {svc_name.capitalize()}")

    except Exception as e:
        await query.edit_message_text(f"❌ Error: {str(e)}")
    finally:
        if os.path.exists(local_audio): os.remove(local_audio)
        if os.path.exists(local_txt): os.remove(local_txt)
        user_data.pop('temp_voice', None)
        
async def explorar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    folder_id = context.args[0] if context.args else None
    items = db.get_folder_contents(folder_id)
    nombre_ruta = db.get_folder_by_id(folder_id)['name'] if folder_id else "Raíz"

    keyboard = []
    if folder_id:
        parent = db.get_parent_folder(folder_id)
        keyboard.append([InlineKeyboardButton("⬆️ Volver", callback_data=f"cd_{parent['id'] if parent else 'root'}")])

    for item in items:
        icon = "📁" if item['type'] == 'folder' else "📄"
        keyboard.append([InlineKeyboardButton(f"{icon} {item['name']}", callback_data=f"{'cd' if item['type']=='folder' else 'info'}_{item['id']}")])
    
    keyboard.append([InlineKeyboardButton("➕ Crear Carpeta", callback_data=f"mkdir_{folder_id or 'root'}")])
    await (update.callback_query.edit_message_text if update.callback_query else update.message.reply_text)(
        f"📂 *Explorador:* `{nombre_ruta}`", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
def generar_teclado_explorador(folder_id=None):
    from main import db # Importación local para evitar líos de circularidad
    items = db.get_folder_contents(folder_id)
    keyboard = []
    
    # Botón para subir de nivel
    if folder_id:
        parent = db.get_parent_folder(folder_id) # Asegúrate de tener este método en db_handler
        parent_id = parent['id'] if parent else "root"
        keyboard.append([InlineKeyboardButton("⬆️ Volver atrás", callback_data=f"cd_{parent_id}")])

    # Listar carpetas primero
    for item in items:
        if item['type'] == 'folder':
            keyboard.append([InlineKeyboardButton(f"📁 {item['name']}", callback_data=f"cd_{item['id']}")])
        else:
            keyboard.append([InlineKeyboardButton(f"📄 {item['name']}", callback_data=f"info_{item['id']}")])
            
    # Botón de acción
    keyboard.append([InlineKeyboardButton("➕ Crear Carpeta", callback_data=f"mkdir_{folder_id or 'root'}")])
            
    return InlineKeyboardMarkup(keyboard)

