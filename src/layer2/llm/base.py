from __future__ import annotations

from abc import ABC, abstractmethod


class BaseLLMClient(ABC):
    model_name: str

    @abstractmethod
    def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        raise NotImplementedError

