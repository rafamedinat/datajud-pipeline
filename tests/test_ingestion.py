import json
from pathlib import Path

import pytest

from datajud_pipeline.config import Settings, endpoint
from datajud_pipeline.ingestion import DataJudError, build_query, ingest

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sample_hits.json").read_text("utf-8"))


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Devolve as páginas em sequência e registra os corpos enviados."""

    def __init__(self, pages: list[list[dict]], status_code: int = 200):
        self.pages = pages
        self.status_code = status_code
        self.headers: dict = {}
        self.bodies: list[dict] = []

    def post(self, url, json, timeout):
        self.bodies.append(json)
        page = self.pages.pop(0) if self.pages else []
        return FakeResponse(self.status_code, {"hits": {"hits": page}})


def test_endpoint_usa_sigla_minuscula():
    assert endpoint("TJSP").endswith("/api_publica_tjsp/_search")


def test_build_query_com_filtros_e_paginacao():
    q = build_query(100, classe=7, ajuizado_desde="2020-01-01", search_after=[123])
    filtros = q["query"]["bool"]["filter"]
    assert {"match": {"classe.codigo": 7}} in filtros
    assert {"range": {"dataAjuizamento": {"gte": "2020-01-01"}}} in filtros
    assert q["search_after"] == [123]
    assert q["sort"] == [{"@timestamp": {"order": "asc"}}]


def test_build_query_sem_filtros():
    assert build_query(10)["query"] == {"match_all": {}}


def test_ingest_pagina_e_grava_jsonl(tmp_path):
    from datajud_pipeline.ingestion import DataJudClient

    settings = Settings(data_dir=tmp_path, page_size=2)
    session = FakeSession([FIXTURE[:2], FIXTURE[2:]])
    client = DataJudClient(settings, session=session)

    total = ingest("tjsp", settings=settings, client=client)

    assert total == 4
    arquivos = sorted(tmp_path.glob("raw/tribunal=tjsp/extracted_at=*/page_*.jsonl"))
    assert len(arquivos) == 2
    # a segunda página continua do `sort` do último hit da primeira
    assert session.bodies[1]["search_after"] == FIXTURE[1]["sort"]
    assert session.headers["Authorization"].startswith("APIKey ")


def test_ingest_respeita_max_docs(tmp_path):
    from datajud_pipeline.ingestion import DataJudClient

    settings = Settings(data_dir=tmp_path, page_size=1000)
    session = FakeSession([FIXTURE[:3]])
    client = DataJudClient(settings, session=session)

    ingest("tjsp", max_docs=3, settings=settings, client=client)
    assert session.bodies[0]["size"] == 3
    assert len(session.bodies) == 1


def test_erro_4xx_nao_e_repetido(tmp_path):
    from datajud_pipeline.ingestion import DataJudClient

    settings = Settings(data_dir=tmp_path)
    session = FakeSession([[]], status_code=401)
    client = DataJudClient(settings, session=session)

    with pytest.raises(DataJudError):
        ingest("tjsp", settings=settings, client=client)
    assert len(session.bodies) == 1
