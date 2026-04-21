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
# 3. INICIALIZACIÓN DEL BOT DE TELEGRAM
# ==========================================
def start_telegram_bot():
    # Al importar 'main' como módulo, NO se ejecuta el bloque if __name__ de main.py
    import main 
    
    # Ejecutamos la bienvenida que creaste
    main.print_server_welcome()
    
    # Construimos el bot (copiado de la configuración de tu main.py)
    bot_app = main.ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).post_init(main.post_init).post_stop(main.post_stop).build()
    
    # Registramos todos tus handlers exactamente como los tienes en main.py
    bot_app.add_handler(main.TypeHandler(main.Update, main.auth_middleware), group=-1)
    bot_app.add_handler(main.CommandHandler("start", main.start))
    bot_app.add_handler(main.CommandHandler("stats", main.stats_command))
    bot_app.add_handler(main.CommandHandler("listar", main.list_files_command))
    bot_app.add_handler(main.CommandHandler("buscar", main.search_command))
    bot_app.add_handler(main.CommandHandler("buscar_ia", main.search_ia_command))
    bot_app.add_handler(main.CommandHandler("indexar", main.indexar_command))
    bot_app.add_handler(main.CommandHandler("eliminar", main.delete_command))
    bot_app.add_handler(main.CommandHandler("ayuda", main.help_command))
    bot_app.add_handler(main.CommandHandler("help", main.help_command))
    bot_app.add_handler(main.CallbackQueryHandler(main.voice_options_callback, pattern="^voice_"))
    bot_app.add_handler(main.CommandHandler(["cancelar", "salir", "stop"], main.cancelar_handler))
    bot_app.add_handler(main.MessageHandler(main.filters.COMMAND, main.unknown_command_handler))
            
    bot_app.add_handler(main.MessageHandler(
        (main.filters.Document.ALL | main.filters.PHOTO | main.filters.VIDEO | 
         main.filters.VIDEO_NOTE | main.filters.AUDIO | main.filters.VOICE | main.filters.LOCATION), 
        main.handle_any_file
    ))
    
    bot_app.add_handler(main.MessageHandler(main.filters.TEXT & (~main.filters.COMMAND), main.handle_text_input))
    bot_app.add_handler(main.CallbackQueryHandler(main.button_callback))
    
    # Registramos el manejador de errores
    bot_app.add_error_handler(main.error_handler)
    
    print("🚀 CloudGram PRO v1.0 ONLINE (Bot + Panel Web)")
    # Esto bloquea el hilo principal manteniendo el proceso vivo para el bot
    bot_app.run_polling()

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