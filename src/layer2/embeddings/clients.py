from __future__ import annotations

import hashlib
from math import sqrt

import httpx

from layer2.embeddings.base import BaseEmbeddingClient

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - optional dependency
    SentenceTransformer = None


def _normalize(values: list[float]) -> list[float]:
    magnitude = sqrt(sum(value * value for value in values)) or 1.0
    return [value / magnitude for value in values]


class HashingEmbeddingClient(BaseEmbeddingClient):
    def __init__(self, model_name: str = "hashing-bge-small-en-v1.5", dimensions: int = 384):
        self.model_name = model_name
        self.dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            buckets = [0.0] * self.dimensions
            tokens = text.lower().split()
            if not tokens:
                vectors.append(buckets)
                continue
            for token in tokens:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                buckets[index] += sign
            vectors.append(_normalize(buckets))
        return vectors


class SentenceTransformerEmbeddingClient(BaseEmbeddingClient):
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        if SentenceTransformer is None:  # pragma: no cover - optional dependency
            raise RuntimeError("sentence-transformers is not installed")
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        self.dimensions = self._model.get_sentence_embedding_dimension()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, row)) for row in vectors]


class OpenAICompatibleEmbeddingClient(BaseEmbeddingClient):
    def __init__(self, base_url: str, model_name: str, api_key: str | None = None, dimensions: int = 384):
        self.model_name = model_name
        self.dimensions = dimensions
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        response = httpx.post(
            f"{self._base_url}/embeddings",
            headers=headers,
            json={"model": self.model_name, "input": texts},
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", [])
        vectors = [list(map(float, item["embedding"])) for item in data]
        if vectors:
            self.dimensions = len(vectors[0])
        return vectors


class MockEmbeddingClient(HashingEmbeddingClient):
    def __init__(self, dimensions: int = 384):
        super().__init__(model_name="mock-embedding", dimensions=dimensions)

