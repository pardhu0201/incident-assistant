"""Hybrid retrieval: BM25 + cosine similarity, fused by score and rank.

Same fusion design validated in the sibling projects: a min-max normalised
score blended with a normalised reciprocal rank from each leg (60/40) - pure
Reciprocal Rank Fusion throws away a decisive BM25 score margin at this
corpus size; pure score fusion lets one leg's scale dominate. A
heading-relevance boost rewards a chunk whose section title already answers
the question. Near-duplicate suppression removes the overlapping windows
chunking produces without demoting genuinely distinct sections.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Chunk, Document
from app.rag.embeddings import get_embedder, tokenize

RRF_K = 12
NEAR_DUPLICATE_JACCARD = 0.6
HEADING_BOOST = 0.35
_QUERY_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "for",
    "on",
    "is",
    "are",
    "do",
    "does",
    "did",
    "how",
    "what",
    "when",
    "where",
    "who",
    "why",
    "can",
    "i",
    "my",
    "me",
    "much",
    "many",
    "long",
    "should",
    "would",
    "we",
    "our",
}


@dataclass
class ScoredChunk:
    chunk_id: str
    document_id: str
    document_title: str
    heading: str
    content: str
    score: float = 0.0
    dense_score: float = 0.0
    lexical_score: float = 0.0
    rank: int = 0

    @property
    def snippet(self) -> str:
        body = self.content.split("]\n", 1)[-1] if self.content.startswith("[") else self.content
        body = " ".join(body.split())
        return body[:420] + ("..." if len(body) > 420 else "")

    def to_citation(self, index: int) -> dict:
        return {
            "index": index,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "document_title": self.document_title,
            "heading": self.heading,
            "snippet": self.snippet,
            "score": round(self.score, 4),
            "dense_score": round(self.dense_score, 4),
            "lexical_score": round(self.lexical_score, 4),
        }


# --- BM25 --------------------------------------------------------------
@dataclass
class BM25Index:
    rows: list[tuple[Chunk, Document]]
    doc_tokens: list[list[str]]
    doc_freq: Counter
    avg_len: float
    k1: float = 1.5
    b: float = 0.75

    @classmethod
    def build(cls, rows: list[tuple[Chunk, Document]]) -> BM25Index:
        doc_tokens = [tokenize(c.content) for c, _ in rows]
        doc_freq: Counter = Counter()
        for tokens in doc_tokens:
            doc_freq.update(set(tokens))
        avg_len = (sum(len(t) for t in doc_tokens) / len(doc_tokens)) if doc_tokens else 0.0
        return cls(rows=rows, doc_tokens=doc_tokens, doc_freq=doc_freq, avg_len=avg_len or 1.0)

    def search(self, query: str, limit: int) -> list[tuple[int, float]]:
        q_tokens = tokenize(query)
        if not q_tokens or not self.rows:
            return []
        n = len(self.rows)
        scores = np.zeros(n, dtype=np.float32)
        for term in set(q_tokens):
            df = self.doc_freq.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            for i, tokens in enumerate(self.doc_tokens):
                tf = tokens.count(term)
                if tf == 0:
                    continue
                norm = 1.0 - self.b + self.b * (len(tokens) / self.avg_len)
                scores[i] += idf * (tf * (self.k1 + 1.0)) / (tf + self.k1 * norm)
        order = np.argsort(-scores)[:limit]
        return [(int(i), float(scores[int(i)])) for i in order if scores[int(i)] > 0]


_cache: dict[str, BM25Index] = {}


def _corpus_version(db: Session) -> str:
    count = db.execute(select(func.count(Chunk.id))).scalar() or 0
    latest = db.execute(select(func.max(Document.created_at))).scalar()
    return f"{count}:{latest}"


def _get_index(db: Session) -> BM25Index:
    version = _corpus_version(db)
    cached = _cache.get(version)
    if cached is None:
        _cache.clear()
        rows = list(
            db.execute(
                select(Chunk, Document).join(Document, Chunk.document_id == Document.id)
            ).all()
        )
        cached = BM25Index.build(rows)
        _cache[version] = cached
    return cached


def invalidate_index() -> None:
    _cache.clear()


def _fuse(legs: list[tuple[list[tuple[str, float]], float]]) -> dict[str, float]:
    fused: dict[str, float] = {}
    for hits, weight in legs:
        if not hits:
            continue
        scores = [s for _, s in hits]
        low, high = min(scores), max(scores)
        span = (high - low) or 1.0
        for rank, (chunk_id, score) in enumerate(hits):
            normalised = (score - low) / span
            reciprocal = (RRF_K + 1) / (RRF_K + rank + 1)
            fused[chunk_id] = fused.get(chunk_id, 0.0) + weight * (
                0.6 * normalised + 0.4 * reciprocal
            )
    return fused


def _near_duplicate_suppress(
    candidates: list[ScoredChunk], top_k: int, penalty: float = 0.5
) -> list[ScoredChunk]:
    if len(candidates) <= top_k:
        return candidates
    token_sets = [set(tokenize(c.content)) for c in candidates]
    selected: list[int] = []
    remaining = set(range(len(candidates)))

    while remaining and len(selected) < top_k:
        best_idx, best_value = None, -1e9
        for i in remaining:
            redundancy = 0.0
            for j in selected:
                union = token_sets[i] | token_sets[j]
                if union:
                    redundancy = max(redundancy, len(token_sets[i] & token_sets[j]) / len(union))
            excess = max(0.0, redundancy - NEAR_DUPLICATE_JACCARD) / (1.0 - NEAR_DUPLICATE_JACCARD)
            value = candidates[i].score - penalty * excess
            if value > best_value:
                best_idx, best_value = i, value
        selected.append(best_idx)  # type: ignore[arg-type]
        remaining.discard(best_idx)  # type: ignore[arg-type]
    return [candidates[i] for i in selected]


def retrieve(
    db: Session, query: str, top_k: int | None = None, candidates: int | None = None
) -> list[ScoredChunk]:
    top_k = top_k or settings.retrieval_top_k
    candidates = candidates or settings.retrieval_candidates

    index = _get_index(db)
    if not index.rows:
        return []

    embedder = get_embedder()
    query_vec = embedder.embed_query(query)

    dense_hits: list[tuple[str, float]] = []
    for chunk, _doc in index.rows:
        if not chunk.embedding:
            continue
        vec = np.asarray(chunk.embedding, dtype=np.float32)
        denom = (np.linalg.norm(vec) * np.linalg.norm(query_vec)) or 1.0
        dense_hits.append((chunk.id, float(vec @ query_vec / denom)))
    dense_hits.sort(key=lambda p: p[1], reverse=True)
    dense_hits = dense_hits[:candidates]

    lexical_hits = [(index.rows[i][0].id, score) for i, score in index.search(query, candidates)]

    fused = _fuse([(dense_hits, 0.55), (lexical_hits, 1.0)])
    if not fused:
        return []

    by_id = {c.id: (c, d) for c, d in index.rows}
    dense_by_id = dict(dense_hits)
    lexical_by_id = dict(lexical_hits)

    content_terms = {t for t in tokenize(query) if t not in _QUERY_STOPWORDS and len(t) > 2}

    built: list[ScoredChunk] = []
    for chunk_id, base_score in fused.items():
        chunk, document = by_id[chunk_id]
        score = base_score
        if content_terms:
            heading_terms = set(tokenize(chunk.heading))
            overlap = len(content_terms & heading_terms) / len(content_terms)
            score *= 1.0 + HEADING_BOOST * overlap
        built.append(
            ScoredChunk(
                chunk_id=chunk.id,
                document_id=document.id,
                document_title=document.title,
                heading=chunk.heading,
                content=chunk.content,
                score=score,
                dense_score=dense_by_id.get(chunk_id, 0.0),
                lexical_score=lexical_by_id.get(chunk_id, 0.0),
            )
        )

    best = max((c.score for c in built), default=1.0) or 1.0
    for chunk in built:
        chunk.score /= best
    built.sort(key=lambda c: c.score, reverse=True)

    diversified = _near_duplicate_suppress(built[: max(top_k * 3, top_k)], top_k)
    for position, chunk in enumerate(diversified, start=1):
        chunk.rank = position
    return diversified


def build_context_block(chunks: list[ScoredChunk], token_budget: int = 3200) -> str:
    parts: list[str] = []
    used = 0
    for i, chunk in enumerate(chunks, start=1):
        body = chunk.content
        cost = max(1, len(body) // 4)
        if used + cost > token_budget and parts:
            break
        used += cost
        parts.append(
            f"[{i}] source={chunk.document_title} | section={chunk.heading or 'n/a'}\n{body}"
        )
    return "\n\n---\n\n".join(parts)
