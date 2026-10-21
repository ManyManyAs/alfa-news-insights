"""Настройки приложения: переменные окружения + YAML-конфиги из /config."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    # LLM: OpenAI-совместимый эндпоинт организаторов
    llm_base_url: str = "https://example.invalid/v1"
    llm_api_key: str = ""
    # auto — live при наличии ключа, иначе replay; live — реальные модели; replay — только сохранённые
    # ответы (снапшот в репозитории, работает без ключа); mock — без LLM вообще (тесты)
    llm_mode: str = "auto"
    llm_timeout_s: float = 60.0

    config_dir: Path = REPO_ROOT / "config"
    data_dir: Path = REPO_ROOT / "data"
    db_path: Path = REPO_ROOT / "data" / "processed" / "app.db"

    # Явно задать каталог с CSV (по умолчанию data/raw, а если там пусто — data/sample)
    data_source_dir: Path | None = None
    # Подгружать снапшот результатов LLM из data/snapshot при первом старте
    use_snapshot: bool = True
    # Локальная embedding-модель для гибридного поиска (пусто — только BM25)
    embeddings_model: str = ""
    # Сколько компаний загружать (для быстрого старта демо можно ограничить)
    max_companies: int = 0  # 0 = все
    # Фоновый сбор новостей из RSS
    enable_scheduler: bool = False
    # Разрешить загрузку произвольного URL новости через /api/news/analyze
    allow_url_fetch: bool = True
    # Демо-авторизация: общий пароль демо-учёток, секрет подписи сессий (пусто — сгенерировать и хранить в БД),
    # заголовок X-User-Id — только для тестов и локальной отладки
    demo_password: str = "alfa2026"
    session_secret: str = ""
    allow_header_auth: bool = False
    cookie_secure: bool = False

    @field_validator("llm_api_key", "llm_base_url", mode="before")
    @classmethod
    def _strip(cls, v):
        """Секреты часто копируются с пробелом/переводом строки — это ломает заголовок Authorization."""
        return v.strip() if isinstance(v, str) else v

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def knowledge_dir(self) -> Path:
        return self.data_dir / "knowledge"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_yaml(name: str) -> dict[str, Any]:
    path = get_settings().config_dir / name
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
