import os
import asyncio
import threading
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, request, flash, Response, stream_with_context
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# Núcleo del proyecto
from src.database.db_handler import DatabaseHandler
from indexador import ejecutar_indexacion_completa, ejecutar_indexacion_paso_a_paso
from src.services.dropbox_service import DropboxService
from src.services.google_drive_service import GoogleDriveService
from refresh_drive_token import refresh_google_token

# Inicialización
db = DatabaseHandler()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_key_only")

# -------------------------------
# CONFIGURACIÓN LOGIN
# -------------------------------

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


# Funciones útiles en plantillas
@app.context_processor
def inject_utils():
    return dict(
        now=datetime.now(),
        hasattr=hasattr
    )


# -------------------------------
# AUTENTICACIÓN
# -------------------------------

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


# -------------------------------
# DASHBOARD
# -------------------------------

@app.route('/dashboard')
@login_required
def dashboard():
    try:
        with db._connect() as conn:
            with conn.cursor() as cur:
                # Total archivos
                cur.execute("SELECT COUNT(*) FROM files")
                total_db = cur.fetchone()[0]

                # Con embeddings válidos
                cur.execute("""
                    SELECT COUNT(*) FROM files 
                    WHERE embedding IS NOT NULL
                    AND embedding NOT IN ('', '[]', 'error_limit')
                """)
                count_ia = cur.fetchone()[0]

                # Pendientes reales
                cur.execute("""
                    SELECT COUNT(*) FROM files 
                    WHERE embedding IS NULL 
                    OR embedding IN ('', '[]', 'error_limit')
                """)
                count_pending = cur.fetchone()[0]

                # Multimedia
                cur.execute("""
                    SELECT COUNT(*) FROM files
                    WHERE type IN ('🖼️ Foto', '🎥 Video', 'jpg', 'png', 'jpeg')
                    OR name ILIKE '%.jpg'
                    OR name ILIKE '%.png'
                    OR name ILIKE '%.jpeg'
                """)
                count_fotos = cur.fetchone()[0]

        # 🔎 Estados reales
        db_status = db.test_connection()

        # Drive status (simple prueba)
        try:
            drive_status = refresh_google_token()
        except:
            drive_status = False

        # Dropbox status (puedes hacer método test_connection si quieres)
        dropbox_status = True  # placeholder real si tienes método

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
        print(f"Error dashboard: {e}")
        return render_template("dashboard.html",
                               total_total=0,
                               total_ia=0,
                               total_fotos=0,
                               total_pending=0,
                               db_status=False,
                               drive_status=False,
                               dropbox_status=False)
# -------------------------------
# GESTIÓN DE ARCHIVOS
# -------------------------------

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

        try:
            if service == 'dropbox':
                success_cloud = asyncio.run(
                    DropboxService.delete_file(f"/{name}")
                )
            elif service == 'drive':
                success_cloud = asyncio.run(
                    GoogleDriveService.delete_file(name)
                )
        except Exception:
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
    def thread_wrapper():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(ejecutar_indexacion_completa())
        finally:
            loop.close()

    threading.Thread(target=thread_wrapper, daemon=True).start()
    return {"status": "success"}, 200


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


@app.route('/download-db')
@login_required
def download_db():
    try:
        sql_content = db.export_to_sql()
        return Response(
            sql_content,
            mimetype="application/sql",
            headers={
                "Content-disposition":
                f"attachment; filename=backup_{datetime.now().strftime('%Y%m%d')}.sql"
            }
        )
    except Exception as e:
        flash(f"Error: {e}", "error")
        return redirect(url_for('dashboard'))


# -------------------------------
# PERFIL
# -------------------------------

@app.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        nueva_pass = request.form.get('new_password')

        if nombre:
            db.update_user_name(current_user.id, nombre)
            current_user.nombre = nombre  # actualizar sesión

        if nueva_pass and len(nueva_pass) >= 6:
            hash_p = generate_password_hash(nueva_pass, method='scrypt')
            db.update_user_password(current_user.id, hash_p)
            flash("Contraseña actualizada con éxito.", "success")

        flash("Perfil actualizado correctamente.", "success")
        return redirect(url_for('perfil'))

    user_data = db.get_user_by_id(current_user.id)
    return render_template('profile.html', user=user_data)


# -------------------------------
# UTILIDADES
# -------------------------------

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


@app.route('/fix-drive-token', methods=['POST'])
@login_required
def fix_drive_token():
    if refresh_google_token():
        flash("Conexión con Google Drive restaurada correctamente.", "success")
    else:
        flash("No se pudo restaurar la conexión. Verifica las credenciales.", "error")
    return redirect(url_for('dashboard'))


@app.route('/archivos-errores')
@login_required
def archivos_errores():
    try:
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


# -------------------------------
# RUN
# -------------------------------

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5050))
    app.run(host='0.0.0.0', port=port)