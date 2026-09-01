-- =====================================================================
-- 005_seed_semantic.sql —— 语义层初始内容
--   9 个数据源 / 18 个指标 / 12 个维度 / 15 条口径规则 / 20 条 Few-shot
--
-- 说明：使用 $tag$ 美元引用书写 SQL 文本，内部单引号无需转义。
-- =====================================================================

BEGIN;

-- ── 数据源（对应前端「数据源选择器」）────────────────────────────────
INSERT INTO sem_data_source (id, group_name, name, object_name, object_type, description, enabled, sort_order) VALUES
(1, '台账数据', '商业市场台账', 'bi.fact_contract', 'table', '商业市场合同/收入/回款明细，2025-01 ~ 2026-12', TRUE, 1),
(2, '台账数据', 'PPL明细台账',  'bi.fact_ppl',      'table', '销售机会点管线，含阶段与风险等级',                 TRUE, 2),
(3, '台账数据', '整体目标台账', 'bi.fact_goal',     'table', '各经营单元分月/分年商业目标与商解目标',           TRUE, 3),
(4, '统计报表', '整体达成',     'bi.v_overall_achieve',  'view', '各经营单元目标、收入、回款、完成率与缺口',    TRUE, 1),
(5, '统计报表', '产品分析',     'bi.v_product_analysis', 'view', '按产品线/类型/型号的收入、台套与毛利',        TRUE, 2),
(6, '统计报表', '商解专项',     'bi.v_solution_analysis','view', '商解目标 vs 商解收入与达成率',                TRUE, 3),
(7, '统计报表', '行业达成',     'bi.v_industry_achieve', 'view', '按行业大类/小类的收入与客户覆盖',             TRUE, 4),
(8, '统计报表', '重点单元',     'bi.v_key_unit',         'view', '重点单元完成率排名与预警标记',                TRUE, 5),
(9, '统计报表', '同比分析',     'bi.v_achieve_yoy',      'view', '各经营单元收入同比与变动额',                  TRUE, 6)
ON CONFLICT (id) DO UPDATE SET
    group_name = EXCLUDED.group_name, name = EXCLUDED.name,
    object_name = EXCLUDED.object_name, object_type = EXCLUDED.object_type,
    description = EXCLUDED.description, enabled = EXCLUDED.enabled,
    sort_order = EXCLUDED.sort_order;

-- ── 指标（18 个）───────────────────────────────────────────────────
INSERT INTO sem_metric (id, code, name, aliases, expr_sql, source_id, unit, value_type, agg_default, caliber, default_format, enabled) VALUES
(1,  'biz_income',        '商业收入',       '["收入","营收","签约额","销售额","商业收入","收入额"]'::jsonb,
     $sql$SUM(f.year_income)$sql$, 1, '万元', 'decimal', 'SUM',
     '商业收入 = 不含税签约额，取台账已确认的年度收入（分月收入之和）', '#,##0.00', TRUE),
(2,  'biz_payment',       '商业回款',       '["回款","到账","收款","回款额"]'::jsonb,
     $sql$SUM(f.year_payment)$sql$, 1, '万元', 'decimal', 'SUM',
     '回款 = 已到账金额，回款月通常滞后落地月 1~3 个月', '#,##0.00', TRUE),
(3,  'contract_amount',   '合同金额',       '["金额","合同额","签约金额","合同金额"]'::jsonb,
     $sql$SUM(f.amount)$sql$, 1, '万元', 'decimal', 'SUM', '合同金额 = 含税签约额', '#,##0.00', TRUE),
(4,  'contract_count',    '合同数量',       '["合同数","单量","合同量","项目数","合同数量"]'::jsonb,
     $sql$COUNT(*)$sql$, 1, '个', 'int', 'COUNT', '按合同编号计数', '#,##0', TRUE),
(5,  'qty',               '台套数',         '["台套","台数","数量","出货量","台套数"]'::jsonb,
     $sql$SUM(f.qty)$sql$, 1, '台', 'int', 'SUM', '设备出货台套数', '#,##0', TRUE),
