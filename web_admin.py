import os
import threading
import asyncio
from io import StringIO
from datetime import datetime

from flask import Response, stream_with_context


from flask import Flask, render_template, redirect, url_for, request, flash, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# Importaciones del núcleo del proyecto
from src.database.db_handler import DatabaseHandler
from indexador import ejecutar_indexacion_completa, ejecutar_indexacion_paso_a_paso
from src.services.dropbox_service import DropboxService
from src.services.google_drive_service import GoogleDriveService

# Inicialización
db = DatabaseHandler()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_key_only")

# --- CONFIGURACIÓN DE LOGIN ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, email, password):
        self.id = id
        self.email = email
        self.password = password

@login_manager.user_loader
def load_user(user_id):
    user_data = db.get_user_by_id(user_id)
    if user_data:
        return User(user_data['id'], user_data['email'], user_data['password_hash'])
    return None

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
            user_obj = User(user_row['id'], user_row['email'], user_row['password_hash'])
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
        # Usamos una sola conexión para obtener todas las métricas
        with db._connect() as conn:
            with conn.cursor() as cur:
                # 1. Total de archivos
                cur.execute("SELECT COUNT(*) FROM files")
                total_db = cur.fetchone()[0]

                # 2. Archivos indexados con IA (Embeddings)
                cur.execute("SELECT COUNT(*) FROM files WHERE embedding IS NOT NULL")
                count_ia = cur.fetchone()[0]
                
                # 3. Archivos multimedia (Fotos/Videos)
                cur.execute("""
                    SELECT COUNT(*) FROM files 
                    WHERE type IN ('🖼️ Foto', '🎥 Video', 'jpg', 'png', 'jpeg')
                """)
                count_fotos = cur.fetchone()[0]
        
        return render_template('dashboard.html', 
                             total_ia=count_ia, 
                             total_fotos=count_fotos,
                             total_total=total_db)
    except Exception as e:
        print(f"❌ Error al cargar dashboard: {e}")
        return render_template('dashboard.html', total_ia=0, total_fotos=0, total_total=0)

# --- GESTIÓN DE ARCHIVOS Y MANTENIMIENTO ---

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

        # Intento de borrado en la nube
        success_cloud = False
        try:
            if service == 'dropbox':
                success_cloud = asyncio.run(DropboxService.delete_file(f"/{name}"))
            elif service == 'drive':
                success_cloud = asyncio.run(GoogleDriveService.delete_file(name))
        except:
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
        flash("♻️ Archivos marcados con error han sido reseteados para re-indexación.", "info")
    except Exception as e:
        flash(f"Error al resetear: {e}", "error")
    return redirect(url_for('dashboard'))

@app.route('/run-indexer', methods=['POST'])
@login_required
def run_indexer_endpoint():
    from indexador import ejecutar_indexacion_completa
    
    def thread_wrapper():
        # Creamos un nuevo evento de loop para este hilo específico
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(ejecutar_indexacion_completa())
        finally:
            loop.close()

    thread = threading.Thread(target=thread_wrapper, daemon=True)
    thread.start()
    return {"status": "success"}, 200

@app.route('/progress-indexer')
@login_required
def progress_indexer():
    # Adaptador para que Gunicorn (sync) acepte el generador de logs (async)
    def generate():
        loop = asyncio.new_event_loop()
        gen = ejecutar_indexacion_paso_a_paso()
        try:
            while True:
                try:
                    # Obtenemos el siguiente mensaje del generador
                    msg = loop.run_until_complete(anext(gen))
                    yield msg
                except StopAsyncIteration:
                    break
        finally:
            loop.close()
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/download-db')
@login_required
def download_db():
    try:
        sql_content = db.export_to_sql() # Asegúrate de tener este método en db_handler
        return Response(
            sql_content,
            mimetype="application/sql",
            headers={"Content-disposition": f"attachment; filename=backup_{datetime.now().strftime('%Y%m%d')}.sql"}
        )
    except Exception as e:
        flash(f"Error: {e}", "error")
        return redirect(url_for('dashboard'))

@app.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        nueva_pass = request.form.get('new_password')
        
        if nombre:
            db.update_user_name(current_user.id, nombre)
        
        if nueva_pass and len(nueva_pass) >= 6:
            hash_p = generate_password_hash(nueva_pass, method='scrypt')
            db.update_user_password(current_user.id, hash_p)
            flash("Contraseña actualizada con éxito.", "success")
        
        flash("Perfil actualizado correctamente.", "success")
        return redirect(url_for('perfil'))
    
    user_data = db.get_user_by_id(current_user.id)
    return render_template('profile.html', user=user_data)

@app.route('/status-check')
@login_required
def status_check():
    try:
        db_ok = db.test_connection()
        msg = "Sistema Operativo ✅" if db_ok else "Error de conexión a DB ❌"
        flash(msg, "info" if db_ok else "error")
    except Exception as e:
        flash(f"Error de diagnóstico: {e}", "error")
    return redirect(url_for('dashboard'))

@app.route('/archivos-errores')
@login_required
def archivos_errores():
    try:
        # Consultamos archivos donde el embedding es nulo o tiene marcadores de error
        with db._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, cloud_url, service, created_at 
                    FROM files 
                    WHERE embedding IS NULL 
                    OR embedding IN ('', '[]', 'error_limit')
                    ORDER BY created_at DESC
                """)
                rows = cur.fetchall()
        
        return render_template('archivos_errores.html', files=rows)
    except Exception as e:
        flash(f"Error al cargar lista de errores: {e}", "error")
        return redirect(url_for('dashboard'))

@app.route('/fix-drive-token', methods=['POST'])
@login_required
def fix_drive_token():
    try:
        # Aquí llamarías a la lógica de refresh_google_token()
        # O simplemente limpiarías los errores de la DB para forzar reintento
        from refresh_drive_token import refresh_google_token
        success = refresh_google_token()
        
        if success:
            flash("✅ Token de Google Drive actualizado correctamente.", "success")
        else:
            flash("⚠️ No se pudo autorizar automáticamente. Revisa credentials.json.", "warning")
            
    except Exception as e:
        flash(f"Error técnico: {e}", "error")
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    # Puerto dinámico para despliegues tipo Railway/Heroku
    port = int(os.environ.get("PORT", 5050))
    app.run(host='0.0.0.0', port=port)