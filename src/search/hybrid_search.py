# src/search/hybrid_search.py
"""
Motor de búsqueda híbrida tipo Google:
- Combina semántica (embeddings) + full-text (BM25) + metadata
- Usa REDIS para caché de embeddings y resultados
- Reranking inteligente de resultados
"""
import json
import asyncio
import os
import hashlib
from typing import List, Dict, Optional
import numpy as np
import logging

logger = logging.getLogger(__name__)

class RedisCache:
    """Wrapper simple para cache con REDIS."""
    
    def __init__(self):
        self.redis_url = os.getenv("REDIS_URL") or os.getenv("REDIS_BROKER_URL")
        self.client = None
        self._init_redis()
    
    def _init_redis(self):
        """Inicializa conexión con REDIS de forma lazy."""
        if not self.redis_url:
            logger.warning("⚠️ REDIS_URL no configurado - caché deshabilitado")
            return
        
        try:
            import redis
            self.client = redis.from_url(
                self.redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=True
            )
            self.client.ping()
            logger.info("✅ Cache REDIS inicializado")
        except Exception as e:
            logger.warning(f"⚠️ No se pudo conectar a REDIS: {e}")
            self.client = None
    
    def get(self, key: str):
        """Obtiene valor del cache."""
        if not self.client:
            return None
        try:
            value = self.client.get(key)
            return json.loads(value) if value else None
        except Exception as e:
            logger.warning(f"Error leyendo REDIS: {e}")
            return None
    
    def set(self, key: str, value, ttl: int = 86400):
        """Guarda valor en cache (TTL por defecto 24h)."""
        if not self.client:
            return False
        try:
            self.client.setex(key, ttl, json.dumps(value))
            return True
        except Exception as e:
            logger.warning(f"Error escribiendo REDIS: {e}")
            return False
    
    def is_available(self) -> bool:
        """Verifica si REDIS está disponible."""
        return self.client is not None
    
    def delete(self, key: str) -> bool:
        """Elimina una clave del cache."""
        if not self.client:
            return False
        try:
            self.client.delete(key)
            return True
        except Exception as e:
            logger.warning(f"Error eliminando REDIS key: {e}")
            return False


