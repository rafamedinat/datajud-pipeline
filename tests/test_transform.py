import json
from pathlib import Path

import duckdb
import pytest

from datajud_pipeline.config import Settings
from datajud_pipeline.ingestion import write_raw_page
from datajud_pipeline.transform import transform

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sample_hits.json").read_text("utf-8"))


@pytest.fixture()
def warehouse(tmp_path):
    settings = Settings(data_dir=tmp_path)
    out = settings.raw_dir / "tribunal=tjsp" / "extracted_at=2026-10-15"
    write_raw_page(FIXTURE, out, 1)
    counts = transform(settings)
    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    yield counts, con
    con.close()


def test_deduplica_mantendo_versao_mais_recente(warehouse):
    counts, con = warehouse
    assert counts["silver.processos"] == 3
    n_mov = con.execute(
        "SELECT count(*) FROM silver.movimentos WHERE numero_processo = '00000011120198260100'"
    ).fetchone()[0]
    assert n_mov == 2  # veio da versão de 2024, não da de 2023 (sem movimentos)


def test_aceita_os_dois_formatos_de_data(warehouse):
    _, con = warehouse
    datas = dict(
        con.execute("SELECT numero_processo, data_ajuizamento::DATE::VARCHAR FROM silver.processos")
        .fetchall()
    )
    assert datas["00000011120198260100"] == "2019-01-10"
    assert datas["00000022220208260100"] == "2020-05-20"


def test_calcula_tempo_ate_encerramento(warehouse):
    _, con = warehouse
    rows = dict(
        con.execute(
            "SELECT numero_processo, dias_ate_encerramento FROM gold.processos_features"
        ).fetchall()
    )
    assert rows["00000011120198260100"] == 365
    assert rows["00000022220208260100"] is None  # ainda em andamento


def test_exclui_processos_sigilosos(warehouse):
    counts, _ = warehouse
    assert counts["gold.processos_features"] == 2


def test_assunto_principal_e_quantidade(warehouse):
    _, con = warehouse
    row = con.execute(
        "SELECT assunto_principal_nome, qtd_assuntos FROM gold.processos_features "
        "WHERE numero_processo = '00000011120198260100'"
    ).fetchone()
    assert row == ("Indenização por Dano Moral", 2)