(6,  'gross_profit',      '毛利',           '["毛利","利润额"]'::jsonb,
     $sql$SUM(f.gross_profit)$sql$, 1, '万元', 'decimal', 'SUM', '毛利 = 金额 - 成本', '#,##0.00', TRUE),
(7,  'gross_margin_rate', '毛利率',         '["毛利率","利润率"]'::jsonb,
     $sql$ROUND(SUM(f.gross_profit) / NULLIF(SUM(f.amount), 0) * 100, 2)$sql$, 1, '%', 'decimal', 'NONE',
     '毛利率 = 毛利 / 合同金额 × 100，除数为 0 返回 NULL', '#,##0.00', TRUE),
(8,  'new_product_income','新产品收入',     '["新产品收入","新品收入"]'::jsonb,
     $sql$SUM(CASE WHEN f.is_new_product THEN f.year_income ELSE 0 END)$sql$, 1, '万元', 'decimal', 'SUM',
     '仅统计 is_new_product = true 的合同', '#,##0.00', TRUE),
(9,  'high_risk_count',   '高风险项目数',   '["高风险项目","风险项目数","高风险","风险项目"]'::jsonb,
     $sql$COUNT(*) FILTER (WHERE f.risk_level = '高')$sql$, 1, '个', 'int', 'COUNT',
     '风险等级为「高」的项目计数', '#,##0', TRUE),
(10, 'high_risk_amount',  '高风险项目金额', '["风险金额","高风险金额"]'::jsonb,
     $sql$SUM(CASE WHEN f.risk_level = '高' THEN f.amount ELSE 0 END)$sql$, 1, '万元', 'decimal', 'SUM',
     '风险等级为「高」的合同金额之和', '#,##0.00', TRUE),
(11, 'customer_count',    '客户数',         '["客户数","客户数量","覆盖客户"]'::jsonb,
     $sql$COUNT(DISTINCT f.customer_code)$sql$, 1, '家', 'int', 'COUNT', '按客户编码去重计数', '#,##0', TRUE),
(12, 'solution_income',   '商解收入',       '["商解收入","解决方案收入"]'::jsonb,
     $sql$SUM(CASE WHEN p.product_line = '商业解决方案' THEN f.year_income ELSE 0 END)$sql$, 1, '万元', 'decimal', 'SUM',
     '商解收入 = 产品线为「商业解决方案」的合同收入（需 JOIN dim_product）', '#,##0.00', TRUE),
(13, 'biz_goal',          '商业目标',       '["目标","商业目标","任务","预算"]'::jsonb,
     $sql$SUM(g.biz_goal)$sql$, 3, '万元', 'decimal', 'SUM',
     '目标取 fact_goal，month=0 为年度目标，month=1..12 为月度目标', '#,##0.00', TRUE),
(14, 'solution_goal',     '商解目标',       '["商解目标","解决方案目标"]'::jsonb,
     $sql$SUM(g.solution_goal)$sql$, 3, '万元', 'decimal', 'SUM', '商解（商业解决方案）目标', '#,##0.00', TRUE),
(15, 'achieve_income',    '达成收入',       '["达成收入","实际收入","完成收入"]'::jsonb,
     $sql$SUM(income)$sql$, 4, '万元', 'decimal', 'SUM', '整体达成视图中的实际收入', '#,##0.00', TRUE),
(16, 'achieve_rate',      '完成率',         '["完成率","达成率","完成度"]'::jsonb,
     $sql$ROUND(SUM(income) / NULLIF(SUM(biz_goal), 0) * 100, 2)$sql$, 4, '%', 'decimal', 'NONE',
     '完成率 = 收入 / 目标 × 100，除数为 0 返回 NULL', '#,##0.00', TRUE),
(17, 'income_gap',        '目标缺口',       '["缺口","目标缺口","差额"]'::jsonb,
     $sql$SUM(biz_goal) - SUM(income)$sql$, 4, '万元', 'decimal', 'NONE',
     '缺口 = 目标 - 收入，正值表示未达成', '#,##0.00', TRUE),
