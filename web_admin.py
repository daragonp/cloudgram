import os
import threading
import asyncio
import csv
from io import StringIO
from datetime import datetime

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

# --- INDEXADOR IA ---

@app.route('/run-indexer', methods=['POST'])
@login_required
def run_indexer_endpoint():
    try:
        def run_sync():
            # Importamos aquí para evitar ciclos
            from indexador import ejecutar_indexacion_completa
            asyncio.run(ejecutar_indexacion_completa())

        thread = threading.Thread(target=run_sync, daemon=True)
        thread.start()
        flash("🚀 Indexación iniciada en segundo plano.", "info")
    except Exception as e:
        flash(f"❌ Error: {e}", "error")
    return redirect(url_for('dashboard'))

@app.route('/progress-indexer')
@login_required
def progress_indexer():
    return Response(ejecutar_indexacion_paso_a_paso(), mimetype='text/event-stream')

# --- PERFIL Y SISTEMA ---

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

@app.route('/download-db')
@login_required
def download_db():
    try:
        files_data = db.get_all_files()
        si = StringIO()
        cw = csv.writer(si)
        cw.writerow(['ID', 'Nombre', 'URL', 'Servicio', 'Tipo', 'Texto Extraído', 'Fecha'])
        
        for f in files_data:
            cw.writerow([f.get('id'), f.get('name'), f.get('cloud_url'), 
                         f.get('service'), f.get('type'), f.get('content_text'), 
                         f.get('created_at')])
        
        output = si.getvalue()
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename=backup_cloudgram_{datetime.now().strftime('%Y%m%d')}.csv"}
        )
    except Exception as e:
        flash(f"Error al exportar base de datos: {e}", "error")
        return redirect(url_for('dashboard'))

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

if __name__ == '__main__':
    # Puerto dinámico para despliegues tipo Railway/Heroku
    port = int(os.environ.get("PORT", 5050))
    app.run(host='0.0.0.0', port=port)