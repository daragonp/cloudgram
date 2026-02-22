import sqlite3
import json
import numpy as np
from datetime import datetime

class DatabaseHandler:

    def __init__(self, db_path="cloudgram.db"):
        self.db_path = db_path
        self._setup_initial_db()

    def _connect(self):
        # Usamos check_same_thread=False porque Flask y el Bot corren en hilos distintos
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _setup_initial_db(self):
        """Crea las tablas necesarias si no existen, sin borrar nada actual."""
        with self._connect() as conn:
            # 1. Tabla de Usuarios (Para la Web)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    telegram_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # 2. Tabla de Archivos (Mantenemos tu estructura original + user_id)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id TEXT,
                    user_id INTEGER,
                    name TEXT,
                    type TEXT,
                    cloud_url TEXT,
                    service TEXT,
                    content_text TEXT,
                    embedding TEXT, 
                    created_at TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            ''')
            conn.commit()

    # --- FUNCIONES QUE YA USAS EN EL BOT (INTACTAS) ---

    def register_file(self, telegram_id, name, f_type, cloud_url, service, content_text=None, embedding=None):
        with self._connect() as conn:
            conn.execute('''
                INSERT INTO files (telegram_id, name, type, cloud_url, service, content_text, embedding, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (telegram_id, name, f_type, cloud_url, service, content_text, 
                  json.dumps(embedding) if embedding else None, datetime.now()))
            conn.commit()

    def search_by_name(self, keyword):
        with self._connect() as conn:
            cursor = conn.execute('SELECT id, name, cloud_url, service FROM files WHERE name LIKE ?', (f'%{keyword}%',))
            return cursor.fetchall()

    def get_last_files(self, limit=20):
        with self._connect() as conn:
            cursor = conn.execute('SELECT id, name, cloud_url, service, created_at FROM files ORDER BY created_at DESC LIMIT ?', (limit,))
            return cursor.fetchall()

    # --- NUEVAS FUNCIONES PARA IA Y WEB ---

    def search_semantic(self, query_embedding, limit=3):
        """Búsqueda por contexto (Embeddings)."""
        with self._connect() as conn:
            cursor = conn.execute('SELECT id, name, cloud_url, embedding FROM files WHERE embedding IS NOT NULL')
            all_files = cursor.fetchall()
            
            results = []
            q_vec = np.array(query_embedding)

            for f_id, name, url, f_emb_json in all_files:
                try:
                    f_vec = np.array(json.loads(f_emb_json))
                    # Similitud de coseno
                    similarity = np.dot(q_vec, f_vec) / (np.linalg.norm(q_vec) * np.linalg.norm(f_vec))
                    results.append((f_id, name, url, similarity))
                except: continue
            
            results.sort(key=lambda x: x[3], reverse=True)
            return results[:limit]

    def delete_file_by_id(self, file_id):
        with self._connect() as conn:
            conn.execute('DELETE FROM files WHERE id = ?', (file_id,))
            conn.commit()

    # --- GESTIÓN DE USUARIOS (Para web_admin.py) ---

    def get_user_by_email(self, email):
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM users WHERE email = ?', (email,))
            return cursor.fetchone()

    def get_user_by_id(self, user_id):
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,))
            return cursor.fetchone()
    
    def get_all_with_embeddings(self):
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row 
            cursor = conn.execute('''
                SELECT id, name, cloud_url, service, content_text, embedding 
                FROM files 
                WHERE embedding IS NOT NULL 
                AND embedding NOT IN ('', '[]', 'error_limit')
            ''')
            # Retornamos los 6 valores exactos
            return [(r['id'], r['name'], r['cloud_url'], r['service'], r['content_text'], r['embedding']) 
                    for r in cursor.fetchall()]
    
    def reset_failed_embeddings(self):
        """Borra las marcas de error para permitir que el indexador lo intente de nuevo."""
        with self._connect() as conn:
            conn.execute("UPDATE files SET embedding = NULL WHERE embedding IN ('error_limit', '[]')")
            conn.commit()