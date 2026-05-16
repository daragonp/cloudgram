# src/search/hybrid_search.py
"""
Motor de búsqueda híbrida para CloudGram Pro.

Mejoras frente a la versión anterior (que daba resultados ilógicos):

1) **Reciprocal Rank Fusion (RRF)** en lugar de suma ponderada de scores.
   RRF combina rankings, no scores absolutos, así que es robusto a
   escalas distintas (cosine-similarity vs BM25 vs metadata heurístico).

2) **Threshold de relevancia mínima**: si la similitud semántica del top
   resultado es muy baja Y no hay match léxico claro, devolvemos vacío
   en lugar de inflar resultados irrelevantes.

3) **Boost por coincidencia exacta en el nombre del archivo** (factor x1.5)
   y boost moderado si la query aparece en `tags` (x1.2).

4) **Normalización de la query** (lower-case + unidecode si está disponible
   + colapso de espacios). Aplica la misma normalización al matchear nombres.

5) **Análisis de tipos de archivo en la query**: si el usuario dice "pdf",
   "imagen", "foto", "audio"… filtramos por tipo automáticamente.

6) **Caché REDIS sensible al estado**: la clave incluye un epoch de la BD
   para invalidar resultados cuando se reindexa.

7) **Logging claro** del score final y de cuántos vinieron de cada fuente.
"""
import json
import asyncio
import os
import hashlib
import re
import logging
from typing import List, Dict, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------

try:
    from unidecode import unidecode  # opcional, pero recomendado
except ImportError:  # pragma: no cover - fallback ASCII básico
    def unidecode(text: str) -> str:
        return text


_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)


def normalize(text: str) -> str:
    """Normaliza para comparación textual: minúsculas, sin acentos, sin
    puntuación, espacios colapsados. Idempotente."""
    if not text:
        return ""
    text = unidecode(str(text)).lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


# Diccionario muy simple de "intenciones" de tipo de archivo.
# Si el usuario dice "buscar_ia foto de la familia", aplicamos filtro a fotos.
_FILE_TYPE_HINTS: Dict[str, List[str]] = {
    "pdf": ["pdf"],
    "doc": ["doc", "docx"],
    "documento": ["pdf", "doc", "docx", "txt"],
    "documentos": ["pdf", "doc", "docx", "txt"],
    "word": ["doc", "docx"],
    "excel": ["xls", "xlsx", "csv"],
    "hoja": ["xls", "xlsx", "csv"],
    "imagen": ["jpg", "jpeg", "png", "webp", "heic", "bmp"],
    "imagenes": ["jpg", "jpeg", "png", "webp", "heic", "bmp"],
    "imágenes": ["jpg", "jpeg", "png", "webp", "heic", "bmp"],
    "foto": ["jpg", "jpeg", "png", "webp", "heic"],
    "fotos": ["jpg", "jpeg", "png", "webp", "heic"],
    "video": ["mp4", "mov", "avi", "mkv", "webm"],
    "videos": ["mp4", "mov", "avi", "mkv", "webm"],
    "audio": ["mp3", "wav", "ogg", "m4a", "flac", "opus"],
    "voz": ["mp3", "wav", "ogg", "m4a", "flac", "opus"],
    "musica": ["mp3", "wav", "flac"],
    "música": ["mp3", "wav", "flac"],
}


def detect_file_types(query_norm: str) -> Optional[List[str]]:
    """Devuelve la lista de extensiones implícitas en la query, o None."""
    tokens = query_norm.split()
    matched: Set[str] = set()
    for tok in tokens:
        if tok in _FILE_TYPE_HINTS:
            matched.update(_FILE_TYPE_HINTS[tok])
    return sorted(matched) if matched else None


# ---------------------------------------------------------------------------
# Cache REDIS
# ---------------------------------------------------------------------------

