# main.py
import os
import json
import warnings
import platform
import sys
import numpy as np
from datetime import datetime
from dotenv import load_dotenv  # <-- ESTO FALTABA

# 1. CARGA DE ENTORNO (Debe ir antes de importar servicios)
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
async def list_files_command(update, context):
    files = db.get_last_files(20)
    if not files:
        await update.message.reply_text("📭 No hay archivos registrados aún.")
        return
    
    response = "📂 *Últimos 20 archivos subidos:*\n\n"
    for fid, name, url, service, date in files:
        # Manejo de fecha flexible
        try:
            dt = datetime.fromisoformat(str(date)).strftime("%d/%m %H:%M")
        except:
            dt = "Reciente"
        response += f"• `{name}`\n  └ {service.capitalize()}: [Abrir]({url}) | _{dt}_\n\n"
    
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def search_command(update, context):
    if not context.args:
        await update.message.reply_text("🔍 Uso: `/buscar palabra`")
        return
    
    query = " ".join(context.args)
    results = db.search_by_name(query)
    
    if not results:
        await update.message.reply_text(f"❌ No encontré archivos con: `{query}`")
        return
    
    text = f"🔎 *Resultados para:* `{query}`\n\n"
    for fid, name, url, service in results:
        text += f"ID: `{fid}` - [{name}]({url}) ({service.capitalize()})\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    text = update.message.text.strip()
    state = user_data.get('state')

    # --- LÓGICA 1: ELIMINACIÓN POR ÍNDICE (PERSISTENTE) ---
    if state == 'waiting_delete_selection':
        if text.lower() in ['cancelar', 'terminar', 'salir']:
            user_data['state'] = None
            user_data.pop('search_results', None)
            user_data.pop('current_page', None)
            return await update.message.reply_text("🚫 Sesión de limpieza finalizada.")

        if text.isdigit():
            idx = int(text) - 1
            results = user_data.get('search_results', [])
            
            if 0 <= idx < len(results):
                fid, name, url, service = results.pop(idx)
                msg = await update.message.reply_text(f"⏳ Eliminando `{name}`...")
                
                cloud_deleted = False
                try:
                    if service == 'dropbox':
                        cloud_deleted = await dropbox_svc.delete_file(f"/{name}")
                    elif service == 'drive':
                        cloud_deleted = await drive_svc.delete_file(name)
                except Exception as e:
                    print(f"Error en borrado físico: {e}")

                db.delete_file_by_id(fid)
                
                status = "✅ Eliminado por completo." if cloud_deleted else "⚠️ Eliminado solo de la Base de Datos."
                await msg.edit_text(f"{status}\nArchivo: `{name}`")

                if not results:
                    user_data['state'] = None
                    return await update.message.reply_text("📭 Ya no quedan más archivos en esta búsqueda.")
                
                items_per_page = 10
                if user_data['current_page'] * items_per_page >= len(results):
                    user_data['current_page'] = max(0, user_data['current_page'] - 1)

                await update.message.reply_text("🔄 Lista actualizada:")
                return await send_delete_page(update, context, edit=False)
            else:
                return await update.message.reply_text(f"❌ Número inválido. Elige entre 1 y {len(results)}.")

    # --- LÓGICA 2: RENOMBRADO ---
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
            
        except Exception as e:
            print(f"Error creando carpeta: {e}")
            await status_msg.edit_text(f"❌ Error al crear la carpeta: {str(e)}")

# 4. PROCESO DE SUBIDA Y CALLBACKS
async def upload_process(update, context, target_files_info: list, predefined_embedding=None):
    user_data = context.user_data
    selected_clouds = user_data.get('selected_clouds', set())
    final_report = []

    # Si no hay nubes, no podemos subir, pero avisamos
    if not selected_clouds:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text="⚠️ No has seleccionado ninguna nube. Por favor, selecciona al menos una antes de continuar."
        )
        return

    for local_path, file_name, original_info in target_files_info:
        # --- CORRECCIÓN CRÍTICA: Asegurar descarga ---
        # Si el archivo NO existe localmente (y no es ubicación), hay que descargarlo AHORA
        if not os.path.exists(local_path) and not str(original_info['id']).startswith("LOC_"):
            try:
                tg_f = await context.bot.get_file(original_info['id'])
                await tg_f.download_to_drive(local_path)
            except Exception as e:
                print(f"Error descargando para proceso: {e}")
                continue

        cloud_links = []
        texto_extraido = None
        vector_ia = predefined_embedding

        # Extraer texto solo si el archivo existe
        if not vector_ia and os.path.exists(local_path):
            texto_extraido = await AIHandler.extract_text(local_path)
            if texto_extraido:
                vector_ia = await AIHandler.get_embedding(texto_extraido)

        for cloud in selected_clouds:
            try:
                url = None
                if cloud == 'dropbox': url = await dropbox_svc.upload(local_path, file_name)
                elif cloud == 'drive': url = await drive_svc.upload(local_path, file_name)
                
                if url:
                    cloud_links.append(f"🔗 [{cloud.capitalize()}]({url})")
                    db.register_file(
                        telegram_id=original_info['id'],
                        name=file_name,
                        f_type=original_info['type'],
                        cloud_url=url,
                        service=cloud,
                        content_text=texto_extraido,
                        embedding=vector_ia,
                        folder_id=user_data.get('current_folder_id')
                    )
            except Exception as e:
                print(f"Error subida {cloud}: {e}")
                cloud_links.append(f"❌ {cloud}: Error")

        final_report.append(f"📄 `{file_name}`\n" + "\n".join(cloud_links))
        if os.path.exists(local_path): os.remove(local_path)

    report = "✅ *Subida completada*\n\n" + "\n\n".join(final_report)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=report, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

