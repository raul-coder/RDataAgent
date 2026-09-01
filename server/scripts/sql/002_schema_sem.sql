-- =====================================================================
-- 002_schema_sem.sql —— 语义层 sem_*（Text2SQL 的元数据基础）
--
-- 设计要点：
--   sem_metric / sem_dimension 把「业务口径」固化为 SQL 表达式，
--   LLM 只能使用注册过的表达式，杜绝臆造列名与口径漂移；
--   sem_rule 是注入 Prompt 的业务规则；
--   sem_fewshot 是可持续沉淀的问答-SQL 样本（verified=true 优先召回）。
-- =====================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS sem_data_source (
    id          BIGSERIAL PRIMARY KEY,
    group_name  VARCHAR(64)  NOT NULL,        -- 台账数据 / 统计报表
    name        VARCHAR(64)  NOT NULL,        -- 商业市场台账
    object_name VARCHAR(128) NOT NULL,        -- bi.fact_contract / bi.v_overall_achieve
    object_type VARCHAR(16)  NOT NULL,        -- table / view
    description TEXT,
    enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    sort_order  INT          NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sem_metric (
    id             BIGSERIAL PRIMARY KEY,
    code           VARCHAR(64) NOT NULL UNIQUE,
    name           VARCHAR(64) NOT NULL,
    aliases        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    expr_sql       TEXT        NOT NULL,
    source_id      BIGINT      NOT NULL REFERENCES sem_data_source (id),
    unit           VARCHAR(16) NOT NULL DEFAULT '万元',
    value_type     VARCHAR(16) NOT NULL DEFAULT 'decimal',
    agg_default    VARCHAR(16) NOT NULL DEFAULT 'SUM',
    caliber        TEXT,
    default_format VARCHAR(32) DEFAULT '#,##0.00',
    enabled        BOOLEAN     NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_metric_source ON sem_metric (source_id);

CREATE TABLE IF NOT EXISTS sem_dimension (
    id           BIGSERIAL PRIMARY KEY,
    code         VARCHAR(64) NOT NULL UNIQUE,
    name         VARCHAR(64) NOT NULL,
    aliases      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    expr_sql     TEXT        NOT NULL,
    display_expr TEXT,
    join_sql     TEXT,
    source_id    BIGINT      NOT NULL REFERENCES sem_data_source (id),
    dim_type     VARCHAR(16) NOT NULL DEFAULT 'categorical',   -- categorical / time / numeric
    value_map    JSONB,
    enabled      BOOLEAN     NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_dim_source ON sem_dimension (source_id);

CREATE TABLE IF NOT EXISTS sem_rule (
    id       BIGSERIAL PRIMARY KEY,
    scene    VARCHAR(64)  NOT NULL,     -- time / caliber / ranking / chart
    title    VARCHAR(128) NOT NULL,
    content  TEXT         NOT NULL,
    priority INT          NOT NULL DEFAULT 0,
    enabled  BOOLEAN      NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_rule_scene ON sem_rule (scene, priority DESC);

CREATE TABLE IF NOT EXISTS sem_fewshot (
    id         BIGSERIAL PRIMARY KEY,
    question   TEXT    NOT NULL,
    rewritten  TEXT,
    sql_text   TEXT    NOT NULL,
    source_ids JSONB,
    notes      TEXT,
    hit_count  INT     NOT NULL DEFAULT 0,
    verified   BOOLEAN NOT NULL DEFAULT FALSE,
    embedding  BYTEA,                    -- 预留：向量由 embedding 任务写入
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
