"""Executa as transformações raw → silver → gold no DuckDB."""

from __future__ import annotations

import logging
from importlib.resources import files

import duckdb

from .config import Settings

log = logging.getLogger(__name__)

SQL_STEPS = [
    "01_silver_processos.sql",
    "02_silver_movimentos_assuntos.sql",
    "03_gold.sql",
]


def transform(settings: Settings | None = None) -> dict[str, int]:
    """Roda todos os passos SQL e devolve a contagem de linhas por tabela."""
    settings = settings or Settings()
    raw_glob = (settings.raw_dir / "**" / "*.jsonl").as_posix()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(settings.warehouse_path))
    try:
        for step in SQL_STEPS:
            sql = files("datajud_pipeline.sql").joinpath(step).read_text(encoding="utf-8")
            log.info("Executando %s", step)
            con.execute(sql.replace("{raw_glob}", raw_glob))

        tables = [
            "silver.processos",
            "silver.movimentos",
            "silver.assuntos",
            "gold.processos_features",
            "gold.kpis_classe",
        ]
        counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        con.close()

    for table, n in counts.items():
        log.info("%-25s %d linhas", table, n)
    return counts
