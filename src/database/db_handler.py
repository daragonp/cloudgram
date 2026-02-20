# src/database/db_handler.py
import sqlite3
import json
from datetime import datetime

class DatabaseHandler:
    def __init__(self, db_path="cloudgram.db"):
        self.db_path = db_path
        self._create_table()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _create_table(self):
        with self._connect() as conn:
            # Añadimos la columna 'embedding' como tipo TEXT (guardaremos el JSON del vector)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id TEXT,
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

    def register_file(self, telegram_id, name, f_type, cloud_url, service, content_text=None, embedding=None):
        with self._connect() as conn:
            conn.execute('''
                INSERT INTO files (telegram_id, name, type, cloud_url, service, content_text, embedding, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (telegram_id, name, f_type, cloud_url, service, content_text, 
                  json.dumps(embedding) if embedding else None, datetime.now()))
            conn.commit()

    def get_last_files(self, limit=20):
        with self._connect() as conn:
            # Traemos también el ID para poder eliminar después
            cursor = conn.execute('SELECT id, name, cloud_url, service, created_at FROM files ORDER BY created_at DESC LIMIT ?', (limit,))
            return cursor.fetchall()

    def search_by_name(self, keyword):
        with self._connect() as conn:
            # CORRECCIÓN: Ahora devolvemos 4 valores para que coincida con el main.py
            cursor = conn.execute('SELECT id, name, cloud_url, service FROM files WHERE name LIKE ?', (f'%{keyword}%',))
            return cursor.fetchall()

    def get_all_with_embeddings(self):
        """Para la búsqueda con IA"""
        with self._connect() as conn:
            cursor = conn.execute('SELECT id, name, cloud_url, service, content_text, embedding FROM files WHERE embedding IS NOT NULL')
            return cursor.fetchall()

    def search_by_id(self, file_id):
        query = "SELECT id, name, cloud_url, service FROM files WHERE id = ?"
        with self._connect() as conn:
            return conn.execute(query, (file_id,)).fetchall()
    
    def delete_file_by_id(self, file_id):
        with self._connect() as conn:
            conn.execute('DELETE FROM files WHERE id = ?', (file_id,))
            conn.commit()