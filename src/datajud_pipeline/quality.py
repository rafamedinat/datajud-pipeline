"""Checagens de qualidade de dados que rodam no fim do `transform`.

Cada checagem é uma consulta que conta registros que violam uma regra. Checagens de
nível "erro" interrompem o pipeline: indicam bug no SQL e a base de modelagem não pode
ser usada. As de nível "aviso" só são registradas: descrevem problemas da fonte (datas
inconsistentes na API, por exemplo) que o pipeline trata descartando os registros.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import duckdb

log = logging.getLogger(__name__)


class QualityError(RuntimeError):
    """Uma ou mais checagens de nível "erro" falharam."""


@dataclass(frozen=True)
class Check:
    nome: str
    nivel: str  # "erro" ou "aviso"
    sql: str  # devolve uma única contagem de violações


CHECKS = [
    Check(
        "silver.processos sem chave duplicada",
        "erro",
        """SELECT count(*) - count(DISTINCT (tribunal, numero_processo, grau))
           FROM silver.processos""",
    ),
    Check(
        "silver.processos com número do processo",
        "erro",
        "SELECT count(*) FROM silver.processos WHERE numero_processo IS NULL",
    ),
    Check(
        "gold.processos_features não está vazia",
        "erro",
        "SELECT CASE WHEN count(*) = 0 THEN 1 ELSE 0 END FROM gold.processos_features",
    ),
    Check(
        "duração preenchida e não negativa",
        "erro",
        """SELECT count(*) FROM gold.processos_features
           WHERE duracao_dias IS NULL OR duracao_dias < 0""",
    ),
    Check(
        "encerrado tem data de encerramento e vice-versa",
        "erro",
        """SELECT count(*) FROM gold.processos_features
           WHERE encerrado <> (data_encerramento IS NOT NULL)""",
    ),
    Check(
        "encerramento não passa da data de corte",
        "erro",
        """SELECT count(*) FROM gold.processos_features
           WHERE data_encerramento > data_corte""",
    ),
    Check(
        "encerrado formal também é encerrado de fato, na mesma data",
        "erro",
        """SELECT count(*) FROM gold.processos_features
           WHERE encerrado AND (NOT encerrado_de_fato
                 OR data_encerramento_de_fato <> data_encerramento)""",
    ),
    Check(
        "duração de fato preenchida, não negativa e até a duração formal",
        "erro",
        """SELECT count(*) FROM gold.processos_features
           WHERE duracao_dias_de_fato IS NULL OR duracao_dias_de_fato < 0
              OR duracao_dias_de_fato > duracao_dias""",
    ),
    Check(
        "nenhum processo sigiloso na base de modelagem",
        "erro",
        """SELECT count(*) FROM gold.processos_features f
           JOIN silver.processos p USING (tribunal, numero_processo, grau)
           WHERE coalesce(p.nivel_sigilo, 0) > 0""",
    ),
    Check(
        "processos descartados por datas inconsistentes",
        "aviso",
        """SELECT count(*) FROM silver.processos p
           WHERE p.data_ajuizamento IS NOT NULL AND coalesce(p.nivel_sigilo, 0) = 0
             AND NOT EXISTS (
                 SELECT 1 FROM gold.processos_features f
                 WHERE f.tribunal = p.tribunal AND f.numero_processo = p.numero_processo
                   AND f.grau = p.grau)""",
    ),
    Check(
        "processos sem data de ajuizamento",
        "aviso",
        "SELECT count(*) FROM silver.processos WHERE data_ajuizamento IS NULL",
    ),
    Check(
        "movimentos sem data",
        "aviso",
        "SELECT count(*) FROM silver.movimentos WHERE data_hora IS NULL",
    ),
    Check(
        "processos sem assunto principal",
        "aviso",
        "SELECT count(*) FROM gold.processos_features WHERE assunto_principal_codigo IS NULL",
    ),
]


def run_checks(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Roda as checagens, registra o resultado e falha se alguma de nível erro violar."""
    resultados: dict[str, int] = {}
    falhas: list[str] = []
    for check in CHECKS:
        n = con.execute(check.sql).fetchone()[0]
        resultados[check.nome] = n
        if n == 0:
            log.info("[ok]    %s", check.nome)
        elif check.nivel == "aviso":
            log.warning("[aviso] %s: %d", check.nome, n)
        else:
            log.error("[erro]  %s: %d violações", check.nome, n)
            falhas.append(f"{check.nome} ({n})")
    if falhas:
        raise QualityError("Checagens de qualidade falharam: " + "; ".join(falhas))
    return resultados
