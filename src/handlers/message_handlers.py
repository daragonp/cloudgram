import time
import os
import asyncio
import ssl
import certifi
import random  # Movido aquí arriba
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from geopy.geocoders import Nominatim
import geopy.geocoders

# Configuración SSL para Mac
ctx = ssl.create_default_context(cafile=certifi.where())
geopy.geocoders.options.default_ssl_context = ctx
geolocator = Nominatim(user_agent="cloudgram_bot")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✨ *CloudGram Pro Activo*", parse_mode=ParseMode.MARKDOWN)

async def handle_any_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id, file_name, file_type = None, "archivo_desconocido", "documento"
    ts = int(time.time())
    rand_suffix = random.randint(1000, 9999)

    # Detección y asignación de nombres únicos
    if update.message.document:
        file_id = update.message.document.file_id
        file_name = update.message.document.file_name
        file_type = "📦 Documento"
    elif update.message.voice:
        file_id = update.message.voice.file_id
        file_name = f"voz_{ts}_{rand_suffix}.ogg"
        file_type = "🎙️ Nota de voz"
    elif update.message.audio:
        file_id = update.message.audio.file_id
        base = update.message.audio.file_name or f"audio_{ts}"
        file_name = f"{rand_suffix}_{base}"
        file_type = "🎵 Audio"
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_name = f"foto_{ts}_{rand_suffix}.jpg"
        file_type = "🖼️ Foto"
    elif update.message.video:
        file_id = update.message.video.file_id
        base = update.message.video.file_name or f"video_{ts}"
        file_name = f"{rand_suffix}_{base}"
        file_type = "🎥 Video"
    elif update.message.video_note:
        file_id = update.message.video_note.file_id
        file_name = f"video_nota_{ts}_{rand_suffix}.mp4"
        file_type = "🎬 Nota de Video"
    elif update.message.location:
        lat, lon = update.message.location.latitude, update.message.location.longitude
        try:
            location = geolocator.reverse(f"{lat}, {lon}", timeout=10)
            direccion = location.address if location else f"{lat}, {lon}"
        except: direccion = f"{lat}, {lon}"
        file_name = f"Ubicacion_{ts}_{rand_suffix}.txt"
        file_path = os.path.join("descargas", file_name)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"Dirección: {direccion}\nCoordenadas: {lat}, {lon}")
        file_id = f"LOC_{ts}_{rand_suffix}"; file_type = "📍 Ubicación"

    # Manejo de la cola de archivos
    if 'file_queue' not in context.user_data or not context.user_data['file_queue']:
        context.user_data['file_queue'] = []
        context.user_data['selected_clouds'] = set() 

    context.user_data['file_queue'].append({'id': file_id, 'name': file_name, 'type': file_type})

    # Temporizador de agrupación (1.2 segundos para esperar más archivos)
    if 'menu_timer' in context.user_data:
        context.user_data['menu_timer'].cancel()

    async def _wait():
        await asyncio.sleep(1.2)
        await show_cloud_menu(update, context)
    
    context.user_data['menu_timer'] = asyncio.create_task(_wait())
    
async def show_cloud_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    user_data = context.user_data
    queue = user_data.get('file_queue', [])
    if not queue: return

    selected = user_data.get('selected_clouds', set())
    
    if len(queue) == 1:
        display_name = queue[-1]['name']
    else:
        display_name = f"{len(queue)} archivos nuevos"
    
    dbx_check = "✅" if "dropbox" in selected else "📦"
    drive_check = "✅" if "drive" in selected else "📁"

    keyboard = [
        [InlineKeyboardButton(f"{dbx_check} Dropbox", callback_data='toggle_dropbox')],
        [InlineKeyboardButton(f"{drive_check} Google Drive", callback_data='toggle_drive')],
        [InlineKeyboardButton("🚀 CONFIRMAR SUBIDA", callback_data='confirm_upload')]
    ]
    
    last_item = queue[-1]
    last_type = last_item['type'].lower()
    is_audio = "voz" in last_type or "audio" in last_type
    
    # Limpiar nombre para evitar errores de Markdown
    safe_name = display_name.replace("_", "\\_").replace("*", "\\*").replace("`", "")
    
    if is_audio:
        keyboard.insert(0, [InlineKeyboardButton("🤖 Transcribir y Subir con IA", callback_data='ai_transcribe')])
        if not selected:
            text = (f"🎙️ *Nota de voz detectada*\n📄 `{safe_name}`\n\n"
                    f"⚠️ *Paso obligatorio:* Selecciona primero una nube de destino abajo y luego pulsa el botón de IA.")
        else:
            destinos = ", ".join([c.capitalize() for c in selected])
            text = (f"🎙️ *Nota de voz lista*\n📄 `{safe_name}`\nDestino: *{destinos}*\n\n¿Quieres transcribir ahora?")
    else:
        text = f"📄 *Archivo:* `{safe_name}`\nSelecciona destino para la subida:"

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.effective_chat.send_message(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        if "Message is not modified" not in str(e):
            print(f"Error menú: {e}")