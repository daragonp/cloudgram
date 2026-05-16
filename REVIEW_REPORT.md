# 🔍 Revisión Técnica — CloudGram PRO

**Fecha:** 2026-01  
**Repositorio:** CloudGram PRO v1.0  
**Tipo:** Bot de Telegram + Panel Web Flask + Indexación IA  

---

## 📋 Resumen ejecutivo

CloudGram PRO es un **proyecto bien concebido y razonablemente completo** que integra Telegram + Dropbox/Drive/OneDrive + IA (OpenAI/Gemini) + búsqueda híbrida (pgvector + BM25). La arquitectura es sólida y muchas decisiones (Celery opcional, búsqueda híbrida, caché de carpetas) están bien pensadas.

Sin embargo, **hay problemas críticos** que comprometen seguridad, despliegue y mantenibilidad. La siguiente tabla resume el estado:

| Área | Estado | Notas |
|---|---|---|
| Arquitectura | 🟢 Buena | Capas claras (handlers / services / db / utils) |
| Seguridad | 🔴 Crítica | Tokens en `.env.example`, secrets en disco, escape SQL flojo |
| Calidad de código | 🟠 Media | 137 errores de lint, mucho código duplicado y bare `except:` |
| Estabilidad | 🟠 Media | Estado en memoria, hilos asyncio, sin tests |
| Despliegue | 🟠 Media | Versiones inválidas en `requirements.txt`, `.env` mutable en runtime |
| Documentación | 🔴 Baja | `README.md` no existe en el repo activo |

**Prioridades sugeridas:** P0 → arreglar seguridad y `requirements.txt`; P1 → bugs concretos (campo `nombre`, métodos duplicados); P2 → calidad/refactor.

---

## 🔴 P0 — Crítico (bloquea seguridad o despliegue)

### P0-1. `.env.example` contiene un token y un ADMIN_ID con apariencia real
**Archivo:** `.env.example` líneas 1–2
```env
TELEGRAM_BOT_TOKEN=8287:fbhfbhfhvfhsvfsCFsti8cE
ADMIN_ID=7858578752475
```
Aunque parecen placeholders, el formato es válido para Telegram (`<botid>:<token>`) y se ha publicado en el repo. **Hay que asumir que están comprometidos**. Acciones:
1. Regenerar el token del bot en `@BotFather → /revoke`.
2. Reemplazar por placeholders sin estructura válida, p.ej. `TELEGRAM_BOT_TOKEN=ponga_su_token_aqui`.
3. Verificar el historial de Git: `git log --all -S "8287:fbhfbhfhvfhsvfsCFsti8cE"`. Si aparece, considerar rotación completa.

### P0-2. `requirements.txt` con versiones inexistentes
**Archivo:** `requirements.txt`
```
pandas==3.0.1       # ❌ no existe (la actual es ~2.2.x)
gunicorn==25.1.0    # ❌ no existe (la actual es ~23.x)
numpy==2.4.2        # ⚠️ confirmar — la rama 2.x existe pero esta versión no aparece en PyPI
certifi==2026.1.4   # ⚠️ futurible
packaging==26.0     # ⚠️ futurible
```
**Esto romperá el `pip install` en cualquier despliegue limpio (Render, Oracle VM, contenedor nuevo).** Aparenta haber sido escrito por una IA con conocimiento futuro o un `pip freeze` desde un entorno corrupto. Solución:
```bash
pip install pandas gunicorn numpy certifi packaging
pip freeze > requirements.txt
```

### P0-3. Secretos persistidos en `.env` en tiempo de ejecución (`save_env_secret`)
**Archivo:** `web_admin.py` líneas 677–699 y rutas `/update-api-key`, `/auth/*/finish`

Los tokens OAuth de Dropbox/Drive/OneDrive y las API keys (Gemini/OpenAI) se **escriben en disco al `.env` desde el panel web**.
Problemas:
- **En Render/contenedores efímeros se pierden en cada deploy.** El usuario reconecta una y otra vez.
- No es atómico — dos peticiones concurrentes pueden corromper el fichero.
- Exponer `OPENAI_API_KEY` / `GEMINI_API_KEY` a través del panel admite escribir cualquier valor; sin validación se acepta texto vacío y rompe la IA.
- En el filesystem del contenedor, otro proceso (worker Celery) no se entera del cambio (no recarga).

