# Plan de Implementación: Mejoras para CloudGram Pro

Este plan detalla la hoja de ruta para implementar las mejoras detectadas en el análisis del bot. Las fases están ordenadas desde el mayor impacto funcional y de rendimiento, hasta las mejoras opcionales de UX.

## User Review Required

> [!IMPORTANT]  
> Revisa este plan de implementación. Si apruebas el plan general, procederé a ejecutar la **Fase 1** y avanzaremos progresivamente. Puedes indicar si prefieres cambiar el orden de las prioridades.

## Open Questions

> [!WARNING]  
> 1. **Redis:** Para implementar colas de tareas robustas (Celery) necesitamos un servidor Redis. En Render o Railway suele ser fácil añadir uno. ¿Cuentas con disponibilidad para añadir Redis a tu infraestructura, o prefieres mantener los procesos en segundo plano ligeros (sin Celery por ahora)?
> 2. **Base de datos (pgvector):** ¿Confirmas que tu instancia de Supabase actual tiene los permisos suficientes para ejecutar `CREATE EXTENSION IF NOT EXISTS vector;`?

---

## Fases de Implementación

### Fase 1: Núcleo y Rendimiento (Alta Prioridad)

Mejoras críticas para que el bot pueda escalar a miles de archivos sin que el servidor de Telegram o la Base de Datos colapsen.

1. **Búsqueda Vectorial Nativa (`pgvector`)**
   - **Objetivo:** Mover el cálculo de similitud (cosine similarity) de Python (`numpy`) a PostgreSQL para búsquedas instantáneas.
   - **Archivos afectados:** `src/database/db_handler.py`
   - **Acciones:**
     - Crear script de migración para activar la extensión `vector` en Supabase y cambiar la columna `embedding` a tipo `vector(1536)`.
     - Refactorizar `search_semantic` para usar `ORDER BY embedding <-> %s LIMIT X`.
2. **Cola de Tareas para Indexación Web (Celery / Redis)** *(Depende de tu respuesta a Open Questions)*
   - **Objetivo:** Evitar que los procesos masivos del panel web (`/run-embeddings`) se cancelen si el servidor de Render se reinicia.
   - **Archivos afectados:** `web_admin.py`, `requirements.txt`, nuevo archivo `celery_worker.py`.

### Fase 2: Experiencia de Usuario e IA (Alto Impacto)

Nuevas capacidades para que el bot sea más interactivo y útil.

3. **Chat con Documentos Específicos (RAG - Retrieval-Augmented Generation)**
   - **Objetivo:** Comando `/preguntar <ID> <pregunta>` para consultar dudas exactas sobre un contrato, PDF o imagen, usando `gpt-4o-mini`.
   - **Archivos afectados:** `main.py`, `src/handlers/message_handlers.py`, `src/utils/ai_handler.py`.
4. **Auto-Etiquetado Inteligente (Hashtags)**
   - **Objetivo:** Modificar el prompt de resumen de IA para que genere 3-5 hashtags relevantes que se guardarán junto al resumen.
   - **Archivos afectados:** `src/utils/ai_handler.py` y `src/handlers/message_handlers.py`.

### Fase 3: UX y Administración (Medio Impacto)

Mejoras de usabilidad dentro de Telegram y para el administrador en el panel web.

5. **Modo Inline en Telegram (`@botname buscar`)**
   - **Objetivo:** Buscar archivos en tu nube escribiendo `@tu_bot_usuario consulta` en cualquier chat privado o grupo, para compartir enlaces rápidamente.
   - **Archivos afectados:** `main.py` (Añadir `InlineQueryHandler`).
6. **Visor de Logs en el Panel Web**
   - **Objetivo:** Interfaz gráfica para ver los errores, warnings y actividades del bot (tabla `bot_logs`).
   - **Archivos afectados:** `web_admin.py`, `templates/logs.html` (NUEVO), `templates/base.html` (Añadir link en sidebar).

### Fase 4: Opcionales y Casos de Borde

7. **Procesamiento de Archivos `.ZIP`**
   - **Objetivo:** Permitir al usuario descomprimir un archivo ZIP enviado al bot y procesar/indexar sus archivos internos individualmente.
   - **Archivos afectados:** `src/handlers/message_handlers.py`.
8. **Seguridad: Rate Limiting para Telegram**
   - **Objetivo:** Limitar la cantidad de consultas de IA (ej. `/buscar_ia`) por usuario en Telegram para evitar agotar el saldo de OpenAI por spam.
   - **Archivos afectados:** `main.py`, middleware en `src/handlers/message_handlers.py`.

## Verification Plan

### Automated Tests
- Verificaré que el esquema de la base de datos se actualice correctamente al usar `pgvector` antes de alterar datos.
- Usaré `view_file` para comprobar si las dependencias (ej. Celery) no rompen la versión actual de la app.

### Manual Verification
- Te pediré que pruebes el modo "Inline" en Telegram.
- Revisaremos juntos el panel de "Logs" en Flask.
- Confirmaremos que el comando `/preguntar` extrae la información correcta de un documento de prueba.
