import psycopg2
from psycopg2.extras import RealDictCursor
import json
import numpy as np
from datetime import datetime
import os
import sqlite3 


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
        # Si la URL es de Postgres, usamos psycopg2
        if "postgresql" in self.db_url:
            try:
                return psycopg2.connect(self.db_url)
            except Exception as e:
                print(f"❌ ERROR DE CONEXIÓN A SUPABASE: {e}")
                raise e
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

                
                # Migración manual por si las columnas no existen en tablas ya creadas
                try:
                    cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS summary TEXT")
                    cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS technical_description TEXT")
                    cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS folder_id INTEGER")
                except: pass

            conn.commit()
    
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
        cache = {'dropbox': {}, 'drive': {}}
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT category_name, service, cloud_id FROM category_folder_cache")
                    for row in cur.fetchall():
                        service = row['service'].lower()
                        if service in ['dropbox', 'drive']:
                            cache[service][row['category_name']] = row['cloud_id']
        except Exception as e:
            print(f"⚠️  Error cargando caché de carpetas: {e}")
        return cache
    
    # --- FUNCIONES DEL BOT ---
    
    def register_file(self, telegram_id, name, f_type, cloud_url, service, content_text=None, embedding=None, folder_id=None, summary=None, technical_description=None):
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
                            content_text, embedding, folder_id, summary, technical_description
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (name, service) 
                        DO UPDATE SET 
                            summary = EXCLUDED.summary,
                            technical_description = EXCLUDED.technical_description,
                            embedding = COALESCE(EXCLUDED.embedding, files.embedding),
                            content_text = COALESCE(EXCLUDED.content_text, files.content_text),
                            cloud_url = EXCLUDED.cloud_url,
                            telegram_id = EXCLUDED.telegram_id
                    """, (
                        telegram_id, name, f_type, cloud_url, service, 
                        content_text, embedding, folder_id, summary, technical_description
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
                    SELECT id, name, cloud_url, service, summary, technical_description 
                    FROM files 
                    WHERE name ILIKE %s 
                    OR type ILIKE %s 
                    OR technical_description ILIKE %s
                    LIMIT 1000
                    """
                    like_keyword = f'%{keyword}%'
                    cur.execute(query, (like_keyword, like_keyword, like_keyword))
                    return cur.fetchall()
        except Exception as e:
            print(f"❌ Error en search_by_name: {e}")
            return []
        
    def search_semantic(self, query_embedding, limit=5):
        """Búsqueda vectorial con cálculo de similitud."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT id, name, cloud_url, embedding, summary, service FROM files WHERE embedding IS NOT NULL')
                    all_files = cur.fetchall()
                
                results = []
                q_vec = np.array(query_embedding)

                for f_id, name, url, f_emb_json, summary, service in all_files:
                    try:
                        if not f_emb_json: continue
                        f_vec = np.array(json.loads(f_emb_json) if isinstance(f_emb_json, str) else f_emb_json)
                        
                        norm = (np.linalg.norm(q_vec) * np.linalg.norm(f_vec))
                        similarity = np.dot(q_vec, f_vec) / norm if norm > 0 else 0
                        
                        results.append({
                            "id": f_id, "name": name, "url": url, 
                            "similarity": float(similarity), "summary": summary, "service": service
                        })
                    except: continue
                
                results.sort(key=lambda x: x["similarity"], reverse=True)
                return results[:limit]
        except Exception as e:
            print(f"❌ Error semántico: {e}")
            return []

    def get_last_files(self, limit=20):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id, name, cloud_url, service, created_at FROM files ORDER BY created_at DESC LIMIT %s', (limit,))
                return cur.fetchall()

    def get_file_by_id(self, file_id):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, service, cloud_url FROM files WHERE id = %s", (file_id,))
                return cur.fetchone() # Esto devolverá un diccionario gracias al RealDictCursor
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
                    SELECT id, name, cloud_url, service, content_text, embedding 
                    FROM files 
                    WHERE embedding IS NOT NULL 
                    AND embedding NOT IN ('', '[]', 'error_limit')
                ''')
                rows = cur.fetchall()
                return [(r['id'], r['name'], r['cloud_url'], r['service'], r['content_text'], r['embedding']) 
                        for r in rows]
    
    def reset_failed_embeddings(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE files SET embedding = NULL WHERE embedding IN ('error_limit', '[]')")
            conn.commit()

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

    def get_folder_contents(self, parent_id=None):
        """Retorna subcarpetas y archivos usando diccionarios"""
        with self._connect() as conn:
            # El cursor_factory es la clave para que item['type'] funcione
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Obtener Carpetas
                if parent_id is None or parent_id == "root":
                    cur.execute("SELECT id, name, 'folder' as type FROM folders WHERE parent_id IS NULL")
                else:
                    cur.execute("SELECT id, name, 'folder' as type FROM folders WHERE parent_id = %s", (parent_id,))
                subfolders = cur.fetchall()
                
                # 2. Obtener Archivos
                if parent_id is None or parent_id == "root":
                    cur.execute("SELECT id, name, 'file' as type FROM files WHERE folder_id IS NULL")
                else:
                    cur.execute("SELECT id, name, 'file' as type FROM files WHERE folder_id = %s", (parent_id,))
                files = cur.fetchall()
                
                return subfolders + files

        """Obtiene datos de una carpeta específica"""
        if not folder_id or folder_id == "root": return None
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM folders WHERE id = %s", (folder_id,))
                return cur.fetchone()

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
        """Actualiza el nombre para mostrar del administrador"""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET nombre = %s WHERE id = %s",
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