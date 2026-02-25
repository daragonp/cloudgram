import sqlite3
import psycopg2
import json

SQLITE_DB = "cloudgram.db"

SUPABASE_URL = "postgresql://postgres.wbdxmeohtlkwmaxomlyd:hF1J5G3aCNlPoPeL@aws-1-us-east-1.pooler.supabase.com:6543/postgres"

# Conectar SQLite
sqlite_conn = sqlite3.connect(SQLITE_DB)
sqlite_cursor = sqlite_conn.cursor()

# Conectar Supabase
pg_conn = psycopg2.connect(SUPABASE_URL)
pg_cursor = pg_conn.cursor()

# Leer datos
sqlite_cursor.execute("SELECT * FROM files")
rows = sqlite_cursor.fetchall()

for row in rows:
    (
        id_,
        telegram_id,
        name,
        type_,
        cloud_url,
        service,
        content_text,
        embedding,
        created_at
    ) = row

    # Si embedding está guardado como texto JSON en SQLite
    if isinstance(embedding, str):
        embedding = json.loads(embedding)

    # Convertir lista Python -> formato PostgreSQL vector
    embedding_str = "[" + ",".join(map(str, embedding)) + "]"

    pg_cursor.execute("""
        INSERT INTO files (
            id, telegram_id, name, type,
            cloud_url, service, content_text,
            embedding, created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        id_,
        telegram_id,
        name,
        type_,
        cloud_url,
        service,
        content_text,
        embedding_str,
        created_at
    ))

pg_conn.commit()

# Ajustar secuencia
pg_cursor.execute("""
    SELECT setval(
        pg_get_serial_sequence('files', 'id'),
        (SELECT MAX(id) FROM files)
    );
""")

pg_conn.commit()

sqlite_conn.close()
pg_conn.close()

print("Migración completada 🚀")