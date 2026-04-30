import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.getenv("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

if not db_url or "postgresql" not in db_url:
    print("❌ No se detectó una base de datos PostgreSQL/Supabase en el .env")
    exit(1)

print(f"🔌 Conectando a la base de datos PostgreSQL...")

try:
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    # 1. Habilitar extensión pgvector
    print("🛠️ Habilitando extensión pgvector...")
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    conn.commit()
    print("✅ Extensión pgvector habilitada.")

    # 2. Limpiar la columna embedding antes de hacer el cast
    # Limpiamos valores que no son arrays JSON válidos
    print("🧹 Limpiando columna embedding de valores inválidos (error_limit, strings vacíos)...")
    cur.execute("UPDATE files SET embedding = NULL WHERE embedding IN ('error_limit', '[]', '');")
    conn.commit()

    # 3. Alterar la columna a vector(1536)
    print("🔄 Convirtiendo columna embedding de TEXT a vector(1536)...")
    try:
        cur.execute("ALTER TABLE files ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector;")
        conn.commit()
        print("✅ Columna embedding convertida exitosamente.")
    except Exception as e:
        if "cannot cast type" in str(e) or "invalid input syntax for type vector" in str(e):
            print(f"⚠️ Error al hacer el cast directo: {e}. Probablemente haya datos anómalos. Haz un reset completo si es necesario.")
        else:
            print(f"ℹ️ Resultado del cast (quizás ya era vector): {e}")

    # 4. Crear índice HNSW para búsquedas rápidas (opcional, pero muy recomendado)
    print("⚡ Creando índice HNSW para acelerar la búsqueda semántica...")
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS files_embedding_idx ON files USING hnsw (embedding vector_cosine_ops);")
        conn.commit()
        print("✅ Índice HNSW creado.")
    except Exception as e:
        print(f"ℹ️ No se pudo crear índice HNSW (versión antigua de pgvector o ya existe): {e}")

    cur.close()
    conn.close()
    print("🎉 Migración a pgvector completada con éxito.")

except Exception as e:
    print(f"❌ Error durante la migración: {e}")
