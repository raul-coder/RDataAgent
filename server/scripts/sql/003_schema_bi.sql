-- =====================================================================
-- 003_schema_bi.sql —— 业务库 bi schema：维度表 + 事实表
--
-- 说明：本 schema 由造数脚本填充，应用侧通过只读账号 bi_readonly 访问，
--       与 public（系统/会话/语义层）物理隔离，降低越权风险。
-- =====================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS bi;

-- ── 维度 ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bi.dim_unit (
    unit_code   VARCHAR(16) PRIMARY KEY,
    unit_name   VARCHAR(64) NOT NULL,
    region      VARCHAR(32) NOT NULL,      -- 华北 / 华东 / 华南 / 西部 / 渠道
    is_key_unit BOOLEAN     NOT NULL DEFAULT FALSE,
    manager     VARCHAR(32)
);

CREATE TABLE IF NOT EXISTS bi.dim_industry (
    industry_code VARCHAR(16) PRIMARY KEY,
    industry_cat  VARCHAR(32) NOT NULL,    -- 政企 / 运营商 / 商业市场 / 渠道部
    industry_sub  VARCHAR(32) NOT NULL     -- 智能制造 / 数字政府 / 渠道（ISV）/ 大企业
);

CREATE TABLE IF NOT EXISTS bi.dim_product (
    product_code VARCHAR(32) PRIMARY KEY,
    product_line VARCHAR(32) NOT NULL,     -- 通用计算 / 智能计算 / 商业解决方案
    product_type VARCHAR(32) NOT NULL,     -- 服务器 / 存储 / 网络
    model        VARCHAR(64) NOT NULL,
    new_model    VARCHAR(64),
    is_new       BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS bi.dim_sales (
    sales_id   VARCHAR(16) PRIMARY KEY,
    sales_name VARCHAR(32) NOT NULL,
    unit_code  VARCHAR(16) NOT NULL REFERENCES bi.dim_unit (unit_code),
    role       VARCHAR(16) NOT NULL        -- 销售 / 行销
);

CREATE TABLE IF NOT EXISTS bi.dim_customer (
    customer_code  VARCHAR(32) PRIMARY KEY,
    customer_name  VARCHAR(128) NOT NULL,
    industry_code  VARCHAR(16)  REFERENCES bi.dim_industry (industry_code),
    customer_level VARCHAR(8)   NOT NULL   -- A / B / C
);

CREATE TABLE IF NOT EXISTS bi.dim_date (
    d            DATE PRIMARY KEY,
    year         INT         NOT NULL,
    quarter      INT         NOT NULL,
    month        INT         NOT NULL,
    year_month   VARCHAR(7)  NOT NULL,
    year_quarter VARCHAR(7)  NOT NULL
);

-- ── 商业市场台账（主事实表）────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bi.fact_contract (
    id             BIGSERIAL PRIMARY KEY,
    contract_no    VARCHAR(64)  NOT NULL,
    presale_no     VARCHAR(64),
    opp_no         VARCHAR(64),
    unit_code      VARCHAR(16)  NOT NULL REFERENCES bi.dim_unit (unit_code),
    industry_code  VARCHAR(16)  NOT NULL REFERENCES bi.dim_industry (industry_code),
    product_code   VARCHAR(32)  NOT NULL REFERENCES bi.dim_product (product_code),
    sales_id       VARCHAR(16)  NOT NULL REFERENCES bi.dim_sales (sales_id),
    customer_code  VARCHAR(32)  NOT NULL REFERENCES bi.dim_customer (customer_code),
    project_name   VARCHAR(255),
    qty            INT          NOT NULL,
    amount         NUMERIC(18, 2) NOT NULL,   -- 金额（万元）
    amount_ex_tax  NUMERIC(18, 2) NOT NULL,   -- 不含税金额
    cost           NUMERIC(18, 2),
    gross_profit   NUMERIC(18, 2),
    land_date      DATE         NOT NULL,     -- 落地时间
    risk_level     VARCHAR(8)   NOT NULL,     -- 低 / 中 / 高
    is_new_product BOOLEAN      NOT NULL DEFAULT FALSE,
    rebate_flag    BOOLEAN      NOT NULL DEFAULT FALSE,
    m1_income  NUMERIC(18, 2) DEFAULT 0, m2_income  NUMERIC(18, 2) DEFAULT 0,
    m3_income  NUMERIC(18, 2) DEFAULT 0, m4_income  NUMERIC(18, 2) DEFAULT 0,
    m5_income  NUMERIC(18, 2) DEFAULT 0, m6_income  NUMERIC(18, 2) DEFAULT 0,
    m7_income  NUMERIC(18, 2) DEFAULT 0, m8_income  NUMERIC(18, 2) DEFAULT 0,
    m9_income  NUMERIC(18, 2) DEFAULT 0, m10_income NUMERIC(18, 2) DEFAULT 0,
    m11_income NUMERIC(18, 2) DEFAULT 0, m12_income NUMERIC(18, 2) DEFAULT 0,
    q1_income  NUMERIC(18, 2) DEFAULT 0, q2_income  NUMERIC(18, 2) DEFAULT 0,
    q3_income  NUMERIC(18, 2) DEFAULT 0, q4_income  NUMERIC(18, 2) DEFAULT 0,
    year_income  NUMERIC(18, 2) NOT NULL,
    m1_payment  NUMERIC(18, 2) DEFAULT 0, m2_payment  NUMERIC(18, 2) DEFAULT 0,
    m3_payment  NUMERIC(18, 2) DEFAULT 0, m4_payment  NUMERIC(18, 2) DEFAULT 0,
    m5_payment  NUMERIC(18, 2) DEFAULT 0, m6_payment  NUMERIC(18, 2) DEFAULT 0,
    m7_payment  NUMERIC(18, 2) DEFAULT 0, m8_payment  NUMERIC(18, 2) DEFAULT 0,
    m9_payment  NUMERIC(18, 2) DEFAULT 0, m10_payment NUMERIC(18, 2) DEFAULT 0,
    m11_payment NUMERIC(18, 2) DEFAULT 0, m12_payment NUMERIC(18, 2) DEFAULT 0,
    q1_payment  NUMERIC(18, 2) DEFAULT 0, q2_payment  NUMERIC(18, 2) DEFAULT 0,
    q3_payment  NUMERIC(18, 2) DEFAULT 0, q4_payment  NUMERIC(18, 2) DEFAULT 0,
    year_payment NUMERIC(18, 2) NOT NULL,
    remark     TEXT,
    year       INT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fc_unit    ON bi.fact_contract (unit_code);
CREATE INDEX IF NOT EXISTS idx_fc_ind     ON bi.fact_contract (industry_code);
CREATE INDEX IF NOT EXISTS idx_fc_prod    ON bi.fact_contract (product_code);
CREATE INDEX IF NOT EXISTS idx_fc_sales   ON bi.fact_contract (sales_id);
CREATE INDEX IF NOT EXISTS idx_fc_land    ON bi.fact_contract (land_date);
CREATE INDEX IF NOT EXISTS idx_fc_year    ON bi.fact_contract (year);
CREATE INDEX IF NOT EXISTS idx_fc_year_unit ON bi.fact_contract (year, unit_code);
CREATE INDEX IF NOT EXISTS idx_fc_risk    ON bi.fact_contract (risk_level);

-- ── PPL 明细台账（销售机会点管线）──────────────────────────────────
CREATE TABLE IF NOT EXISTS bi.fact_ppl (
    id               BIGSERIAL PRIMARY KEY,
    contract_no      VARCHAR(64),
    presale_no       VARCHAR(64),
    opp_no           VARCHAR(64),
    industry_cat     VARCHAR(32)  NOT NULL,
    industry_sub     VARCHAR(32)  NOT NULL,
    unit_code        VARCHAR(16)  NOT NULL REFERENCES bi.dim_unit (unit_code),
    sales_name       VARCHAR(32)  NOT NULL,
    project_name     VARCHAR(255),
    customer_name    VARCHAR(128),
    product_type     VARCHAR(32)  NOT NULL,
    model            VARCHAR(64)  NOT NULL,
    qty              INT          NOT NULL,
    amount           NUMERIC(18, 2) NOT NULL,
    amount_ex_tax    NUMERIC(18, 2) NOT NULL,
    stage            VARCHAR(16)  NOT NULL,   -- 商机/方案/投标/商务/签约/交付
    risk_level       VARCHAR(8)   NOT NULL,   -- 低 / 中 / 高
    expect_land_date DATE         NOT NULL,
    year             INT          NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ppl_unit  ON bi.fact_ppl (unit_code);
CREATE INDEX IF NOT EXISTS idx_ppl_stage ON bi.fact_ppl (stage);
CREATE INDEX IF NOT EXISTS idx_ppl_risk  ON bi.fact_ppl (risk_level);
CREATE INDEX IF NOT EXISTS idx_ppl_year  ON bi.fact_ppl (year);

-- ── 整体目标台账（month = 0 表示年度合计）────────────────────────
CREATE TABLE IF NOT EXISTS bi.fact_goal (
    id            BIGSERIAL PRIMARY KEY,
    unit_code     VARCHAR(16)    NOT NULL REFERENCES bi.dim_unit (unit_code),
    year          INT            NOT NULL,
    month         INT            NOT NULL,
    biz_goal      NUMERIC(18, 2) NOT NULL,   -- 商业目标
    solution_goal NUMERIC(18, 2) NOT NULL,   -- 商解目标
    UNIQUE (unit_code, year, month)
);
CREATE INDEX IF NOT EXISTS idx_goal_year ON bi.fact_goal (year);

COMMIT;
