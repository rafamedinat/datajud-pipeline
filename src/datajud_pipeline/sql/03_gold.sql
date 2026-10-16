-- Camada gold: tabelas prontas para o modelo de ML e para o dashboard.
--
-- Encerramento, segundo a Tabela Processual Unificada (TPU) do CNJ:
--   * eventos de fim:        22 (Baixa Definitiva) e 246 (Arquivamento Definitivo);
--   * eventos de reabertura: 849 (Reativação) e 893 (Desarquivamento).
-- Um processo só está encerrado se o último evento de fim não foi seguido de reabertura, e
-- a data de encerramento é a desse último evento. Usar o primeiro evento de fim encurtaria
-- processos que foram arquivados e voltaram a andar.
--
-- Processos não encerrados entram como censurados à direita: sabemos só que duraram pelo
-- menos até a data de corte, que é o último momento em que o processo foi observado (a
-- última atualização na API ou o último movimento, o que for mais recente). Usar a data
-- de hoje inventaria tempo não observado, porque a base pública não é atualizada todo dia.
-- Confira os códigos na TPU: https://www.cnj.jus.br/sgt/consulta_publica_movimentos.php

CREATE SCHEMA IF NOT EXISTS gold;

CREATE OR REPLACE TABLE gold.encerramentos AS
WITH eventos AS (
    SELECT
        tribunal,
        numero_processo,
        grau,
        max(data_hora) FILTER (WHERE movimento_codigo IN (22, 246))  AS ultimo_fim,
        max(data_hora) FILTER (WHERE movimento_codigo IN (849, 893)) AS ultima_reabertura,
        max(data_hora)                                               AS ultimo_movimento
    FROM silver.movimentos
    GROUP BY ALL
)
SELECT
    *,
    CASE
        WHEN ultimo_fim IS NOT NULL
         AND (ultima_reabertura IS NULL OR ultima_reabertura < ultimo_fim)
        THEN ultimo_fim
    END AS data_encerramento
FROM eventos;

-- Base de modelagem: só atributos conhecidos no ajuizamento, para não vazar o futuro.
-- O alvo é o par (duracao_dias, encerrado), no formato de análise de sobrevivência.
CREATE OR REPLACE TABLE gold.processos_features AS
WITH base AS (
    SELECT
        p.tribunal,
        p.numero_processo,
        p.grau,
        p.classe_codigo,
        p.classe_nome,
        p.sistema,
        p.formato,
        p.orgao_codigo,
        p.orgao_nome,
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
        greatest(p.atualizado_em, e.ultimo_movimento)      AS data_corte,
        e.data_encerramento IS NOT NULL                    AS encerrado
    FROM silver.processos AS p
    LEFT JOIN silver.assuntos AS a
           ON a.tribunal = p.tribunal AND a.numero_processo = p.numero_processo
          AND a.grau = p.grau AND a.ordem = 1
    LEFT JOIN gold.encerramentos AS e
           ON e.tribunal = p.tribunal AND e.numero_processo = p.numero_processo
          AND e.grau = p.grau
    WHERE p.data_ajuizamento IS NOT NULL
      AND coalesce(p.nivel_sigilo, 0) = 0  -- qualquer nível de sigilo (1 a 5) fica de fora
)
SELECT
    *,
    date_diff('day', data_ajuizamento, coalesce(data_encerramento, data_corte)) AS duracao_dias,
    CASE WHEN encerrado
         THEN date_diff('day', data_ajuizamento, data_encerramento) END         AS dias_ate_encerramento
FROM base
-- Datas inconsistentes (fim antes do início, sem data de corte) ficam de fora; o
-- relatório de qualidade do transform informa quantos processos foram descartados.
WHERE data_corte IS NOT NULL
  AND coalesce(data_encerramento, data_corte) >= data_ajuizamento;

-- Agregado para o dashboard. A mediana de dias considera só os encerrados, então é
-- enviesada para baixo quando há muitos censurados; use pct_encerrados junto.
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
