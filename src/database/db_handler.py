import psycopg2
from psycopg2.extras import RealDictCursor
import json
import numpy as np
from datetime import datetime
import os
import sqlite3 
import time
import logging

logger = logging.getLogger(__name__)

class ConnectionWrapper:
    """Envoltorio para asegurar que la conexión se cierre al salir de un bloque with."""
    def __init__(self, conn):
        self.conn = conn
    def __enter__(self):
        # El método __enter__ de psycopg2 devuelve la conexión
        return self.conn.__enter__()
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            # El método __exit__ de psycopg2 maneja el commit/rollback
            return self.conn.__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.conn.close()

class DatabaseHandler:

    def __init__(self):
        # 1. Intentar capturar la URL de Railway
        self.db_url = os.getenv("DATABASE_URL")
        
        print(f"--- DIAGNÓSTICO DE DB ---")
        if not self.db_url:
            print("❌ ERROR: La variable DATABASE_URL está VACÍA en Railway.")
            # Si está vacía, aquí es donde creaba el .db. Vamos a forzar el error mejor.
            self.db_url = "sqlite:///error_no_variable.db" 
        else:
            # Aseguramos el prefijo correcto para SQLAlchemy/Psycopg2
            if self.db_url.startswith("postgres://"):
                self.db_url = self.db_url.replace("postgres://", "postgresql://", 1)
            print(f"✅ Variable detectada: {self.db_url[:15]}...")
        
        self._setup_initial_db()

    def _connect(self):
        # Si la URL es de Postgres, usamos psycopg2 con reintentos
        if "postgresql" in self.db_url:
            max_attempts = 3
            last_err = None
            for attempt in range(max_attempts):
                try:
                    conn = psycopg2.connect(self.db_url)
                    return ConnectionWrapper(conn)
                except Exception as e:
                    last_err = e
                    print(f"⚠️ Intento {attempt + 1}/{max_attempts} de conexión DB fallido: {e}")
                    if attempt < max_attempts - 1:
                        time.sleep(2 ** attempt) # Exponential backoff simple
            
            print(f"❌ ERROR AGOTADO DE CONEXIÓN A SUPABASE: {last_err}")
            raise last_err
        else:
            # Si por alguna razón sigue intentando SQLite
            print("⚠️ CUIDADO: Usando SQLite local.")
            return sqlite3.connect("cloudgram.db")

    def _setup_initial_db(self):
        """Crea las tablas con las nuevas columnas y la restricción UNIQUE."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                # 1. Tabla de Usuarios
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        name TEXT NOT NULL,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        telegram_id TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # 2. Tabla de Carpetas (Necesaria para folder_id)
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS folders (
                        id SERIAL PRIMARY KEY,
                        name TEXT NOT NULL,
                        service TEXT,
                        cloud_folder_id TEXT,
                        parent_id INTEGER REFERENCES folders(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # 3. Tabla de Archivos (CON COLUMNAS NUEVAS Y CONSTRAINT)
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS files (
                        id SERIAL PRIMARY KEY,
                        telegram_id TEXT,
                        user_id INTEGER REFERENCES users(id),
                        name TEXT,
                        type TEXT,
                        cloud_url TEXT,
                        service TEXT,
                        content_text TEXT,
                        embedding TEXT,
                        summary TEXT,
                        technical_description TEXT,
                        tags TEXT,
                        folder_id INTEGER REFERENCES folders(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT unique_file_per_service UNIQUE (name, service)
                    )
                ''')
                
                # 4. Tabla de Caché de Carpetas de Categoría
                try:
                    cur.execute('''
                        CREATE TABLE IF NOT EXISTS category_folder_cache (
                            id SERIAL PRIMARY KEY,
                            category_name TEXT NOT NULL,
                            service TEXT NOT NULL,
                            cloud_id TEXT NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            CONSTRAINT unique_category_service UNIQUE (category_name, service)
                        )
                    ''')
                except Exception as e:
                    print(f"⚠️  Tabla category_folder_cache ya existe: {e}")

                
                # 5. Tabla de Logs del Sistema
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS bot_logs (
                        id SERIAL PRIMARY KEY,
                        level VARCHAR(20) NOT NULL,
                        module VARCHAR(100),
                        message TEXT NOT NULL,
                        metadata JSONB,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Migración manual por si las columnas no existen en tablas ya creadas
                try:
                    cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS summary TEXT")
                    cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS technical_description TEXT")
                    cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS tags TEXT")
                    cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS folder_id INTEGER")
                except: pass

            conn.commit()
            
    # --- FUNCIONES DE LOGS DEL SISTEMA ---
    
    def log_event(self, level, module, message, metadata=None):
        """Registra un evento en la base de datos (logs)."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    # En SQLite no existe JSONB, usamos TEXT para metadata como compatibilidad cruzada básico
                    # Sin embargo, usamos Postgres primariamente.
                    meta_str = json.dumps(metadata) if metadata else None
                    cur.execute("""
                        INSERT INTO bot_logs (level, module, message, metadata)
                        VALUES (%s, %s, %s, %s)
                    """, (level, module, message, meta_str))
                    conn.commit()
        except Exception as e:
            print(f"❌ Error guardando log: {e}")

    def get_logs(self, limit=100, level=None, module=None, start_date=None, end_date=None):
        """Obtiene logs con filtros opcionales."""
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    query = "SELECT * FROM bot_logs WHERE 1=1"
                    params = []
                    
                    if level and level != 'ALL':
                        query += " AND level = %s"
                        params.append(level)
                    if module:
                        query += " AND module = %s"
                        params.append(module)
                    if start_date:
                        query += " AND created_at >= %s"
                        params.append(start_date)
                    if end_date:
                        query += " AND created_at <= %s"
                        params.append(end_date)
                        
                    query += f" ORDER BY created_at DESC LIMIT {int(limit)}"
                    
                    cur.execute(query, tuple(params))
                    return cur.fetchall()
        except Exception as e:
            print(f"❌ Error recuperando logs: {e}")
            return []

    # --- FUNCIONES DE CACHÉ DE CARPETAS CATEGORÍA ---
    
    def save_category_folder(self, category_name, service, cloud_id):
        """Guarda o actualiza el ID de una carpeta de categoría en la BD."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO category_folder_cache (category_name, service, cloud_id)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (category_name, service)
                        DO UPDATE SET cloud_id = EXCLUDED.cloud_id
                    """, (category_name, service, cloud_id))
                conn.commit()
                return True
        except Exception as e:
            print(f"❌ Error guardando caché de carpeta: {e}")
            return False

    def get_category_folder(self, category_name, service):
        """Recupera el ID de una carpeta de categoría desde la BD."""
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT cloud_id FROM category_folder_cache
                        WHERE category_name = %s AND service = %s
                    """, (category_name, service))
                    result = cur.fetchone()
                    return result['cloud_id'] if result else None
        except Exception as e:
            print(f"❌ Error recuperando caché de carpeta: {e}")
            return None

    def load_category_cache(self):
        """Carga el caché completo de carpetas desde la BD."""
        cache = {'dropbox': {}, 'drive': {}, 'onedrive': {}}
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT category_name, service, cloud_id FROM category_folder_cache")
                    for row in cur.fetchall():
                        service = row['service'].lower()
                        if service in ['dropbox', 'drive', 'onedrive']:
                            cache[service][row['category_name']] = row['cloud_id']
        except Exception as e:
            print(f"⚠️  Error cargando caché de carpetas: {e}")
        return cache
    
    # --- FUNCIONES DEL BOT ---
    
    def register_file(self, telegram_id, name, f_type, cloud_url, service, content_text=None, embedding=None, folder_id=None, summary=None, technical_description=None, tags=None):
        """Registro con ON CONFLICT corregido."""
        try:
            # Convertir embedding a string JSON si es una lista/array
            if isinstance(embedding, (list, np.ndarray)):
                embedding = json.dumps(embedding.tolist() if isinstance(embedding, np.ndarray) else embedding)

            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO files (
                            telegram_id, name, type, cloud_url, service, 
                            content_text, embedding, folder_id, summary, technical_description, tags
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (name, service) 
                        DO UPDATE SET 
                            summary = COALESCE(EXCLUDED.summary, files.summary),
                            technical_description = COALESCE(EXCLUDED.technical_description, files.technical_description),
                            tags = COALESCE(EXCLUDED.tags, files.tags),
                            embedding = COALESCE(EXCLUDED.embedding, files.embedding),
                            content_text = COALESCE(EXCLUDED.content_text, files.content_text),
                            cloud_url = EXCLUDED.cloud_url,
                            telegram_id = EXCLUDED.telegram_id
                    """, (
                        telegram_id, name, f_type, cloud_url, service, 
                        content_text, embedding, folder_id, summary, technical_description, tags
                    ))
                    conn.commit()
                    print(f"✅ DB: Archivo '{name}' registrado/actualizado.")
        except Exception as e:
            print(f"❌ ERROR CRÍTICO DB EN register_file: {e}")
            
    def search_by_name(self, keyword):
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    # Traemos los nuevos campos para el Bot
                    query = """
                    SELECT id, name, cloud_url, service, summary, technical_description, tags 
                    FROM files 
                    WHERE name ILIKE %s 
                    OR type ILIKE %s 
                    OR technical_description ILIKE %s
                    OR tags ILIKE %s
                    LIMIT 1000
                    """
                    like_keyword = f'%{keyword}%'
                    cur.execute(query, (like_keyword, like_keyword, like_keyword, like_keyword))
                    return cur.fetchall()
        except Exception as e:
            print(f"❌ Error en search_by_name: {e}")
            return []
        
    def search_semantic(self, query_embedding, limit=5, file_types=None):
        """Búsqueda vectorial con cálculo de similitud y soporte de filtros de tipo de archivo (nativo con pgvector)."""
        try:
            with self._connect() as conn:
                # Usamos RealDictCursor
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    query_vec_str = json.dumps(query_embedding.tolist() if isinstance(query_embedding, np.ndarray) else query_embedding)
                    
                    query = '''
                        SELECT id, name, cloud_url, summary, service, tags,
                               1 - (embedding <=> %s::vector) AS similarity 
                        FROM files 
                        WHERE embedding IS NOT NULL
                    '''
                    params = [query_vec_str]
                    
                    if file_types and isinstance(file_types, list) and len(file_types) > 0:
                        type_conditions = []
                        for ft in file_types:
                            ft_clean = ft.replace('.', '').strip().lower()
                            type_conditions.append("name ILIKE %s OR type ILIKE %s")
                            params.extend([f"%.{ft_clean}%", f"%{ft_clean}%"])
                            
                        if type_conditions:
                            query += f" AND ({' OR '.join(type_conditions)})"
                            
                    query += f" ORDER BY embedding <=> %s::vector LIMIT {int(limit)}"
                    params.append(query_vec_str)
                    
                    cur.execute(query, tuple(params))
                    results = cur.fetchall()
                    
                    formatted_results = []
                    for r in results:
                        formatted_results.append({
                            "id": r['id'],
                            "name": r['name'],
                            "url": r['cloud_url'],
                            "similarity": float(r['similarity']) if r['similarity'] is not None else 0.0,
                            "summary": r['summary'],
                            "service": r['service'],
                            "tags": r.get('tags')
                        })
                    
                    return formatted_results
        except Exception as e:
            print(f"❌ Error semántico pgvector: {e}")
            return []
    
    def search_fulltext_improved(self, query: str, limit: int = 20, file_types=None):
        """
        Búsqueda full-text mejorada con ranking BM25 (localStorage).
        Obtiene todos los candidatos y rankea con BM25 en memoria.
        Esto es más rápido y efectivo que ILIKE.
        """
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    # Obtener TODOS los documentos posibles (sin filtrar aún)
                    like_query = f'%{query}%'
                    sql = '''
                        SELECT id, name, cloud_url, summary, service, tags, type, technical_description
                        FROM files
                        WHERE 
                            name ILIKE %s OR
                            tags ILIKE %s OR
                            technical_description ILIKE %s OR
                            summary ILIKE %s
                        LIMIT 200
                    '''
                    
                    params = [like_query, like_query, like_query, like_query]
                    
                    # Filtrar por tipos si es necesario
                    if file_types and isinstance(file_types, list) and len(file_types) > 0:
                        type_conditions = []
                        for ft in file_types:
                            ft_clean = ft.replace('.', '').strip().lower()
                            type_conditions.append("(name ILIKE %s OR type ILIKE %s)")
                            params.extend([f"%.{ft_clean}%", f"%{ft_clean}%"])
                        
                        sql = sql.replace("WHERE", f"WHERE ({' OR '.join(type_conditions)}) AND")
                    
                    cur.execute(sql, tuple(params))
                    raw_results = cur.fetchall()
                    
                    # Aplicar BM25 ranking en memoria
                    if raw_results:
                        try:
                            from src.search.bm25_search import BM25Search
                            
                            # Convertir a dicts para BM25
                            docs_for_bm25 = [dict(r) for r in raw_results]
                            
                            # Rankear con BM25 (pesos: nombre > tags > desc > summary)
                            ranked = BM25Search.search(
                                docs_for_bm25, 
                                query,
                                field_weights={'name': 3.0, 'tags': 2.0, 'technical_description': 1.5, 'summary': 1.0}
                            )
                            
                            # Formatear resultados
                            formatted_results = []
                            for doc, bm25_score in ranked[:limit]:
                                formatted_results.append({
                                    "id": doc['id'],
                                    "name": doc['name'],
                                    "url": doc['cloud_url'],
                                    "score": min(bm25_score / 20.0, 1.0),  # Normalizar a 0-1
                                    "summary": doc['summary'],
                                    "service": doc['service'],
                                    "tags": doc.get('tags'),
                                    "type": doc.get('type')
                                })
                            
                            return formatted_results
                        except Exception as bm25_error:
                            logger.warning(f"BM25 error, fallback a ILIKE: {bm25_error}")
                            # Fallback a scoring simple
                            formatted_results = []
                            for r in raw_results[:limit]:
                                formatted_results.append({
                                    "id": r['id'],
                                    "name": r['name'],
                                    "url": r['cloud_url'],
                                    "score": 0.5,
                                    "summary": r['summary'],
                                    "service": r['service'],
                                    "tags": r.get('tags'),
                                    "type": r.get('type')
                                })
                            return formatted_results
                    
                    return []
        except Exception as e:
            logger.error(f"❌ Error en búsqueda full-text: {e}")
            return []
    
    def search_by_metadata(self, query: str, limit: int = 20, file_types=None):
        """
        Búsqueda por metadatos (tags, descripción técnica).
        Útil para búsquedas más específicas.
        """
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    like_query = f'%{query}%'
                    
                    sql = '''
                        SELECT 
                            id, name, cloud_url, summary, service, tags, type,
                            CASE 
                                WHEN tags ILIKE %s THEN 8.0
                                WHEN technical_description ILIKE %s THEN 6.0
                                WHEN summary ILIKE %s THEN 3.0
                                ELSE 1.0
                            END as metadata_score
                        FROM files
                        WHERE 
                            tags IS NOT NULL AND tags ILIKE %s
                            OR technical_description IS NOT NULL AND technical_description ILIKE %s
                    '''
                    
                    params = [
                        like_query,  # tags exacto
                        like_query,  # technical description
                        like_query,  # summary
                        like_query,  # WHERE - tags
                        like_query   # WHERE - technical description
                    ]
                    
                    # Filtrar por tipos
                    if file_types and isinstance(file_types, list) and len(file_types) > 0:
                        type_conditions = []
                        for ft in file_types:
                            ft_clean = ft.replace('.', '').strip().lower()
                            type_conditions.append("(name ILIKE %s OR type ILIKE %s)")
                            params.extend([f"%.{ft_clean}%", f"%{ft_clean}%"])
                        
                        sql += f" AND ({' OR '.join(type_conditions)})"
                    
                    sql += " ORDER BY metadata_score DESC LIMIT %s"
                    params.append(limit)
                    
                    cur.execute(sql, tuple(params))
                    results = cur.fetchall()
                    
                    formatted_results = []
                    for r in results:
                        formatted_results.append({
                            "id": r['id'],
                            "name": r['name'],
                            "url": r['cloud_url'],
                            "score": float(r['metadata_score']) / 8.0,  # Normalizar a 0-1
                            "summary": r['summary'],
                            "service": r['service'],
                            "tags": r.get('tags'),
                            "type": r.get('type')
                        })
                    
                    return formatted_results
        except Exception as e:
            print(f"❌ Error en búsqueda de metadata: {e}")
            return []

    def get_last_files(self, limit=20):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id, name, cloud_url, service, created_at FROM files ORDER BY created_at DESC LIMIT %s', (limit,))
                return cur.fetchall()

    def get_file_by_id(self, file_id):
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, telegram_id, user_id, name, type, cloud_url, service, content_text, embedding, summary, technical_description, tags, folder_id, created_at FROM files WHERE id = %s",
                    (file_id,)
                )
                return cur.fetchone()
    # --- IA Y WEB ---
    
    def get_user_by_email(self, email):
        with self._connect() as conn:
            # RealDictCursor emula el sqlite3.Row para acceder por nombre: user['id']
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT * FROM users WHERE email = %s', (email,))
                return cur.fetchone()

    def get_user_by_id(self, user_id):
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT * FROM users WHERE id = %s', (user_id,))
                return cur.fetchone()
    
    def get_all_with_embeddings(self):
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('''
                    SELECT id, name, cloud_url, service, content_text, embedding::text AS embedding 
                    FROM files 
                    WHERE embedding IS NOT NULL 
                ''')
                rows = cur.fetchall()
                return [(r['id'], r['name'], r['cloud_url'], r['service'], r['content_text'], r['embedding']) 
                        for r in rows]
    
    def reset_all_embeddings(self):
        """Reinicia TODOS los archivos (borra resúmenes y embeddings) para un recálculo desde cero."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE files SET embedding = NULL, summary = NULL, content_text = NULL")
                conn.commit()
        except Exception as e:
            print(f"❌ Error al resetear toda la DB: {e}")

    # --- INDEXACIÓN MANUAL ---

    def get_files_without_embedding(self, limit=10, offset=0):
        """Retorna archivos que no tienen embedding válido, paginados.
        
        Incluye archivos donde embedding es NULL, vacío, '[]' o 'error_limit'.
        Se ordenan por fecha de creación descendente para mostrar los más recientes primero.
        
        Returns:
            list[dict]: Lista de dicts con id, name, service, cloud_url, content_text
        """
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT id, name, service, cloud_url, content_text, type
                        FROM files
                        WHERE embedding IS NULL
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s
                    """, (limit, offset))
                    return cur.fetchall()
        except Exception as e:
            print(f"❌ Error en get_files_without_embedding: {e}")
            return []

    def count_files_without_embedding(self):
        """Retorna el total de archivos sin embedding válido."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(*) FROM files
                        WHERE embedding IS NULL
                    """)
                    return cur.fetchone()[0]
        except Exception as e:
            print(f"❌ Error en count_files_without_embedding: {e}")
            return 0

    def update_file_embedding(self, file_id, embedding, summary=None, content_text=None, tags=None):
        """Actualiza embedding, summary, content_text y tags de un archivo ya registrado.
        
        Se usa después de re-indexar un archivo desde el bot sin necesidad de re-subirlo.
        Solo actualiza los campos que se pasen como no-None.
        
        Args:
            file_id: ID del archivo en la tabla files
            embedding: Lista/array con el vector de embedding
            summary: Resumen del contenido (opcional)
            content_text: Texto extraído del archivo (opcional)
            tags: Etiquetas generadas por IA (opcional)
        """
        try:
            emb_json = json.dumps(
                embedding.tolist() if isinstance(embedding, np.ndarray) else embedding
            ) if isinstance(embedding, (list, np.ndarray)) else embedding

            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE files
                        SET embedding = %s,
                            summary = COALESCE(%s, summary),
                            content_text = COALESCE(%s, content_text),
                            tags = COALESCE(%s, tags)
                        WHERE id = %s
                    """, (emb_json, summary, content_text, tags, file_id))
                conn.commit()
            print(f"✅ DB: Embedding actualizado para archivo ID={file_id}")
            return True
        except Exception as e:
            print(f"❌ Error en update_file_embedding (id={file_id}): {e}")
            return False



    def clean_corrupted_files(self):
        """Blanquea solo los archivos cuyo analysis IA falló guardando mensajes de error en base de datos."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE files 
                        SET content_text = NULL, summary = NULL, embedding = NULL
                        WHERE content_text LIKE 'Error: %' 
                           OR summary LIKE 'Resumen no disponible%'
                           OR content_text LIKE 'Error al extraer%'
                           OR content_text LIKE '[Error en transcripci%'
                    """)
                    affected = cur.rowcount
                conn.commit()
                return affected
        except Exception as e:
            print(f"❌ Error al limpiar archivos corruptos: {e}")
            return 0

    def check_connection(self):
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    print("✅ Conexión a Supabase: EXITOSA")
                    return True
        except Exception as e:
            print(f"❌ Error de conexión a Supabase: {e}")
            return False
        
    def check_db_type(self):
        if "supabase" in self.db_url.lower() or "postgre" in self.db_url.lower():
            print("🛢️ CONECTADO A: Supabase (PostgreSQL)")
        else:
            print("⚠️ CONECTADO A: SQLite Local (¡Cuidado en Railway!)")

    def create_folder(self, name, service, cloud_folder_id, parent_id=None):
        with self._connect() as conn:
            # Usar RealDictCursor convierte la tupla en diccionario automáticamente
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO folders (name, service, cloud_folder_id, parent_id)
                    VALUES (%s, %s, %s, %s) RETURNING id
                """, (name, service, cloud_folder_id, parent_id))
                result = cur.fetchone()
                return result['id'] # Ahora sí funcionará ['id']

    def get_folder_by_id(self, folder_id):
        if not folder_id or folder_id == "root": return None
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM folders WHERE id = %s", (folder_id,))
                return cur.fetchone()

    def get_folder_contents(self, parent_id=None, service=None):
        """Retorna subcarpetas y archivos usando diccionarios, filtrado por servicio."""
        with self._connect() as conn:
            # El cursor_factory es la clave para que item['type'] funcione
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Obtener Carpetas
                if parent_id is None or parent_id == "root":
                    if service:
                        cur.execute("SELECT id, name, 'folder' as type FROM folders WHERE parent_id IS NULL AND service = %s", (service,))
                    else:
                        cur.execute("SELECT id, name, 'folder' as type FROM folders WHERE parent_id IS NULL")
                else:
                    if service:
                        cur.execute("SELECT id, name, 'folder' as type FROM folders WHERE parent_id = %s AND service = %s", (parent_id, service))
                    else:
                        cur.execute("SELECT id, name, 'folder' as type FROM folders WHERE parent_id = %s", (parent_id,))
                subfolders = cur.fetchall()
                
                # 2. Obtener Archivos
                if parent_id is None or parent_id == "root":
                    if service:
                        cur.execute("SELECT id, name, 'file' as type FROM files WHERE folder_id IS NULL AND service = %s", (service,))
                    else:
                        cur.execute("SELECT id, name, 'file' as type FROM files WHERE folder_id IS NULL")
                else:
                    if service:
                        cur.execute("SELECT id, name, 'file' as type FROM files WHERE folder_id = %s AND service = %s", (parent_id, service))
                    else:
                        cur.execute("SELECT id, name, 'file' as type FROM files WHERE folder_id = %s", (parent_id,))
                files = cur.fetchall()
                
                return subfolders + files

    def get_parent_folder(self, folder_id):
        
        """Obtiene la carpeta padre para el botón 'Volver atrás'"""
        if not folder_id or folder_id == "root": return None
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT p.id, p.name 
                    FROM folders c 
                    LEFT JOIN folders p ON c.parent_id = p.id 
                    WHERE c.id = %s
                """, (folder_id,))
                return cur.fetchone()

    def get_all_files(self):
        """Retorna todos los archivos registrados para la exportación CSV."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, name, cloud_url, service, type, content_text, created_at 
                        FROM files ORDER BY created_at DESC
                    """)
                    columns = [desc[0] for desc in cur.description]
                    return [dict(zip(columns, row)) for row in cur.fetchall()]
        except Exception as e:
            print(f"❌ Error en get_all_files: {e}")
            return []

    def get_file_by_name_and_service(self, name, service):
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM files WHERE name = %s AND service = %s", (name, service))
                    return cur.fetchone()
        except: return None

    def delete_file_by_id(self, file_id):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM files WHERE id = %s', (file_id,))
            conn.commit()
            
    def reset_failed_embeddings(self):
        
        """Limpia el campo embedding de los archivos que no se procesaron bien."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE files SET embedding = NULL WHERE content_text IS NULL OR content_text = ''")
                    conn.commit()
                    return True
        except Exception as e:
            print(f"❌ Error en reset_failed_embeddings: {e}")
            return False
        
    def export_to_sql(self):
        """Genera un string con el volcado SQL completo de la tabla files."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    # Obtener todos los registros
                    cur.execute("SELECT * FROM files")
                    rows = cur.fetchall()
                    colnames = [desc[0] for desc in cur.description]
                    
                    sql_output = "-- CloudGram Backup SQL\n"
                    sql_output += f"-- Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    
                    # Sentencia de creación (Ajusta los tipos según tu esquema real)
                    sql_output += "CREATE TABLE IF NOT EXISTS files (\n"
                    sql_output += "    id SERIAL PRIMARY KEY,\n"
                    sql_output += "    telegram_id BIGINT,\n"
                    sql_output += "    name TEXT,\n"
                    sql_output += "    f_type TEXT,\n"
                    sql_output += "    cloud_url TEXT,\n"
                    sql_output += "    service TEXT,\n"
                    sql_output += "    content_text TEXT,\n"
                    sql_output += "    embedding VECTOR(1536), -- Si usas pgvector\n"
                    sql_output += "    folder_id TEXT,\n"
                    sql_output += "    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n"
                    sql_output += ");\n\n"

                    # Generar Inserts
                    for row in rows:
                        values = []
                        for val in row:
                            if val is None:
                                values.append("NULL")
                            elif isinstance(val, (int, float)):
                                values.append(str(val))
                            else:
                                # Escapar comillas simples para SQL
                                safe_val = str(val).replace("'", "''")
                                values.append(f"'{safe_val}'")
                        
                        sql_output += f"INSERT INTO files ({', '.join(colnames)}) VALUES ({', '.join(values)});\n"
                    
                    return sql_output
        except Exception as e:
            print(f"❌ Error generando SQL: {e}")
            return f"-- Error en la exportación: {e}"
    
    def update_user_name(self, user_id, nuevo_nombre):
        """Actualiza el nombre para mostrar del administrador.

        La columna en la tabla `users` se llama `name` (ver _setup_initial_db).
        Históricamente este método apuntaba a `nombre` y rompía el endpoint
        /perfil del panel web.
        """
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET name = %s WHERE id = %s",
                        (nuevo_nombre, user_id)
                    )
                    conn.commit()
            return True
        except Exception as e:
            print(f"❌ Error actualizando nombre: {e}")
            return False

    def update_user_password(self, user_id, nuevo_hash):
        """Actualiza la contraseña (hash) del administrador"""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET password_hash = %s WHERE id = %s",
                        (nuevo_hash, user_id)
                    )
                    conn.commit()
            return True
        except Exception as e:
            print(f"❌ Error actualizando password: {e}")
            return False