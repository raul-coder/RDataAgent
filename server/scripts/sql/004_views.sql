-- =====================================================================
-- 004_views.sql —— 统计报表视图
--
-- 5 张对外报表视图（对应 Demo 的「统计报表」数据源）+ 1 张同比视图：
--   v_overall_achieve  整体达成
--   v_product_analysis 产品分析
--   v_solution_analysis 商解专项
--   v_industry_achieve 行业达成
--   v_key_unit         重点单元（TOP/BOTTOM + 预警）
--   v_achieve_yoy      同比分析（支撑「同比/增速」类问题）
-- =====================================================================

BEGIN;

-- CREATE OR REPLACE VIEW 不允许变更列类型，因此先按依赖顺序删除。
-- 依赖链：v_achieve_yoy → v_key_unit → v_overall_achieve
DROP VIEW IF EXISTS bi.v_achieve_yoy        CASCADE;
DROP VIEW IF EXISTS bi.v_key_unit           CASCADE;
DROP VIEW IF EXISTS bi.v_solution_analysis  CASCADE;
DROP VIEW IF EXISTS bi.v_industry_achieve   CASCADE;
DROP VIEW IF EXISTS bi.v_product_analysis   CASCADE;
DROP VIEW IF EXISTS bi.v_overall_achieve    CASCADE;

-- ── 整体达成 ────────────────────────────────────────────────────────
-- ⚠️ 必须先用 CTE 把合同表按「单元×年度」预聚合，再与目标表关联。
--    若直接 fact_goal JOIN fact_contract，一条目标会扇出成 N 条合同记录，
--    SUM(biz_goal) 被放大 N 倍，完成率将被严重低估（实测会算成 0.03%）。
CREATE OR REPLACE VIEW bi.v_overall_achieve AS
WITH contract_agg AS (
    SELECT f.unit_code,
           f.year,
           SUM(f.year_income)  AS income,
           SUM(f.year_payment) AS payment,
           SUM(f.amount)       AS amount,
           COUNT(*)            AS contract_count
    FROM bi.fact_contract f
    GROUP BY f.unit_code, f.year
)
SELECT g.year,
       g.unit_code,
       u.unit_name,
       u.region,
       u.is_key_unit,
       g.biz_goal,
       g.solution_goal,
       COALESCE(ca.income, 0)                                       AS income,
       COALESCE(ca.payment, 0)                                      AS payment,
       COALESCE(ca.amount, 0)                                       AS amount,
       COALESCE(ca.contract_count, 0)                               AS contract_count,
       CASE WHEN g.biz_goal > 0
            THEN ROUND(COALESCE(ca.income, 0) / g.biz_goal * 100, 2) END        AS achieve_rate,
       CASE WHEN g.solution_goal > 0
            THEN ROUND(COALESCE(ca.income, 0) / g.solution_goal * 100, 2) END   AS solution_rate,
       g.biz_goal - COALESCE(ca.income, 0)                          AS income_gap,
       CASE WHEN COALESCE(ca.income, 0) < g.biz_goal * 0.6
            THEN TRUE ELSE FALSE END                                AS is_warning
FROM bi.fact_goal g
JOIN bi.dim_unit u ON u.unit_code = g.unit_code
LEFT JOIN contract_agg ca ON ca.unit_code = g.unit_code AND ca.year = g.year
WHERE g.month = 0;

-- ── 产品分析（产品线 / 类型 / 型号）────────────────────────────────
CREATE OR REPLACE VIEW bi.v_product_analysis AS
SELECT f.year,
       p.product_line,
       p.product_type,
       p.model,
       COUNT(*)                        AS contract_count,
       SUM(f.qty)                      AS qty,
       SUM(f.year_income)              AS income,
       SUM(f.gross_profit)             AS gross_profit,
       CASE WHEN SUM(f.amount) > 0
            THEN ROUND(SUM(f.gross_profit) / SUM(f.amount) * 100, 2) END AS gross_margin_rate
FROM bi.fact_contract f
JOIN bi.dim_product p ON p.product_code = f.product_code
GROUP BY f.year, p.product_line, p.product_type, p.model;

-- ── 商解专项 ────────────────────────────────────────────────────────
-- 同样先用 CTE 预聚合商解收入，避免与目标表 JOIN 时扇出
CREATE OR REPLACE VIEW bi.v_solution_analysis AS
WITH solution_agg AS (
    SELECT f.unit_code,
           f.year,
           SUM(f.year_income) AS solution_income
    FROM bi.fact_contract f
    JOIN bi.dim_product p ON p.product_code = f.product_code
    WHERE p.product_line = '商业解决方案'
    GROUP BY f.unit_code, f.year
)
SELECT g.year,
       g.unit_code,
       u.unit_name,
       g.solution_goal,
       g.biz_goal,
       COALESCE(sa.solution_income, 0)                              AS solution_income,
       CASE WHEN g.solution_goal > 0
            THEN ROUND(COALESCE(sa.solution_income, 0) / g.solution_goal * 100, 2) END AS solution_rate
FROM bi.fact_goal g
JOIN bi.dim_unit u ON u.unit_code = g.unit_code
LEFT JOIN solution_agg sa ON sa.unit_code = g.unit_code AND sa.year = g.year
WHERE g.month = 0;

-- ── 行业达成 ────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW bi.v_industry_achieve AS
SELECT f.year,
       i.industry_cat,
       i.industry_sub,
       COUNT(DISTINCT f.customer_code) AS customer_count,
       COUNT(*)                        AS contract_count,
       SUM(f.qty)                      AS qty,
       SUM(f.year_income)              AS income,
       COALESCE(SUM(f.year_payment), 0) AS payment
FROM bi.fact_contract f
JOIN bi.dim_industry i ON i.industry_code = f.industry_code
GROUP BY f.year, i.industry_cat, i.industry_sub;

-- ── 重点单元（含 TOP/BOTTOM 排名与预警标记）────────────────────────
CREATE OR REPLACE VIEW bi.v_key_unit AS
SELECT a.year,
       a.unit_code,
       a.unit_name,
       a.region,
       a.is_key_unit,
       a.biz_goal,
       a.income,
       a.achieve_rate,
       a.income_gap,
       a.is_warning,
       RANK() OVER (PARTITION BY a.year ORDER BY a.achieve_rate DESC NULLS LAST) AS rate_rank_desc,
       RANK() OVER (PARTITION BY a.year ORDER BY a.achieve_rate ASC  NULLS LAST) AS rate_rank_asc
FROM bi.v_overall_achieve a;

-- ── 同比分析（支撑「同比 / 增速 / 增长」类问题）────────────────────
CREATE OR REPLACE VIEW bi.v_achieve_yoy AS
SELECT cur.year,
       cur.unit_code,
       cur.unit_name,
       cur.region,
       cur.income,
       prv.income                                          AS prev_income,
       cur.income - COALESCE(prv.income, 0)                AS income_delta,
       CASE WHEN COALESCE(prv.income, 0) > 0
            THEN ROUND((cur.income - prv.income) / prv.income * 100, 2) END AS income_yoy,
       cur.biz_goal,
       prv.biz_goal                                        AS prev_biz_goal,
       cur.achieve_rate,
       prv.achieve_rate                                    AS prev_achieve_rate
FROM bi.v_overall_achieve cur
LEFT JOIN bi.v_overall_achieve prv
       ON prv.unit_code = cur.unit_code AND prv.year = cur.year - 1;

COMMIT;
