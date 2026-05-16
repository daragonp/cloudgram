# CloudGram PRO — PRD / Estado del proyecto

## Problema original

> "Revisa mi proyecto del repositorio" → Revisión + aplicación de fixes P0/P1 + mejora de la búsqueda IA que arrojaba resultados ilógicos + deploy a Render.

## Arquitectura

- **Bot Telegram** (`main.py`, ~1462 líneas)
- **Panel Web** Flask (`web_admin.py`, ~1300 líneas)
- **Servicios cloud**: Dropbox / Google Drive / OneDrive bajo la interfaz `CloudService`
- **IA**: OpenAI (`text-embedding-3-small`, `gpt-4o-mini`, `whisper-1`); Gemini legacy
- **DB**: PostgreSQL (Supabase) + pgvector
- **Cola async**: Celery + Redis con fallback a hilos
- **Estado compartido**: `src/utils/state_store.py` (Redis o memoria)
- **Búsqueda híbrida**: pgvector + BM25 + metadata con **Reciprocal Rank Fusion**

## Personas

- **Admin único** (definido por `ADMIN_ID` env var)

## Cambios aplicados en esta sesión (Sprint 2026-01)

### Fixes P0 (críticos)
- ✅ `.env.example` saneado
- ✅ `requirements.txt` con versiones reales (antes `pandas==3.0.1`, `gunicorn==25.1.0`, etc. inexistentes)
- ✅ `FLASK_SECRET_KEY` obligatoria en producción
- ✅ `/db-explorer` con whitelist + `password_hash`/`embedding` enmascarados
- ✅ `auth_handler.py` con deny-by-default

### Fixes P1 (bugs y arquitectura)
- ✅ Columna `name`/`nombre` unificada (login y `/perfil` ya funcionan)
- ✅ `reset_failed_embeddings` duplicada eliminada
- ✅ Drive status real en arranque
- ✅ f-string crudo en `main.py:879` corregido
- ✅ `embed_single` ya no dispara el indexador completo
- ✅ **`get_file_by_id` consistente**: todos los call sites simplificados a dict
- ✅ **Estado compartido vía Redis** (`src/utils/state_store.py`):
  - `RATE_LIMIT_STATE` del bot → Redis
  - `flask_limiter` storage → Redis (antes `memory://`)
  - `app.stop_embeddings` → Redis con TTL 1h
  - `app.auth_flows` (Dropbox/Drive/OneDrive OAuth) → flag en Redis con TTL 10min, flow recreado en `finish`
- ✅ Bare `except:` críticos del dashboard arreglados (`except Exception as e: log(...)`)
- ✅ 17 errores menores autofixeados con `ruff --fix`

### Mejoras en `/buscar_ia` (lo que pedías arreglar)
`src/search/hybrid_search.py` **reescrito**:
1. **Reciprocal Rank Fusion (RRF, k=60)** en lugar de suma ponderada
2. **Threshold de relevancia mínima** (semantic floor 0.30)
3. **Boosts**: x1.5 nombre exacto, x1.25 token, x1.2 tags
4. **Normalización** con `unidecode` + sin puntuación
5. **Detector de tipo en la query**: "foto", "pdf", "audio" → filtro de extensión
6. **Cache REDIS con epoch de la BD**
7. **Sobre-pedimos `limit*3`** a cada fuente para tener candidatos antes de fusionar

### Deploy a Render
- ✅ Fijado Python **3.12.7** en `render.yaml`, `.python-version` y `runtime.txt` (Python 3.14 rompía wheels de `jiter`/`pydantic_core`/`cryptography`)
- ✅ `buildCommand` actualiza pip antes de instalar

## Archivos modificados/creados

- `/app/.env.example` (saneado)
- `/app/.python-version` (nuevo, 3.12.7)
- `/app/runtime.txt` (nuevo, 3.12.7)
- `/app/render.yaml` (pythonVersion + PYTHON_VERSION env)
- `/app/requirements.txt` (versiones reales + `unidecode`)
- `/app/web_admin.py` (estado a Redis, OAuth sin estado in-process, db_explorer whitelist, FLASK_SECRET_KEY obligatoria)
- `/app/main.py` (rate limit a Redis, `get_file_by_id` consistente, f-string fix, autofix ruff)
- `/app/src/database/db_handler.py` (columna name, duplicado eliminado)
- `/app/src/handlers/auth_handler.py` (deny-by-default)
- `/app/src/init_services.py` (Drive status real)
- `/app/src/search/hybrid_search.py` (reescrito con RRF)
- `/app/src/utils/state_store.py` (nuevo — abstracción Redis/memoria)
- `/app/REVIEW_REPORT.md` (reporte completo del análisis)

## Lo que QUEDA pendiente

### P0 sin resolver
- **Secretos en `.env` mutable**: `save_env_secret()` sigue escribiendo al disco. Requiere tabla `app_secrets` cifrada (refactor mayor).

### P2 (calidad / refactor)
- Modularizar `main.py` y `web_admin.py` con Blueprints (1462 + 1300 líneas)
- Suite de tests (BM25, DB setup, `/health`, motor híbrido)
- Quitar referencias a Gemini si ya no se usa
- Unificar logging (eliminar `print()` por `logger`)
- Migrar a binding `pgvector` Python para enviar vectores binarios
- 15 bare `except:` triviales restantes (mayoría `except: pass` en cleanup)

## Variables de entorno necesarias en Render

Obligatorias:
- `FLASK_SECRET_KEY` (generar con `python -c "import secrets; print(secrets.token_hex(32))"`)
- `DATABASE_URL` (Supabase Postgres)
- `TELEGRAM_BOT_TOKEN` (el nuevo, tras `/revoke` en BotFather)
- `ADMIN_ID`
- `OPENAI_API_KEY`

Recomendadas:
- `REDIS_URL` (activa state_store distribuido, caché de búsqueda y rate limiter multi-worker)

Opcionales:
- `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN`
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_DRIVE_TOKEN_JSON`
- `ONEDRIVE_CLIENT_ID`, `ONEDRIVE_CLIENT_SECRET`, `ONEDRIVE_REFRESH_TOKEN`
