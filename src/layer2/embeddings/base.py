from __future__ import annotations

from abc import ABC, abstractmethod


class BaseEmbeddingClient(ABC):
    model_name: str
    dimensions: int

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]
