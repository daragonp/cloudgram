# Tareas de Implementación

## Fase 1: Núcleo y Rendimiento (Alta Prioridad)
- [x] 1. Búsqueda Vectorial Nativa (`pgvector`)
  - [x] Crear script de migración para activar `vector` en Supabase y alterar la tabla `files`
  - [x] Refactorizar `search_semantic` en `src/database/db_handler.py`
  - [x] Ajustar lógica de inserción/actualización de embeddings para soportar `pgvector` nativo.

## Fase 2: Experiencia de Usuario e IA (Alto Impacto)
- [x] 2. Chat con Documentos Específicos (RAG)
  - [x] Añadir comando `/preguntar <ID> <pregunta>` en `main.py` y `message_handlers.py`
  - [x] Implementar lógica de RAG en `src/utils/ai_handler.py`
- [x] 3. Auto-Etiquetado Inteligente (Hashtags)
  - [x] Modificar prompt en `generate_summary` (`ai_handler.py`)
  - [x] Actualizar base de datos y UI de Telegram para guardar/mostrar tags

## Fase 3: UX y Administración (Medio Impacto)
- [x] 4. Modo Inline en Telegram (`@botname buscar`)
  - [x] Configurar `InlineQueryHandler` en `main.py`
  - [x] Integrar con `db.search_semantic`
- [x] 5. Visor de Logs en el Panel Web
  - [x] Crear ruta `/logs` en `web_admin.py`
  - [x] Añadir template `logs.html`

## Fase 4: Opcionales (Casos de Borde)
- [x] 6. Procesamiento de `.ZIP`
- [/] 7. Rate Limiting (Anti-Spam) para Telegram y Celery/Redis (A definir con el usuario)
  - [x] Limitar comandos de Telegram `/buscar_ia` y `/preguntar`
  - [ ] Evaluar Celery/Redis
