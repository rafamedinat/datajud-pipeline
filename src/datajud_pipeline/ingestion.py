"""Ingestão da API pública do DataJud para a camada raw.

A API é um Elasticsearch: a busca é feita por POST com uma query DSL e a paginação
profunda usa `sort` + `search_after`. Cada página é gravada como um arquivo JSONL na
camada raw, particionada por tribunal e data de extração, sem nenhuma transformação.

Cada execução grava num diretório próprio (`run=`), então execuções no mesmo dia não se
sobrescrevem. Depois de cada página o cursor é salvo em `_state.json`, o que permite
retomar uma ingestão interrompida com `resume=True`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import asdict, dataclass
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


STATE_FILE = "_state.json"


@dataclass(frozen=True)
class Recorte:
    """Filtros de uma ingestão. Com `seed`, os processos vêm numa ordem aleatória
    reprodutível, e `max_docs` vira uma amostra aleatória simples do recorte."""

    classe: int | None = None
    grau: str | None = None
    ajuizado_desde: str | None = None
    ajuizado_ate: str | None = None  # exclusivo
    seed: int | None = None


def build_query(
    page_size: int,
    recorte: Recorte | None = None,
    search_after: list[Any] | None = None,
) -> dict[str, Any]:
    """Monta a query DSL de uma página.

    A ordenação sempre termina em `id.keyword` (único por processo e grau) para desempatar:
    só com `@timestamp`, documentos com o mesmo valor na virada da página seriam pulados
    pelo `search_after`.
    """
    recorte = recorte or Recorte()
    filters: list[dict[str, Any]] = []
    if recorte.classe is not None:
        filters.append({"match": {"classe.codigo": recorte.classe}})
    if recorte.grau:
        filters.append({"match": {"grau": recorte.grau}})
    datas = {}
    if recorte.ajuizado_desde:
        datas["gte"] = recorte.ajuizado_desde
    if recorte.ajuizado_ate:
        datas["lt"] = recorte.ajuizado_ate
    if datas:
        filters.append({"range": {"dataAjuizamento": datas}})

    base: dict[str, Any] = {"bool": {"filter": filters}} if filters else {"match_all": {}}
    if recorte.seed is None:
        query = base
        sort: list[dict[str, Any]] = [{"@timestamp": {"order": "asc"}}]
    else:
        query = {
            "function_score": {
                "query": base,
                "random_score": {"seed": recorte.seed, "field": "id.keyword"},
                "boost_mode": "replace",
            }
        }
        sort = [{"_score": {"order": "desc"}}]
    sort.append({"id.keyword": {"order": "asc"}})

    body: dict[str, Any] = {"size": page_size, "query": query, "sort": sort}
    if search_after is not None:
        body["search_after"] = search_after
    return body


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
        recorte: Recorte | None = None,
        max_docs: int | None = None,
        search_after: list[Any] | None = None,
        total: int = 0,
    ) -> Iterator[list[dict[str, Any]]]:
        """Itera páginas de `hits` até acabar os resultados ou atingir `max_docs`.

        `search_after` e `total` permitem continuar de onde uma execução anterior parou.
        """
        url = endpoint(tribunal)

        while True:
            size = self.settings.page_size
            if max_docs:  # None ou 0 = sem limite
                size = min(size, max_docs - total)
                if size <= 0:
                    return

            body = build_query(size, recorte, search_after)
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


def _write_state(run_dir: Path, state: dict[str, Any]) -> None:
    """Grava o estado da execução de forma atômica (arquivo temporário + rename)."""
    tmp = run_dir / f"{STATE_FILE}.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(run_dir / STATE_FILE)


def find_incomplete_run(raw_dir: Path, tribunal: str) -> Path | None:
    """Diretório da execução incompleta mais recente do tribunal, se houver."""
    base = raw_dir / f"tribunal={tribunal.lower()}"
    runs = sorted(base.glob(f"extracted_at=*/run=*/{STATE_FILE}"), key=lambda p: p.parent.name)
    for state_path in reversed(runs):
        if not json.loads(state_path.read_text(encoding="utf-8")).get("complete"):
            return state_path.parent
    return None


def ingest(
    tribunal: str,
    recorte: Recorte | None = None,
    max_docs: int | None = None,
    settings: Settings | None = None,
    client: DataJudClient | None = None,
    resume: bool = False,
) -> int:
    """Executa a ingestão e devolve o total de documentos gravados nesta execução.

    Com `resume=True`, continua a execução incompleta mais recente do tribunal com o
    mesmo recorte dela, a partir da última página gravada.
    """
    settings = settings or Settings()
    client = client or DataJudClient(settings)

    if resume:
        run_dir = find_incomplete_run(settings.raw_dir, tribunal)
        if run_dir is None:
            raise DataJudError(f"Nenhuma ingestão incompleta de {tribunal} para retomar.")
        state = json.loads((run_dir / STATE_FILE).read_text(encoding="utf-8"))
        log.info(
            "Retomando %s: %d docs em %d páginas já gravados (recorte=%s, max_docs=%s)",
            run_dir, state["total"], state["pages"], state["recorte"], state["max_docs"],
        )
    else:
        now = datetime.now(timezone.utc)
        run_dir = (
            settings.raw_dir
            / f"tribunal={tribunal.lower()}"
            / f"extracted_at={now:%Y-%m-%d}"
            / f"run={now:%Y%m%dT%H%M%SZ}"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "tribunal": tribunal.lower(),
            "recorte": asdict(recorte or Recorte()),
            "max_docs": max_docs,
            "search_after": None,
            "pages": 0,
            "total": 0,
            "complete": False,
        }
        _write_state(run_dir, state)

    already = state["total"]
    pages = client.iter_pages(
        state["tribunal"],
        Recorte(**state["recorte"]),
        state["max_docs"],
        search_after=state["search_after"],
        total=state["total"],
    )
    for hits in pages:
        write_raw_page(hits, run_dir, state["pages"] + 1)
        state["pages"] += 1
        state["total"] += len(hits)
        state["search_after"] = hits[-1].get("sort")
        _write_state(run_dir, state)

    state["complete"] = True
    _write_state(run_dir, state)
    log.info("Ingestão concluída: %d documentos em %s", state["total"], run_dir)
    return state["total"] - already
