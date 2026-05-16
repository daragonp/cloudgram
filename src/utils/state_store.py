# src/utils/state_store.py
"""
Almacén de estado compartido entre workers / procesos.

API mínima: get / set / delete / incr_with_ttl.

Backend:
  • REDIS si REDIS_URL / REDIS_BROKER_URL / REDIS_URI están configurados.
  • Diccionario en memoria como fallback (sólo válido para 1 worker).

Diseñado para reemplazar:
  - `app.stop_embeddings`         → estado bool por-tarea.
  - `app.auth_flows`              → flujos OAuth temporales (Dropbox/Drive/OneDrive).
  - `RATE_LIMIT_STATE` de main.py → contadores con TTL para rate-limit del bot.
"""
from __future__ import annotations

import os
import json
import time
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _get_redis_url() -> Optional[str]:
    return os.getenv("REDIS_URL") or os.getenv("REDIS_BROKER_URL") or os.getenv("REDIS_URI")


class _MemoryBackend:
    """Fallback in-process. Sólo válido para 1 worker / desarrollo."""

    def __init__(self):
        self._data: dict = {}
        self._expires: dict = {}
        self._lock = threading.Lock()

    def _cleanup(self, key: str):
        exp = self._expires.get(key)
        if exp is not None and exp < time.time():
            self._data.pop(key, None)
            self._expires.pop(key, None)

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            self._cleanup(key)
            return self._data.get(key)

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        with self._lock:
            self._data[key] = value
            if ttl:
                self._expires[key] = time.time() + ttl
            else:
                self._expires.pop(key, None)
        return True

    def delete(self, key: str) -> bool:
        with self._lock:
            existed = key in self._data
            self._data.pop(key, None)
            self._expires.pop(key, None)
        return existed

    def incr_with_ttl(self, key: str, ttl: int) -> int:
        with self._lock:
            self._cleanup(key)
            current = int(self._data.get(key, 0)) + 1
            self._data[key] = str(current)
            self._expires.setdefault(key, time.time() + ttl)
        return current


class _RedisBackend:
    def __init__(self, url: str):
        import redis
        self.client = redis.from_url(
            url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        # Probar conexión al construir; si falla, el caller debe capturar y caer al memory backend.
        self.client.ping()

    def get(self, key: str) -> Optional[str]:
        try:
            return self.client.get(key)
        except Exception as e:
            logger.warning(f"state_store.get redis error: {e}")
            return None

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        try:
            if ttl:
                self.client.setex(key, ttl, value)
            else:
                self.client.set(key, value)
            return True
        except Exception as e:
            logger.warning(f"state_store.set redis error: {e}")
            return False

    def delete(self, key: str) -> bool:
        try:
            return bool(self.client.delete(key))
        except Exception as e:
            logger.warning(f"state_store.delete redis error: {e}")
            return False

    def incr_with_ttl(self, key: str, ttl: int) -> int:
        try:
            pipe = self.client.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl, nx=True)  # sólo poner TTL si no había uno
            result = pipe.execute()
            return int(result[0])
        except Exception as e:
            logger.warning(f"state_store.incr_with_ttl redis error: {e}")
            return 1


class StateStore:
    """Façade pública. Usa Redis si está disponible, memoria si no."""

    def __init__(self):
        self._backend: Any = _MemoryBackend()
        self.using_redis = False
        url = _get_redis_url()
        if url:
            try:
                self._backend = _RedisBackend(url)
                self.using_redis = True
                logger.info("✅ StateStore: Redis activo.")
            except Exception as e:
                logger.warning(f"⚠️ StateStore: no se pudo conectar a Redis ({e}); usando memoria.")
                self._backend = _MemoryBackend()
                self.using_redis = False
        else:
            logger.info("ℹ️ StateStore: REDIS_URL no configurado; usando memoria (sólo válido para 1 worker).")

    # ----- API conveniente -----

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self._backend.get(key)
        if v is None:
            return default
        return str(v).lower() in ("1", "true", "yes")

    def set_bool(self, key: str, value: bool, ttl: Optional[int] = None) -> bool:
        return self._backend.set(key, "1" if value else "0", ttl=ttl)

    def get_json(self, key: str):
        v = self._backend.get(key)
        if v is None:
            return None
        try:
            return json.loads(v)
        except Exception:
            return None

    def set_json(self, key: str, value, ttl: Optional[int] = None) -> bool:
        try:
            return self._backend.set(key, json.dumps(value), ttl=ttl)
        except Exception as e:
            logger.warning(f"state_store.set_json error: {e}")
            return False

    def delete(self, key: str) -> bool:
        return self._backend.delete(key)

    def incr_with_ttl(self, key: str, ttl: int) -> int:
        return self._backend.incr_with_ttl(key, ttl)


# Singleton de módulo. Se inicializa al primer import.
state_store = StateStore()
