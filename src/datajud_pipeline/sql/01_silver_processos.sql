-- Camada silver: um registro por processo (tribunal + número + grau), tipado e deduplicado.
-- A raw é lida como JSON bruto para tolerar mudanças de schema da API.

CREATE OR REPLACE MACRO parse_ts(s) AS
    CASE
        WHEN s IS NULL THEN NULL
        WHEN regexp_matches(s, '^\d{14}$') THEN strptime(s, '%Y%m%d%H%M%S')
        WHEN regexp_matches(s, '^\d{8}$') THEN strptime(s, '%Y%m%d')
        ELSE try_cast(replace(s, 'Z', '') AS TIMESTAMP)
    END;

CREATE SCHEMA IF NOT EXISTS silver;

CREATE OR REPLACE TABLE silver.processos AS
WITH raw AS (
    SELECT json AS doc
    FROM read_ndjson_objects('{raw_glob}')
),
typed AS (
    SELECT
        doc ->> '$.numeroProcesso'                              AS numero_processo,
        lower(doc ->> '$.tribunal')                              AS tribunal,
        doc ->> '$.grau'                                         AS grau,
        try_cast(doc ->> '$.classe.codigo' AS INTEGER)           AS classe_codigo,
        doc ->> '$.classe.nome'                                  AS classe_nome,
        doc ->> '$.sistema.nome'                                 AS sistema,
        doc ->> '$.formato.nome'                                 AS formato,
        try_cast(doc ->> '$.orgaoJulgador.codigo' AS INTEGER)    AS orgao_codigo,
        doc ->> '$.orgaoJulgador.nome'                           AS orgao_nome,
        try_cast(doc ->> '$.orgaoJulgador.codigoMunicipioIBGE' AS INTEGER) AS municipio_ibge,
        try_cast(doc ->> '$.nivelSigilo' AS INTEGER)             AS nivel_sigilo,
        parse_ts(doc ->> '$.dataAjuizamento')                    AS data_ajuizamento,
        parse_ts(doc ->> '$.dataHoraUltimaAtualizacao')          AS atualizado_em,
        doc -> '$.assuntos'                                      AS assuntos_json,
        doc -> '$.movimentos'                                    AS movimentos_json
    FROM raw
)
SELECT *
FROM typed
WHERE numero_processo IS NOT NULL
QUALIFY row_number() OVER (
    PARTITION BY tribunal, numero_processo, grau
    ORDER BY atualizado_em DESC NULLS LAST
) = 1;