class RedisCache:
    """Wrapper simple para cache con REDIS."""

    def __init__(self):
        self.redis_url = (
            os.getenv("REDIS_URL")
            or os.getenv("REDIS_BROKER_URL")
            or os.getenv("REDIS_URI")
        )
        self.client = None
        self._init_redis()

    def _init_redis(self):
        if not self.redis_url:
            logger.warning("⚠️ REDIS_URL no configurado - caché deshabilitado")
            return

        try:
            import redis
            self.client = redis.from_url(
                self.redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=True,
            )
            self.client.ping()
            logger.info("✅ Cache REDIS inicializado")
        except Exception as e:
            logger.warning(f"⚠️ No se pudo conectar a REDIS: {e}")
            self.client = None

    def get(self, key: str):
        if not self.client:
            return None
        try:
            value = self.client.get(key)
            return json.loads(value) if value else None
        except Exception as e:
            logger.warning(f"Error leyendo REDIS: {e}")
            return None

    def set(self, key: str, value, ttl: int = 86400):
        if not self.client:
            return False
        try:
            self.client.setex(key, ttl, json.dumps(value))
            return True
        except Exception as e:
            logger.warning(f"Error escribiendo REDIS: {e}")
            return False

    def is_available(self) -> bool:
        return self.client is not None

    def delete(self, key: str) -> bool:
        if not self.client:
            return False
        try:
            self.client.delete(key)
            return True
        except Exception as e:
            logger.warning(f"Error eliminando REDIS key: {e}")
            return False


# ---------------------------------------------------------------------------
# Motor híbrido
# ---------------------------------------------------------------------------

