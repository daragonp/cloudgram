# CloudGram PRO — PRD / Estado del proyecto

## Problema original

> "Revisa mi proyecto del repositorio" → Revisión + aplicación de fixes P0/P1 + mejora de la búsqueda IA que arrojaba resultados ilógicos.

## Arquitectura

- **Bot Telegram** (`main.py`, ~1462 líneas): `python-telegram-bot` con handlers de comandos, callbacks, file upload a múltiples nubes.
- **Panel Web** (`web_admin.py`, ~1240 líneas): Flask + Flask-Login + CSRF + SSE para progreso del indexador.
- **Servicios cloud**: Dropbox / Google Drive / OneDrive bajo la interfaz `CloudService`.
- **IA**: OpenAI (`text-embedding-3-small`, `gpt-4o-mini`, `whisper-1`). Gemini queda como legacy.
- **DB**: PostgreSQL (Supabase) + pgvector. SQLite local como fallback.
- **Cola async**: Celery + Redis, con fallback a hilos si no hay broker.
- **Búsqueda híbrida**: pgvector + BM25 + metadata, ahora con **Reciprocal Rank Fusion**.

## Personas

- **Admin único** (definido por `ADMIN_ID` env var): usa el bot vía Telegram y accede al panel web.

## Requisitos centrales

- Subir archivos al bot y guardarlos categorizados en Dropbox / Drive / OneDrive.
- Indexarlos con IA (texto, embeddings, resúmenes, tags).
- Buscarlos por nombre o por significado (`/buscar_ia`).
- Panel web para monitorizar, reindexar y mantener tokens.

## Cambios aplicados en esta sesión (2026-01)

### Fixes P0 (críticos)
- ✅ `.env.example` saneado (token Telegram y ADMIN_ID con apariencia real → placeholders neutros).
- ✅ `requirements.txt` reescrito con versiones reales (antes tenía `pandas==3.0.1`, `gunicorn==25.1.0`, `certifi==2026.1.4`, etc. — todas inexistentes).
- ✅ `FLASK_SECRET_KEY` ahora **es obligatoria en producción**; en dev se genera con warning explícito.
- ✅ `/db-explorer` con whitelist `{files, folders, bot_logs, category_folder_cache}` + columnas sensibles enmascaradas (`password_hash`, `embedding`).
- ✅ `auth_handler.py` reescrito con **deny-by-default**: bloquea también cuando `effective_user` es `None` o `ADMIN_ID` no está configurado.

### Fixes P1 (bugs concretos)
- ✅ Login y `/perfil` ya funcionan: `load_user` y `update_user_name` corregidos para usar la columna `name` (no `nombre`).
- ✅ Borrado `reset_failed_embeddings` duplicado (la primera era `pass`, silenciaba a la real).
- ✅ `init_services.py` ahora invoca `drive_svc._get_service()` en el arranque para reflejar el estado real de Drive en los logs.
- ✅ `main.py:879` — quitada la `r` del f-string crudo (Telegram mostraba `\n\n` literal).
- ✅ `web_admin.py::embed_single` ya no dispara el indexador batch además del archivo individual.
- ✅ 3 bare `except:` críticos del dashboard convertidos en `except Exception as e: log(...)`.

### Mejoras en `/buscar_ia` (lo que pedías)
**Antes:** suma ponderada de scores con escalas mezcladas (cosine vs BM25 normalizado vs metadata heurístico) + sin threshold ⇒ archivos casi irrelevantes podían rankear arriba.

**Ahora (`src/search/hybrid_search.py` reescrito):**
1. **Reciprocal Rank Fusion (RRF, k=60)** — combina rankings, no scores absolutos. Estándar de la industria (Elasticsearch, Vespa).
2. **Threshold de relevancia mínima**: si el top semantic_similarity < 0.30 y no hay ningún match léxico, devuelve vacío en vez de inflar ruido.
3. **Boost multiplicativo**:
   - x1.5 cuando la query aparece literal en el nombre del archivo
   - x1.25 cuando aparece un token clave del query en el nombre
   - x1.2 cuando aparece en `tags`
4. **Normalización** (lower-case + `unidecode` + sin puntuación) para que "fáctura" matchee "factura".
5. **Detector de tipos en la query**: "foto", "pdf", "audio", "documento" → aplica filtro de extensión automáticamente.
6. **Caché REDIS con epoch de la BD** para invalidar al reindexar.
7. **Sobre-pedimos `limit * 3`** a cada fuente para tener candidatos antes de fusionar.
8. **Logging** de cuántos resultados vinieron de cada fuente y de qué `_sources` aparece cada archivo.

Verificado con un test ad-hoc: dada la query `"factura"`, el archivo `Factura.pdf` que matchea por nombre + tags se prioriza sobre uno que matchea sólo en semántica.

## Archivos modificados

- `/app/.env.example`
- `/app/requirements.txt`
- `/app/web_admin.py`
- `/app/main.py`
- `/app/src/database/db_handler.py`
- `/app/src/handlers/auth_handler.py`
- `/app/src/init_services.py`
- `/app/src/search/hybrid_search.py` (reescrito)
- `/app/REVIEW_REPORT.md` (nuevo, reporte completo del análisis)

## Lo que QUEDA pendiente (backlog priorizado)

### P0 sin resolver (requiere refactor mayor)
- **Secretos en `.env` mutable**: `save_env_secret()` sigue escribiendo al disco. Necesita una tabla `app_secrets` cifrada en PostgreSQL.

### P1 sin resolver
- **Estado por proceso** (`stop_embeddings`, `auth_flows`, `RATE_LIMIT_STATE`, `flask_limiter memory://`) — migrar a Redis.
- **`get_file_by_id`** devuelve a veces tupla, a veces dict — estandarizar a dict.
- **Workflow duplicado**: Render + Oracle VM se despliegan en cada push, decidir uno.
- 11 bare `except:` y errores de `E701` (líneas con `try: ... except: pass`) restantes.

### P2 (calidad)
- Modularizar `main.py` (1462 líneas) y `web_admin.py` (1240 líneas) con Blueprints.
- Crear suite de tests (BM25, DB setup, `/health`, motor híbrido).
- Quitar referencias a Gemini si ya no se usa.
- Unificar logging (eliminar `print()` por `logger`).
- Migrar a `pgvector` Python binding para enviar vectores binarios.

## Próximas acciones sugeridas (para el dueño)

1. Probar la nueva `/buscar_ia` en producción con queries reales y compartir feedback.
2. Aplicar la migración SQL si se confirma que la columna users.name es la correcta (ya no hay `nombre`).
3. Regenerar el token Telegram en BotFather (`/revoke` → `/token`) por la exposición histórica.
4. Reinstalar dependencias en el servidor con el nuevo `requirements.txt` (`pip install -r requirements.txt`).
