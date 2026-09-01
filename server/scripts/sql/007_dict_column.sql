-- 007 数据字典：台账列的中文名 / 类型 / 口径 / 维表关联
--
-- 为什么要单独建这张表，而不直接读 information_schema 的 column comment：
--   1. 台账是明细表，列的「业务口径」（单位、枚举含义、是否可筛）无法从类型推断；
--   2. 编码列（unit_code / product_code …）需要知道去哪张维表换名称，
--      否则台账页只能给用户看一堆编码；
--   3. 语义层生成 SQL 时也需要同一份口径说明（FR-D4：供问数语义层复用）。
--
-- 幂等：唯一约束 (table_name, column_name) + ON CONFLICT DO NOTHING。

CREATE TABLE IF NOT EXISTS sem_dict_column (
    id           BIGSERIAL PRIMARY KEY,
    table_name   VARCHAR(64)  NOT NULL,
    column_name  VARCHAR(64)  NOT NULL,
    cn_name      VARCHAR(64)  NOT NULL,
    -- text / number / date / bool / enum
    data_type    VARCHAR(16)  NOT NULL DEFAULT 'text',
    caliber      VARCHAR(255) NOT NULL DEFAULT '',
    -- 编码列对应的维表：ref_table.ref_key = 本列，取 ref_label 展示
    ref_table    VARCHAR(64),
    ref_key      VARCHAR(64),
    ref_label    VARCHAR(64),
    visible      BOOLEAN      NOT NULL DEFAULT TRUE,   -- 台账页默认是否展示
    filterable   BOOLEAN      NOT NULL DEFAULT TRUE,
    sortable     BOOLEAN      NOT NULL DEFAULT TRUE,
    sort_order   INT          NOT NULL DEFAULT 0,
    UNIQUE (table_name, column_name)
);

CREATE INDEX IF NOT EXISTS idx_dict_column_table
    ON sem_dict_column (table_name, sort_order);

COMMENT ON TABLE sem_dict_column IS '数据字典：台账与报表列的业务口径定义';


-- ── 商业市场台账 fact_contract ────────────────────────────────────
INSERT INTO sem_dict_column
    (table_name, column_name, cn_name, data_type, caliber,
     ref_table, ref_key, ref_label, sort_order) VALUES
    ('fact_contract','contract_no',  '合同编号',   'text',   '一条合同的唯一编号', NULL, NULL, NULL, 1),
    ('fact_contract','presale_no',   '预销售编号', 'text',   '报备阶段编号，可能为空', NULL, NULL, NULL, 2),
    ('fact_contract','opp_no',       '机会点编号', 'text',   'PPL 机会点编号', NULL, NULL, NULL, 3),
    ('fact_contract','unit_code',    '经营单元',   'text',   '签约归属的经营单元', 'bi.dim_unit',     'unit_code',     'unit_name',     4),
    ('fact_contract','industry_code','行业',       'text',   '行业小类', 'bi.dim_industry', 'industry_code', 'industry_sub',  5),
    ('fact_contract','product_code', '产品型号',   'text',   '产品编码，对应型号', 'bi.dim_product',  'product_code',  'model',         6),
    ('fact_contract','sales_id',     '销售',       'text',   '签约销售', 'bi.dim_sales',    'sales_id',      'sales_name',    7),
    ('fact_contract','customer_code','客户',       'text',   '客户编码', 'bi.dim_customer', 'customer_code', 'customer_name', 8),
    ('fact_contract','project_name', '项目名称',   'text',   '', NULL, NULL, NULL, 9),
    ('fact_contract','qty',          '台套数',     'number', '合同设备台套', NULL, NULL, NULL, 10),
    ('fact_contract','amount',       '合同金额',   'number', '单位：万元（含税）', NULL, NULL, NULL, 11),
    ('fact_contract','amount_ex_tax','不含税金额', 'number', '单位：万元', NULL, NULL, NULL, 12),
    ('fact_contract','cost',         '成本',       'number', '单位：万元', NULL, NULL, NULL, 13),
    ('fact_contract','gross_profit', '毛利',       'number', '单位：万元', NULL, NULL, NULL, 14),
    ('fact_contract','land_date',    '落地时间',   'date',   '合同签订/落地日期', NULL, NULL, NULL, 15),
    ('fact_contract','risk_level',   '风险等级',   'enum',   '低 / 中 / 高', NULL, NULL, NULL, 16),
    ('fact_contract','is_new_product','是否新产品','bool',   '', NULL, NULL, NULL, 17),
    ('fact_contract','rebate_flag',  '是否返利',   'bool',   '', NULL, NULL, NULL, 18),
    ('fact_contract','year_income',  '年度收入',   'number', '单位：万元，12 个月收入之和', NULL, NULL, NULL, 19),
    ('fact_contract','year_payment', '年度回款',   'number', '单位：万元，12 个月回款之和', NULL, NULL, NULL, 20),
    ('fact_contract','year',         '年度',       'number', '', NULL, NULL, NULL, 21),
    ('fact_contract','remark',       '备注',       'text',   '', NULL, NULL, NULL, 22)
ON CONFLICT (table_name, column_name) DO NOTHING;

