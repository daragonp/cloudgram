# src/search/bm25_search.py
"""
Implementación de BM25 para búsqueda full-text mejorada.
BM25 es el algoritmo usado por Elasticsearch y es mucho mejor que ILIKE.
"""
import math
from typing import List, Dict, Tuple
from collections import defaultdict

class BM25:
    """Implementación de BM25 (Okapi BM25) para ranking de documentos."""
    
    def __init__(self, corpus: List[Dict[str, str]], k1: float = 1.5, b: float = 0.75):
        """
        Args:
            corpus: Lista de dicts con campos que indexar
            k1: Parámetro de saturación (default 1.5)
            b: Parámetro de normalización de longitud (default 0.75)
        """
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.avgdl = 0
        self.doc_freqs = []
        self.idf = {}
        self._build_index()
    
    def _build_index(self):
        """Construye el índice BM25."""
        num_docs = len(self.corpus)
        if num_docs == 0:
            return
        
        # Tokenizar y contar frecuencias
        doc_lengths = []
        term_doc_count = defaultdict(int)
        
        for doc_idx, doc in enumerate(self.corpus):
            # Combinar todos los campos y tokenizar
            text = " ".join(str(v).lower() for v in doc.values() if v)
            tokens = self._tokenize(text)
            doc_lengths.append(len(tokens))
            
            # Contar qué documentos contienen cada término
            unique_terms = set(tokens)
            for term in unique_terms:
                term_doc_count[term] += 1
        
        # Calcular longitud promedio
        self.avgdl = sum(doc_lengths) / num_docs if doc_lengths else 1
        self.doc_freqs = doc_lengths
        
        # Calcular IDF para cada término
        for term, doc_count in term_doc_count.items():
            self.idf[term] = math.log(
                (num_docs - doc_count + 0.5) / (doc_count + 0.5) + 1.0
            )
    
    def _tokenize(self, text: str) -> List[str]:
        """Tokeniza texto simple."""
        # Normalizar: espacios, puntuación
        text = text.lower()
        text = text.replace('-', ' ').replace('_', ' ')
        tokens = [t for t in text.split() if len(t) > 1]  # Filtrar tokens < 2 chars
        return tokens
    
    def score_doc(self, doc_idx: int, query: str) -> float:
        """Calcula score BM25 de un documento para una query."""
        if doc_idx >= len(self.corpus) or doc_idx < 0:
            return 0.0
        
        doc = self.corpus[doc_idx]
        text = " ".join(str(v).lower() for v in doc.values() if v)
        doc_tokens = self._tokenize(text)
        doc_length = len(doc_tokens)
        
        query_tokens = self._tokenize(query)
        score = 0.0
        
        for token in query_tokens:
            idf = self.idf.get(token, 0)
            
            # Contar ocurrencias del término en el documento
            term_freq = doc_tokens.count(token)
            
            # Fórmula BM25
            numerator = idf * (self.k1 + 1) * term_freq
            denominator = term_freq + self.k1 * (
                1 - self.b + self.b * (doc_length / self.avgdl)
            )
            
            score += numerator / denominator if denominator > 0 else 0
        
        return score
    
    def rank(self, query: str) -> List[Tuple[int, float]]:
        """
        Rankea los documentos por relevancia a la query.
        
        Returns:
            Lista de (doc_index, score) ordenada por score descendente
        """
        scores = []
        for doc_idx in range(len(self.corpus)):
            score = self.score_doc(doc_idx, query)
            if score > 0:
                scores.append((doc_idx, score))
        
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores


class BM25Search:
    """Wrapper para usar BM25 en búsquedas."""
    
    @staticmethod
    def search(docs: List[Dict], query: str, field_weights: Dict[str, float] = None) -> List[Tuple[Dict, float]]:
        """
        Busca documentos usando BM25 con pesos de campo.
        
        Args:
            docs: Lista de documentos (dicts)
            query: Query de búsqueda
            field_weights: Pesos para diferentes campos (ej: {'name': 2.0, 'tags': 1.5})
            
        Returns:
            Lista de (documento, score) ordenada por relevancia
        """
        if not docs or not query:
            return []
        
        field_weights = field_weights or {'name': 2.0, 'summary': 1.0, 'tags': 1.5}
        
        # Crear versión ponderada de documentos
        weighted_docs = []
        for doc in docs:
            weighted_doc = {}
            for field, weight in field_weights.items():
                text = str(doc.get(field, ""))
                weighted_doc[field] = (text + " ") * int(weight)
            weighted_docs.append(weighted_doc)
        
        # Aplicar BM25
        bm25 = BM25(weighted_docs)
        ranked = bm25.rank(query)
        
        # Retornar documentos con scores
        results = [(docs[idx], score) for idx, score in ranked]
        return results
