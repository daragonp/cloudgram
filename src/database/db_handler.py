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
        """Crea las tablas necesarias si no existen."""
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
                # 2. Tabla de Archivos
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
                        created_at TIMESTAMP
                    )
                ''')
            conn.commit()

    # --- FUNCIONES DEL BOT ---
    
    def register_file(self, telegram_id, name, f_type, cloud_url, service, content_text=None, embedding=None, folder_id=None):
        try:
            with self._connect() as conn:
                # Importante usar RealDictCursor para consistencia
                with conn.cursor() as cur:
                    # NOTA: Asegúrate que tu tabla tenga la columna 'type' o 'file_type'
                    # Según tu código anterior, la columna se llama 'type'
                    cur.execute("""
                        INSERT INTO files (telegram_id, name, type, cloud_url, service, content_text, embedding, folder_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (telegram_id, name, f_type, cloud_url, service, content_text, embedding, folder_id))
                    conn.commit()
                    print(f"✅ DB: Archivo '{name}' registrado con éxito.")
        except Exception as e:
            print(f"❌ ERROR CRÍTICO DB EN register_file: {e}")
            # Esto imprimirá el error exacto (ej: falta una columna o tipo de dato mal)
  
    def search_by_name(self, keyword):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id, name, cloud_url, service FROM files WHERE name ILIKE %s', (f'%{keyword}%',))
                return cur.fetchall()

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

    def search_semantic(self, query_embedding, limit=3):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id, name, cloud_url, embedding FROM files WHERE embedding IS NOT NULL')
                all_files = cur.fetchall()
            
            results = []
            q_vec = np.array(query_embedding)

            for f_id, name, url, f_emb_json in all_files:
                try:
                    f_vec = np.array(json.loads(f_emb_json))
                    similarity = np.dot(q_vec, f_vec) / (np.linalg.norm(q_vec) * np.linalg.norm(f_vec))
                    results.append((f_id, name, url, similarity))
                except: continue
            
            results.sort(key=lambda x: x[3], reverse=True)
            return results[:limit]

    def delete_file_by_id(self, file_id):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM files WHERE id = %s', (file_id,))
            conn.commit()

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
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM files WHERE name = %s AND service = %s",
                        (name, service)
                    )
                    columns = [desc[0] for desc in cur.description]
                    row = cur.fetchone()
                    return dict(zip(columns, row)) if row else None
        except Exception as e:
            print(f"❌ Error en get_file_by_name_and_service: {e}")
            return None
    
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