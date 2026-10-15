-- Camada silver: tabelas filhas explodidas a partir do processo.

CREATE OR REPLACE TABLE silver.movimentos AS
WITH arr AS (
    SELECT tribunal, numero_processo, grau, CAST(movimentos_json AS JSON[]) AS itens
    FROM silver.processos
),
expl AS (
    SELECT tribunal, numero_processo, grau, unnest(itens) AS m
    FROM arr
)
SELECT
    tribunal,
    numero_processo,
    grau,
    try_cast(m ->> '$.codigo' AS INTEGER) AS movimento_codigo,
    m ->> '$.nome'                        AS movimento_nome,
    parse_ts(m ->> '$.dataHora')          AS data_hora
FROM expl;

CREATE OR REPLACE TABLE silver.assuntos AS
WITH arr AS (
    SELECT tribunal, numero_processo, grau, CAST(assuntos_json AS JSON[]) AS itens
    FROM silver.processos
),
expl AS (
    SELECT
        tribunal, numero_processo, grau,
        unnest(generate_series(1, len(itens))) AS ordem,
        unnest(itens)                          AS a
    FROM arr
    WHERE len(itens) > 0
)
SELECT
    tribunal,
    numero_processo,
    grau,
    ordem,
    try_cast(a ->> '$.codigo' AS INTEGER) AS assunto_codigo,
    a ->> '$.nome'                        AS assunto_nome
FROM expl;