(18, 'income_yoy',        '收入同比',       '["同比","同比增长","yoy","增速","增长率"]'::jsonb,
     $sql$ROUND((SUM(income) - SUM(prev_income)) / NULLIF(SUM(prev_income), 0) * 100, 2)$sql$, 9, '%', 'decimal', 'NONE',
     '同比 = (本期收入 - 去年同期) / 去年同期 × 100，取自 v_achieve_yoy', '#,##0.00', TRUE)
ON CONFLICT (id) DO UPDATE SET
    code = EXCLUDED.code, name = EXCLUDED.name, aliases = EXCLUDED.aliases,
    expr_sql = EXCLUDED.expr_sql, source_id = EXCLUDED.source_id, unit = EXCLUDED.unit,
    value_type = EXCLUDED.value_type, agg_default = EXCLUDED.agg_default,
    caliber = EXCLUDED.caliber, default_format = EXCLUDED.default_format, enabled = EXCLUDED.enabled;

-- ── 维度（12 个）───────────────────────────────────────────────────
INSERT INTO sem_dimension (id, code, name, aliases, expr_sql, display_expr, join_sql, source_id, dim_type, value_map, enabled) VALUES
(1,  'unit',         '经营单元', '["办事处","代表处","单元","组织","经营单元","部门"]'::jsonb,
     $sql$f.unit_code$sql$, $sql$d.unit_name$sql$,
     $sql$LEFT JOIN bi.dim_unit d ON d.unit_code = f.unit_code$sql$, 1, 'categorical',
     -- 按区域分组的经营单元清单：多轮改写据此识别「北京」→「北京代表处」
     '{"华北":["北京代表处","山西办事处","辽宁办事处","泛政府系统部"],"华东":["上海代表处","浙江代表处","江苏代表处","山东代表处","安徽办事处","福建代表处","江西办事处"],"华南":["湖北办事处"],"西部":["新疆代表处","川藏代表处","陕西办事处"],"渠道":["渠道部"]}'::jsonb, TRUE),
(2,  'region',       '区域',     '["区域","大区","片区"]'::jsonb,
     $sql$d.region$sql$, $sql$d.region$sql$,
     $sql$LEFT JOIN bi.dim_unit d ON d.unit_code = f.unit_code$sql$, 1, 'categorical',
     -- region 的取值就是区域名本身
     '["华北","华东","华南","西部","渠道"]'::jsonb, TRUE),
(3,  'industry_cat', '行业大类', '["行业","行业分类","大类","行业大类"]'::jsonb,
     $sql$i.industry_cat$sql$, $sql$i.industry_cat$sql$,
     $sql$LEFT JOIN bi.dim_industry i ON i.industry_code = f.industry_code$sql$, 1, 'categorical',
     '["政企","运营商","商业市场","渠道部"]'::jsonb, TRUE),
(4,  'industry_sub', '行业小类', '["细分行业","小类","行业小类"]'::jsonb,
     $sql$i.industry_sub$sql$, $sql$i.industry_sub$sql$,
     $sql$LEFT JOIN bi.dim_industry i ON i.industry_code = f.industry_code$sql$, 1, 'categorical',
     '["智能制造","数字政府","渠道（ISV）","大企业"]'::jsonb, TRUE),
(5,  'product_line', '产品线',   '["产品线","产线"]'::jsonb,
     $sql$p.product_line$sql$, $sql$p.product_line$sql$,
     $sql$LEFT JOIN bi.dim_product p ON p.product_code = f.product_code$sql$, 1, 'categorical',
     '["通用计算","智能计算","商业解决方案"]'::jsonb, TRUE),
(6,  'product_type', '产品类型', '["类型","产品类型"]'::jsonb,
     $sql$p.product_type$sql$, $sql$p.product_type$sql$,
     $sql$LEFT JOIN bi.dim_product p ON p.product_code = f.product_code$sql$, 1, 'categorical',
     '["服务器","存储","网络"]'::jsonb, TRUE),
(7,  'model',        '产品型号', '["型号","产品型号"]'::jsonb,
     $sql$p.model$sql$, $sql$p.model$sql$,
     $sql$LEFT JOIN bi.dim_product p ON p.product_code = f.product_code$sql$, 1, 'categorical', NULL, TRUE),