**Recomendación:** Mover los OAuth tokens y API keys a una **tabla `app_secrets`** en PostgreSQL (cifrados con `cryptography.fernet`). Mantener `.env` solo para arranque (DB URL, Telegram token, secret key).

### P0-4. `FLASK_SECRET_KEY` aleatoria por proceso
**Archivo:** `web_admin.py` líneas 62–65
```python
flask_secret = os.getenv("FLASK_SECRET_KEY")
if not flask_secret or flask_secret == "dev_key_only":
    flask_secret = os.urandom(24)
app.secret_key = flask_secret
```
Si no hay `FLASK_SECRET_KEY` configurada:
- Cada reinicio invalida **todas las sesiones de usuario**.
- Con varios workers gunicorn cada uno tiene **una clave distinta** → CSRF roto y sesiones que rotan al azar.

**Fix:** Hacer que sea obligatorio o cachear en disco. Mejor:
```python
flask_secret = os.environ["FLASK_SECRET_KEY"]  # falla rápido si falta
app.secret_key = flask_secret
```

### P0-5. `db_explorer` permite leer cualquier tabla incluyendo `users`
**Archivo:** `web_admin.py` líneas 1095–1138

Aunque hay regex `^[a-zA-Z0-9_]+$` que evita inyección, **se permite consultar `users` y exponer `password_hash`** desde el panel:
```python
cur.execute(f"SELECT * FROM {table_name} LIMIT 500")
```
Soluciones:
- Lista blanca de tablas: `ALLOWED_TABLES = {"files", "folders", "bot_logs"}`.
- O al menos enmascarar columnas sensibles (`password_hash`, `embedding`).

### P0-6. `auth_handler.py` no bloquea actualizaciones sin `effective_user`
**Archivo:** `src/handlers/auth_handler.py` línea 18
```python
if user and user.id not in admin_ids:
    raise ApplicationHandlerStop()
```
Si `update.effective_user` es `None` (canales, edited_channel_post, ciertos callbacks) **no se detiene la propagación**. Cualquier handler downstream se ejecuta. Lógica correcta:
```python
if not user or user.id not in admin_ids:
    # responder y detener
    raise ApplicationHandlerStop()
```

---

## 🟠 P1 — Bugs concretos y comportamiento incorrecto

### P1-1. Columna `nombre` vs `name` — login y perfil rotos
**Archivos:** `src/database/db_handler.py:75`, `web_admin.py:182,218,625,842`

La tabla `users` se crea con columna `name`:
```python
CREATE TABLE IF NOT EXISTS users (... name TEXT NOT NULL, ...)
```
Pero el código lee/escribe **`nombre`**:
```python
nombre=user_data.get('nombre')                                       # web_admin.py:182
cur.execute("UPDATE users SET nombre = %s WHERE id = %s", ...)       # db_handler.py:842
```
**Resultado:** Cualquier intento de leer el nombre del admin devuelve `None`; actualizar nombre en `/perfil` lanza error de columna inexistente.

**Fix:** unificar nombre. Recomiendo renombrar en BD a `nombre` (es código español) o cambiar todos los accesos a `name`. Si decides `nombre`, añade migración:
```sql
ALTER TABLE users RENAME COLUMN name TO nombre;
```

### P1-2. `reset_failed_embeddings` duplicada
**Archivo:** `src/database/db_handler.py:553` y `:776`

Dos definiciones del mismo método; la segunda silencia a la primera. La primera es `pass` (no hace nada), la segunda sí actualiza la BD. Está marcado por ruff como `F811`. Borrar la primera.

### P1-3. `init_services.py` chequea `drive_svc.service` ANTES de inicializarlo
**Archivos:** `src/init_services.py:47-51` y `src/services/google_drive_service.py:10-37`

```python
drive_svc = GoogleDriveService()      # __init__ pone self.service = None
if drive_svc.service:                  # siempre False — nunca se llamó _get_service()
    logger.info("✅ GoogleDriveService conectado")
else:
    logger.warning("⚠️ GoogleDriveService no disponible")
```
El log siempre marcará Drive como no disponible, aunque las credenciales existan. La conexión real se crea perezosamente en el primer `upload`/`list_files`. Fix: añadir un `try: drive_svc._get_service()` no bloqueante al final del `__init__`, o llamar a `test_all_connections()` en el startup.

