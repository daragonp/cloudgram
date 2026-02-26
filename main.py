# main.py
import os
import json
import warnings
import platform
import sys
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
    ContextTypes
)
from telegram.constants import ParseMode
from telegram.error import NetworkError

# 2. IMPORTACIÓN DE SERVICIOS INICIALIZADOS
# Asegúrate de que este archivo exista en src/init_services.py
from src.init_services import db, dropbox_svc, drive_svc, openai_client 

# 3. IMPORTACIÓN DE HANDLERS
from src.handlers.message_handlers import start, handle_any_file, show_cloud_menu, explorar, generar_teclado_explorador
from src.utils.ai_handler import AIHandler

def print_server_welcome():
    """
    Realiza un chequeo exhaustivo del entorno y muestra un reporte 
    de bienvenida en la consola al iniciar el servidor.
    """
    # Cargamos variables de entorno
    load_dotenv()
    
    # Diseño visual en consola
    print("\n" + "╔" + "═"*58 + "╗")
    print("║" + " "*21 + "☁️  CLOUDGRAM PRO v1.0" + " "*21 + "║")
    print("║" + " "*18 + "SISTEMA DE GESTIÓN CLOUD" + " "*16 + "║")
    print("╚" + "═"*58 + "╝")

    # 1. Información del Sistema
    print(f"📅 Fecha de arranque: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"💻 Sistema Operativo: {platform.system()} {platform.release()}")
    print(f"🐍 Python Versión:  {sys.version.split()[0]}")
    print("-" * 60)

    # 2. Verificación de Carpetas (Auto-creación)
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

    # 4. Verificación de Variables Críticas (.env)
    print("-" * 60)
    print("🔑 CHEQUEO DE CREDENCIALES (.env):")
    critical_keys = [
        'TELEGRAM_BOT_TOKEN', 
        'OPENAI_API_KEY', 
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
            # Mostramos solo los primeros 4 caracteres por seguridad
            print(f"   [✅] Configurada: {key} ({val[:4]}***)")

    print("-" * 60)
    if all_ok:
        print("🚀 ¡SERVIDOR LISTO! Conectando con la API de Telegram...")
    else:
        print("⚠️  ADVERTENCIA: Faltan llaves. El bot podría no funcionar.")
    print("═" * 60 + "\n")
# 3. FUNCIONES DE COMANDO
async def list_files_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    files = db.get_last_files(20)
    if not files:
        return await update.message.reply_text("La lista está vacía.")
    
    text = "📋 *Últimos 20 archivos:*\n\n"
    for i, f in enumerate(files, 1): # El '1' inicia el conteo en 1
        # f[1] es el nombre, f[2] es la url
        text += f"{i}. [{f[1]}]({f[2]}) ({f[3].upper()})\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 *Ayuda de CloudGram Pro*\n\n"
        "Comandos principales:\n"
        "• /start - Menú principal\n"
        "• /listar - Mostrar archivos recientes\n"
        "• /buscar <texto> - Buscar por nombre\n"
        "• /buscar_ia <consulta> - Búsqueda semántica (IA)\n"
        "• /explorar - Explorar carpetas\n"
        "• /eliminar <texto> - Eliminar archivos por nombre\n"
        "• /cancelar - Cancelar acciones en curso\n\n"
        "También puedes enviar archivos (documentos, fotos, audio, voz).\n"
        "Al enviar una nota de voz puedes elegir transcribir o subirla y seleccionar la/s nubes donde guardarla."
    )
    await update.message.reply_text(help_text)


async def unknown_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja comandos no reconocidos y muestra la ayuda al usuario."""
    try:
        cmd = update.message.text.split()[0]
    except Exception:
        cmd = update.message.text or "(desconocido)"

    await update.message.reply_text(f"❌ Comando desconocido: {cmd}\nUsa /ayuda para ver la lista de comandos disponibles.")
    await help_command(update, context)
    
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Búsqueda simple por nombre con paginación
    user_data = context.user_data
    query = " ".join(context.args)
    if not query:
        return await update.message.reply_text("🔎 Indica el nombre del archivo.")

    raw = db.search_by_name(query)
    if not raw:
        return await update.message.reply_text("❌ No encontré archivos con ese nombre.")

    # Normalizar y deduplicar
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

    # --- LÓGICA 1: ELIMINACIÓN POR ÍNDICE (PERSISTENTE) ---
    if state == 'waiting_delete_selection':
        if text.lower() in ['cancelar', 'terminar', 'salir']:
            user_data['state'] = None
            user_data.pop('search_results', None)
            return await update.message.reply_text("🚫 Sesión de limpieza finalizada.")

        if text.isdigit():
            idx = int(text) - 1
            results = user_data.get('search_results', [])
            
            if 0 <= idx < len(results):
                # Extraemos datos del archivo seleccionado
                # results[idx] es: (id, name, url, service, summary, tech_desc)
                selected = results[idx]
                fid = selected[0]
                name = selected[1]
                service = selected[3]
                
                msg = await update.message.reply_text(f"⏳ Eliminando `{name}` de {service.upper()}...")
                
                # 1. Borrado en la Nube
                cloud_deleted = False
                try:
                    if service == 'dropbox':
                        # Dropbox usa el path con /
                        cloud_deleted = await dropbox_svc.delete_file(f"/{name}")
                    elif service == 'drive':
                        # Drive usa el nombre para buscar el ID internamente
                        cloud_deleted = await drive_svc.delete_file(name)
                except Exception as e:
                    print(f"Error borrado cloud: {e}")

                # 2. Borrado en DB
                db.delete_file_by_id(fid)
                
                # 3. Remover de la lista local para actualizar la vista inmediatamente
                results.pop(idx)
                user_data['search_results'] = results

                status = "✅ Eliminado por completo." if cloud_deleted else "⚠️ Eliminado de la DB (No se pudo borrar de la nube)."
                await msg.edit_text(f"{status}\nArchivo: `{name}`")

                # 4. Verificar si quedan archivos
                if not results:
                    user_data['state'] = None
                    return await update.message.reply_text("📭 Ya no quedan archivos en esta búsqueda.")
                
                # Ajustar página si es necesario
                items_per_page = 10
                if user_data.get('current_page', 0) * items_per_page >= len(results):
                    user_data['current_page'] = max(0, user_data.get('current_page', 0) - 1)

                # Mostrar la lista actualizada automáticamente
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

    # --- LÓGICA 3: CREACIÓN DE CARPETAS (NUEVA) ---
    if state == 'waiting_folder_name':
        folder_name = text
        parent_id = user_data.get('parent_folder_id') # Viene del callback mkdir_
        
        # Validación básica de nombre
        if any(c in folder_name for c in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']):
            return await update.message.reply_text("❌ El nombre contiene caracteres no permitidos.")

        status_msg = await update.message.reply_text(f"🛠️ Creando carpeta `{folder_name}` en la nube...")
        
        try:
            # 1. Determinar el path del padre si existe (para Dropbox)
            parent_path = ""
            if parent_id:
                p_folder = db.get_folder_by_id(parent_id)
                parent_path = p_folder['cloud_folder_id'] if p_folder else ""

            # 2. Crear en la Nube (Dropbox por defecto o según servicio activo)
            # Asegúrate que dropbox_svc.create_folder devuelva el path/id creado
            cloud_id = await dropbox_svc.create_folder(folder_name, parent_path)
            
            # 3. Registrar en la Base de Datos
            db.create_folder(
                name=folder_name, 
                service='dropbox', 
                cloud_folder_id=cloud_id, 
                parent_id=parent_id
            )
            
            # Limpiar estado
            user_data['state'] = None
            user_data.pop('parent_folder_id', None)
            
            await status_msg.edit_text(f"✅ Carpeta `{folder_name}` creada y registrada correctamente.")
            return
            
        except Exception as e:
            print(f"Error creando carpeta: {e}")
            await status_msg.edit_text(f"❌ Error al crear la carpeta: {str(e)}")
            return

    # Si ninguna de las ramas anteriores coincidió, respondemos con ayuda
    await update.message.reply_text("❌ No reconozco esa entrada. Aquí tienes la ayuda:")
    await help_command(update, context)
    return

# 4. PROCESO DE SUBIDA Y CALLBACKS
async def upload_process(update, context, target_files_info: list, predefined_embedding=None):
    """Procesa archivos, genera resúmenes IA y registra en DB"""
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

        # IA: Texto, Resumen y Embedding
        texto = await AIHandler.extract_text(local_path)
        vector = predefined_embedding
        resumen = None
        ext = file_name.split('.')[-1].lower()
        desc_tec = f"Archivo {ext.upper()}"

        if texto:
            if not vector: vector = await AIHandler.get_embedding(texto)
            resumen = await AIHandler.generate_summary(texto)
        else:
            resumen = f"Documento binario/comprimido ({ext}). No se extrajo texto."

        cloud_links = []
        for cloud in selected_clouds:
            try:
                # Subir y obtener URL
                url = await (dropbox_svc.upload(local_path, file_name) if cloud == 'dropbox' else drive_svc.upload(local_path, file_name))
                if url:
                    # AQUÍ ESTÁ EL CAMBIO: Creamos un link Markdown
                    cloud_links.append(f"[✅ {cloud.capitalize()}]({url})")
                    
                    db.register_file(
                        telegram_id=original_info['id'], name=file_name, f_type=ext,
                        cloud_url=url, service=cloud, content_text=texto,
                        embedding=vector, summary=resumen, technical_description=desc_tec,
                        folder_id=original_info.get('folder_id', user_data.get('current_folder_id'))
                    )
            except Exception as e:
                print(f"Error subiendo a {cloud}: {e}")
                cloud_links.append(f"❌ {cloud.capitalize()}")

        final_report.append(f"📄 `{file_name}`\n" + " | ".join(cloud_links))
        if os.path.exists(local_path): os.remove(local_path)

    await context.bot.send_message(chat_id=update.effective_chat.id, text="🚀 *Subida finalizada:*\n\n" + "\n".join(final_report), parse_mode=ParseMode.MARKDOWN)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_data = context.user_data
    
    # 1. Responder siempre para quitar el estado de "cargando" en Telegram
    await query.answer()

    # 2. Lógica para seleccionar/deseleccionar nubes
    if data.startswith('toggle_'):
        cloud = data.replace('toggle_', '')
        if 'selected_clouds' not in user_data:
            user_data['selected_clouds'] = set()
        
        if cloud in user_data['selected_clouds']:
            user_data['selected_clouds'].remove(cloud)
        else:
            user_data['selected_clouds'].add(cloud)
        
        # Refrescar el menú para mostrar los nuevos checks (✅)
        # El try/except evita que el bot se detenga si Telegram dice "Message not modified"
        try:
            await show_cloud_menu(update, context, edit=True)
        except Exception as e:
            if "Message is not modified" not in str(e):
                print(f"Error al refrescar menú: {e}")

    # 3. Lógica para transcribir notas de voz
    elif data == 'ai_transcribe':
        queue = user_data.get('file_queue', [])
        selected_clouds = user_data.get('selected_clouds', set())
        
        if not selected_clouds:
            await query.message.reply_text("⚠️ Selecciona primero una nube de destino.")
            return
            
        if not queue: return
        
        f_info = queue[-1]
        local_audio = os.path.join("descargas", f_info['name'])
        msg = await query.edit_message_text("🎙️ Transcribiendo audio... por favor espera.")
        
        try:
            # Asegurar descarga
            tg_f = await context.bot.get_file(f_info['id'])
            await tg_f.download_to_drive(local_audio)
            
            # Procesar IA
            texto = await AIHandler.transcribe_audio(local_audio)
            vector = await AIHandler.get_embedding(texto)
            
            txt_name = f"Transcrip_{f_info['name'].rsplit('.', 1)[0]}.txt"
            txt_path = os.path.join("descargas", txt_name)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(texto)
            
            # Subir el archivo de texto resultante
            await upload_process(update, context, [(txt_path, txt_name, f_info)], predefined_embedding=vector)
            
            # Limpieza
            if os.path.exists(local_audio): os.remove(local_audio)
            user_data['file_queue'] = [] # Limpiar cola tras éxito
            await msg.delete()
        except Exception as e:
            await query.message.reply_text(f"❌ Error en IA: {e}")

    # 4. Lógica de confirmación de subida estándar
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
        # Llamamos a la función para refrescar el mensaje sin borrar
        return await send_delete_page(update, context, edit=True)
        
    elif data == 'del_page_prev':
        user_data['current_page'] -= 1
        return await send_delete_page(update, context, edit=True)

    # navegación en resultados IA
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

    # navegación en resultados por nombre (/buscar)
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

    # --- LÓGICA DE CANCELACIÓN ---
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
    """Búsqueda Semántica Avanzada (Punto 2 y 3)"""
    user_data = context.user_data
    if not context.args:
        return await update.message.reply_text("🔎 *Uso:* `/buscar_ia concepto`", parse_mode=ParseMode.MARKDOWN)
    
    query_text = " ".join(context.args)
    msg = await update.message.reply_text("🤖 Consultando mi base neuronal...")
    
    try:
        # 1. Generar Embedding del texto buscado
        response = openai_client.embeddings.create(
            input=[query_text],
            model="text-embedding-3-small"
        )
        query_vector = response.data[0].embedding

        # 2. Llamar a la función de búsqueda semántica de la DB (La que actualizamos antes)
        # Debe devolver: id, name, url, similarity, summary, service
        raw_results = db.search_semantic(query_vector, limit=20)

        normalized = []
        seen_names = set()
        # aplicar umbral para similitud y eliminar duplicados
        semantic = [r for r in raw_results if r.get('similarity', 0) >= 0.2]
        if not semantic:
            await msg.edit_text("🔄 No hay coincidencias relevantes por concepto. Buscando por nombre...")
            tradicional = db.search_by_name(query_text)
            if not tradicional:
                return await msg.edit_text("😔 No encontré nada, ni siquiera por nombre.")

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

        # Guardamos resultados en user_data para paginar
        user_data['search_results_ia'] = normalized
        user_data['ia_current_page'] = 0
        # Ajuste dinámico de items por página: pocos->1, medio->2, muchos->3
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
        await msg.edit_text("⚠️ Hubo un error procesando la búsqueda.")

async def cancelar_handler(update, context):
    """Limpia cualquier estado y responde con éxito"""
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
        # item[1] es name, item[2] es cloud_url, item[3] es service
        # Creamos un hipervínculo en el nombre del archivo
        text += f"{i}. [{item[1]}]({item[2]}) | _{item[3].capitalize()}_\n"
    
    # Construcción de botones de navegación
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Anterior", callback_data="del_page_prev"))
    if end_idx < len(results):
        nav_buttons.append(InlineKeyboardButton("Siguiente ➡️", callback_data="del_page_next"))

    keyboard = [nav_buttons] if nav_buttons else []
    keyboard.append([InlineKeyboardButton("❌ CANCELAR Y SALIR", callback_data="cancel_deletion")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Importante: disable_web_page_preview=True para que no se llene el chat de cuadros de previsualización
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
    """Muestra una página de resultados de `/buscar_ia` almacenados."""
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
    for idx, item in enumerate(current_items, start_idx + 1):
        score = f" ({int(item['score']*100)}%)" if item.get('score') else ""
        # convertir número a emoji (1 -> 1️⃣, 2 -> 2️⃣, ...)
        num_emoji = f"{idx}️⃣"
        text += f"{num_emoji} "
        text += "•—————————————————————\n"
        text += f"📄 *{item['name']}*{score}\n"
        text += f"📝 _{item.get('summary','')}_\n"
        if item.get('url'):
            text += f"🔗 *Enlace:* [Ver en la nube]({item['url']})\n"
        text += "\n"
    text += "•—————————————————————\n"

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
        # 1. Intentar borrar de la Nube
        success = False
        if service == 'dropbox':
            # Dropbox requiere el path completo (ej: /foto.jpg)
            success = await dropbox_svc.delete_file(f"/{name}") 
        elif service == 'drive':
            # Drive suele requerir el ID del archivo, si lo guardaste en DB úsalo
            success = await drive_svc.delete_file(name) 

        # 2. Borrar de la Base de Datos
        db.delete_file_by_id(fid)
        
        status = "y de la nube ✅" if success else "(solo de la DB ⚠️)"
        await update.message.reply_text(f"🗑️ Archivo `{name}` eliminado correctamente {status}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error durante el borrado: {e}")
        
# 6. CONFIGURACIÓN E INICIO

async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "🏠 Menú Principal"),
        BotCommand("buscar_ia", "🤖 Búsqueda Inteligente"),
        BotCommand("explorar", "📂 Mis Carpetas"),
        BotCommand("listar", "📋 Recientes"),
        BotCommand("buscar", "🔎 Buscar por nombre"),
        BotCommand("eliminar", "🗑️ Borrar archivos"),
        BotCommand("ayuda", "🆘 Ayuda"),
        BotCommand("help", "🆘 Help")
    ])
# 7. CARPETAS Y ARCHIVOS (EXPLORADOR)

async def cambiar_directorio(update, context):
    query = update.callback_query
    await query.answer()
    
    # Supongamos que el callback_data es "cd_123"
    folder_id = query.data.split('_')[1]
    
    if folder_id == "root":
        context.user_data['current_folder_id'] = None
        context.user_data['current_path_name'] = "Raíz"
    else:
        folder = db.get_folder_by_id(folder_id)
        context.user_data['current_folder_id'] = folder['id']
        context.user_data['current_path_name'] = folder['name']
        context.user_data['current_cloud_id'] = folder['cloud_folder_id']

    await query.edit_message_text(
        f"📂 Estás en: *{context.user_data['current_path_name']}*\n"
        "Ahora, cualquier archivo que envíes se guardará aquí.",
        parse_mode='Markdown'
    )


async def error_handler(update, context):
    """Maneja errores de red de forma silenciosa si son temporales."""
    if isinstance(context.error, NetworkError):
        # Solo logueamos una advertencia corta en lugar de todo el traceback
        print(f"⚠️ Error de red temporal en Telegram: {context.error}")
    else:
        print(f"❌ Error crítico: {context.error}")



if __name__ == '__main__':
    print_server_welcome()
    if not os.path.exists("descargas"):
        os.makedirs("descargas")
    
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).post_init(post_init).build()
    
    # Comandos principales
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("listar", list_files_command))
    app.add_handler(CommandHandler("buscar", search_command))
    app.add_handler(CommandHandler("buscar_ia", search_ia_command))
    app.add_handler(CommandHandler("eliminar", delete_command))
    app.add_handler(CommandHandler("explorar", explorar))
    app.add_handler(CommandHandler("ayuda", help_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(voice_options_callback, pattern="^voice_"))
    app.add_handler(CommandHandler(["cancelar", "salir", "stop"], cancelar_handler))
    # Capturar comandos no reconocidos y mostrar ayuda
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command_handler))
        
    # Manejo de archivos y multimedia
    app.add_handler(MessageHandler(
        (filters.Document.ALL | filters.PHOTO | filters.VIDEO | 
         filters.VIDEO_NOTE | filters.AUDIO | filters.VOICE | filters.LOCATION), 
        handle_any_file
    ))
    
    # Manejo de texto (para renombrar)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_input))
    
    # Callbacks de botones
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("🚀 CloudGram PRO v1.0 ONLINE")
    app.run_polling()