import sys, os
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__))))
from src.database.db_handler import DatabaseHandler

db = DatabaseHandler()
with db._connect() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, type, substring(content_text, 1, 20), substring(summary, 1, 30), technical_description, folder_id, telegram_id, substring(embedding, 1, 15) FROM files WHERE type IN ('jpg', 'jpeg', 'png') LIMIT 5")
        rows = cur.fetchall()
        for r in rows:
            print(r)
