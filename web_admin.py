from flask import Flask, render_template, redirect, url_for, request, flash, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
import threading
import asyncio

# Importaciones de tu proyecto
from src.database.db_handler import DatabaseHandler
from indexador import ejecutar_indexacion_completa, ejecutar_indexacion_paso_a_paso
from src.services.dropbox_service import DropboxService
from src.services.google_drive_service import GoogleDriveService

db = DatabaseHandler()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

# Configuración de Login
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

# --- RUTAS DE NAVEGACIÓN BÁSICA ---

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
            flash('¡Bienvenido de nuevo!', 'success')
            return redirect(url_for('dashboard'))
        
        flash('Email o contraseña incorrectos', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Has cerrado sesión.', 'info')
    return redirect(url_for('login'))

# --- RUTAS DEL PANEL (ADMIN) ---

@app.route('/dashboard')
@login_required
def dashboard():
    try:
        recent_files = db.get_last_files(20)
        with db._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM files 
                    WHERE embedding IS NOT NULL 
                    AND embedding NOT IN ('', '[]', 'error_limit')
                """)
                count_ia = cur.fetchone()[0]
                
                cur.execute("""
                    SELECT COUNT(*) FROM files 
                    WHERE name ILIKE '%.jpg' OR name ILIKE '%.png' 
                    OR name ILIKE '%.jpeg' OR name ILIKE '%.webp'
                """)
                count_fotos = cur.fetchone()[0]
                
                cur.execute("SELECT COUNT(*) FROM files")
                total_db = cur.fetchone()[0]
        
        return render_template('dashboard.html', 
                             files=recent_files, 
                             total_ia=count_ia, 
                             total_fotos=count_fotos,
                             total_total=total_db)
    except Exception as e:
        print(f"❌ Error Dashboard: {e}")
        return render_template('dashboard.html', files=[], total_ia=0, total_fotos=0, total_total=0)

@app.route('/delete/<int:file_id>')
@login_required
def delete_file(file_id):
    try:
        # 1. Obtener info del archivo antes de borrarlo de la DB
        file_info = db.get_file_by_id(file_id)
        if not file_info:
            flash("Archivo no encontrado.", "error")
            return redirect(url_for('dashboard'))

        name = file_info['name']
        service = file_info['service']

        # 2. Borrado físico en la nube
        success_cloud = False
        if service == 'dropbox':
            # Dropbox usa el path con /
            success_cloud = asyncio.run(DropboxService.delete_file(f"/{name}"))
        elif service == 'drive':
            success_cloud = asyncio.run(GoogleDriveService.delete_file(name))

        # 3. Borrado en Base de Datos
        db.delete_file_by_id(file_id) 
        
        status = "y de la nube ✅" if success_cloud else "(solo de la DB ⚠️)"
        flash(f"Archivo `{name}` eliminado {status}.", "success")
    except Exception as e:
        flash(f"❌ Error al eliminar: {e}", "error")
    return redirect(url_for('dashboard'))

# --- RUTAS DEL INDEXADOR (UNIFICADA) ---

@app.route('/run-indexer', methods=['POST'])
@login_required
def run_indexer_endpoint():
    """Lanza la indexación en segundo plano para evitar timeouts en Railway"""
    try:
        # Usamos un hilo para que la respuesta web sea instantánea
        def run_async_indexer():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(ejecutar_indexacion_completa())
            loop.close()

        thread = threading.Thread(target=run_async_indexer)
        thread.start()
        
        flash("🚀 Indexación iniciada. Los nuevos archivos aparecerán en unos minutos.", "info")
    except Exception as e:
        flash(f"❌ Error al iniciar el indexador: {e}", "error")
    return redirect(url_for('dashboard'))

@app.route('/progress-indexer')
@login_required
def progress_indexer():
    return Response(ejecutar_indexacion_paso_a_paso(), mimetype='text/event-stream')

# --- CONFIGURACIÓN DE PERFIL ---

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

# --- MANTENIMIENTO ---

@app.route('/reset-errors', methods=['POST'])
@login_required
def reset_errors():
    try:
        db.reset_failed_embeddings()
        flash("♻️ Se han reseteado los archivos fallidos.", "info")
    except Exception as e:
        flash(f"Error al resetear: {e}", "error")
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5050))
    app.run(host='0.0.0.0', port=port)