-- 月度 / 季度收入与回款共 32 列：口径规律一致，用 generate_series 批量登记，
-- 且默认不展示（台账页只呈现业务主列，避免横向滚动几十列）
INSERT INTO sem_dict_column
    (table_name, column_name, cn_name, data_type, caliber, visible, sort_order)
SELECT 'fact_contract', 'm' || m || '_income', m || '月收入', 'number', '单位：万元', FALSE, 100 + m
FROM generate_series(1, 12) AS m
ON CONFLICT (table_name, column_name) DO NOTHING;

INSERT INTO sem_dict_column
    (table_name, column_name, cn_name, data_type, caliber, visible, sort_order)
SELECT 'fact_contract', 'q' || q || '_income', '第' || q || '季度收入', 'number', '单位：万元', FALSE, 120 + q
FROM generate_series(1, 4) AS q
ON CONFLICT (table_name, column_name) DO NOTHING;

INSERT INTO sem_dict_column
    (table_name, column_name, cn_name, data_type, caliber, visible, sort_order)
SELECT 'fact_contract', 'm' || m || '_payment', m || '月回款', 'number', '单位：万元', FALSE, 200 + m
FROM generate_series(1, 12) AS m
ON CONFLICT (table_name, column_name) DO NOTHING;

INSERT INTO sem_dict_column
    (table_name, column_name, cn_name, data_type, caliber, visible, sort_order)
SELECT 'fact_contract', 'q' || q || '_payment', '第' || q || '季度回款', 'number', '单位：万元', FALSE, 220 + q
FROM generate_series(1, 4) AS q
ON CONFLICT (table_name, column_name) DO NOTHING;


-- ── PPL 明细台账 fact_ppl ────────────────────────────────────────
INSERT INTO sem_dict_column
    (table_name, column_name, cn_name, data_type, caliber,
     ref_table, ref_key, ref_label, sort_order) VALUES
    ('fact_ppl','contract_no',    '合同编号',   'text',   '已签约的关联合同，未签约为空', NULL, NULL, NULL, 1),
    ('fact_ppl','presale_no',     '预销售编号', 'text',   '', NULL, NULL, NULL, 2),
    ('fact_ppl','opp_no',         '机会点编号', 'text',   '', NULL, NULL, NULL, 3),
    ('fact_ppl','industry_cat',   '行业大类',   'enum',   '政企 / 运营商 / 商业市场 / 渠道部', NULL, NULL, NULL, 4),
    ('fact_ppl','industry_sub',   '行业小类',   'enum',   '智能制造 / 数字政府 / 渠道(ISV) / 大企业', NULL, NULL, NULL, 5),
    ('fact_ppl','unit_code',      '经营单元',   'text',   '', 'bi.dim_unit', 'unit_code', 'unit_name', 6),
    ('fact_ppl','sales_name',     '销售',       'text',   '', NULL, NULL, NULL, 7),
    ('fact_ppl','project_name',   '项目名称',   'text',   '', NULL, NULL, NULL, 8),
    ('fact_ppl','customer_name',  '最终客户',   'text',   '', NULL, NULL, NULL, 9),
    ('fact_ppl','product_type',   '产品类型',   'enum',   '服务器 / 存储 / 网络', NULL, NULL, NULL, 10),
    ('fact_ppl','model',          '产品型号',   'text',   '', NULL, NULL, NULL, 11),
    ('fact_ppl','qty',            '台套数',     'number', '', NULL, NULL, NULL, 12),
    ('fact_ppl','amount',         '金额',       'number', '单位：万元（含税）', NULL, NULL, NULL, 13),
    ('fact_ppl','amount_ex_tax',  '不含税金额', 'number', '单位：万元', NULL, NULL, NULL, 14),
    ('fact_ppl','stage',          '阶段',       'enum',   '商机 / 方案 / 投标 / 商务 / 签约 / 交付', NULL, NULL, NULL, 15),
    ('fact_ppl','risk_level',     '竞争风险',   'enum',   '低 / 中 / 高', NULL, NULL, NULL, 16),
    ('fact_ppl','expect_land_date','预计落地时间','date',  '', NULL, NULL, NULL, 17),
    ('fact_ppl','year',           '年度',       'number', '', NULL, NULL, NULL, 18)
ON CONFLICT (table_name, column_name) DO NOTHING;


-- ── 整体目标台账 fact_goal ───────────────────────────────────────
INSERT INTO sem_dict_column
    (table_name, column_name, cn_name, data_type, caliber,
     ref_table, ref_key, ref_label, sort_order) VALUES
    ('fact_goal','unit_code',     '经营单元',   'text',   '', 'bi.dim_unit', 'unit_code', 'unit_name', 1),
    ('fact_goal','year',          '年度',       'number', '', NULL, NULL, NULL, 2),
    ('fact_goal','month',         '月份',       'number', '1~12', NULL, NULL, NULL, 3),
    ('fact_goal','biz_goal',      '商业目标',   'number', '单位：万元', NULL, NULL, NULL, 4),
    ('fact_goal','solution_goal', '商解目标',   'number', '单位：万元', NULL, NULL, NULL, 5)
ON CONFLICT (table_name, column_name) DO NOTHING;
