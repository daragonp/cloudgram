import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

import asyncio
import json

import threading
from src.database.db_handler import DatabaseHandler
from src.utils.ai_handler import AIHandler
from src.services.dropbox_service import DropboxService
from src.services.google_drive_service import GoogleDriveService
from telegram import Bot
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# Inicialización de servicios (Asegurando que usen las variables de entorno)
db = DatabaseHandler()
dropbox_svc = DropboxService(
    app_key=os.getenv("DROPBOX_APP_KEY"),
    app_secret=os.getenv("DROPBOX_APP_SECRET"),
    refresh_token=os.getenv("DROPBOX_REFRESH_TOKEN")
)
drive_svc = GoogleDriveService()

def limpiar_y_recortar_texto(texto, max_chars=15000):
    if not texto: return ""
    if len(texto) > max_chars:
        print(f"✂️ Fragmentando texto largo para embedding ({len(texto)} chars)...")
        return texto[:max_chars]
    return texto

async def procesar_archivos_viejos(progreso_callback=None):
    """
    Escanea Dropbox y Drive, compara con la DB e indexa lo faltante.
    """
    if progreso_callback: await progreso_callback("Iniciando escaneo global de nubes...")
    
    reporte = {"nuevos": 0, "errores": 0}
    
    # Asegurar carpeta de descargas
    if not os.path.exists("descargas"):
        os.makedirs("descargas")

    # 1. Escaneo de Dropbox
    if progreso_callback: await progreso_callback("Escaneando archivos en Dropbox...")
    try:
        # Listamos archivos (list_files debe devolver metadatos completos si es posible)
        dbx_files = await dropbox_svc.list_files("")
        for file_item in dbx_files:
            # Si el service devuelve solo nombres (strings), procesamos. 
            # Si devuelve objetos, filtramos carpetas aquí.
            name = file_item if isinstance(file_item, str) else file_item.get('name')
            await _indexar_si_falta(name, 'dropbox', reporte, progreso_callback)
    except Exception as e:
        if progreso_callback: await progreso_callback(f"Error Dropbox: {str(e)}")

    # 2. Escaneo de Google Drive
    if progreso_callback: await progreso_callback("Escaneando archivos en Google Drive...")
    try:
        drive_files = await drive_svc.list_files(limit=50)
        for name in drive_files:
            await _indexar_si_falta(name, 'drive', reporte, progreso_callback)
    except Exception as e:
        if progreso_callback: await progreso_callback(f"Error Drive: {str(e)}")

    final_msg = f"COMPLETADO: {reporte['nuevos']} nuevos, {reporte['errores']} errores."
    if progreso_callback: await progreso_callback(final_msg)
    return final_msg
# ... (tus otros imports se mantienen igual)

async def _indexar_si_falta(name, servicio, reporte, progreso_callback=None):
    """Lógica mejorada para procesar cualquier archivo y generar resúmenes"""
    
    if not name or name in [".", "..", "None", "General", "Imágenes"]:
        return

    existente = db.get_file_by_name_and_service(name, servicio)
    
    # Si ya tiene embedding y summary, saltamos
    if existente and existente.get('embedding') and existente.get('summary'):
        return

    if progreso_callback: await progreso_callback(f"Procesando: {name} ({servicio})...")
    
    local_path = os.path.join("descargas", name)
    extension = name.split('.')[-1].lower() if '.' in name else 'desconocido'
    
    try:
        success = False
        url = "link_no_disponible"
        
        # 1. Descarga
        if servicio == 'dropbox':
            success = await dropbox_svc.download_file(f"/{name}", local_path)
            if success: url = await dropbox_svc.get_link(f"/{name}")
        elif servicio == 'drive':
            success = await drive_svc.download_file_by_name(name, local_path)
            if success: url = await drive_svc.get_link_by_name(name)

        if not success or not os.path.exists(local_path):
            raise Exception("No se pudo descargar.")

        # 2. Análisis IA
        texto_limpio = ""
        vector = None
        resumen = ""
        desc_tecnica = f"Documento {extension.upper()}"

        try:
            texto = await AIHandler.extract_text(local_path)
            texto_limpio = limpiar_y_recortar_texto(texto)
            
            # Si el archivo tiene contenido real
            if texto_limpio and len(texto_limpio.strip()) > 50:
                # Obtenemos resumen y embedding en paralelo para ganar velocidad
                resumen, vector = await asyncio.gather(
                    AIHandler.generate_summary(texto_limpio),
                    AIHandler.get_embedding(texto_limpio)
                )
            else:
                # Punto 2: Fallback para archivos sin texto (ZIP, EXE, etc.)
                resumen = f"Archivo tipo .{extension} indexado por nombre. Sin contenido de texto extraíble."
                desc_tecnica = f"Contenedor/Binario {extension.upper()}"
                texto_limpio = None
        
        except Exception as ai_err:
            print(f"⚠️ IA saltada para {name}: {ai_err}")
            resumen = f"Archivo .{extension} registrado (Análisis IA no disponible)."
            texto_limpio = None
        # 3. Registro en DB con las nuevas columnas
        # Asegúrate de que tu db.register_file acepte estos nuevos argumentos
        db.register_file(
            telegram_id="INDEXER_SYNC",
            name=name,
            f_type=extension,
            cloud_url=url,
            service=servicio,
            content_text=texto_limpio,
            embedding=vector,
            summary=resumen, # NUEVA
            technical_description=desc_tecnica # NUEVA
        )
        
        reporte['nuevos'] += 1
        if progreso_callback: await progreso_callback(f"✅ Registrado: {name}")
        
        # Pausa para evitar exceder el límite de 15 peticiones/min de Gemini Free Tier
        await asyncio.sleep(4.5)

    except Exception as e:
        error_msg = str(e)
        # Filtrar el error común de carpetas para no saturar el log
        if "not_file" in error_msg or "is a directory" in error_msg:
            if progreso_callback: await progreso_callback(f"⏩ Saltando carpeta: {name}")
        else:
            print(f"❌ Error indexando {name}: {e}")
            if progreso_callback: await progreso_callback(f"❌ Error en {name}: {error_msg}")
            reporte['errores'] += 1
    finally:
        if os.path.exists(local_path):
            try: os.remove(local_path)
            except: pass

