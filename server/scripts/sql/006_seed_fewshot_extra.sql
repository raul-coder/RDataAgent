-- =====================================================================
-- 006_seed_fewshot_extra.sql —— Few-shot 样本扩充（20 → 50）
--
-- 覆盖更多问法与维度组合，降低模型"自由发挥"的概率：
--   · 区域 / 客户 / 销售 / 产品线 / 型号 / 行业 多维度
--   · 毛利、回款、新产品、机会点(PPL)、目标缺口等专项
--   · 区间筛选、交叉维度、单值聚合、明细列表
-- =====================================================================

BEGIN;

INSERT INTO sem_fewshot (id, question, rewritten, sql_text, source_ids, notes, verified) VALUES
(21, '各区域收入汇总', '2026年按区域汇总的商业收入',
 $sql$SELECT d.region AS 区域, SUM(f.year_income) AS 商业收入 FROM bi.fact_contract AS f LEFT JOIN bi.dim_unit AS d ON d.unit_code = f.unit_code WHERE f.year = 2026 GROUP BY d.region ORDER BY 商业收入 DESC$sql$,
 '[1]', '区域维度来自 dim_unit.region', TRUE),

(22, '2026年毛利率情况', '2026年各产品线的金额、毛利与毛利率',
 $sql$SELECT p.product_line AS 产品线, SUM(f.amount) AS 金额, SUM(f.gross_profit) AS 毛利, ROUND(SUM(f.gross_profit) / NULLIF(SUM(f.amount), 0) * 100, 2) AS 毛利率 FROM bi.fact_contract AS f LEFT JOIN bi.dim_product AS p ON p.product_code = f.product_code WHERE f.year = 2026 GROUP BY p.product_line ORDER BY 毛利率 DESC$sql$,
 '[1]', '毛利率分母是金额不是收入', TRUE),

(23, '客户数覆盖情况', '2026年各经营单元成交客户数',
 $sql$SELECT d.unit_name AS 经营单元, COUNT(DISTINCT f.customer_code) AS 客户数 FROM bi.fact_contract AS f LEFT JOIN bi.dim_unit AS d ON d.unit_code = f.unit_code WHERE f.year = 2026 GROUP BY d.unit_name ORDER BY 客户数 DESC$sql$,
 '[1]', '客户数用 COUNT(DISTINCT)', TRUE),

(24, '新产品收入占比是多少', '2026年新产品收入占商业收入的比例',
 $sql$SELECT ROUND(SUM(CASE WHEN f.is_new_product THEN f.year_income ELSE 0 END) / NULLIF(SUM(f.year_income), 0) * 100, 2) AS 新产品收入占比 FROM bi.fact_contract AS f WHERE f.year = 2026$sql$,
 '[1]', '单值结果用指标卡', TRUE),

(25, '2026年回款率是多少', '2026年整体回款金额占收入金额的比例',
 $sql$SELECT ROUND(SUM(f.year_payment) / NULLIF(SUM(f.year_income), 0) * 100, 2) AS 回款率 FROM bi.fact_contract AS f WHERE f.year = 2026$sql$,
 '[1]', '回款率 = 回款 / 收入', TRUE),

(26, '各销售的业绩排名', '2026年各销售的收入与合同数排名',
 $sql$SELECT s.sales_name AS 销售, SUM(f.year_income) AS 收入, COUNT(*) AS 合同数 FROM bi.fact_contract AS f LEFT JOIN bi.dim_sales AS s ON s.sales_id = f.sales_id WHERE f.year = 2026 GROUP BY s.sales_name ORDER BY 收入 DESC LIMIT 20$sql$,
 '[1]', '人员维度需 JOIN dim_sales', TRUE),

(27, '2026年目标缺口有多少', '2026年各经营单元目标与收入的缺口',
 $sql$SELECT v.unit_name AS 经营单元, v.biz_goal AS 商业目标, v.income AS 收入, v.income_gap AS 缺口 FROM bi.v_overall_achieve AS v WHERE v.year = 2026 ORDER BY v.income_gap DESC$sql$,
 '[4]', '缺口直接用视图字段 income_gap', TRUE),

(28, '商解目标完成情况', '2026年商解目标与商解收入的整体达成率',
 $sql$SELECT ROUND(SUM(sa.solution_income) / NULLIF(SUM(sa.solution_goal), 0) * 100, 2) AS 商解整体达成率 FROM bi.v_solution_analysis AS sa WHERE sa.year = 2026$sql$,
 '[6]', '整体达成率需先汇总再相除', TRUE),

(29, '金额大于500万的合同有哪些', '2026年金额大于500万元的合同明细',
 $sql$SELECT f.contract_no AS 合同编号, c.customer_name AS 客户名称, f.amount AS 金额, f.land_date AS 落地时间 FROM bi.fact_contract AS f LEFT JOIN bi.dim_customer AS c ON c.customer_code = f.customer_code WHERE f.year = 2026 AND f.amount > 500 ORDER BY f.amount DESC LIMIT 100$sql$,
 '[1]', '数值区间筛选', TRUE),