### P1-4. f-string crudo con `\n` literal
**Archivo:** `main.py:879`
```python
rf"✅ *¡Indexado!* `{nombre}` ya es buscable con IA.\n\n"
```
`r"..."` deshace los escapes; **Telegram mostrará literal `\n\n`** en vez de saltos de línea. Quitar la `r`.

### P1-5. `update_user_name` apunta a columna inexistente
Mismo problema que P1-1: `db_handler.py:842` usa `nombre` cuando la columna es `name`.

### P1-6. Estado por proceso → roto con multi-worker
- `app.stop_embeddings` (línea 79 `web_admin.py`)
- `app.auth_flows` (línea 104 `web_admin.py`)
- `app.embed_queue`, `app.categorizer_queue`
- `flask_limiter` con `storage_uri="memory://"` (línea 75)
- `RATE_LIMIT_STATE` (línea 61 `main.py`)

Con Gunicorn arrancando varios workers (por defecto en Render), el botón "detener embeddings" puede no llegar al worker correcto, y el rate limiter cuenta por worker. Mover a Redis (que ya está en el stack para Celery).

### P1-7. Bare `except:` esconde errores reales
**14 ocurrencias** entre `main.py`, `db_handler.py`, `message_handlers.py`, `web_admin.py`. Especialmente peligrosas:
- `web_admin.py:497-499` — en SSE devuelve `continue` infinito, puede hacer loop apretado contra `Empty`.
- `web_admin.py:282-284` — silencia cualquier error de Dropbox y marca OFFLINE sin log.
- `main.py:622` — silencia errores de descarga de Telegram, archivos se pierden silenciosamente.

Reemplazar por `except SpecificException as e: logger.warning(...)`.

### P1-8. `embed_single` ejecuta indexador completo + un archivo
**Archivo:** `web_admin.py` líneas 932–933
```python
async def _process():
    from src.scripts.indexador import generar_embeddings_pendientes
    reporte = await generar_embeddings_pendientes(limite=1, ...)
    # Luego procesa el archivo individual a mano
```
Hace **dos** trabajos: dispara el indexador batch _y_ procesa el archivo solicitado. Lint marca `reporte` como variable no usada. Hay que eliminar la primera llamada.

### P1-9. `get_file_by_id` devuelve a veces dict y a veces tupla
Varias rutas hacen `isinstance(archivo, tuple)` para decidir cómo leerlo (`main.py:866`, `:1072`, `web_admin.py:1052`). Es síntoma de inconsistencia. Estandarizar el handler a **siempre devolver un dict** (`RealDictCursor`).

### P1-10. `requirements.txt` falta `pgvector`
La BD usa `embedding <=> %s::vector` (pgvector), pero la dependencia Python `pgvector` no está en `requirements.txt`. Funciona porque psycopg2 envía el string crudo, pero **la mejor práctica** es usar `pgvector.psycopg2.register_vector(conn)` para enviar vectores binarios y evitar parsear JSON gigantes.

### P1-11. Workflow GitHub Actions y Render.yaml duplican despliegue
- `.github/workflows/deploy.yml` empuja a una VM en Oracle por SSH.
- `render.yaml` despliega a Render automáticamente al push.

Ambos se disparan en cada push a `main`. Decidir cuál es la fuente de verdad y deshabilitar el otro.

### P1-12. `run_keep_alive` arranca en cada worker
**Archivo:** `web_admin.py:99-100`
```python
if os.environ.get('RENDER_EXTERNAL_URL'):
    threading.Thread(target=run_keep_alive, daemon=True).start()
```
Con N workers → N hilos pingeando cada 14 min. No es crítico, pero:
- Ejecutar solo en el master (puedes detectar workers de gunicorn con `if os.environ.get('RUN_KEEP_ALIVE') == '1'`).
- O mejor, **eliminar el keep-alive** y configurar el plan de Render con instancias siempre activas (es la práctica recomendada por Render desde 2024).

---

## 🟡 P2 — Calidad, mantenibilidad y mejoras

### P2-1. Lint
137 errores de `ruff`. Principales:
- 41 × `F541` f-string sin placeholders (basta con quitar la `f`).
- 14 × `E722` bare except.
- ~30 × `E701` múltiples sentencias en una línea (`try: ... except: pass`).
- 5 × `F811` redefiniciones.

Comando rápido (autofix seguro):
```bash
ruff check --fix /app
```