(8,  'sales',        '销售',     '["销售员","业务员","销售","销售代表"]'::jsonb,
     $sql$s.sales_name$sql$, $sql$s.sales_name$sql$,
     $sql$LEFT JOIN bi.dim_sales s ON s.sales_id = f.sales_id$sql$, 1, 'categorical', NULL, TRUE),
(9,  'customer',     '客户',     '["客户","最终客户","客户名称"]'::jsonb,
     $sql$c.customer_name$sql$, $sql$c.customer_name$sql$,
     $sql$LEFT JOIN bi.dim_customer c ON c.customer_code = f.customer_code$sql$, 1, 'categorical', NULL, TRUE),
(10, 'risk_level',   '风险等级', '["风险","风险等级","风险级别"]'::jsonb,
     $sql$f.risk_level$sql$, $sql$f.risk_level$sql$, NULL, 1, 'categorical',
     '["低","中","高"]'::jsonb, TRUE),
(11, 'land_date',    '落地时间', '["落地时间","时间","日期","月份"]'::jsonb,
     $sql$f.land_date$sql$, $sql$f.land_date$sql$, NULL, 1, 'time', NULL, TRUE),
(12, 'year',         '年度',     '["年度","年份","年"]'::jsonb,
     $sql$f.year$sql$, $sql$f.year$sql$, NULL, 1, 'numeric', '[2025,2026]'::jsonb, TRUE)
ON CONFLICT (id) DO UPDATE SET
    code = EXCLUDED.code, name = EXCLUDED.name, aliases = EXCLUDED.aliases,
    expr_sql = EXCLUDED.expr_sql, display_expr = EXCLUDED.display_expr, join_sql = EXCLUDED.join_sql,
    source_id = EXCLUDED.source_id, dim_type = EXCLUDED.dim_type, value_map = EXCLUDED.value_map,
    enabled = EXCLUDED.enabled;

-- ── 口径规则（16 条）───────────────────────────────────────────────
INSERT INTO sem_rule (id, scene, title, content, priority, enabled) VALUES
(1,  'time',    '默认年份',
     $txt$用户未指定时间时，默认查询 2026 年。数据截止日期为 2026-12-31。$txt$, 100, TRUE),
(2,  'time',    '今年与去年',
     $txt$「今年」「本年」= 2026 年；「去年」= 2025 年；「前年」= 2024 年（无数据，需提示用户）。$txt$, 95, TRUE),
(3,  'time',    '季度口径',
     $txt$按数据截止日 2026-12-31 计：「本季度」= 2026-Q4，「上季度」= 2026-Q3，「去年同期」= 2025 年同季度。季度用 EXTRACT(QUARTER FROM f.land_date) 计算。$txt$, 90, TRUE),
(4,  'time',    '近 N 个月',
     $txt$「近 N 个月」以 2026-12-31 为基准向前推算，用 f.land_date >= DATE '2026-12-31' - INTERVAL 'N months' 过滤。$txt$, 85, TRUE),
(5,  'time',    '时间列按表区分',
     $txt$时间列因表而异，写 SQL 前必须先确认 FROM 的是哪张表，禁止套用同一套写法：
1) bi.fact_contract（别名 f）：日期列 f.land_date（date）、年度 f.year。月度用 EXTRACT(MONTH FROM f.land_date)，季度用 EXTRACT(QUARTER FROM f.land_date)。
2) bi.fact_ppl（别名 ppl）：日期列 ppl.expect_land_date（date）、年度 ppl.year。
3) bi.fact_goal（别名 g）：只有年度 g.year 与月份 g.month（integer，取值 0-12）。该表【没有任何日期列】，不存在 land_date、month_date、goal_date 之类字段——不要臆造。按月查目标直接用 g.month 分组，例如 GROUP BY g.month；不要写 EXTRACT(MONTH FROM g.月份)。
⚠️ 关于 g.month = 0：它是【年度汇总行】（每个经营单元一行，其 biz_goal 等于该单元 1-12 月之和）。因此查「全年目标/总目标」用 WHERE g.month = 0；查「各月目标分布」用 WHERE g.month BETWEEN 1 AND 12。绝不可在不限定 g.month 的情况下 SUM(g.biz_goal)，否则汇总行与明细行相加，结果正好翻倍。
4) 视图（v_overall_achieve / v_product_analysis / v_achieve_yoy / v_industry_achieve / v_solution_analysis / v_key_unit）：只有 year 列，没有日期列也没有月份列，无法按月或按季度拆分。若问题要求月度/季度粒度，必须回到 bi.fact_contract（用 f.land_date）或 bi.fact_goal（用 g.month）。
年度过滤统一用「<别名>.year = YYYY」，禁止用日期范围猜测年度。$txt$, 80, TRUE),
(6,  'caliber', '商业收入口径',
     $txt$商业收入 = 不含税签约额，单位万元，取 bi.fact_contract.year_income（等于该合同 12 个月收入之和）。$txt$, 100, TRUE),