# En main.py
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
    """Búsqueda semántica usando embeddings de OpenAI y registros de Supabase"""
    if not context.args:
        return await update.message.reply_text("🔎 *Uso:* `/buscar_ia concepto` (ej: contrato de renta)", parse_mode=ParseMode.MARKDOWN)
    
    query_text = " ".join(context.args).lower()
    msg = await update.message.reply_text("🤖 Analizando mi memoria neuronal...")
    
    try:
        # 1. Obtener Embedding de la consulta (Usando el openai_client global)
        response = openai_client.embeddings.create(
            input=[query_text],
            model="text-embedding-3-small"
        )
        query_vector = response.data[0].embedding

        # 2. Obtener archivos con embeddings de la DB (Variable db global)
        files = db.get_all_with_embeddings()
        results = []
        
        # 3. Procesar similitud (Cálculo de Coseno)
        for f_id, name, url, service, content, emb_json in files:
            try:
                if not emb_json or emb_json in ["error_limit", "[]"]: 
                    continue
                
                emb = json.loads(emb_json)
                
                # Math: Dot product / (norm_a * norm_b)
                score = np.dot(query_vector, emb) / (np.linalg.norm(query_vector) * np.linalg.norm(emb))
                
                # Bonus por coincidencia exacta en nombre o contenido
                if query_text in (content or "").lower() or query_text in name.lower():
                    score += 0.2
                    
                if score > 0.35: # Umbral de relevancia
                    results.append((score, name, url, service))
            except Exception as e:
                continue 
        
        results.sort(key=lambda x: x[0], reverse=True)
        
        if not results:
            return await msg.edit_text("😔 No encontré nada relacionado con ese contexto.")
        
        out = "🎯 *Resultados de búsqueda inteligente:*\n\n"
        for s, n, u, sv in results[:5]:
            final_score = min(int(s * 100), 100)
            out += f"🔹 [{n}]({u}) \n    _Confianza: {final_score}%_ | `{sv.upper()}`\n\n"
        
        await msg.edit_text(out, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

    except Exception as e:
        print(f"❌ ERROR EN BUSQUEDA IA: {e}")
        await msg.edit_text("⚠️ No pude completar la búsqueda. Revisa los logs del servidor.")

async def cancelar_handler(update, context):
    """Limpia cualquier estado y responde con éxito"""
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 ¡Entendido, {user_name}! He detenido cualquier proceso activo.\n"
        "Estoy listo para tu siguiente búsqueda o archivo."
    )

async def delete_command(update, context):
    if not context.args:
        return await update.message.reply_text("🗑️ Uso: `/eliminar palabra` (ej: `/eliminar foto`)")
    
    query = " ".join(context.args)
    results = db.search_by_name(query)
    
    if not results:
        return await update.message.reply_text(f"❌ No se encontraron archivos con: `{query}`")

    # Guardamos los datos en context para la paginación
    context.user_data['search_results'] = results
    context.user_data['current_page'] = 0
    context.user_data['state'] = 'waiting_delete_selection'
    
    await send_delete_page(update, context)

async def send_delete_page(update, context, edit=False):
    user_data = context.user_data
    results = user_data['search_results']
    page = user_data['current_page']
    items_per_page = 10
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    current_items = results[start_idx:end_idx]
    
    text = f"🔍 *Resultados de búsqueda* (Página {page + 1}):\n\n"
    for i, (fid, name, url, service) in enumerate(current_items, start_idx + 1):
        text += f"{i}. `{name}` ({service.capitalize()})\n"
    
    text += "\n🔢 Responde con el **número** para eliminar o usa los botones:"

    # Construcción de botones de navegación
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Atrás", callback_data="del_page_prev"))
    if end_idx < len(results):
        nav_buttons.append(InlineKeyboardButton("Siguiente ➡️", callback_data="del_page_next"))

    keyboard = [nav_buttons] if nav_buttons else []
    keyboard.append([InlineKeyboardButton("❌ CANCELAR", callback_data="cancel_deletion")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.effective_chat.send_message(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

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
    """Configura los comandos en el botón Menú de Telegram"""
    await application.bot.set_my_commands([
        BotCommand("start", "Reactivar el bot"),
        BotCommand("listar", "Ver últimos archivos"),
        BotCommand("explorar", "Ver carpetas y archivos"),
        BotCommand("buscar", "Búsqueda por nombre"),
        BotCommand("buscar_ia", "Búsqueda inteligente"),
        BotCommand("eliminar", "Borrar registros")

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



if __name__ == '__main__':
    print_server_welcome()
    if not os.path.exists("descargas"):
        os.makedirs("descargas")
    
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).post_init(post_init).build()
    
    # --- NO OLVIDES REGISTRARLOS ---
    # Comandos principales
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("listar", list_files_command))
    app.add_handler(CommandHandler("buscar", search_command))
    app.add_handler(CommandHandler("buscar_ia", search_ia_command))
    app.add_handler(CommandHandler("eliminar", delete_command))
    app.add_handler(CommandHandler("explorar", explorar))
    app.add_handler(CommandHandler(["cancelar", "salir", "stop"], cancelar_handler))
        
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