(30, '金额在100万到300万之间的合同', '2026年金额介于100万与300万之间的合同',
 $sql$SELECT f.contract_no AS 合同编号, c.customer_name AS 客户名称, f.amount AS 金额 FROM bi.fact_contract AS f LEFT JOIN bi.dim_customer AS c ON c.customer_code = f.customer_code WHERE f.year = 2026 AND f.amount BETWEEN 100 AND 300 ORDER BY f.amount DESC LIMIT 100$sql$,
 '[1]', 'BETWEEN 区间', TRUE),

(31, '2026年各产品线的台套数', '2026年按产品线汇总的出货台套数',
 $sql$SELECT p.product_line AS 产品线, SUM(f.qty) AS 台套数, SUM(f.year_income) AS 收入 FROM bi.fact_contract AS f LEFT JOIN bi.dim_product AS p ON p.product_code = f.product_code WHERE f.year = 2026 GROUP BY p.product_line ORDER BY 台套数 DESC$sql$,
 '[1]', '台套数用 SUM(qty)', TRUE),

(32, '哪个客户贡献的收入最高', '2026年贡献收入最高的前10个客户',
 $sql$SELECT c.customer_name AS 客户名称, SUM(f.year_income) AS 收入, COUNT(*) AS 合同数 FROM bi.fact_contract AS f LEFT JOIN bi.dim_customer AS c ON c.customer_code = f.customer_code WHERE f.year = 2026 GROUP BY c.customer_name ORDER BY 收入 DESC LIMIT 10$sql$,
 '[1]', '客户维度需 JOIN dim_customer', TRUE),

(33, '2026年每个月的收入趋势', '2026年按落地月汇总的商业收入',
 $sql$SELECT CAST(EXTRACT(MONTH FROM f.land_date) AS INT) AS 月份, SUM(f.year_income) AS 商业收入 FROM bi.fact_contract AS f WHERE f.year = 2026 GROUP BY 1 ORDER BY 1$sql$,
 '[1]', '月度趋势，输出 12 行', TRUE),

(34, '2026年各季度的收入', '2026年按季度汇总的商业收入',
 $sql$SELECT CAST(EXTRACT(QUARTER FROM f.land_date) AS INT) AS 季度, SUM(f.year_income) AS 商业收入 FROM bi.fact_contract AS f WHERE f.year = 2026 GROUP BY 1 ORDER BY 1$sql$,
 '[1]', '季度汇总', TRUE),

(35, '高风险项目金额统计', '2026年高风险项目的数量与金额合计',
 $sql$SELECT COUNT(*) AS 高风险项目数, SUM(f.amount) AS 高风险金额 FROM bi.fact_contract AS f WHERE f.risk_level = '高' AND f.year = 2026$sql$,
 '[1]', '风险统计聚合', TRUE),

(36, '各风险等级的项目分布', '2026年按风险等级统计的项目数与金额',
 $sql$SELECT f.risk_level AS 风险等级, COUNT(*) AS 项目数, SUM(f.amount) AS 金额 FROM bi.fact_contract AS f WHERE f.year = 2026 GROUP BY f.risk_level ORDER BY 金额 DESC$sql$,
 '[1]', '分组统计', TRUE),

(37, '2026年PPL中各阶段的机会金额', '2026年PPL管线按阶段汇总的机会金额',
 $sql$SELECT ppl.stage AS 阶段, COUNT(*) AS 机会数, SUM(ppl.amount) AS 机会金额 FROM bi.fact_ppl AS ppl WHERE ppl.year = 2026 GROUP BY ppl.stage ORDER BY 机会金额 DESC$sql$,
 '[2]', 'PPL 走专用事实表', TRUE),

(38, 'PPL中高风险机会有哪些', '2026年PPL管线中高风险的机会点明细',
 $sql$SELECT ppl.opp_no AS 机会点编号, ppl.customer_name AS 客户, ppl.model AS 型号, ppl.amount AS 金额, ppl.stage AS 阶段 FROM bi.fact_ppl AS ppl WHERE ppl.year = 2026 AND ppl.risk_level = '高' ORDER BY ppl.amount DESC LIMIT 100$sql$,
 '[2]', 'PPL 明细列表', TRUE),

(39, '在途商机金额有多少', '2026年PPL管线中处于商机与方案阶段的金额合计',
 $sql$SELECT COUNT(*) AS 机会数, SUM(ppl.amount) AS 在途金额 FROM bi.fact_ppl AS ppl WHERE ppl.year = 2026 AND ppl.stage IN ('商机', '方案')$sql$,
 '[2]', '阶段过滤', TRUE),

(40, '2026年重点经营单元的达成情况', '2026年重点经营单元的目标、收入与完成率',
 $sql$SELECT v.unit_name AS 经营单元, v.biz_goal AS 目标, v.income AS 收入, v.achieve_rate AS 完成率 FROM bi.v_overall_achieve AS v WHERE v.year = 2026 AND v.is_key_unit = TRUE ORDER BY v.achieve_rate DESC$sql$,
 '[4]', '重点单元用 is_key_unit 标记', TRUE),