(7,  'caliber', '完成率口径',
     $txt$完成率 = 收入 / 目标 × 100，保留 2 位小数；除数为 0 时用 NULLIF 返回 NULL，不得报除零错误。$txt$, 98, TRUE),
(8,  'caliber', '商解口径',
     $txt$商解 = 商业解决方案产品线。商解收入 = SUM(CASE WHEN p.product_line = '商业解决方案' THEN f.year_income ELSE 0 END)，必须 JOIN bi.dim_product。$txt$, 96, TRUE),
(9,  'caliber', '毛利口径',
     $txt$毛利 = 金额 - 成本；毛利率 = 毛利 / 金额 × 100。金额指含税签约额 f.amount。$txt$, 94, TRUE),
(10, 'caliber', '高风险口径',
     $txt$高风险 = f.risk_level = '高'。统计高风险项目数用 COUNT(*) FILTER (WHERE f.risk_level = '高')。$txt$, 92, TRUE),
(11, 'ranking', 'TOP N 规则',
     $txt$「TOP N」「排名前 N」按指标降序排序并 LIMIT N；数值相同时按维度名称升序作为稳定次序。未指定 N 时默认取 10。$txt$, 100, TRUE),
(12, 'ranking', '排序必带 LIMIT',
     $txt$涉及排名的问题必须同时出现 ORDER BY 与 LIMIT；未指定条数时 LIMIT 10，其他场景 LIMIT 1000，结果行数上限 5000。$txt$, 90, TRUE),
(13, 'ranking', '占比计算',
     $txt$「占比」需额外输出百分比列，使用 指标 * 100.0 / SUM(指标) OVER () 计算，保留 2 位小数。$txt$, 85, TRUE),
(14, 'chart',   '图表类型推荐',
     $txt$月度/季度趋势用折线图；占比用饼图或环形图；排名用横向条形图；目标与实际对比用分组柱状图并叠加完成率折线（双轴）。$txt$, 100, TRUE),
(15, 'chart',   '单值不画图',
     $txt$结果仅一行一列（单值）时用指标卡展示，不输出图表；结果为空时明确说明「该条件下无数据」并给出放宽建议。$txt$, 90, TRUE),
(17, 'caliber', '同比口径',
     $txt$同比（yoy）=（本期 − 同期）/ 同期 × 100，ROUND 保留 2 位小数；除数为 0 必须用 NULLIF(...,0) 返回 NULL，不得报除零错误。按年度同比时统一用 SUM(CASE WHEN f.year = 2026 THEN f.year_income ELSE 0 END) 作本期、SUM(CASE WHEN f.year = 2025 THEN f.year_income ELSE 0 END) 作同期。
重要边界：bi.v_achieve_yoy（同比分析）视图【仅含经营单元维度】（year/unit_code/unit_name/region/income/prev_income/income_yoy），没有 product_line、industry_code、customer_code 列。若问题涉及产品线、行业、客户等其它维度的同比，必须基于 bi.fact_contract 用上面的 CASE WHEN 自行计算，不得假设任何视图存在 income_yoy 列——多数视图并没有该列。$txt$, 97, TRUE)
ON CONFLICT (id) DO UPDATE SET
    scene = EXCLUDED.scene, title = EXCLUDED.title, content = EXCLUDED.content,
    priority = EXCLUDED.priority, enabled = EXCLUDED.enabled;

