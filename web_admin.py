import os
import json
import asyncio
import threading
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, request, flash, Response, stream_with_context

from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect

# --- NÚCLEO DEL PROYECTO ---
from src.database.db_handler import DatabaseHandler
from src.scripts.indexador import ejecutar_indexacion_completa, ejecutar_indexacion_paso_a_paso, ejecutar_embeddings_batch_sse
from src.services.dropbox_service import DropboxService
from src.services.google_drive_service import GoogleDriveService
from src.scripts.refresh_drive_token import refresh_google_token

# Inicialización
db = DatabaseHandler()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_key_only")
csrf = CSRFProtect(app)

# --- LOGO / FAVICON HANDLING ------------------------------------------------
# the admin logo may now live inside the static folder (static/logo/logo.JPG)
# so we look there first, otherwise fall back to the project root. if a
# source image is found we generate a favicon.ico in the static dir. no
# additional copying is needed when the file already lives under static.
possible_sources = [
    os.path.join(app.static_folder, "logo", "logo.JPG"),
    os.path.join(os.getcwd(), "logo.JPG")
]
source_logo = None
for p in possible_sources:
    if os.path.exists(p):
        source_logo = p
        break

if source_logo:
    # ensure a copy exists at static/logo.JPG for backwards compatibility
    static_root_logo = os.path.join(app.static_folder, "logo.JPG")
    try:
        if source_logo != static_root_logo and not os.path.exists(static_root_logo):
            import shutil
            shutil.copy(source_logo, static_root_logo)
    except Exception as e:
        print(f"⚠️ No se pudo copiar logo al static root: {e}")

    # if requested, also produce a transparent PNG version for modern clients
    try:
        from PIL import Image
        img = Image.open(source_logo).convert("RGBA")
        datas = img.getdata()
        new_data = []
        for item in datas:
            # treat near-white as transparent (simple heuristic)
            if item[0] > 240 and item[1] > 240 and item[2] > 240:
                new_data.append((255, 255, 255, 0))
            else:
                new_data.append(item)
        img.putdata(new_data)
        png_path = os.path.join(app.static_folder, "logo", "logo.png")
        img.save(png_path, "PNG")
    except Exception as e:
        print(f"⚠️ Error generando logo.png transparente: {e}")

    # generate favicon if missing, using the resolved source
    favicon_path = os.path.join(app.static_folder, "favicon.ico")
    if not os.path.exists(favicon_path):
        try:
            img = Image.open(source_logo)
            img.save(favicon_path, format='ICO')
        except Exception as e:
            print(f"⚠️ Error generando favicon: {e}")

# --- CONFIGURACIÓN LOGIN ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, email, password_hash, nombre=None):
        self.id = id
        self.email = email
        self.password_hash = password_hash
        self.nombre = nombre if nombre else "Administrador"

@login_manager.user_loader
def load_user(user_id):
    user_data = db.get_user_by_id(user_id)
    if user_data:
        return User(
            user_data['id'],
            user_data['email'],
            user_data['password_hash'],
            nombre=user_data.get('nombre')
        )
    return None

# Registro de utilidades para Jinja2
@app.context_processor
def inject_utils():
    return dict(
        now=datetime.now(),
        hasattr=hasattr
    )

# --- RUTAS DE AUTENTICACIÓN ---

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user_row = db.get_user_by_email(email)

        if user_row and check_password_hash(user_row['password_hash'], password):
            user_obj = User(
                user_row['id'],
                user_row['email'],
                user_row['password_hash'],
                nombre=user_row.get('nombre')
            )
            login_user(user_obj)
            flash('¡Bienvenido al panel administrativo!', 'success')
            return redirect(url_for('dashboard'))

        flash('Credenciales inválidas.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Sesión cerrada.', 'info')
    return redirect(url_for('login'))

# --- PANEL PRINCIPAL (DASHBOARD) ---

