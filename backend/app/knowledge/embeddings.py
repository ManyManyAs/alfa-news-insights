"""Локальные эмбеддинги для гибридного поиска (организаторы разрешили локальные embedding-модели).

Включаются переменной EMBEDDINGS_MODEL (например, intfloat/multilingual-e5-large или
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) и пакетом `fastembed`
(backend/requirements-embeddings.txt). Без них поиск работает на BM25 — сервис не ломается.
"""
from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from typing import Protocol

import numpy as np

from app.config import get_settings

log = logging.getLogger(__name__)


class Embedder(Protocol):
    def index(self, texts: list[str]) -> np.ndarray: ...
    def rank(self, query: str, matrix: np.ndarray) -> list[int]: ...


class FastEmbedEmbedder:
    def __init__(self, model_name: str):
        from fastembed import TextEmbedding  # опциональная зависимость

        self.model_name = model_name
        cache = get_settings().db_path.parent / "models"  # том data/processed — модель скачивается один раз
        self.model = TextEmbedding(model_name=model_name, cache_dir=str(cache))
        self.e5 = "e5" in model_name.lower()  # e5 требует префиксов query:/passage:

    def _embed(self, texts: list[str]) -> np.ndarray:
        m = np.array(list(self.model.embed(texts)), dtype=np.float32)
        return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)

    def index(self, texts: list[str]) -> np.ndarray:
        cache_dir = get_settings().db_path.parent
        h = hashlib.sha256(("|".join(texts) + self.model_name).encode()).hexdigest()[:16]
        path = cache_dir / f"emb-{h}.npy"
        if path.exists():
            return np.load(path)
        m = self._embed([("passage: " + t) if self.e5 else t for t in texts])
        cache_dir.mkdir(parents=True, exist_ok=True)
        np.save(path, m)
        return m

    def rank(self, query: str, matrix: np.ndarray) -> list[int]:
        q = self._embed([("query: " + query) if self.e5 else query])[0]
        return list(np.argsort(-(matrix @ q)))


@lru_cache
def get_embedder() -> Embedder | None:
    name = get_settings().embeddings_model
    if not name:
        return None
    try:
        return FastEmbedEmbedder(name)
    except Exception as e:  # нет пакета/модели — работаем на BM25
        log.warning("Эмбеддинги недоступны (%s), поиск — BM25", e)
        return None