-- ── Few-shot 样本（20 条）──────────────────────────────────────────
INSERT INTO sem_fewshot (id, question, rewritten, sql_text, source_ids, notes, verified) VALUES
(1,  '2026年各经营单元收入排名', '2026年各经营单元商业收入排名TOP10',
     $sql$SELECT d.unit_name AS 经营单元, SUM(f.year_income) AS 商业收入 FROM bi.fact_contract f LEFT JOIN bi.dim_unit d ON d.unit_code = f.unit_code WHERE f.year = 2026 GROUP BY d.unit_name ORDER BY 商业收入 DESC LIMIT 10$sql$,
     '[1]', '排名类：必须带 ORDER BY + LIMIT', TRUE),
(2,  '北京代表处今年达成情况', '2026年北京代表处的目标、收入与完成率',
     $sql$SELECT d.unit_name AS 经营单元, SUM(g.biz_goal) AS 商业目标, COALESCE(SUM(c.year_income), 0) AS 收入, ROUND(COALESCE(SUM(c.year_income), 0) / NULLIF(SUM(g.biz_goal), 0) * 100, 2) AS 完成率 FROM bi.fact_goal g JOIN bi.dim_unit d ON d.unit_code = g.unit_code LEFT JOIN bi.fact_contract c ON c.unit_code = g.unit_code AND c.year = g.year WHERE g.month = 0 AND g.year = 2026 AND d.unit_name = '北京代表处' GROUP BY d.unit_name$sql$,
     '[3,1]', '单主体达成：目标表 LEFT JOIN 合同表，注意 c.year = g.year', TRUE),
(3,  '高风险项目有哪些', '2026年风险等级为高的项目明细',
     $sql$SELECT f.contract_no AS 合同编号, c.customer_name AS 客户名称, f.amount AS 金额, f.land_date AS 落地时间 FROM bi.fact_contract f LEFT JOIN bi.dim_customer c ON c.customer_code = f.customer_code WHERE f.risk_level = '高' AND f.year = 2026 ORDER BY f.amount DESC LIMIT 100$sql$,
     '[1]', '风险过滤 + 明细列表', TRUE),
(4,  '各产品线收入占比', '2026年各产品线收入及其占比',
     $sql$SELECT p.product_line AS 产品线, SUM(f.year_income) AS 收入, ROUND(SUM(f.year_income) * 100.0 / SUM(SUM(f.year_income)) OVER (), 2) AS 占比 FROM bi.fact_contract f LEFT JOIN bi.dim_product p ON p.product_code = f.product_code WHERE f.year = 2026 GROUP BY p.product_line ORDER BY 收入 DESC$sql$,
     '[1]', '占比用窗口函数 SUM(...) OVER ()', TRUE),
(5,  '2026年每月的合同金额趋势', '2026年按落地月的合同金额汇总',
     $sql$SELECT EXTRACT(MONTH FROM f.land_date)::int AS 月份, SUM(f.amount) AS 合同金额 FROM bi.fact_contract f WHERE f.year = 2026 GROUP BY 1 ORDER BY 1$sql$,
     '[1]', '趋势类：按月份 GROUP BY，输出 12 行', TRUE),
(6,  '政企行业收入3000万到5000万的经营单元', '2026年政企行业收入在3000万至5000万之间的经营单元',
     $sql$SELECT d.unit_name AS 经营单元, SUM(f.year_income) AS 收入 FROM bi.fact_contract f LEFT JOIN bi.dim_unit d ON d.unit_code = f.unit_code LEFT JOIN bi.dim_industry i ON i.industry_code = f.industry_code WHERE f.year = 2026 AND i.industry_cat = '政企' GROUP BY d.unit_name HAVING SUM(f.year_income) BETWEEN 3000 AND 5000 ORDER BY 收入 DESC$sql$,
     '[1]', '聚合后过滤用 HAVING', TRUE),
