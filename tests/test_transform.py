import json
from pathlib import Path

import duckdb
import pytest

from datajud_pipeline.config import Settings
from datajud_pipeline.ingestion import write_raw_page
from datajud_pipeline.quality import Check, QualityError, run_checks
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


def test_em_andamento_e_censurado_na_data_de_corte(warehouse):
    _, con = warehouse
    row = con.execute(
        "SELECT encerrado, duracao_dias, data_corte::DATE::VARCHAR FROM gold.processos_features "
        "WHERE numero_processo = '00000022220208260100'"
    ).fetchone()
    # sem encerramento: dura até a última atualização na API, não até hoje
    assert row == (False, 1381, "2024-03-01")


def _processo(numero: str, movimentos: list[tuple[int, str]]) -> dict:
    return {
        "_source": {
            "numeroProcesso": numero,
            "tribunal": "TJSP",
            "grau": "G1",
            "classe": {"codigo": 7, "nome": "Procedimento Comum Cível"},
            "nivelSigilo": 0,
            "dataAjuizamento": "20180101000000",
            "dataHoraUltimaAtualizacao": "2024-01-01T00:00:00.000Z",
            "movimentos": [
                {"codigo": c, "nome": str(c), "dataHora": f"{d}T00:00:00.000Z"}
                for c, d in movimentos
            ],
        }
    }


def test_reabertura_desfaz_encerramento(tmp_path):
    settings = Settings(data_dir=tmp_path)
    page = [
        # arquivado e reativado depois: continua em andamento
        _processo("1", [(246, "2019-01-01"), (849, "2020-01-01")]),
        # arquivado, desarquivado e baixado de novo: encerra na última baixa
        _processo("2", [(246, "2019-01-01"), (893, "2020-01-01"), (22, "2023-06-01")]),
        # baixa definitiva sem reabertura
        _processo("3", [(22, "2019-01-01")]),
    ]
    write_raw_page(page, settings.raw_dir / "tribunal=tjsp" / "extracted_at=2026-10-15", 1)
    transform(settings)
    with duckdb.connect(str(settings.warehouse_path), read_only=True) as con:
        rows = {
            r[0]: r[1:]
            for r in con.execute(
                "SELECT numero_processo, encerrado, dias_ate_encerramento "
                "FROM gold.processos_features"
            ).fetchall()
        }
    assert rows["1"] == (False, None)
    assert rows["2"] == (True, 1977)
    assert rows["3"] == (True, 365)


def test_checagem_de_erro_interrompe_o_pipeline(monkeypatch):
    from datajud_pipeline import quality

    monkeypatch.setattr(
        quality,
        "CHECKS",
        [Check("sempre ok", "erro", "SELECT 0"), Check("quebrada", "erro", "SELECT 3")],
    )
    with pytest.raises(QualityError, match="quebrada"):
        run_checks(duckdb.connect())


def test_checagem_de_aviso_nao_interrompe(monkeypatch):
    from datajud_pipeline import quality

    monkeypatch.setattr(quality, "CHECKS", [Check("fonte suja", "aviso", "SELECT 5")])
    assert run_checks(duckdb.connect()) == {"fonte suja": 5}


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
