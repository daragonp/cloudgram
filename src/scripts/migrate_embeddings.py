#!/usr/bin/env python3
"""
Migración de Embeddings para CloudGram Pro
Usa los modelos correctos: gemini-embedding-001, gemini-embedding-2-preview

USO:
    python3 migrate_embeddings_fixed.py [--dry-run] [--limit N]
"""

import os
import sys
import json
import logging
import time
import urllib.request
import urllib.error

from dotenv import load_dotenv
load_dotenv()

import psycopg2

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuración
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BATCH_SIZE = 5
DELAY_SECONDS = 5  # Aumentado para evitar rate limits

# Modelos correctos según diagnóstico
EMBEDDING_MODELS = ["gemini-embedding-001", "gemini-embedding-2-preview"]


def generate_embedding(text, model_name="gemini-embedding-001"):
    """Genera embedding usando REST API de Gemini"""
    try:
        # Limpiar texto
        text = text.replace('\x00', '').strip()
        if not text or len(text) < 10:
            return None
        if len(text) > 8000:
            text = text[:8000]
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:embedContent?key={GEMINI_API_KEY}"
        
        data = json.dumps({
            "content": {"parts": [{"text": text}]}
        }).encode('utf-8')
        
        req = urllib.request.Request(
            url,
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode())
            
            if 'embedding' in result and 'values' in result['embedding']:
                return result['embedding']['values']
        
        return None
        
    except urllib.error.HTTPError as e:
        error = e.read().decode()
        logger.error(f"HTTP {e.code}: {error[:100]}")
        return None
    except Exception as e:
        logger.error(f"Error: {e}")
        return None


def test_embedding_api():
    """Prueba la API de embedding con los modelos disponibles"""
    print("\n🧪 PROBANDO API DE EMBEDDINGS...")
    
    for model in EMBEDDING_MODELS:
        print(f"   Probando {model}...", end=" ")
        embedding = generate_embedding("test de conexión", model)
        
        if embedding:
            dims = len(embedding)
            print(f"✅ {dims} dimensiones")
            return model, dims
        else:
            print("❌")
    
    return None, 0


class Migrator:
    def __init__(self, dry_run=False, limit=None):
        self.dry_run = dry_run
        self.limit = limit
        self.conn = None
        self.model = None
        self.stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
    
    def connect(self):
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        self.conn = psycopg2.connect(url)
        logger.info("✅ Conectado a DB")
    
    def close(self):
        if self.conn:
            self.conn.close()
    
    def get_files(self):
        query = """
            SELECT id, name, content_text 
            FROM files 
            WHERE (embedding IS NULL OR embedding = '' OR embedding = '[]')
            AND content_text IS NOT NULL 
            AND content_text != ''
            AND LENGTH(content_text) > 20
            ORDER BY created_at DESC
        """
        if self.limit:
            query += f" LIMIT {self.limit}"
        
        with self.conn.cursor() as cur:
            cur.execute(query)
            return cur.fetchall()
    
    def process_file(self, file_id, name, content_text):
        try:
            if not content_text or len(content_text.strip()) < 20:
                self.stats["skipped"] += 1
                return False
            
            # Intentar con el modelo detectado
            embedding = generate_embedding(content_text, self.model)
            
            if embedding:
                dims = len(embedding)
                
                if not self.dry_run:
                    with self.conn.cursor() as cur:
                        cur.execute(
                            "UPDATE files SET embedding = %s WHERE id = %s",
                            (json.dumps(embedding), file_id)
                        )
                        self.conn.commit()
                
                logger.info(f"   ✅ {name[:45]:<47} → {dims} dims")
                self.stats["success"] += 1
                return True
            else:
                logger.error(f"   ❌ {name[:45]:<47} → Sin embedding")
                self.stats["failed"] += 1
                return False
                
        except Exception as e:
            logger.error(f"   ❌ Error: {e}")
            self.stats["failed"] += 1
            return False
    
    def run(self):
        print("\n" + "="*60)
        print("🔄 MIGRACIÓN DE EMBEDDINGS")
        print("="*60)
        print(f"   Modo: {'DRY RUN (sin cambios)' if self.dry_run else 'PRODUCCIÓN'}")
        print(f"   Límite: {self.limit or 'Sin límite'}")
        print("="*60)
        
        # Probar API primero
        model, dims = test_embedding_api()
        if not model:
            print("\n❌ No se pudo conectar con la API de embeddings")
            print("   Verifica tu GEMINI_API_KEY y conexión")
            return
        
        self.model = model
        print(f"\n✅ Usando modelo: {model} ({dims} dimensiones)")
        
        self.connect()
        
        try:
            files = self.get_files()
            self.stats["total"] = len(files)
            
            if not files:
                logger.info("\n✅ No hay archivos para migrar")
                return
            
            logger.info(f"\n📋 {len(files)} archivos a procesar\n")
            
            for i, (file_id, name, content_text) in enumerate(files, 1):
                self.process_file(file_id, name, content_text)
                
                # Delay cada BATCH_SIZE archivos para evitar rate limit
                if i % BATCH_SIZE == 0 and i < len(files):
                    logger.info(f"   ⏳ Procesados {i}/{len(files)}, esperando {DELAY_SECONDS}s...")
                    time.sleep(DELAY_SECONDS)
            
            # Resumen
            print("\n" + "="*60)
            print("📊 RESUMEN")
            print("="*60)
            print(f"   Total:     {self.stats['total']}")
            print(f"   ✅ Éxito:  {self.stats['success']}")
            print(f"   ❌ Falló:  {self.stats['failed']}")
            print(f"   ⏩ Saltó:  {self.stats['skipped']}")
            print("="*60)
            
            if self.dry_run:
                print("\n⚠️ DRY RUN - No se hicieron cambios")
            elif self.stats["success"] > 0:
                print(f"\n✅ Migración completada: {self.stats['success']} embeddings generados")
            
        finally:
            self.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migrar embeddings a Gemini")
    parser.add_argument("--dry-run", action="store_true", help="Solo probar sin guardar")
    parser.add_argument("--limit", type=int, help="Limitar número de archivos")
    args = parser.parse_args()
    
    Migrator(dry_run=args.dry_run, limit=args.limit).run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n🛑 Cancelado por el usuario")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()