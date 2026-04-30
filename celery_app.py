import os
from dotenv import load_dotenv
from celery import Celery

load_dotenv()

BROKER_URL = os.getenv("REDIS_URL") or os.getenv("REDIS_BROKER_URL") or os.getenv("REDIS_URI")
RESULT_BACKEND = os.getenv("REDIS_RESULT_BACKEND") or BROKER_URL

if not BROKER_URL:
    raise RuntimeError(
        "REDIS_URL is required to start Celery. Set REDIS_URL or REDIS_BROKER_URL in your environment."
    )

celery = Celery(
    "cloudgram",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_max_tasks_per_child=50,
)


def _build_progress_callback(task):
    async def _callback(message):
        task.update_state(state="PROGRESS", meta={"message": message})
    return _callback


@celery.task(bind=True)
def generate_embeddings(self, limite=10):
    import asyncio
    from src.scripts.indexador import generar_embeddings_pendientes

    async def run():
        callback = _build_progress_callback(self)
        result = await generar_embeddings_pendientes(int(limite), callback)
        return result

    return asyncio.run(run())


@celery.task(bind=True)
def run_full_indexer(self):
    import asyncio
    from src.scripts.indexador import procesar_archivos_viejos

    async def run():
        callback = _build_progress_callback(self)
        result = await procesar_archivos_viejos(callback)
        return result

    return asyncio.run(run())
