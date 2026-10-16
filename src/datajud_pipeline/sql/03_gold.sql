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
--
-- Encerramento de fato: muitos processos são arquivados provisoriamente (245) e nunca mais
-- se mexem, sem receber baixa formal. Na amostra do TJSP, 90% das reaberturas após um
-- arquivamento provisório acontecem em até ~3 anos; por isso, um arquivamento provisório
-- seguido de 3 anos ou mais sem movimento relevante até a data de corte conta como
-- encerramento de fato, na data do arquivamento. É uma inferência a partir dos dados, não
-- uma regra do CNJ: as colunas formais e de fato ficam lado a lado na gold.
-- Movimentos não relevantes (rotina de cartório, não indicam que o processo voltou a andar):
-- 60 Expedição de documento, 581 Documento, 11383 Ato ordinatório, 85 Petição, 92 Publicação.
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
        max(data_hora) FILTER (WHERE movimento_codigo = 245)         AS ultimo_arq_provisorio,
        max(data_hora) FILTER (
            WHERE movimento_codigo NOT IN (245, 60, 581, 11383, 85, 92)
        )                                                            AS ultimo_mov_relevante,
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
-- O alvo é o par (duracao_dias, encerrado), no formato de análise de sobrevivência, com a
-- variante (duracao_dias_de_fato, encerrado_de_fato) que inclui o encerramento de fato.
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
        e.data_encerramento IS NOT NULL                    AS encerrado,
        e.ultimo_arq_provisorio,
        e.ultimo_mov_relevante
    FROM silver.processos AS p
    LEFT JOIN silver.assuntos AS a
           ON a.tribunal = p.tribunal AND a.numero_processo = p.numero_processo
          AND a.grau = p.grau AND a.ordem = 1
    LEFT JOIN gold.encerramentos AS e
           ON e.tribunal = p.tribunal AND e.numero_processo = p.numero_processo
          AND e.grau = p.grau
    WHERE p.data_ajuizamento IS NOT NULL
      AND coalesce(p.nivel_sigilo, 0) = 0  -- qualquer nível de sigilo (1 a 5) fica de fora
),
de_fato AS (
    SELECT
        * EXCLUDE (ultimo_arq_provisorio, ultimo_mov_relevante),
        CASE
            WHEN encerrado THEN data_encerramento
            WHEN ultimo_arq_provisorio IS NOT NULL
             AND (ultimo_mov_relevante IS NULL OR ultimo_mov_relevante < ultimo_arq_provisorio)
             AND date_diff('day', ultimo_arq_provisorio, data_corte) >= 3 * 365
            THEN ultimo_arq_provisorio
        END AS data_encerramento_de_fato
    FROM base
)
SELECT
    *,
    date_diff('day', data_ajuizamento, coalesce(data_encerramento, data_corte)) AS duracao_dias,
    CASE WHEN encerrado
         THEN date_diff('day', data_ajuizamento, data_encerramento) END         AS dias_ate_encerramento,
    data_encerramento_de_fato IS NOT NULL                                       AS encerrado_de_fato,
    date_diff(
        'day', data_ajuizamento, coalesce(data_encerramento_de_fato, data_corte)
    )                                                                           AS duracao_dias_de_fato
FROM de_fato
-- Datas inconsistentes (fim antes do início, sem data de corte) ficam de fora; o
-- relatório de qualidade do transform informa quantos processos foram descartados.
WHERE data_corte IS NOT NULL
  AND coalesce(data_encerramento, data_corte) >= data_ajuizamento
  AND coalesce(data_encerramento_de_fato, data_corte) >= data_ajuizamento;

-- Agregado para o dashboard. A mediana de dias considera só os encerrados, então é
-- enviesada para baixo quando há muitos censurados; use pct_encerrados junto.
CREATE OR REPLACE TABLE gold.kpis_classe AS
SELECT
    tribunal,
    classe_nome,
    count(*)                                          AS processos,
    count(*) FILTER (WHERE encerrado)                 AS encerrados,
    round(100.0 * count(*) FILTER (WHERE encerrado) / count(*), 1) AS pct_encerrados,
    round(100.0 * count(*) FILTER (WHERE encerrado_de_fato) / count(*), 1) AS pct_encerrados_de_fato,
    median(dias_ate_encerramento)                     AS mediana_dias_encerramento
FROM gold.processos_features
GROUP BY ALL
ORDER BY processos DESC;