@app.route('/dashboard')
@login_required
def dashboard():
    try:
        with db._connect() as conn:
            with conn.cursor() as cur:
                # 1. Total archivos
                cur.execute("SELECT COUNT(*) FROM files")
                total_db = cur.fetchone()[0]

                # 2. Indexados IA (Embeddings válidos)
                cur.execute("""
                    SELECT COUNT(*) FROM files 
                    WHERE embedding IS NOT NULL
                    AND embedding NOT IN ('', '[]', 'error_limit')
                """)
                count_ia = cur.fetchone()[0]

                # 3. Pendientes reales
                cur.execute("""
                    SELECT COUNT(*) FROM files 
                    WHERE embedding IS NULL 
                    OR embedding IN ('', '[]', 'error_limit')
                """)
                count_pending = cur.fetchone()[0]

                # 4. Multimedia
                cur.execute("""
                    SELECT COUNT(*) FROM files
                    WHERE type IN ('🖼️ Foto', '🎥 Video', 'jpg', 'png', 'jpeg')
                    OR name ILIKE '%.jpg' OR name ILIKE '%.png' OR name ILIKE '%.jpeg'
                """)
                count_fotos = cur.fetchone()[0]

        # Diagnóstico de estados
        db_status = db.check_connection()
        try:
            # Verificamos si el token de Drive es funcional o renovable
            drive_status = refresh_google_token()
        except:
            drive_status = False
        
        dropbox_status = True  # Status base para Dropbox

        return render_template(
            "dashboard.html",
            total_total=total_db,
            total_ia=count_ia,
            total_fotos=count_fotos,
            total_pending=count_pending,
            db_status=db_status,
            drive_status=drive_status,
            dropbox_status=dropbox_status
        )

    except Exception as e:
        print(f"❌ Error dashboard: {e}")
        return render_template("dashboard.html",
                               total_total=0, total_ia=0, total_fotos=0, total_pending=0,
                               db_status=False, drive_status=False, dropbox_status=False)

# --- GESTIÓN DE ARCHIVOS ---

@app.route('/delete/<int:file_id>')
@login_required
def delete_file(file_id):
    try:
        file_info = db.get_file_by_id(file_id)
        if not file_info:
            flash("Archivo no encontrado.", "error")
            return redirect(url_for('dashboard'))

        name = file_info['name']
        service = file_info['service']
        success_cloud = False

        # Manejo de llamadas asíncronas para servicios Cloud
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            if service == 'dropbox':
                success_cloud = loop.run_until_complete(DropboxService.delete_file(f"/{name}"))
            elif service == 'drive':
                success_cloud = loop.run_until_complete(GoogleDriveService.delete_file(name))
            loop.close()
        except Exception as e:
            print(f"⚠️ Error Cloud Delete: {e}")
            success_cloud = False

        db.delete_file_by_id(file_id)
        msg_cloud = "y de la nube ✅" if success_cloud else "(solo de la base de datos ⚠️)"
        flash(f"Archivo `{name}` eliminado {msg_cloud}.", "success")

    except Exception as e:
        flash(f"❌ Error al eliminar: {e}", "error")
    return redirect(url_for('dashboard'))

@app.route('/reset-errors', methods=['POST'])
@login_required
def reset_errors():
    try:
        db.reset_failed_embeddings()
        flash("♻️ Archivos marcados con error reseteados correctamente.", "info")
    except Exception as e:
        flash(f"Error al resetear: {e}", "error")
    return redirect(url_for('dashboard'))

@app.route('/reset-all', methods=['POST'])
@login_required
def reset_all():
    try:
        db.reset_all_embeddings()
        db.log_event("WARNING", "SISTEMA", "Reinicio total de Embeddings e IA ejecutado manualmente.")
        flash("♻️ Todos los embeddings y resúmenes han sido borrados. Ejecuta el Indexador IA para recalcular todo.", "warning")
    except Exception as e:
        flash(f"Error al resetear todo: {e}", "error")
    return redirect(url_for('dashboard'))