### P2-2. Archivos enormes y monolíticos
- `main.py` = 1462 líneas, con UI, callbacks, búsqueda, lógica de subida, comandos…  
- `web_admin.py` = 1238 líneas con auth, OAuth, BD, SSE, OAuth, etc.

Sugerencia:
- `main.py` → dividir en `bot/commands.py`, `bot/callbacks.py`, `bot/uploaders.py`.
- `web_admin.py` → usar **Flask Blueprints**: `auth_bp`, `dashboard_bp`, `indexer_bp`, `db_explorer_bp`, `cloud_oauth_bp`.

### P2-3. Falta de tests
No hay carpeta `tests/` (existe sólo `test.jpg`, una imagen). Recomiendo arrancar con:
- Tests unitarios de `BM25Search` (es lógica pura, fácil de testear).
- Test de smoke para `DatabaseHandler._setup_initial_db()` con SQLite in-memory.
- Test de integración del endpoint `/health`.

### P2-4. Inconsistencia de respuestas Flask
Algunas rutas devuelven `(dict, status_code)` (Flask serializa a JSON automáticamente desde 1.1), otras `jsonify(...)`, otras `json.dumps(...) + headers manuales` (`embed_single` línea 1023). Estandarizar a `jsonify`.

### P2-5. Mezcla de IA Gemini/OpenAI obsoleta
`init_services.py:67-77` (comentario):
> Gemini (GEMINI_API_KEY) ya no se usa en el pipeline principal.  
> Se mantiene en .env por compatibilidad pero no es requerida.

Sin embargo:
- `ai_handler.py:62` aún define `GEMINI_CHAT_MODELS`.
- `test_all_connections` (línea 113) sigue testeando `gemini_chat`.
- El panel sigue permitiendo actualizar `GEMINI_API_KEY` (línea 715).

Decidir: o se elimina Gemini totalmente del código, o se mantiene como alternativa. Hoy es **deuda muerta** confusa.

### P2-6. Logging inconsistente
Coexisten:
- `print(...)` (200+ ocurrencias)
- `logger.info(...)` / `logger.error(...)`
- `db.log_event("INFO", ...)`

Idealmente todo a `logger` configurado, y `log_event` solo para eventos críticos de negocio.

### P2-7. Errores manejados con strings opacos
Ejemplos:
- `db.search_semantic` → `except Exception as e: print(...); return []` — el llamador no sabe si la BD estaba caída o si la query era inválida.
- `dropbox_service.py:78` — devuelve `False` ante cualquier error, perdiendo el motivo.

Recomiendo lanzar excepciones tipadas (`DBError`, `CloudServiceError`) y dejar que el endpoint final decida cómo presentarlas.

### P2-8. Búsqueda híbrida puede recalcular embedding de query 2 veces
En `hybrid_search.HybridSearchEngine.search()`, suele pedirse el embedding de la query y luego ejecutar tres búsquedas (semántica, full-text, metadata) en paralelo. Verificar que el embedding solo se calcula una vez (no lo he abierto en detalle, pero por la arquitectura es sospechoso).

### P2-9. `BM25` reconstruye el índice por cada query
**Archivo:** `src/search/bm25_search.py:144`

`BM25Search.search()` construye el índice BM25 sobre todos los `docs` recibidos cada vez. Con 1000 docs y muchas búsquedas es derrochador. Para una mejora futura: persistir el índice (o usar `rank_bm25` de PyPI, que ya lo tiene optimizado).

### P2-10. Reenvío de tokens entre `init_services.py` y `web_admin.py`
`init_services.py` importa `os.getenv` para Dropbox **al iniciar el módulo**. Si el panel guarda un nuevo refresh token al `.env` (vía `save_env_secret`), el `dropbox_svc` ya creado **no se entera** — sigue con el viejo token. Hace falta reiniciar el proceso o exponer un `dropbox_svc.refresh_credentials()`.

### P2-11. Typos y comentarios obsoletos
- `web_admin.py:393` `"manually"` (mezcla inglés/español, pero correcto).
- `web_admin.py:959` `"manualmentne"` → "manualmente".
- `init_services.py:117` comentario "Test Database" cuando ya hay otro encima.
- `main.py:1424` cabecera "5. BÚSQUEDA IA Y ELIMINAR" pero el código siguiente es otro.

### P2-12. `templates/db_explorer.html` no se ha revisado
No lo abrí, pero al permitir mostrar contenido arbitrario de `data` (incluido `password_hash` si pasa el regex), conviene auditar también que escape correctamente con Jinja autoescape (que está activado por defecto, así que probablemente bien).

