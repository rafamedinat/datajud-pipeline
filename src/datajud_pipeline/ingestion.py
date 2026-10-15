"""Ingestão da API pública do DataJud para a camada raw.

A API é um Elasticsearch: a busca é feita por POST com uma query DSL e a paginação
profunda usa `sort` + `search_after`. Cada página é gravada como um arquivo JSONL na
camada raw, particionada por tribunal e data de extração, sem nenhuma transformação.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import Settings, endpoint

log = logging.getLogger(__name__)


class DataJudError(RuntimeError):
    """Erro não recuperável retornado pela API."""


class TransientError(RuntimeError):
    """Erro temporário (timeout, 429, 5xx): vale tentar de novo."""


def build_query(
    page_size: int,
    classe: int | None = None,
    ajuizado_desde: str | None = None,
    search_after: list[Any] | None = None,
) -> dict[str, Any]:
    """Monta a query DSL de uma página."""
    filters: list[dict[str, Any]] = []
    if classe is not None:
        filters.append({"match": {"classe.codigo": classe}})
    if ajuizado_desde:
        filters.append({"range": {"dataAjuizamento": {"gte": ajuizado_desde}}})

    query: dict[str, Any] = {
        "size": page_size,
        "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
        "sort": [{"@timestamp": {"order": "asc"}}],
    }
    if search_after is not None:
        query["search_after"] = search_after
    return query


class DataJudClient:
    def __init__(self, settings: Settings | None = None, session: requests.Session | None = None):
        self.settings = settings or Settings()
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"APIKey {self.settings.api_key}",
                "Content-Type": "application/json",
            }
        )

    @retry(
        retry=retry_if_exception_type(TransientError),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self.session.post(url, json=body, timeout=self.settings.timeout_s)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientError(str(exc)) from exc

        if resp.status_code == 429 or resp.status_code >= 500:
            raise TransientError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise DataJudError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    def iter_pages(
        self,
        tribunal: str,
        classe: int | None = None,
        ajuizado_desde: str | None = None,
        max_docs: int | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        """Itera páginas de `hits` até acabar os resultados ou atingir `max_docs`."""
        url = endpoint(tribunal)
        search_after: list[Any] | None = None
        total = 0

        while True:
            size = self.settings.page_size
            if max_docs is not None:
                size = min(size, max_docs - total)
                if size <= 0:
                    return

            body = build_query(size, classe, ajuizado_desde, search_after)
            data = self._post(url, body)
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                return

            total += len(hits)
            log.info("tribunal=%s página com %d docs (acumulado %d)", tribunal, len(hits), total)
            yield hits

            search_after = hits[-1].get("sort")
            if search_after is None:  # sem sort não há como continuar paginando
                return


def write_raw_page(hits: list[dict[str, Any]], out_dir: Path, page_number: int) -> Path:
    """Grava uma página como JSONL, um documento `_source` por linha."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"page_{page_number:05d}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for hit in hits:
            fh.write(json.dumps(hit.get("_source", {}), ensure_ascii=False) + "\n")
    return path


def ingest(
    tribunal: str,
    classe: int | None = None,
    ajuizado_desde: str | None = None,
    max_docs: int | None = None,
    settings: Settings | None = None,
    client: DataJudClient | None = None,
) -> int:
    """Executa a ingestão e devolve o total de documentos gravados."""
    settings = settings or Settings()
    client = client or DataJudClient(settings)
    extracted_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = settings.raw_dir / f"tribunal={tribunal.lower()}" / f"extracted_at={extracted_at}"

    total = 0
    for i, hits in enumerate(client.iter_pages(tribunal, classe, ajuizado_desde, max_docs), 1):
        write_raw_page(hits, out_dir, i)
        total += len(hits)

    log.info("Ingestão concluída: %d documentos em %s", total, out_dir)
    return total