class HybridSearchEngine:
    """
    Motor de búsqueda híbrida que combina múltiples estrategias.
    Usado por /buscar_ia para dar resultados tipo Google.
    """
    
    def __init__(self, db_handler, ai_handler):
        self.db = db_handler
        self.ai = ai_handler
        self.cache = RedisCache()
    
    async def search(self, 
                    query: str, 
                    limit: int = 20,
                    file_types: Optional[List[str]] = None) -> List[Dict]:
        """
        Búsqueda híbrida principal con caché de resultados.
        
        Combina múltiples fuentes de datos y retorna resultados rankeados.
        Usa REDIS para caché de embeddings Y resultados frecuentes.
        
        Args:
            query: Texto de búsqueda
            limit: Cantidad máxima de resultados
            file_types: Filtros de tipo de archivo opcionales
            
        Returns:
            Lista de archivos rankeados con scores combinados
        """
        logger.info(f"🔍 Búsqueda híbrida iniciada: '{query}'")
        
        query = query.strip()
        if not query or len(query) < 2:
            return []
        
        # 0. INTENTAR DESDE CACHÉ DE RESULTADOS
        cache_key = f"search_results:{hashlib.md5(f'{query}:{limit}'.encode()).hexdigest()}"
        if self.cache.is_available():
            cached_results = self.cache.get(cache_key)
            if cached_results:
                logger.info(f"📦 Resultados desde REDIS para: '{query}'")
                return cached_results
        
        # 1. OBTENER EMBEDDING (con caché)
        embedding = await self._get_embedding_cached(query)
        if not embedding:
            logger.warning(f"⚠️ No se pudo generar embedding para: {query}")
            # Fallback: solo búsqueda full-text
            return await self._fulltext_search(query, limit, file_types)
        
        # 2. BÚSQUEDAS PARALELAS
        semantic_results, fulltext_results, metadata_results = await asyncio.gather(
            self._semantic_search(embedding, limit * 2, file_types),
            self._fulltext_search(query, limit * 2, file_types),
            self._metadata_search(query, limit * 2, file_types),
        )
        
        # 3. RERANKING Y FUSIÓN
        combined = self._fuse_and_rerank(
            semantic_results, 
            fulltext_results, 
            metadata_results,
            query
        )
        
        # Limitar y retornar
        final_results = combined[:limit]
        logger.info(f"✅ Búsqueda completada: {len(final_results)} resultados")
        
        # 4. GUARDAR EN CACHÉ DE RESULTADOS (compresión automática por REDIS)
        if self.cache.is_available() and final_results:
            self.cache.set(cache_key, final_results, ttl=3600)  # 1 hora TTL para resultados
        
        return final_results
    
    async def _get_embedding_cached(self, text: str) -> Optional[List[float]]:
        """Obtiene embedding con caché en REDIS."""
        text = text.strip()
        cache_key = f"embedding:{hashlib.md5(text.encode()).hexdigest()}"
        
        # Intentar desde caché
        if self.cache.is_available():
            cached = self.cache.get(cache_key)
            if cached:
                logger.info(f"📦 Embedding desde REDIS para: '{text}'")
                return cached
        
        # Generar nuevo embedding
        try:
            embedding = await self.ai.get_embedding(text)
            
            # Guardar en caché
            if embedding and self.cache.is_available():
                self.cache.set(cache_key, embedding, ttl=86400)
            
            return embedding
        except Exception as e:
            logger.error(f"Error generando embedding: {e}")
            return None
    
    async def _semantic_search(self, 
                              embedding: List[float], 
                              limit: int,
                              file_types: Optional[List[str]] = None) -> List[Dict]:
        """Búsqueda semántica (pgvector)."""
        try:
            results = self.db.search_semantic(embedding, limit=limit, file_types=file_types)
            for r in results:
                r['_score_semantic'] = max(0, r.get('similarity', 0))
            logger.info(f"📊 Semántica: {len(results)} resultados")
            return results
        except Exception as e:
            logger.error(f"Error en búsqueda semántica: {e}")
            return []
    
    async def _fulltext_search(self, 
                              query: str, 
                              limit: int,
                              file_types: Optional[List[str]] = None) -> List[Dict]:
        """Búsqueda full-text mejorada (en DB)."""
        try:
            results = self.db.search_fulltext_improved(query, limit=limit, file_types=file_types)
            for r in results:
                r['_score_fulltext'] = max(0, r.get('score', 0.5))
            logger.info(f"📄 Full-text: {len(results)} resultados")
            return results
        except Exception as e:
            logger.error(f"Error en búsqueda full-text: {e}")
            return []
    
    async def _metadata_search(self, 
                              query: str, 
                              limit: int,
                              file_types: Optional[List[str]] = None) -> List[Dict]:
        """Búsqueda por tags y metadata."""
        try:
            results = self.db.search_by_metadata(query, limit=limit, file_types=file_types)
            for r in results:
                r['_score_metadata'] = max(0, r.get('score', 0.5))
            logger.info(f"🏷️ Metadata: {len(results)} resultados")
            return results
        except Exception as e:
            logger.error(f"Error en búsqueda de metadata: {e}")
            return []
    
    def _fuse_and_rerank(self, 
                        semantic: List[Dict], 
                        fulltext: List[Dict], 
                        metadata: List[Dict],
                        query: str) -> List[Dict]:
        """
        Fusiona resultados de múltiples fuentes y rerangkea.
        Estrategia: Combina scores de forma inteligente.
        """
        # Usar diccionario para deduplicar por ID
        merged = {}
        
        # Procesar cada fuente
        for item in semantic:
            file_id = item['id']
            if file_id not in merged:
                merged[file_id] = {**item, '_scores': {}}
            merged[file_id]['_scores']['semantic'] = item.get('_score_semantic', 0)
        
        for item in fulltext:
            file_id = item['id']
            if file_id not in merged:
                merged[file_id] = {**item, '_scores': {}}
            merged[file_id]['_scores']['fulltext'] = item.get('_score_fulltext', 0)
        
        for item in metadata:
            file_id = item['id']
            if file_id not in merged:
                merged[file_id] = {**item, '_scores': {}}
            merged[file_id]['_scores']['metadata'] = item.get('_score_metadata', 0)
        
        # Calcular score combinado
        results = []
        for file_id, item in merged.items():
            scores = item.pop('_scores', {})
            
            # Pesos: semantic (50%) + fulltext (40%) + metadata (10%)
            combined_score = (
                scores.get('semantic', 0) * 0.50 +
                scores.get('fulltext', 0) * 0.40 +
                scores.get('metadata', 0) * 0.10
            )
            
            item['combined_score'] = combined_score
            item['score'] = combined_score  # También como 'score' para compatibilidad
            results.append(item)
        
        # Ordenar por score combinado
        results.sort(key=lambda x: x['combined_score'], reverse=True)
        
        logger.info(f"🎯 Score promedio: {np.mean([r['combined_score'] for r in results]) if results else 0:.3f}")
        
        return results