@app.route('/clean-corrupted', methods=['POST'])
@login_required
def clean_corrupted():
    try:
        affected = db.clean_corrupted_files()
        db.log_event("INFO", "SISTEMA", f"Limpieza de archivos corruptos ejecutada manually. {affected} archivos limpiados.")
        flash(f"🧹 Se han eliminado los rastros de IA de {affected} archivos que estaban dañados. Ejecuta el indexador para re-procesarlos.", "success")
    except Exception as e:
        flash(f"Error al limpiar archivos: {e}", "error")
    return redirect(url_for('dashboard'))

@app.route('/run-indexer', methods=['POST'])
@login_required
def run_indexer_endpoint():
    def thread_wrapper():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(ejecutar_indexacion_completa())
        finally:
            loop.close()

    threading.Thread(target=thread_wrapper, daemon=True).start()
    return {"status": "success"}, 200

@app.route('/run-categorizer', methods=['POST'])
@login_required
def run_categorizer_endpoint():
    """Inicia en background el script de categorización de archivos ya en la nube con SSE logging."""
    from queue import Queue
    
    if not hasattr(app, 'categorizer_queue'):
        app.categorizer_queue = Queue()
    else:
        # Limpiar queue anterior si existe
        try:
            while not app.categorizer_queue.empty():
                app.categorizer_queue.get_nowait()
        except:
            pass
    
    def thread_wrapper():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            async def run_categorizer_async():
                try:
                    from src.scripts.categorize_with_logs import categorize_with_logs
                    async for message in categorize_with_logs():
                        app.categorizer_queue.put(message)
                    app.categorizer_queue.put(None)  # Señal de fin
                except ImportError as e:
                    app.categorizer_queue.put(f"[ERROR] Importación fallida: {str(e)}")
                    app.categorizer_queue.put(None)
                except Exception as e:
                    import traceback
                    error_msg = f"[ERROR] {str(e)}"
                    app.categorizer_queue.put(error_msg)
                    app.categorizer_queue.put(None)
            
            loop.run_until_complete(run_categorizer_async())
        except Exception as e:
            import traceback
            print(f"[ERROR en thread] {str(e)}")
            traceback.print_exc()
            app.categorizer_queue.put(f"[ERROR] Fallo en thread: {str(e)}")
            app.categorizer_queue.put(None)
        finally:
            loop.close()

    threading.Thread(target=thread_wrapper, daemon=True).start()
    return {"status": "success", "message": "Categorización iniciada"}, 200

@app.route('/progress-categorizer')
@login_required
def progress_categorizer():
    """SSE endpoint para streaming de logs del categorizer."""
    def generate():
        if not hasattr(app, 'categorizer_queue'):
            yield f"data: {json.dumps({'message': '[INFO] No hay categorización en progreso'})}\n\n"
            return
        
        while True:
            try:
                message = app.categorizer_queue.get(timeout=1)
                if message is None:  # Fin del proceso
                    yield f"data: {json.dumps({'message': '[FINALIZADO] ✅ Categorización completada'})}\n\n"
                    break
                yield f"data: {json.dumps({'message': message})}\n\n"
            except:
                # Queue vacío o timeout
                continue
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/progress-indexer')

@login_required
def progress_indexer():
    def generate():
        loop = asyncio.new_event_loop()
        gen = ejecutar_indexacion_paso_a_paso()
        try:
            while True:
                try:
                    msg = loop.run_until_complete(anext(gen))
                    yield msg
                except StopAsyncIteration:
                    break
        finally:
            loop.close()
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/run-embeddings', methods=['POST'])
@login_required
def run_embeddings_endpoint():
    """Inicia generación de embeddings pendientes con límite configurable."""
    try:
        limite = int(request.form.get('limite', 10))
    except (ValueError, TypeError):
        limite = 10

    if not hasattr(app, 'embed_queue'):
        app.embed_queue = None

    from queue import Queue
    app.embed_queue = Queue()
    app.embed_limite = limite

    def thread_wrapper():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def run():
                from src.scripts.indexador import generar_embeddings_pendientes

                async def cb(msg):
                    app.embed_queue.put(msg)

                result = await generar_embeddings_pendientes(limite, cb)
                app.embed_queue.put(f"✅ Finalizado: {result['procesados']} embeddings, {result['errores']} errores.")
                app.embed_queue.put(None)  # Señal de fin

            loop.run_until_complete(run())
        except Exception as e:
            app.embed_queue.put(f"[❌ ERROR] {e}")
            app.embed_queue.put(None)
        finally:
            loop.close()

    threading.Thread(target=thread_wrapper, daemon=True).start()
    return {"status": "success", "limite": limite}, 200


