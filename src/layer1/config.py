from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://layer1:layer1@localhost:5432/layer1",
        alias="DATABASE_URL",
    )
    ocr_enabled: bool = Field(default=False, alias="OCR_ENABLED")
    camelot_enabled: bool = Field(default=False, alias="CAMELOT_ENABLED")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    boilerplate_repetition_threshold: int = Field(default=2, alias="BOILERPLATE_REPETITION_THRESHOLD")


@lru_cache
def get_settings() -> Settings:
    return Settings()