(7,  '各经营单元目标与收入对比', '2026年各经营单元商业目标、收入与完成率',
     $sql$SELECT v.unit_name AS 经营单元, v.biz_goal AS 商业目标, v.income AS 收入, v.achieve_rate AS 完成率 FROM bi.v_overall_achieve v WHERE v.year = 2026 ORDER BY v.achieve_rate DESC$sql$,
     '[4]', '优先用报表视图，避免重复 JOIN', TRUE),
(8,  '完成率低于60%的预警单元', '2026年触发低达成预警的经营单元',
     $sql$SELECT v.unit_name AS 经营单元, v.biz_goal AS 目标, v.income AS 收入, v.achieve_rate AS 完成率, v.income_gap AS 缺口 FROM bi.v_overall_achieve v WHERE v.year = 2026 AND v.is_warning ORDER BY v.achieve_rate ASC$sql$,
     '[4]', '预警直接用视图的 is_warning 标记', TRUE),
(9,  '2026年各经营单元收入同比', '2026年各经营单元相对2025年的收入同比增长率',
     $sql$SELECT y.unit_name AS 经营单元, y.prev_income AS 去年同期, y.income AS 本期收入, y.income_yoy AS 同比 FROM bi.v_achieve_yoy y WHERE y.year = 2026 ORDER BY y.income_yoy DESC$sql$,
     '[9]', '同比走 v_achieve_yoy，不要自己写自连接', TRUE),
(10, '销售最多的3个产品型号', '2026年台套数排名前3的产品型号',
     $sql$SELECT p.model AS 产品型号, SUM(f.qty) AS 台套数, SUM(f.year_income) AS 收入 FROM bi.fact_contract f LEFT JOIN bi.dim_product p ON p.product_code = f.product_code WHERE f.year = 2026 GROUP BY p.model ORDER BY 台套数 DESC LIMIT 3$sql$,
     '[1]', '「销售最多」按台套数理解，而非金额', TRUE),
(11, '各销售员的业绩排名', '2026年各销售的收入与合同数排名',
     $sql$SELECT s.sales_name AS 销售, SUM(f.year_income) AS 收入, COUNT(*) AS 合同数 FROM bi.fact_contract f LEFT JOIN bi.dim_sales s ON s.sales_id = f.sales_id WHERE f.year = 2026 GROUP BY s.sales_name ORDER BY 收入 DESC LIMIT 20$sql$,
     '[1]', '人员维度需 JOIN dim_sales', TRUE),
(12, '商解收入达成情况', '2026年各经营单元商解目标、商解收入与商解达成率',
     $sql$SELECT sa.unit_name AS 经营单元, sa.solution_goal AS 商解目标, sa.solution_income AS 商解收入, sa.solution_rate AS 商解达成率 FROM bi.v_solution_analysis sa WHERE sa.year = 2026 ORDER BY sa.solution_rate DESC$sql$,
     '[6]', '商解专项走专用视图', TRUE),
(13, '各行业收入分布', '2026年按行业大类与小类的收入分布',
     $sql$SELECT i.industry_cat AS 行业大类, i.industry_sub AS 行业小类, SUM(f.year_income) AS 收入 FROM bi.fact_contract f LEFT JOIN bi.dim_industry i ON i.industry_code = f.industry_code WHERE f.year = 2026 GROUP BY i.industry_cat, i.industry_sub ORDER BY 收入 DESC$sql$,
     '[1]', '行业维度需 JOIN dim_industry', TRUE),
(14, '2026年回款情况', '2026年各经营单元收入、回款与回款率',
     $sql$SELECT d.unit_name AS 经营单元, SUM(f.year_income) AS 收入, SUM(f.year_payment) AS 回款, ROUND(SUM(f.year_payment) / NULLIF(SUM(f.year_income), 0) * 100, 2) AS 回款率 FROM bi.fact_contract f LEFT JOIN bi.dim_unit d ON d.unit_code = f.unit_code WHERE f.year = 2026 GROUP BY d.unit_name ORDER BY 回款 DESC$sql$,
     '[1]', '回款率 = 回款 / 收入', TRUE),