@app.route('/progress-embeddings')
@login_required
def progress_embeddings():
    """SSE endpoint para streaming del progreso de embeddings."""
    def generate():
        if not hasattr(app, 'embed_queue') or app.embed_queue is None:
            yield f"data: {json.dumps({'message': 'No hay proceso en ejecución.'})}\n\n"
            return

        while True:
            try:
                msg = app.embed_queue.get(timeout=1)
                if msg is None:
                    yield f"data: {json.dumps({'message': '[✅ FINALIZADO] Proceso completado'})}\n\n"
                    break
                yield f"data: {json.dumps({'message': msg})}\n\n"
            except Exception:
                continue

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/download-db')
@login_required
def download_db():
    try:
        sql_content = db.export_to_sql()
        return Response(
            sql_content,
            mimetype="application/sql",
            headers={"Content-disposition": f"attachment; filename=backup_{datetime.now().strftime('%Y%m%d')}.sql"}
        )
    except Exception as e:
        flash(f"Error al exportar: {e}", "error")
        return redirect(url_for('dashboard'))

@app.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        nueva_pass = request.form.get('new_password')

        if nombre:
            db.update_user_name(current_user.id, nombre)
            current_user.nombre = nombre

        if nueva_pass and len(nueva_pass) >= 6:
            hash_p = generate_password_hash(nueva_pass, method='scrypt')
            db.update_user_password(current_user.id, hash_p)
            flash("Contraseña actualizada.", "success")

        flash("Perfil actualizado correctamente.", "success")
        return redirect(url_for('perfil'))

    user_data = db.get_user_by_id(current_user.id)
    return render_template('profile.html', user=user_data)

@app.route('/status-check')
@login_required
def status_check():
    try:
        db_ok = db.check_connection()
        msg = "Sistema Operativo ✅" if db_ok else "Error de conexión a DB ❌"
        flash(msg, "info" if db_ok else "error")
    except Exception as e:
        flash(f"Error de diagnóstico: {e}", "error")
    return redirect(url_for('dashboard'))

@app.route('/fix-drive-token', methods=['POST'])
@login_required
def fix_drive_token():
    if refresh_google_token():
        flash("Conexión con Google Drive restaurada correctamente.", "success")
    else:
        flash("No se pudo restaurar la conexión. Revisa tus variables en Railway.", "error")
    return redirect(url_for('dashboard'))