class HybridSearchEngine:
    """Motor de búsqueda híbrida con Reciprocal Rank Fusion + re-ranker LLM."""

    # Constante k del RRF — 60 es el valor canónico (Cormack et al., 2009).
    RRF_K = 60

    # Boost multiplicativo cuando hay coincidencia clara en `name` o `tags`.
    NAME_EXACT_BOOST = 1.50
    NAME_TOKEN_BOOST = 1.25
    TAGS_BOOST = 1.20

    # Umbrales (absolutos, sobre similitud coseno 0..1):
    #   • SEMANTIC_FLOOR: por debajo se considera ruido si tampoco hay match
    #     léxico → la query devuelve [].
    #   • DISPLAY_FLOOR: para los resultados que SÍ devolvemos, escondemos
    #     los que tengan score final < este valor.
    SEMANTIC_FLOOR = 0.40
    DISPLAY_FLOOR = 0.30

    # ¿Activar el reranker LLM? Se puede desactivar con USE_LLM_RERANKER=0.
    # Se activa automáticamente si OPENAI_API_KEY está presente.
    @staticmethod
    def _llm_reranker_enabled() -> bool:
        flag = os.getenv("USE_LLM_RERANKER", "auto").lower()
        if flag in ("0", "false", "no", "off"):
            return False
        if flag in ("1", "true", "yes", "on"):
            return True
        # auto: encendido si hay clave OpenAI.
        return bool(os.getenv("OPENAI_API_KEY"))

    # ¿Cuántos candidatos enviar al LLM como máximo?
    LLM_RERANK_TOP = 12

    def __init__(self, db_handler, ai_handler):
        self.db = db_handler
        self.ai = ai_handler
        self.cache = RedisCache()

    # ----- API pública -----

    async def search(self,
                     query: str,
                     limit: int = 20,
                     file_types: Optional[List[str]] = None) -> List[Dict]:
        """Búsqueda híbrida.

        Args:
            query: texto del usuario.
            limit: máximo de resultados.
            file_types: filtros explícitos por extensión (override del detector).
        """
        query = (query or "").strip()
        if len(query) < 2:
            return []

        query_norm = normalize(query)

        # Detectar tipos de archivo implícitos si el caller no los pasó.
        if file_types is None:
            file_types = detect_file_types(query_norm)
            if file_types:
                logger.info(f"🎛️ Filtro de tipo inferido de la query: {file_types}")

        cache_key = self._build_cache_key(query, limit, file_types)
        if self.cache.is_available():
            cached = self.cache.get(cache_key)
            if cached is not None:
                logger.info(f"📦 Resultados desde REDIS para: '{query}'")
                return cached

        # 1. Embedding (con caché)
        embedding = await self._get_embedding_cached(query)

        # 2. Búsquedas en paralelo. Sobre-pedimos para reranking.
        over_fetch = max(limit * 3, 30)
        semantic_task = (
            self._semantic_search(embedding, over_fetch, file_types)
            if embedding else self._empty_results()
        )
        fulltext_task = self._fulltext_search(query, over_fetch, file_types)
        metadata_task = self._metadata_search(query, over_fetch, file_types)

        semantic, fulltext, metadata = await asyncio.gather(
            semantic_task, fulltext_task, metadata_task
        )

        logger.info(
            "🔀 Resultados crudos — semántica:%d, full-text:%d, metadata:%d",
            len(semantic), len(fulltext), len(metadata),
        )

        # 3. Detección de "ningún resultado realmente relevante".
        if self._is_irrelevant(query_norm, semantic, fulltext, metadata):
            logger.info(f"🪫 Query '{query}' sin resultados relevantes (filtro).")
            return []

        # 4. RRF + boosts (ordena candidatos, sin asignar todavía el score
        #    final que verá el usuario).
        fused = self._rrf_fuse(semantic, fulltext, metadata, query_norm)

        # 5. Re-ranking con LLM sobre los mejores candidatos. CLAVE: en vez
        #    de tomar simplemente los top-K del RRF, garantizamos que entran
        #    los top-N de CADA fuente. Esto evita que un resultado muy
        #    relevante semánticamente pero ausente en full-text se pierda
        #    porque el RRF favorece items que aparecen en varias listas.
        llm_applied = False
        if self._llm_reranker_enabled() and fused:
            try:
                candidates_for_llm = self._select_llm_candidates(
                    fused, semantic, fulltext, metadata,
                )
                reranked = await self.ai.rerank_search_results(
                    query=query,
                    candidates=candidates_for_llm,
                    top_k=len(candidates_for_llm),  # evaluar TODOS los seleccionados
                )
                # `reranked` mantiene anotaciones (`llm_score`, `llm_reason`).
                # Hay que fusionar de vuelta con `fused` para mantener el resto.
                llm_by_id = {r['id']: r for r in reranked}
                merged: List[Dict] = []
                seen_ids = set()
                for r in reranked:
                    merged.append(r)
                    seen_ids.add(r['id'])
                for r in fused:
                    if r['id'] not in seen_ids:
                        merged.append(r)
                fused = merged
                llm_applied = True
                logger.info("🤖 Re-ranking LLM aplicado a %d candidatos.",
                            len(candidates_for_llm))
            except Exception as rr_err:
                logger.warning(f"⚠️ Reranker LLM error: {rr_err}. Sigo con RRF.")

        # 5.b. Si el LLM evaluó el head y TODO es irrelevante
        #      (mejor llm_score < DISPLAY_FLOOR), asumimos que la query no
        #      tiene match real.
        if llm_applied:
            head_with_llm = [r for r in fused if r.get('llm_score') is not None]
            if head_with_llm:
                best_llm = max(r['llm_score'] for r in head_with_llm)
                if best_llm < self.DISPLAY_FLOOR:
                    logger.info(
                        "🪫 El LLM descartó todo el top (mejor=%.2f<%.2f). "
                        "Devolviendo vacío para la query '%s'.",
                        best_llm, self.DISPLAY_FLOOR, query,
                    )
                    return []
                # Si el LLM evaluó, sólo conservamos los que él evaluó:
                # confiamos en su juicio sobre los más prometedores.
                fused = head_with_llm

        # 6. Asignar score FINAL absoluto al usuario.
        for item in fused:
            llm_score = item.get('llm_score')
            sem = float(item.get('_semantic_similarity', 0) or 0)
            boost = float(item.get('_lex_boost', 1.0))

            if llm_score is not None:
                # El LLM ya consideró léxica + semántica + summary.
                final = max(0.0, min(1.0, llm_score))
            else:
                # Score absoluto = semantic_similarity con un pequeño nudge
                # (cap a 1.0) si hay coincidencias literales en nombre/tags.
                final = min(1.0, sem * boost) if sem > 0 else 0.0

            item['combined_score'] = final
            item['score'] = final

        # 7. Filtrar por DISPLAY_FLOOR y reordenar.
        filtered = [r for r in fused if r.get('combined_score', 0) >= self.DISPLAY_FLOOR]
        filtered.sort(key=lambda x: x['combined_score'], reverse=True)
        final_results = filtered[:limit]

        logger.info(
            "✅ Búsqueda '%s' → %d resultados (de %d candidatos tras RRF+LLM).",
            query, len(final_results), len(fused),
        )

        if self.cache.is_available() and final_results:
            self.cache.set(cache_key, final_results, ttl=600)

        return final_results

    # ----- Internos -----

    async def _empty_results(self) -> List[Dict]:
        return []

    def _build_cache_key(self, query: str, limit: int,
                         file_types: Optional[List[str]]) -> str:
        # Incluimos un "epoch" basado en el número total de archivos para
        # invalidar la caché cuando se reindexa o se borran archivos.
        try:
            total = self.db.count_files_without_embedding() if hasattr(
                self.db, "count_files_without_embedding") else 0
        except Exception:
            total = 0
        ft_part = ",".join(file_types) if file_types else ""
        raw = f"{normalize(query)}|{limit}|{ft_part}|{total}"
        return f"search:v2:{hashlib.md5(raw.encode()).hexdigest()}"

    async def _get_embedding_cached(self, text: str) -> Optional[List[float]]:
        cache_key = f"embedding:{hashlib.md5(normalize(text).encode()).hexdigest()}"
        if self.cache.is_available():
            cached = self.cache.get(cache_key)
            if cached:
                return cached
        try:
            embedding = await self.ai.get_embedding(text)
            if embedding is not None and self.cache.is_available():
                # Permitir tanto list como np.array
                if hasattr(embedding, "tolist"):
                    embedding = embedding.tolist()
                self.cache.set(cache_key, embedding, ttl=86400)
            return embedding
        except Exception as e:
            logger.error(f"Error generando embedding: {e}")
            return None

    async def _semantic_search(self, embedding, limit, file_types):
        try:
            results = self.db.search_semantic(embedding, limit=limit,
                                              file_types=file_types) or []
            for r in results:
                r['_score_semantic'] = max(0.0, float(r.get('similarity', 0) or 0))
            return results
        except Exception as e:
            logger.error(f"Error en búsqueda semántica: {e}")
            return []

    async def _fulltext_search(self, query, limit, file_types):
        try:
            results = self.db.search_fulltext_improved(query, limit=limit,
                                                       file_types=file_types) or []
            for r in results:
                r['_score_fulltext'] = max(0.0, float(r.get('score', 0) or 0))
            return results
        except Exception as e:
            logger.error(f"Error en búsqueda full-text: {e}")
            return []

    async def _metadata_search(self, query, limit, file_types):
        try:
            results = self.db.search_by_metadata(query, limit=limit,
                                                  file_types=file_types) or []
            for r in results:
                r['_score_metadata'] = max(0.0, float(r.get('score', 0) or 0))
            return results
        except Exception as e:
            logger.error(f"Error en búsqueda de metadata: {e}")
            return []

    def _is_irrelevant(self, query_norm: str, semantic, fulltext, metadata) -> bool:
        """Devuelve True cuando ningún canal aporta evidencia mínima."""
        if not semantic and not fulltext and not metadata:
            return True

        # Si lo único que tenemos es semántica con similitud muy baja → ruido.
        top_sem = max((r.get('_score_semantic', 0) for r in semantic), default=0)
        has_literal = bool(fulltext or metadata)
        if top_sem < self.SEMANTIC_FLOOR and not has_literal:
            return True
        return False

    def _select_llm_candidates(self, fused, semantic, fulltext, metadata):
        """Selecciona qué candidatos enviar al LLM reranker.

        Estrategia: garantizar diversidad — incluir los TOP-N de cada fuente
        individual + completar con el top del RRF. Esto evita que un item
        muy relevante semánticamente pero ausente del full-text se pierda
        porque el RRF favorece items que aparecen en múltiples listas.
        """
        TOP_PER_SOURCE = 6  # top de cada fuente garantizado
        TOTAL_BUDGET = 15   # presupuesto total para el LLM

        selected: List[Dict] = []
        seen: Set[int] = set()

        for source_list in (semantic, fulltext, metadata):
            for item in source_list[:TOP_PER_SOURCE]:
                fid = item.get('id')
                if fid is None or fid in seen:
                    continue
                enriched = next((r for r in fused if r['id'] == fid), item)
                selected.append(enriched)
                seen.add(fid)
                if len(selected) >= TOTAL_BUDGET:
                    return selected

        for item in fused:
            fid = item.get('id')
            if fid in seen:
                continue
            selected.append(item)
            seen.add(fid)
            if len(selected) >= TOTAL_BUDGET:
                break

        return selected

    def _rrf_fuse(self, semantic, fulltext, metadata,
                  query_norm: str) -> List[Dict]:
        """Fusión por Reciprocal Rank Fusion + boosts.

        Esta versión NO asigna todavía el `combined_score` final que se va a
        mostrar al usuario — sólo ORDENA los candidatos y deja anotaciones
        (`_rrf_score`, `_semantic_similarity`, `_lex_boost`, `_sources`) que
        consume el caller. El score final se asigna después, usando el
        re-ranker LLM si está disponible.
        """
        rrf_scores: Dict[int, float] = {}
        item_by_id: Dict[int, Dict] = {}
        sources: Dict[int, Set[str]] = {}
        semantic_sim: Dict[int, float] = {}

        def add(lst, source_label):
            for rank, item in enumerate(lst, start=1):
                fid = item.get('id')
                if fid is None:
                    continue
                contribution = 1.0 / (self.RRF_K + rank)
                rrf_scores[fid] = rrf_scores.get(fid, 0.0) + contribution
                sources.setdefault(fid, set()).add(source_label)
                if fid not in item_by_id:
                    item_by_id[fid] = dict(item)
                else:
                    for k, v in item.items():
                        if v is not None and item_by_id[fid].get(k) in (None, ""):
                            item_by_id[fid][k] = v
                if source_label == "semantic":
                    semantic_sim[fid] = max(
                        semantic_sim.get(fid, 0.0),
                        float(item.get('_score_semantic', 0) or 0),
                    )

        add(semantic, "semantic")
        add(fulltext, "fulltext")
        add(metadata, "metadata")

        results: List[Dict] = []
        for fid, raw_score in rrf_scores.items():
            item = item_by_id[fid]
            name_norm = normalize(item.get('name', ''))
            tags_norm = normalize(item.get('tags', '') or '')

            boost = 1.0
            if query_norm and query_norm in name_norm:
                boost *= self.NAME_EXACT_BOOST
            else:
                q_tokens = [t for t in query_norm.split() if len(t) >= 3]
                if q_tokens and any(t in name_norm for t in q_tokens):
                    boost *= self.NAME_TOKEN_BOOST
            if query_norm and tags_norm and query_norm in tags_norm:
                boost *= self.TAGS_BOOST

            item['_rrf_score'] = raw_score
            item['_sources'] = sorted(sources[fid])
            item['_semantic_similarity'] = semantic_sim.get(fid, 0.0)
            item['_lex_boost'] = boost
            results.append(item)

        # Orden inicial por RRF (lo refinará luego el LLM).
        results.sort(key=lambda x: x['_rrf_score'], reverse=True)
        return results
