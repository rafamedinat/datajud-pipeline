-- Camada gold: tabelas prontas para o modelo de ML e para o dashboard.
--
-- Encerramento = primeiro movimento de baixa/arquivamento definitivo, segundo a Tabela
-- Processual Unificada (TPU) do CNJ: 22 (Baixa Definitiva) e 246 (Arquivamento Definitivo).
-- Confira os códigos na TPU antes de tirar conclusões: https://www.cnj.jus.br/sgt/consulta_publica_movimentos.php

CREATE SCHEMA IF NOT EXISTS gold;

CREATE OR REPLACE TABLE gold.encerramentos AS
SELECT
    tribunal,
    numero_processo,
    grau,
    min(data_hora) AS data_encerramento
FROM silver.movimentos
WHERE movimento_codigo IN (22, 246)
GROUP BY ALL;

-- Base de modelagem: só atributos conhecidos no ajuizamento, para não vazar o futuro.
CREATE OR REPLACE TABLE gold.processos_features AS
SELECT
    p.tribunal,
    p.numero_processo,
    p.grau,
    p.classe_codigo,
    p.classe_nome,
    p.sistema,
    p.formato,
    p.orgao_codigo,
    p.municipio_ibge,
    a.assunto_codigo                                   AS assunto_principal_codigo,
    a.assunto_nome                                     AS assunto_principal_nome,
    (SELECT count(*) FROM silver.assuntos s
      WHERE s.tribunal = p.tribunal AND s.numero_processo = p.numero_processo
        AND s.grau = p.grau)                           AS qtd_assuntos,
    year(p.data_ajuizamento)                           AS ano_ajuizamento,
    month(p.data_ajuizamento)                          AS mes_ajuizamento,
    p.data_ajuizamento,
    e.data_encerramento,
    e.data_encerramento IS NOT NULL                    AS encerrado,
    date_diff('day', p.data_ajuizamento, e.data_encerramento) AS dias_ate_encerramento
FROM silver.processos AS p
LEFT JOIN silver.assuntos AS a
       ON a.tribunal = p.tribunal AND a.numero_processo = p.numero_processo
      AND a.grau = p.grau AND a.ordem = 1
LEFT JOIN gold.encerramentos AS e
       ON e.tribunal = p.tribunal AND e.numero_processo = p.numero_processo
      AND e.grau = p.grau
WHERE p.data_ajuizamento IS NOT NULL
  AND p.nivel_sigilo IS DISTINCT FROM 1
  AND (e.data_encerramento IS NULL OR e.data_encerramento >= p.data_ajuizamento);

-- Agregado para o dashboard.
CREATE OR REPLACE TABLE gold.kpis_classe AS
SELECT
    tribunal,
    classe_nome,
    count(*)                                          AS processos,
    count(*) FILTER (WHERE encerrado)                 AS encerrados,
    round(100.0 * count(*) FILTER (WHERE encerrado) / count(*), 1) AS pct_encerrados,
    median(dias_ate_encerramento)                     AS mediana_dias_encerramento
FROM gold.processos_features
GROUP BY ALL
ORDER BY processos DESC;
