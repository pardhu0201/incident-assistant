"""Dependency-free hashed embedder, shared with the BM25 tokenizer.

Same design as the sibling projects: signed feature hashing over stemmed
words, bigrams and character 4-grams, weighted so word identity dominates
character noise. Costs nothing, needs no model download, deterministic.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from functools import lru_cache

import numpy as np

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")


def stem(word: str) -> str:
    if len(word) <= 3 or word.isdigit():
        return word
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    if word.endswith("ed") and len(word) > 4:
        word = word[:-2]
    elif word.endswith("ing") and len(word) > 5:
        word = word[:-3]
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    return [stem(w) for w in _WORD_RE.findall(text.lower())]


class HashingEmbedder:
    name = "hashing"
    FEATURE_WEIGHTS = {"w": 1.0, "b": 0.7, "c": 0.3}

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    @staticmethod
    def _features(text: str) -> Counter[str]:
        words = tokenize(text)
        feats: Counter[str] = Counter()
        for w in words:
            feats[f"w:{w}"] += 1
            padded = f"^{w}$"
            for i in range(len(padded) - 3):
                feats[f"c:{padded[i : i + 4]}"] += 1
        for a, b in zip(words, words[1:], strict=False):
            feats[f"b:{a}_{b}"] += 1
        return feats

    @staticmethod
    def _bucket(feature: str, dim: int) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        sign = 1.0 if (value >> 63) & 1 else -1.0
        return value % dim, sign

    def _encode_one(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for feature, count in self._features(text).items():
            idx, sign = self._bucket(feature, self.dim)
            weight = self.FEATURE_WEIGHTS.get(feature[0], 1.0)
            vec[idx] += sign * weight * (1.0 + math.log(count))
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack([self._encode_one(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._encode_one(text)


@lru_cache
def get_embedder() -> HashingEmbedder:
    embedder = HashingEmbedder(dim=settings.embedding_dim)
    log.info("Embeddings: hashing (dim=%d)", embedder.dim)
    return embedder


def cosine_matrix(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return np.zeros(0, dtype=np.float32)
    denom = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(query) or 1.0)
    denom[denom == 0] = 1.0
    return (matrix @ query) / denom
