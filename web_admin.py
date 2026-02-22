from flask import Flask, render_template, redirect, url_for, request, flash, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
import threading

# Importaciones de tu proyecto
# from src.database.db_handler_local import DatabaseHandler
from src.database.db_handler import DatabaseHandler
from indexador import ejecutar_indexacion_completa, ejecutar_indexacion_paso_a_paso

db = DatabaseHandler()
app = Flask(__name__)
# Asegúrate de definir esta clave en tu archivo .env
app.secret_key = os.getenv("FLASK_SECRET_KEY", "una_clave_muy_segura_123")

# Configuración de Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Redirige aquí si no está logueado

class User(UserMixin):
    def __init__(self, id, email, password):
        self.id = id
        self.email = email
        self.password = password

@login_manager.user_loader
def load_user(user_id):
    user_data = db.get_user_by_id(user_id)
    if user_data:
        # sqlite3.Row permite acceder por nombre de columna
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
        
        # Conexión compatible con Supabase
        with db._connect() as conn:
            with conn.cursor() as cur:
                # 1. Archivos procesados con éxito
                cur.execute("""
                    SELECT COUNT(*) FROM files 
                    WHERE embedding IS NOT NULL 
                    AND embedding NOT IN ('', '[]', 'error_limit')
                """)
                count_ia = cur.fetchone()[0]
                
                # 2. Conteo de imágenes (ILIKE es mejor para Postgres)
                cur.execute("""
                    SELECT COUNT(*) FROM files 
                    WHERE name ILIKE '%.jpg' OR name ILIKE '%.png' 
                    OR name ILIKE '%.jpeg' OR name ILIKE '%.webp'
                """)
                count_fotos = cur.fetchone()[0]
                
                # 3. Total absoluto
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
        db.delete_file_by_id(file_id) 
        flash(f"✅ Archivo eliminado correctamente.", "success")
    except Exception as e:
        flash(f"❌ Error al eliminar: {e}", "error")
    return redirect(url_for('dashboard'))

# --- RUTAS DEL INDEXADOR ---

@app.route('/run-indexer', methods=['POST'])
@login_required
def run_indexer():
    try:
        resultado = ejecutar_indexacion_completa()
        flash(f"✅ {resultado}", "success")
    except Exception as e:
        flash(f"❌ Error al ejecutar el indexador: {e}", "error")
    return redirect(url_for('dashboard'))

@app.route('/progress-indexer')
@login_required
def progress_indexer():
    # Retorna el generador de pasos para el EventSource del frontend
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
        elif nueva_pass:
            flash("La contraseña debe tener al menos 6 caracteres.", "warning")
        
        flash("Perfil actualizado correctamente.", "success")
        return redirect(url_for('perfil'))
    
    user_data = db.get_user_by_id(current_user.id)
    return render_template('profile.html', user=user_data)

# --- MANTENIMIENTO ---

@app.route('/run-indexer', methods=['POST'])
@login_required
def run_indexer():
    # Lanzar en segundo plano para no bloquear la web
    thread = threading.Thread(target=asyncio.run, args=(ejecutar_indexacion_completa(),))
    thread.start()
    flash("🚀 Indexación iniciada en segundo plano. Los resultados aparecerán en breve.", "info")
    return redirect(url_for('dashboard'))

@app.route('/reset-errors', methods=['POST'])
@login_required
def reset_errors():
    try:
        db.reset_failed_embeddings()
        flash("♻️ Se han reseteado los archivos fallidos. Puedes reintentar la indexación.", "info")
    except Exception as e:
        flash(f"Error al resetear: {e}", "error")
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    # Railway asigna el puerto automáticamente en la variable PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)