---

## ✅ Lo que está bien hecho

- **Arquitectura por capas:** services / handlers / search / utils / database es clara.
- **Búsqueda híbrida (semántica + BM25 + metadata)** con reranking — es una buena solución profesional.
- **Soporte multinube** (Dropbox + Drive + OneDrive) con la misma interfaz `CloudService`.
- **Celery opcional**: el código degrada a hilos si Redis no está, lo cual es muy práctico para desarrollo.
- **SSE para progreso** del indexador y categorizer — buena UX en el panel.
- **Rate limiter en login** (`5 per minute`) — buena práctica.
- **CSRF habilitado** y tokens presentes en templates.
- **Validación de tabla en `db_explorer`** con regex (aunque insuficiente, hay intención).
- **`ON CONFLICT DO UPDATE`** en `register_file` para evitar duplicados.
- **Reintentos exponenciales** en `_connect` (3 intentos con backoff).
- **Reintentos** del reporte final en `upload_process` ante timeouts de red.

---

## 🎯 Acciones recomendadas (priorizadas)

### Sprint inmediato (1–2 días)
1. **Rotar el token de Telegram** del `.env.example` y reemplazar por placeholders neutros.
2. **Arreglar `requirements.txt`** con versiones reales:
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt  # esto fallará; ir ajustando hasta que pase
   pip freeze > requirements.txt
   ```
3. **Renombrar columna `name` → `nombre`** en `users` (o cambiar el código). El login del panel está roto a la vista.
4. **Borrar el primer `reset_failed_embeddings`** (línea 553, queda muerto).
5. **Quitar la `r` del f-string** en `main.py:879`.
6. **Listar tablas permitidas** en `/db-explorer` y enmascarar `password_hash`.
7. **Hacer obligatorio `FLASK_SECRET_KEY`** (fail-fast si no está).
8. **Corregir el middleware `auth_middleware`** para denegar también cuando `user` es `None`.

### Sprint corto (1 semana)
9. **Mover secretos OAuth y API keys a tabla `app_secrets`** en PostgreSQL (cifrados).
10. **`ruff check --fix /app`** y luego revisar los manuales.
11. **Migrar `flask_limiter` y `RATE_LIMIT_STATE`** a Redis (ya disponible).
12. **Estandarizar `get_file_by_id`** a devolver siempre dict.
13. **Eliminar Gemini** del pipeline si ya no se usa (o documentar claramente cuándo se activa).

### Backlog (mediano plazo)
14. Modularizar `main.py` y `web_admin.py` en sub-módulos / Blueprints.
15. Empezar a escribir tests (BM25, DB setup, /health).
16. Cambiar el almacenamiento del índice BM25 (`rank_bm25` o caché en Redis).
17. Usar `pgvector` Python binding para enviar vectores binarios.
18. Decidir entre Render y Oracle VM; eliminar uno de los workflows.
19. Añadir `README.md` actualizado con el flujo de despliegue y los ENV vars.

---

## 🧪 Cómo verificar este reporte

Para confirmar los hallazgos sin levantar todo el stack:

```bash
cd /app

# 1. Confirmar versiones inválidas
pip install pandas==3.0.1     # → ERROR: No matching distribution
pip install gunicorn==25.1.0  # → ERROR: No matching distribution

# 2. Confirmar columna inconsistente
grep -n "nombre" src/database/db_handler.py web_admin.py

# 3. Confirmar duplicado reset_failed_embeddings
grep -n "def reset_failed_embeddings" src/database/db_handler.py

# 4. Confirmar ruff
pip install ruff
ruff check .

# 5. Confirmar tokens en .env.example
cat .env.example | head -3
```

---

## 🎁 Bonus: una idea de producto

Cuando arregles lo crítico, considera añadir un endpoint **`/api/v1/search`** público (con API key) para que tus archivos indexados se puedan consultar desde otras apps (un Notion plugin, un Raycast extension, una Shortcut de iOS). Hoy todo está acoplado a Telegram + el panel web; expones una API y tu indexación pasa a ser un **knowledge graph reutilizable**. Es a un día de trabajo desde el estado actual.

---

*Fin del reporte. Si quieres, puedo continuar y **aplicar los fixes P0 y P1** uno por uno; bastará con que me digas "procede".*
