"""Configuração central do pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api-publica.datajud.cnj.jus.br"

# Chave pública publicada pelo CNJ (pode ser trocada pelo órgão; ver .env.example).
DEFAULT_API_KEY = "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw=="


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default_factory=lambda: os.getenv("DATAJUD_API_KEY", DEFAULT_API_KEY))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "data")))
    page_size: int = 1000  # máximo aceito por página na API pública
    timeout_s: int = 60

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def warehouse_path(self) -> Path:
        return self.data_dir / "warehouse.duckdb"


def endpoint(tribunal: str) -> str:
    """URL de busca para um tribunal (ex.: 'tjsp', 'trt2', 'stj')."""
    return f"{BASE_URL}/api_publica_{tribunal.lower()}/_search"