@app.route('/archivos-errores')
@login_required
def archivos_errores():
    try:
        with db._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, cloud_url, service, created_at, content_text
                    FROM files
                    WHERE embedding IS NULL
                    OR embedding IN ('', '[]', 'error_limit')
                    ORDER BY created_at DESC
                """)
                rows = cur.fetchall()
        return render_template('archivos_errores.html', files=rows)
    except Exception as e:
        flash(f"Error al cargar lista: {e}", "error")
        return redirect(url_for('dashboard'))


@app.route('/embed-single/<int:file_id>', methods=['POST'])
@login_required
def embed_single(file_id):
    """Genera el embedding para un archivo individual. Devuelve JSON."""
    import json as _json
    import numpy as _np

    try:
        # 1. Leer el registro de la BD
        with db._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, service, content_text FROM files WHERE id = %s",
                    (file_id,)
                )
                row = cur.fetchone()

        if not row:
            return _json.dumps({"ok": False, "error": "Archivo no encontrado"}), 404

        fid, name, service, content_text = row

        # 2. Correr el análisis IA en un hilo (Flask es síncrono)
        result = {"ok": False, "error": "Sin resultado"}

        def run_async():
            nonlocal result
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def _process():
                    from src.scripts.indexador import generar_embeddings_pendientes
                    reporte = await generar_embeddings_pendientes(limite=1,
                        progreso_callback=None)
                    # Llamar directamente al handler de un solo archivo
                    from src.utils.ai_handler import AIHandler
                    import os

                    texto = content_text
                    if not texto or len(texto.strip()) < 10:
                        # Sin texto: intentar descargar y analizar
                        from src.services.dropbox_service import DropboxService
                        from src.services.google_drive_service import GoogleDriveService
                        local_path = os.path.join("descargas", name)
                        if not os.path.exists("descargas"):
                            os.makedirs("descargas")
                        try:
                            svc = DropboxService(
                                app_key=os.getenv("DROPBOX_APP_KEY"),
                                app_secret=os.getenv("DROPBOX_APP_SECRET"),
                                refresh_token=os.getenv("DROPBOX_REFRESH_TOKEN")
                            )
                            ok = False
                            if service == 'dropbox':
                                try:
                                    ok = await svc.download_file(f"/{name}", local_path)
                                except Exception as e:
                                    if "not_found" in str(e).lower():
                                        return {"ok": False, "error": f"Archivo '{name}' no encontrado en Dropbox. Posiblemente fue borrado manualmentne."}
                                    raise e
                            else:
                                drive = GoogleDriveService()
                                ok = await drive.download_file_by_name(name, local_path)
                                if not ok:
                                    return {"ok": False, "error": f"Archivo '{name}' no encontrado en Google Drive."}
                            
                            if ok and os.path.exists(local_path):
                                texto = await AIHandler.extract_text(local_path)
                        except Exception as dl_err:
                            return {"ok": False, "error": f"Error de conexión con la nube: {dl_err}"}
                        finally:
                            if os.path.exists(local_path):
                                try: os.remove(local_path)
                                except: pass

                    if not texto or len(texto.strip()) < 10:
                        return {"ok": False, "error": "No se pudo extraer texto del archivo"}

                    # Truncar a 15000 caracteres
                    texto = texto[:15000]

                    # Generar embedding y resumen en paralelo
                    resumen, vector = await asyncio.gather(
                        AIHandler.generate_summary(texto),
                        AIHandler.get_embedding(texto)
                    )

                    if not vector:
                        return {"ok": False, "error": "La IA no pudo generar el embedding"}

                    # Guardar en BD
                    emb_str = _json.dumps(
                        vector.tolist() if isinstance(vector, _np.ndarray) else vector
                    )
                    with db._connect() as conn2:
                        with conn2.cursor() as cur2:
                            cur2.execute("""
                                UPDATE files
                                SET embedding    = %s,
                                    summary      = COALESCE(%s, summary),
                                    content_text = COALESCE(%s, content_text)
                                WHERE id = %s
                            """, (emb_str, resumen, texto, fid))
                        conn2.commit()

                    return {"ok": True, "dims": len(vector), "summary": resumen[:120] if resumen else ""}

                result = loop.run_until_complete(_process())
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            finally:
                loop.close()

        t = threading.Thread(target=run_async)
        t.start()
        t.join(timeout=60)  # máximo 60 segundos de espera

        return _json.dumps(result), (200 if result.get("ok") else 500), {"Content-Type": "application/json"}

    except Exception as e:
        return _json.dumps({"ok": False, "error": str(e)}), 500, {"Content-Type": "application/json"}


@app.route('/embed-single-stream/<int:file_id>')
@login_required
def embed_single_stream(file_id):
    """SSE endpoint para procesar un solo archivo con logs en tiempo real."""
    from queue import Queue
    local_queue = Queue()

    def thread_wrapper():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _process():
                # Obtener datos del archivo
                row = db.get_file_by_id(file_id)
                if not row:
                    local_queue.put(f"❌ Error: Archivo ID {file_id} no encontrado.")
                    local_queue.put(None)
                    return

                # RealDictCursor devuelve dict, pero db.get_file_by_id parece devolver tupla en algunos hilos?
                # Revisemos db_handler.py: get_file_by_id usa fetchone() sin cursor_factory en la línea 327
                # Pero en la línea 331 dice "Esto devolverá un diccionario gracias al RealDictCursor" (comentario erróneo?)
                # Hagamos una extracción segura:
                if isinstance(row, dict):
                    fid, name, service, cloud_url, content_text = row['id'], row['name'], row['service'], row['cloud_url'], row.get('content_text')
                else:
                    fid, name, service, cloud_url = row[:4]
                    content_text = row[4] if len(row) > 4 else None

                from src.scripts.indexador import procesar_un_archivo_core
                
                async def log_cb(msg):
                    local_queue.put(msg)

                local_queue.put(f"🚀 Iniciando proceso para: {name}...")
                ok = await procesar_un_archivo_core(fid, name, service, cloud_url, content_text, log_cb)
                
                if ok:
                    local_queue.put("[COMPLETED]")
                else:
                    local_queue.put("⚠️ El proceso terminó con errores.")
                local_queue.put(None)

            loop.run_until_complete(_process())
        except Exception as e:
            local_queue.put(f"❌ Error crítico: {str(e)}")
            local_queue.put(None)
        finally:
            loop.close()

    threading.Thread(target=thread_wrapper, daemon=True).start()

    def generate():
        while True:
            # Timeout para no quedarse bloqueado eternamente si algo falla en el hilo
            try:
                msg = local_queue.get(timeout=120)
                if msg is None: break
                yield f"data: {json.dumps({'message': msg})}\n\n"
            except:
                yield f"data: {json.dumps({'message': '❌ Timeout de conexión con el servidor.'})}\n\n"
                break

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/db-explorer')
@login_required
def db_explorer_list():
    """Lista las tablas disponibles en la base de datos."""
    try:
        with db._connect() as conn:
            with conn.cursor() as cur:
                # Consultar tablas públicas en Postgres
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
                tables = [row[0] for row in cur.fetchall()]
        return render_template('db_explorer.html', tables=tables)
    except Exception as e:
        flash(f"Error al listar tablas: {e}", "danger")
        return redirect(url_for('dashboard'))

@app.route('/db-explorer/<table_name>')
@login_required
def db_explorer_table(table_name):
    """Muestra el contenido de una tabla específica."""
    try:
        # Prevención básica de inyección: solo permitir nombres alfanuméricos y guiones
        import re
        if not re.match(r'^[a-zA-Z0-9_]+$', table_name):
             flash("Nombre de tabla inválido", "danger")
             return redirect(url_for('db_explorer_list'))

        from psycopg2.extras import RealDictCursor
        with db._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # LIMIT 500 para no saturar el navegador si la tabla es gigante
                cur.execute(f"SELECT * FROM {table_name} LIMIT 500")
                data = cur.fetchall()
                
                # Obtener nombres de columnas
                columns = data[0].keys() if data else []
                
        return render_template('db_explorer.html', 
                               table_name=table_name, 
                               columns=columns, 
                               data=data,
                               total_rows=len(data))
    except Exception as e:
        flash(f"Error al leer la tabla {table_name}: {e}", "danger")
        return redirect(url_for('db_explorer_list'))

@app.route('/logs')
@login_required
def logs():
    level = request.args.get('level', 'ALL')
    module = request.args.get('module', '')
    limit = request.args.get('limit', 100)
    
    logs_data = db.get_logs(limit=limit, level=level, module=module if module else None)
    return render_template('logs.html', logs=logs_data, current_level=level, current_module=module, limit=limit)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5050))
    app.run(host='0.0.0.0', port=port)