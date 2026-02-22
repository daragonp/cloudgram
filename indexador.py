import asyncio
import os
import json
from src.database.db_handler_local import DatabaseHandler
from src.utils.ai_handler import AIHandler
from telegram import Bot
from dotenv import load_dotenv
import time

load_dotenv()
db = DatabaseHandler()
bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))

def limpiar_y_recortar_texto(texto, max_chars=20000):
    """Evita el error de tokens recortando el texto si es muy largo."""
    if not texto:
        return ""
    if len(texto) > max_chars:
        print(f"✂️ Texto demasiado largo ({len(texto)} chars). Recortando a {max_chars}...")
        return texto[:max_chars]
    return texto

# Reemplaza estas funciones en tu indexador.py

async def procesar_archivos_viejos():
    print("🔍 Buscando archivos sin indexar...")
    # Añadimos DISTINCT para evitar procesar el mismo telegram_id varias veces si está duplicado
    with db._connect() as conn:
        cursor = conn.execute('''
            SELECT id, telegram_id, name 
            FROM files 
            WHERE embedding IS NULL OR embedding = ''
            GROUP BY telegram_id
        ''')
        filas = cursor.fetchall()

    if not filas:
        print("✅ Todo está indexado.")
        return "Todo estaba indexado."

    for fid, tid, name in filas:
        # ... (el resto del código de procesamiento que ya tienes funciona bien) ...
        # Asegúrate de que el UPDATE use el 'fid' correcto para marcarlo como hecho
        pass

async def notify_admin(mensaje):
    """Versión robusta para evitar el error 'Event loop is closed'"""
    try:
        # Creamos una instancia fresca para la notificación
        from telegram import Bot
        import os
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        admin_id = os.getenv("ADMIN_ID")
        if admin_id and token:
            local_bot = Bot(token=token)
            async with local_bot:
                await local_bot.send_message(chat_id=admin_id, text=f"🔔 *Indexador:* {mensaje}", parse_mode='Markdown')
    except Exception as e:
        print(f"No se pudo enviar notificación: {e}")

# Cambiamos la forma en que se ejecuta al final del archivo:
if __name__ == "__main__":
    # Ejecutamos todo en un solo bloque para mantener el loop abierto
    async def main():
        res = await procesar_archivos_viejos()
        await notify_admin(f"Proceso finalizado. {res}")
    
    asyncio.run(main())
    
async def notify_admin(mensaje):
    admin_id = os.getenv("ADMIN_ID")
    if admin_id:
        try:
            await bot.send_message(chat_id=admin_id, text=f"🔔 *Indexador:* {mensaje}", parse_mode='Markdown')
        except Exception as e:
            print(f"No se pudo enviar notificación: {e}")

# --- FUNCIONES PARA LA INTERFAZ WEB ---

def ejecutar_indexacion_completa():
    """Llamada por el botón del Dashboard"""
    print("Iniciando indexación desde la Web...")
    try:
        # Ejecutamos el loop asíncrono desde el entorno síncrono de Flask
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        resultado = loop.run_until_complete(procesar_archivos_viejos())
        loop.close()
        return f"Éxito: {resultado}"
    except Exception as e:
        return f"Error: {str(e)}"

def ejecutar_indexacion_paso_a_paso():
    """Llamada por la barra de progreso de la Web"""
    with db._connect() as conn:
        cursor = conn.execute('SELECT COUNT(*) FROM files WHERE embedding IS NULL')
        total = cursor.fetchone()[0]

    if total == 0:
        yield f"data: 100\n\n"
        return

    # Simulación de pasos para la barra (mejorable conectándolo al loop real)
    yield f"data: 10\n\n"
    ejecutar_indexacion_completa()
    yield f"data: 100\n\n"

if __name__ == "__main__":
    asyncio.run(procesar_archivos_viejos())
    asyncio.run(notify_admin("✅ Proceso de indexación masiva finalizado. Todos los archivos son ahora buscables con IA."))