# --- COMPATIBILIDAD CON DASHBOARD (SSE) ---

async def ejecutar_indexacion_completa():
    """Versión asíncrona principal para el hilo."""
    return await procesar_archivos_viejos()


async def procesar_un_archivo_core(fid, name, servicio, cloud_url, content_text, log_callback):
    """
    Procesamiento atómico de un solo archivo (Download -> Extract -> Summary -> Embedding -> DB Update).
    """
    async def log(msg):
        if log_callback:
            await log_callback(msg)
    
    extension = name.split('.')[-1].lower() if '.' in name else 'desconocido'
    
    try:
        texto_limpio = None
        resumen = None
        vector = None

        # CASO A: Ya tenemos el texto en la BD → solo generar embedding y resumen
        if content_text and len(content_text.strip()) > 20:
            texto_limpio = limpiar_y_recortar_texto(content_text)
            await log(f"   ↳ Usando content_text existente ({len(texto_limpio)} chars)")
        else:
            # CASO B: Sin texto → intentar descargar y analizar
            await log(f"   ↳ Sin content_text, descargando para análisis IA...")
            local_path = os.path.join("descargas", name)
            if not os.path.exists("descargas"):
                os.makedirs("descargas")

            success = False
            file_missing = False
            try:
                if servicio == 'dropbox':
                    try:
                        success = await dropbox_svc.download_file(f"/{name}", local_path)
                    except Exception as e:
                        if "not_found" in str(e).lower():
                            file_missing = True
                        raise e
                elif servicio == 'drive':
                    success = await drive_svc.download_file_by_name(name, local_path)
                    if not success:
                        file_missing = True
            except Exception as dl_err:
                await log(f"   ⚠️ Error descargando {name}: {dl_err}")

            if file_missing:
                await log(f"   🚫 Archivo no encontrado en la nube. Marcando como huérfano.")
                with db._connect() as conn2:
                    with conn2.cursor() as cur2:
                        cur2.execute("UPDATE files SET embedding = 'error_missing_in_cloud' WHERE id = %s", (fid,))
                    conn2.commit()
                return False

            if success and os.path.exists(local_path):
                try:
                    await log(f"   🧠 Extrayendo texto ({extension})...")
                    texto = await AIHandler.extract_text(local_path)
                    texto_limpio = limpiar_y_recortar_texto(texto)
                except Exception as ai_err:
                    await log(f"   ⚠️ Error IA: {ai_err}")
                finally:
                    try: os.remove(local_path)
                    except: pass
        
        # Generar embedding + resumen si tenemos texto
        if texto_limpio and len(texto_limpio.strip()) > 20:
            await log(f"   🤖 Generando resumen y embedding...")
            resumen, vector = await asyncio.gather(
                AIHandler.generate_summary(texto_limpio),
                AIHandler.get_embedding(texto_limpio)
            )
        else:
            resumen = f"Archivo .{extension} sin contenido de texto extraíble."
            texto_limpio = None

        if vector:
            import json as _json
            import numpy as _np
            emb_str = _json.dumps(vector.tolist() if isinstance(vector, _np.ndarray) else vector)
            with db._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE files
                        SET embedding = %s,
                            summary   = COALESCE(%s, summary),
                            content_text = COALESCE(%s, content_text)
                        WHERE id = %s
                    """, (emb_str, resumen, texto_limpio, fid))
                conn.commit()
            await log(f"   ✅ Embedding guardado ({len(vector)} dims)")
            return True
        else:
            await log(f"   ⚠️ No se pudo generar embedding para {name}")
            return False

    except Exception as e:
        await log(f"   ❌ Error en {name}: {e}")
        return False


async def generar_embeddings_pendientes(limite: int, progreso_callback=None):
    """
    Genera embeddings para archivos que YA están en la BD pero sin embedding.
    """
    async def log(msg):
        print(f"[EMBED] {msg}")
        if progreso_callback:
            await progreso_callback(msg)

    await log(f"🔍 Buscando archivos sin embedding{' (TODOS)' if limite == 0 else f' (máx. {limite})'}...")

    try:
        with db._connect() as conn:
            with conn.cursor() as cur:
                sql = """
                    SELECT id, name, service, cloud_url, content_text
                    FROM files
                    WHERE embedding IS NULL OR embedding IN ('', '[]', 'error_limit')
                    ORDER BY created_at DESC
                """
                if limite > 0:
                    sql += f" LIMIT {int(limite)}"
                cur.execute(sql)
                pendientes = cur.fetchall()
    except Exception as e:
        await log(f"❌ Error consultando BD: {e}")
        return {"procesados": 0, "errores": 0}

    total = len(pendientes)
    await log(f"📋 Encontrados: {total} archivos pendientes.")

    if total == 0:
        await log("✅ No hay archivos pendientes. ¡Todo indexado!")
        return {"procesados": 0, "errores": 0}

    reporte = {"procesados": 0, "errores": 0}

    for i, (fid, name, servicio, cloud_url, content_text) in enumerate(pendientes, 1):
        await log(f"[{i}/{total}] Procesando: {name} ({servicio})")
        
        ok = await procesar_un_archivo_core(fid, name, servicio, cloud_url, content_text, log)
        if ok:
            reporte["procesados"] += 1
        else:
            reporte["errores"] += 1

        # Pausa para respetar rate-limit de Gemini Free Tier
        await asyncio.sleep(4)

    await log(f"🏁 Completado: {reporte['procesados']} embeddings generados, {reporte['errores']} errores.")
    return reporte


async def ejecutar_embeddings_batch_sse(limite: int):
    """
    Generador asíncrono para SSE streaming del proceso de embeddings por lotes.
    Yields strings en formato 'data: mensaje\\n\\n'
    """
    yield "data: 5\n\n"
    yield f"data: 🧠 Iniciando generación de embeddings (lote: {'TODOS' if limite == 0 else limite})...\n\n"

    queue = asyncio.Queue()

    async def callback(msg):
        await queue.put(msg)

    task = asyncio.create_task(generar_embeddings_pendientes(limite, callback))

    while not task.done() or not queue.empty():
        try:
            msg = await asyncio.wait_for(queue.get(), timeout=1.0)
            yield f"data: {msg}\n\n"
            yield "data: 50\n\n"
        except asyncio.TimeoutError:
            continue

    resultado = await task
    yield f"data: ✅ Finalizado: {resultado['procesados']} generados, {resultado['errores']} errores.\n\n"
    yield "data: 100\n\n"


async def ejecutar_indexacion_paso_a_paso():
    """
    Generador asíncrono que envía datos al EventSource del Dashboard.
    Envía porcentajes y mensajes de texto.
    """
    yield "data: 5\n\n"
    yield "data: 🔍 Iniciando sincronización de nubes...\n\n"

    queue = asyncio.Queue()

    # Función interna para capturar logs del indexador y meterlos en la cola
    async def callback_progreso(msg):
        await queue.put(msg)

    # Lanzamos la indexación en una tarea separada
    task = asyncio.create_task(procesar_archivos_viejos(callback_progreso))
    
    # Mientras la tarea no termine, sacamos mensajes de la cola y los enviamos al navegador
    while not task.done() or not queue.empty():
        try:
            # Esperamos un mensaje con un timeout pequeño para no bloquear
            msg = await asyncio.wait_for(queue.get(), timeout=1.0)
            yield f"data: {msg}\n\n"
            # Enviamos un progreso ficticio intermedio para mover la barra
            yield f"data: 50\n\n"
        except asyncio.TimeoutError:
            continue

    resultado_final = await task
    yield f"data: {resultado_final}\n\n"
    yield "data: 100\n\n"
    

# --- BLOQUE DE EJECUCIÓN MANUAL ---
if __name__ == "__main__":
    async def main():
        print("🚀 Iniciando proceso de indexación manual...")
        
        # Definimos un callback simple para ver el progreso en consola
        async def consola_progreso(mensaje):
            print(f"  [LOG] {mensaje}")
            
        resultado = await procesar_archivos_viejos(consola_progreso)
        print(f"\n✨ Proceso finalizado: {resultado}")

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Indexación cancelada por el usuario.")
    except Exception as e:
        print(f"\n❌ Error fatal: {e}")