(15, '新产品收入占比是多少', '2026年新产品收入占商业收入的比例',
     $sql$SELECT ROUND(SUM(CASE WHEN f.is_new_product THEN f.year_income ELSE 0 END) / NULLIF(SUM(f.year_income), 0) * 100, 2) AS 新产品收入占比 FROM bi.fact_contract f WHERE f.year = 2026$sql$,
     '[1]', '单值结果用指标卡展示', TRUE),
(16, '各区域收入汇总', '2026年按区域汇总的商业收入',
     $sql$SELECT d.region AS 区域, SUM(f.year_income) AS 收入 FROM bi.fact_contract f LEFT JOIN bi.dim_unit d ON d.unit_code = f.unit_code WHERE f.year = 2026 GROUP BY d.region ORDER BY 收入 DESC$sql$,
     '[1]', '区域来自 dim_unit.region', TRUE),
(17, '2026年毛利率情况', '2026年各产品线的金额、毛利与毛利率',
     $sql$SELECT p.product_line AS 产品线, SUM(f.amount) AS 金额, SUM(f.gross_profit) AS 毛利, ROUND(SUM(f.gross_profit) / NULLIF(SUM(f.amount), 0) * 100, 2) AS 毛利率 FROM bi.fact_contract f LEFT JOIN bi.dim_product p ON p.product_code = f.product_code WHERE f.year = 2026 GROUP BY p.product_line ORDER BY 毛利率 DESC$sql$,
     '[1]', '毛利率分母是金额不是收入', TRUE),
(18, '客户数覆盖情况', '2026年各经营单元成交客户数',
     $sql$SELECT d.unit_name AS 经营单元, COUNT(DISTINCT f.customer_code) AS 客户数 FROM bi.fact_contract f LEFT JOIN bi.dim_unit d ON d.unit_code = f.unit_code WHERE f.year = 2026 GROUP BY d.unit_name ORDER BY 客户数 DESC$sql$,
     '[1]', '客户数用 COUNT(DISTINCT)', TRUE),
(19, '2025年与2026年产品线收入对比', '分产品线对比2025与2026年收入',
     $sql$SELECT p.product_line AS 产品线, SUM(CASE WHEN f.year = 2025 THEN f.year_income ELSE 0 END) AS Y2025, SUM(CASE WHEN f.year = 2026 THEN f.year_income ELSE 0 END) AS Y2026 FROM bi.fact_contract f LEFT JOIN bi.dim_product p ON p.product_code = f.product_code GROUP BY p.product_line ORDER BY Y2026 DESC$sql$,
     '[1]', '跨年对比用条件聚合，不要用 WHERE 限定单年', TRUE),
(20, '高风险项目金额统计', '2026年高风险项目的数量与金额合计',
     $sql$SELECT COUNT(*) AS 高风险项目数, SUM(f.amount) AS 高风险金额 FROM bi.fact_contract f WHERE f.risk_level = '高' AND f.year = 2026$sql$,
     '[1]', '风险统计聚合', TRUE)
ON CONFLICT (id) DO UPDATE SET
    question = EXCLUDED.question, rewritten = EXCLUDED.rewritten, sql_text = EXCLUDED.sql_text,
    source_ids = EXCLUDED.source_ids, notes = EXCLUDED.notes, verified = EXCLUDED.verified;

-- ── 序列对齐（显式指定了 id，需同步序列）──────────────────────────
SELECT setval('sem_data_source_id_seq', (SELECT COALESCE(MAX(id), 1) FROM sem_data_source));
SELECT setval('sem_metric_id_seq',      (SELECT COALESCE(MAX(id), 1) FROM sem_metric));
SELECT setval('sem_dimension_id_seq',   (SELECT COALESCE(MAX(id), 1) FROM sem_dimension));
SELECT setval('sem_rule_id_seq',        (SELECT COALESCE(MAX(id), 1) FROM sem_rule));
SELECT setval('sem_fewshot_id_seq',     (SELECT COALESCE(MAX(id), 1) FROM sem_fewshot));

COMMIT;
