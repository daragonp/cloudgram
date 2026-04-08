# app.py
import os
import threading
import time
import requests

# ==========================================
# 1. IMPORTAR EL PANEL WEB (FLASK)
# ==========================================
from web_admin import app as flask_app
app = flask_app # Alias para que Gunicorn encuentre 'app' en 'app.py'

def run_flask():
    # Render inyecta el puerto en la variable de entorno PORT. Es obligatorio usarla.
    port = int(os.environ.get("PORT", 8080))
    print(f"🌐 Panel Web iniciándose en el puerto {port}...")
    # use_reloader=False es CLAVE para que Render no ejecute esto dos veces
    flask_app.run(host='0.0.0.0', port=port, threaded=True, use_reloader=False)

# ==========================================
# 4. INICIO DEL SISTEMA
# ==========================================
if __name__ == '__main__':
    if not os.path.exists("descargas"):
        os.makedirs("descargas")

    print("ℹ️ Iniciando en modo LOCAL / MONOLÍTICO (Bot + Web)")
    
    # 1. Lanzamos el Panel Web en un hilo invisible
    # Nota: web_admin.py ya maneja su propio keep_alive si detecta RENDER_EXTERNAL_URL
    threading.Thread(target=run_flask, daemon=True).start()

    # 2. Lanzamos el Bot de Telegram en el hilo principal
    start_telegram_bot()