-- 初始化只读账号：Agent 取数专用连接
-- 挂载到 /docker-entrypoint-initdb.d，仅在首次初始化数据目录时执行

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_readonly') THEN
        CREATE ROLE bi_readonly LOGIN PASSWORD 'bi_readonly';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE jingguan TO bi_readonly;
GRANT USAGE ON SCHEMA bi TO bi_readonly;

-- 注意：业务表由 scripts/db_init.py 创建，建表后会自动执行一次
-- GRANT SELECT ON ALL TABLES IN SCHEMA bi TO bi_readonly（见 db_init.py）
