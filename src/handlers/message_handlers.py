import time
import os
import asyncio
import ssl
import certifi
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from geopy.geocoders import Nominatim
import geopy.geocoders
from src.init_services import db, dropbox_svc, drive_svc, openai_client

# Configuración SSL para Mac
ctx = ssl.create_default_context(cafile=certifi.where())
geopy.geocoders.options.default_ssl_context = ctx
geolocator = Nominatim(user_agent="cloudgram_bot")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✨ *CloudGram Pro Activo*", parse_mode=ParseMode.MARKDOWN)

async def buscar_ia_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /buscar_ia [pregunta de contexto]"""
    
    # 1. Verificar si el usuario escribió algo después del comando
    if not context.args:
        await update.message.reply_text(
            "🔎 *Uso:* `/buscar_ia ¿dónde está el contrato de alquiler?`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    query = " ".join(context.args)
    espera_msg = await update.message.reply_text("🤖 Consultando a mi memoria neuronal...")

    try:
        # 2. Generar el Embedding de la pregunta del usuario usando OpenAI
        # (Asegúrate de tener definido 'openai_client' globalmente o importado)
        from main import openai_client 
        
        response = openai_client.embeddings.create(
            input=[query],
            model="text-embedding-3-small"
        )
        query_vector = response.data[0].embedding

        # 3. Llamar a la función que añadimos a DatabaseHandler
        # resultados trae: (id, name, url, similarity)
        from main import db # Importamos la instancia de la DB
        resultados = db.search_semantic(query_vector, limit=3)

        # 4. Filtrar por un umbral de confianza (ej: 0.3)
        if resultados and resultados[0][3] > 0.3:
            texto_respuesta = "🎯 *He encontrado estos archivos por contexto:*\n\n"
            for res in resultados:
                # res[3] es la similitud. La convertimos a porcentaje para el usuario
                porcentaje = int(res[3] * 100)
                texto_respuesta += f"📄 *{res[1]}* ({porcentaje}% coincidencia)\n🔗 [Ver archivo]({res[2]})\n\n"
            
            await espera_msg.edit_text(texto_respuesta, parse_mode=ParseMode.MARKDOWN)
        else:
            await espera_msg.edit_text("😔 No encontré nada que coincida con ese contexto. Intenta con otras palabras.")

    except Exception as e:
        print(f"Error en buscar_ia: {e}")
        await espera_msg.edit_text("❌ Ocurrió un error al procesar la búsqueda con IA.")
        
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

async def handle_any_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Súper Función: Detecta el tipo de archivo y decide si subir directo 
    (si hay carpeta activa) o mostrar menú de selección.
    """
    user_data = context.user_data
    file_id, file_name, file_type = None, "archivo_desconocido", "documento"
    
    # 1. Identificación del archivo (Tu lógica de detección)
    if update.message.document:
        file_id = update.message.document.file_id
        file_name = update.message.document.file_name
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_name = f"foto_{int(time.time())}.jpg"
    # ... (puedes añadir los otros tipos aquí: audio, video, etc.)

    # 2. ¿Hay una sesión de carpeta activa?
    folder_id = user_data.get('current_folder_id')
    cloud_parent_path = user_data.get('current_cloud_id') # Ej: "/Proyectos"

    if folder_id:
        # MODO DIRECTO: El usuario está "dentro" de una carpeta
        msg = await update.message.reply_text(f"📥 Subiendo directo a *{user_data.get('current_path_name')}*...", parse_mode=ParseMode.MARKDOWN)
        
        try:
            # Descarga
            tg_file = await context.bot.get_file(file_id)
            local_path = os.path.join("descargas", file_name)
            await tg_file.download_to_drive(local_path)

            # Subida (Usamos la instancia global de Dropbox por defecto o la que prefieras)
            from main import dropbox_svc, db
            cloud_url = await dropbox_svc.upload(local_path, file_name, folder=cloud_parent_path)

            if cloud_url:
                db.register_file(
                    telegram_id=update.effective_user.id,
                    name=file_name,
                    cloud_url=cloud_url,
                    service='dropbox',
                    folder_id=folder_id
                )
                await msg.edit_text(f"✅ ¡Listo! Guardado en *{user_data.get('current_path_name')}*", parse_mode=ParseMode.MARKDOWN)
            
            if os.path.exists(local_path): os.remove(local_path)
            
        except Exception as e:
            await msg.edit_text(f"❌ Error en subida directa: {e}")
    
    else:
        # MODO MANUAL: No hay carpeta, mostramos el menú de selección de nubes (Tu lógica anterior)
        if 'file_queue' not in user_data: user_data['file_queue'] = []
        user_data['file_queue'].append({'id': file_id, 'name': file_name, 'type': 'Archivo'})
        
        if 'menu_timer' in user_data: user_data['menu_timer'].cancel()
        
        async def _wait():
            await asyncio.sleep(1.2)
            await show_cloud_menu(update, context)
        
        user_data['menu_timer'] = asyncio.create_task(_wait())

def generar_teclado_explorador(folder_id=None):
    """
    Genera un teclado dinámico basado en el contenido de la DB.
    Debe estar en message_handlers.py para que el bot la use al navegar.
    """
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

async def explorar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # folder_id puede venir de los argumentos /explorar 123
    folder_id = context.args[0] if context.args else None
    
    # Obtenemos contenido y carpeta actual
    items = db.get_folder_contents(folder_id)
    
    # Determinar el nombre de la ruta actual
    nombre_ruta = "Raíz"
    if folder_id:
        folder_info = db.get_folder_by_id(folder_id)
        nombre_ruta = folder_info['name'] if folder_info else "Desconocida"

    keyboard = []
    
    # 1. Botón para subir de nivel (Volver atrás)
    if folder_id:
        parent = db.get_parent_folder(folder_id)
        parent_id = parent['id'] if parent else "root"
        keyboard.append([InlineKeyboardButton("⬆️ Volver a " + ("Raíz" if parent_id == "root" else parent['name']), callback_data=f"cd_{parent_id}")])

    # 2. Listar Carpetas y Archivos
    for item in items:
        icon = "📁" if item['type'] == 'folder' else "📄"
        callback = f"cd_{item['id']}" if item['type'] == 'folder' else f"info_{item['id']}"
        keyboard.append([InlineKeyboardButton(f"{icon} {item['name']}", callback_data=callback)])
    
    # 3. Botón de acción final
    keyboard.append([InlineKeyboardButton("➕ Crear Carpeta aquí", callback_data=f"mkdir_{folder_id or 'root'}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    texto_msg = f"📂 *Explorador:* `{nombre_ruta}`\n\nSelecciona un elemento para navegar o gestionar:"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(texto_msg, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(texto_msg, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)