#!/usr/bin/env python3
"""
Script completo de migración para CloudGram Pro
- Limpia embeddings corruptos
- Regenera embeddings con Gemini
- Compatible con Supabase

USO:
    python migrate_to_gemini.py [--limit N] [--dry-run]
"""

import os
import sys
import asyncio
import json
import logging
from datetime import datetime

# Configurar path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Variables de entorno
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.extras import RealDictCursor

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuración
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BATCH_SIZE = 5  # Procesar de 5 en 5 para no exceder rate limits
DELAY_SECONDS = 4  # Delay entre batches (15 RPM / 5 = 3 req/batch → 4s safe)


class GeminiMigrator:
    def __init__(self, dry_run=False, limit=None):
        self.dry_run = dry_run
        self.limit = limit
        self.conn = None
        self.stats = {
            "total_scanned": 0,
            "corrupted_cleaned": 0,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0
        }
    
    def connect_db(self):
        """Conecta a Supabase/PostgreSQL"""
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL no está configurada")
        
        # Normalizar URL
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        self.conn = psycopg2.connect(url)
        logger.info("✅ Conectado a la base de datos")
    
    def close_db(self):
        """Cierra la conexión"""
        if self.conn:
            self.conn.close()
            logger.info("🔌 Conexión cerrada")
    
    def clean_corrupted_embeddings(self):
        """Limpia embeddings con formato incorrecto"""
        logger.info("\n🧹 Limpiando embeddings corruptos...")
        
        with self.conn.cursor() as cur:
            # Contar corruptos
            cur.execute("SELECT COUNT(*) FROM files WHERE embedding LIKE '{%'")
            count = cur.fetchone()[0]
            logger.info(f"   Encontrados {count} embeddings con formato incorrecto")
            
            if count > 0 and not self.dry_run:
                cur.execute("UPDATE files SET embedding = NULL WHERE embedding LIKE '{%'")
                self.conn.commit()
                logger.info(f"   ✅ Limpiados {count} embeddings corruptos")
            
            self.stats["corrupted_cleaned"] = count
    
    def get_files_to_migrate(self):
        """Obtiene archivos que necesitan embedding"""
        query = """
            SELECT id, name, content_text 
            FROM files 
            WHERE (embedding IS NULL OR embedding = '' OR embedding = '[]' OR embedding LIKE '{%')
            AND content_text IS NOT NULL 
            AND content_text != ''
            AND content_text NOT LIKE '%Error en análisis%'
            AND content_text NOT LIKE '%Error en transcripción%'
            AND LENGTH(content_text) > 20
            ORDER BY created_at DESC
        """
        
        if self.limit:
            query += f" LIMIT {self.limit}"
        
        with self.conn.cursor() as cur:
            cur.execute(query)
            return cur.fetchall()
    
    async def generate_embedding(self, text):
        """Genera embedding usando Gemini API nativo"""
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            
            # Limpiar texto
            text = text.replace('\x00', '').strip()
            if not text:
                return None
            
            # Limitar longitud
            if len(text) > 8000:
                text = text[:8000]
            
            # Generar embedding
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=text
            )
            
            return result['embedding']
            
        except Exception as e:
            logger.error(f"Error generando embedding: {e}")
            return None
    
    async def migrate_batch(self, files):
        """Migra un lote de archivos"""
        for file_id, name, content_text in files:
            self.stats["processed"] += 1
            
            try:
                logger.info(f"   🔄 [{self.stats['processed']}] Procesando: {name[:40]}...")
                
                # Verificar contenido
                if not content_text or len(content_text.strip()) < 20:
                    logger.warning(f"      ⚠️ Contenido muy corto, saltando")
                    self.stats["skipped"] += 1
                    continue
                
                # Generar embedding
                embedding = await self.generate_embedding(content_text)
                
                if embedding and len(embedding) == 768:
                    if not self.dry_run:
                        # Guardar en DB
                        with self.conn.cursor() as cur:
                            cur.execute(
                                "UPDATE files SET embedding = %s WHERE id = %s",
                                (json.dumps(embedding), file_id)
                            )
                            self.conn.commit()
                    
                    logger.info(f"      ✅ Embedding generado (768 dims)")
                    self.stats["success"] += 1
                else:
                    logger.error(f"      ❌ Error: embedding inválido ({len(embedding) if embedding else 0} dims)")
                    self.stats["failed"] += 1
                
            except Exception as e:
                logger.error(f"      ❌ Error: {e}")
                self.stats["failed"] += 1
    
    async def run(self):
        """Ejecuta la migración completa"""
        print("\n" + "="*60)
        print("🔄 MIGRACIÓN A GEMINI EMBEDDINGS")
        print("="*60)
        print(f"   Modo: {'DRY RUN (sin cambios)' if self.dry_run else 'PRODUCCIÓN'}")
        print(f"   Límite: {self.limit or 'Sin límite'}")
        print(f"   Batch size: {BATCH_SIZE}")
        print(f"   Delay entre batches: {DELAY_SECONDS}s")
        print("="*60)
        
        # Conectar
        self.connect_db()
        
        try:
            # 1. Limpiar corruptos
            self.clean_corrupted_embeddings()
            
            # 2. Obtener archivos a migrar
            files = self.get_files_to_migrate()
            self.stats["total_scanned"] = len(files)
            
            if not files:
                logger.info("\n✅ No hay archivos para migrar")
                return
            
            logger.info(f"\n📋 {len(files)} archivos a procesar")
            
            # 3. Procesar en batches
            total_batches = (len(files) + BATCH_SIZE - 1) // BATCH_SIZE
            
            for i in range(0, len(files), BATCH_SIZE):
                batch = files[i:i + BATCH_SIZE]
                batch_num = (i // BATCH_SIZE) + 1
                
                logger.info(f"\n📦 Batch {batch_num}/{total_batches}")
                
                await self.migrate_batch(batch)
                
                # Delay entre batches
                if i + BATCH_SIZE < len(files):
                    logger.info(f"   ⏳ Esperando {DELAY_SECONDS}s...")
                    await asyncio.sleep(DELAY_SECONDS)
            
            # Resumen
            self.print_summary()
            
        finally:
            self.close_db()
    
    def print_summary(self):
        """Imprime resumen de la migración"""
        print("\n" + "="*60)
        print("📊 RESUMEN DE MIGRACIÓN")
        print("="*60)
        print(f"   Embeddings corruptos limpiados: {self.stats['corrupted_cleaned']}")
        print(f"   Total escaneados:               {self.stats['total_scanned']}")
        print(f"   Procesados:                     {self.stats['processed']}")
        print(f"   ✅ Exitosos:                    {self.stats['success']}")
        print(f"   ❌ Fallidos:                    {self.stats['failed']}")
        print(f"   ⏩ Saltados:                    {self.stats['skipped']}")
        print("="*60)
        
        if self.dry_run:
            print("\n⚠️  MODO DRY RUN - No se hicieron cambios reales")
        elif self.stats["success"] > 0:
            print("\n✅ Migración completada!")
        else:
            print("\n❌ No se migró ningún archivo")


async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Migrar embeddings a Gemini")
    parser.add_argument("--dry-run", action="store_true", help="Solo simular sin hacer cambios")
    parser.add_argument("--limit", type=int, help="Limitar número de archivos a procesar")
    args = parser.parse_args()
    
    migrator = GeminiMigrator(dry_run=args.dry_run, limit=args.limit)
    await migrator.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Cancelado por el usuario")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