(41, '浙江代表处2025年的收入', '2025年浙江代表处的商业收入',
 $sql$SELECT d.unit_name AS 经营单元, SUM(f.year_income) AS 商业收入 FROM bi.fact_contract AS f LEFT JOIN bi.dim_unit AS d ON d.unit_code = f.unit_code WHERE f.year = 2025 AND d.unit_name = '浙江代表处' GROUP BY d.unit_name$sql$,
 '[1]', '历史年度 + 单主体', TRUE),

(42, '2026年各行业小类的收入排行', '2026年按行业小类汇总的收入排名',
 $sql$SELECT i.industry_sub AS 行业小类, SUM(f.year_income) AS 收入, COUNT(DISTINCT f.customer_code) AS 客户数 FROM bi.fact_contract AS f LEFT JOIN bi.dim_industry AS i ON i.industry_code = f.industry_code WHERE f.year = 2026 GROUP BY i.industry_sub ORDER BY 收入 DESC LIMIT 10$sql$,
 '[1]', '小类维度', TRUE),

(43, '运营商行业2026年的合同数', '2026年运营商行业的合同数量与金额',
 $sql$SELECT COUNT(*) AS 合同数, SUM(f.amount) AS 合同金额 FROM bi.fact_contract AS f LEFT JOIN bi.dim_industry AS i ON i.industry_code = f.industry_code WHERE f.year = 2026 AND i.industry_cat = '运营商'$sql$,
 '[1]', '单行业聚合', TRUE),

(44, '单价最高的产品型号', '2026年按平均单价排序的产品型号',
 $sql$SELECT p.model AS 产品型号, SUM(f.year_income) AS 收入, SUM(f.qty) AS 台套数, ROUND(SUM(f.year_income) / NULLIF(SUM(f.qty), 0), 2) AS 平均单价 FROM bi.fact_contract AS f LEFT JOIN bi.dim_product AS p ON p.product_code = f.product_code WHERE f.year = 2026 GROUP BY p.model ORDER BY 平均单价 DESC LIMIT 10$sql$,
 '[1]', '派生指标需防除零', TRUE),

(45, '2026年返利合同有多少', '2026年涉及返利的合同数量与金额',
 $sql$SELECT COUNT(*) AS 返利合同数, SUM(f.amount) AS 返利合同金额 FROM bi.fact_contract AS f WHERE f.year = 2026 AND f.rebate_flag = TRUE$sql$,
 '[1]', '布尔标记过滤', TRUE),

(46, '2026年最大单笔合同金额', '2026年单笔金额最大的合同',
 $sql$SELECT f.contract_no AS 合同编号, c.customer_name AS 客户名称, f.amount AS 金额 FROM bi.fact_contract AS f LEFT JOIN bi.dim_customer AS c ON c.customer_code = f.customer_code WHERE f.year = 2026 ORDER BY f.amount DESC LIMIT 1$sql$,
 '[1]', '极值明细', TRUE),

(47, '各区域的高风险项目数', '2026年按区域统计的高风险项目数量',
 $sql$SELECT d.region AS 区域, COUNT(*) AS 高风险项目数 FROM bi.fact_contract AS f LEFT JOIN bi.dim_unit AS d ON d.unit_code = f.unit_code WHERE f.year = 2026 AND f.risk_level = '高' GROUP BY d.region ORDER BY 高风险项目数 DESC$sql$,
 '[1]', '交叉维度 + 过滤', TRUE),

(48, '2026年商业目标总额是多少', '2026年全部经营单元的商业目标合计',
 $sql$SELECT SUM(g.biz_goal) AS 商业目标合计, SUM(g.solution_goal) AS 商解目标合计 FROM bi.fact_goal AS g WHERE g.year = 2026 AND g.month = 0$sql$,
 '[3]', 'month=0 为年度目标', TRUE),

(49, '2026年6月的收入是多少', '2026年6月的商业收入',
 $sql$SELECT SUM(f.year_income) AS 商业收入 FROM bi.fact_contract AS f WHERE f.year = 2026 AND EXTRACT(MONTH FROM f.land_date) = 6$sql$,
 '[1]', '单月过滤', TRUE),

(50, '华东区域各经营单元的达成情况', '2026年华东区域各经营单元的目标与完成率',
 $sql$SELECT v.unit_name AS 经营单元, v.biz_goal AS 目标, v.income AS 收入, v.achieve_rate AS 完成率 FROM bi.v_overall_achieve AS v WHERE v.year = 2026 AND v.region = '华东' ORDER BY v.achieve_rate DESC$sql$,
 '[4]', '区域过滤走视图字段 region', TRUE)
ON CONFLICT (id) DO UPDATE SET
    question = EXCLUDED.question, rewritten = EXCLUDED.rewritten,
    sql_text = EXCLUDED.sql_text, source_ids = EXCLUDED.source_ids,
    notes = EXCLUDED.notes, verified = EXCLUDED.verified;

SELECT setval('sem_fewshot_id_seq', (SELECT COALESCE(MAX(id), 1) FROM sem_fewshot));

COMMIT;
