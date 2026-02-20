# indexador.py
import asyncio
import os
import json
from src.database.db_handler import DatabaseHandler
from src.utils.ai_handler import AIHandler
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()
db = DatabaseHandler()
bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))

async def procesar_archivos_viejos():
    print("🔍 Buscando archivos sin indexar...")
    # Necesitas un método en db_handler que devuelva archivos con embedding NULL
    with db._connect() as conn:
        cursor = conn.execute('SELECT id, telegram_id, name FROM files WHERE embedding IS NULL')
        filas = cursor.fetchall()

    if not filas:
        print("✅ Todo está indexado.")
        return

    for fid, tid, name in filas:
        print(f"📦 Procesando: {name}...")
        local_path = f"descargas/temp_{name}"
        try:
            # 1. Descargar de Telegram usando el ID guardado
            tg_file = await bot.get_file(tid)
            await tg_file.download_to_drive(local_path)

            # 2. Extraer Texto
            texto = await AIHandler.extract_text(local_path)
            if texto:
                # 3. Crear Embedding
                vector = await AIHandler.get_embedding(texto)
                # 4. Actualizar BD
                with db._connect() as conn:
                    conn.execute('UPDATE files SET content_text = ?, embedding = ? WHERE id = ?', 
                               (texto, json.dumps(vector), fid))
                print(f"✨ {name} indexado con éxito.")
            else:
                print(f"⚠️ {name} no contiene texto legible.")
        except Exception as e:
            print(f"❌ Error con {name}: {e}")
        finally:
            if os.path.exists(local_path): os.remove(local_path)

async def notify_admin(mensaje):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    admin_id = os.getenv("ADMIN_ID") # Añade tu ID de Telegram al .env
    if admin_id:
        from telegram import Bot
        bot = Bot(token=bot_token)
        await bot.send_message(chat_id=admin_id, text=f"🔔 *Indexador:* {mensaje}", parse_mode='Markdown')

# En el bloque if __name__ == "__main__":
if __name__ == "__main__":
    asyncio.run(procesar_archivos_viejos())
    asyncio.run(notify_admin("✅ Proceso de indexación masiva finalizado. Todos los archivos son ahora buscables con IA."))
