-- =====================================================================
-- 001_schema_sys.sql —— 主库结构：系统表 sys_* / 会话表 chat_*
--
-- 约定：
--   1. 主键均为 BIGSERIAL；造数 CSV 不含 id 列，COPY 后由 setval 同步序列；
--   2. 布尔值在 CSV 中写作 true/false，可直接 COPY 进 BOOLEAN；
--   3. JSONB 列在 CSV 中为标准 JSON 文本，可直接 COPY；
--   4. 业务台账位于 bi schema（002_schema_bi.sql），应用以只读账号访问。
-- =====================================================================

BEGIN;

-- ── 用户与权限 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sys_user (
    id              BIGSERIAL PRIMARY KEY,
    username        VARCHAR(64)  NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    nickname        VARCHAR(64),
    phone           VARCHAR(32),
    email           VARCHAR(128),
    avatar          VARCHAR(255),
    status          SMALLINT     NOT NULL DEFAULT 1,          -- 1 启用 / 0 禁用
    valid_until     DATE,
    last_login_at   TIMESTAMPTZ,
    last_login_ip   VARCHAR(64),
    pwd_must_change BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_user_status ON sys_user (status);

CREATE TABLE IF NOT EXISTS sys_role (
    id          BIGSERIAL PRIMARY KEY,
    code        VARCHAR(64)  NOT NULL UNIQUE,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255),
    is_builtin  BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sys_user_role (
    user_id BIGINT NOT NULL REFERENCES sys_user (id) ON DELETE CASCADE,
    role_id BIGINT NOT NULL REFERENCES sys_role (id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS sys_menu (
    id         BIGSERIAL PRIMARY KEY,
    parent_id  BIGINT       NOT NULL DEFAULT 0,
    name       VARCHAR(64)  NOT NULL,
    path       VARCHAR(255),
    component  VARCHAR(255),
    icon       VARCHAR(64),
    sort_order INT          NOT NULL DEFAULT 0,
    type       VARCHAR(16)  NOT NULL,      -- M 目录 / C 菜单 / B 按钮
    perm_code  VARCHAR(128),               -- 如 ai:qa、sys:user:edit
    visible    BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_menu_parent ON sys_menu (parent_id);

-- 角色-菜单：ops 为操作权限位数组
-- ["view","add","edit","del","import","export","refresh","batch","filter","query"]
CREATE TABLE IF NOT EXISTS sys_role_menu (
    role_id BIGINT NOT NULL REFERENCES sys_role (id) ON DELETE CASCADE,
    menu_id BIGINT NOT NULL REFERENCES sys_menu (id) ON DELETE CASCADE,
    ops     JSONB  NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (role_id, menu_id)
);

-- 数据权限：按「菜单 × 权限类型 × 经营单元」控制行级可见范围
-- unit_codes 为空数组表示不限制
CREATE TABLE IF NOT EXISTS sys_role_data_perm (
    id         BIGSERIAL PRIMARY KEY,
    role_id    BIGINT      NOT NULL REFERENCES sys_role (id) ON DELETE CASCADE,
    menu_id    BIGINT      NOT NULL REFERENCES sys_menu (id) ON DELETE CASCADE,
    perm_type  VARCHAR(16) NOT NULL,       -- view / operate / delete
    unit_codes JSONB       NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (role_id, menu_id, perm_type)
);

-- ── 系统配置 ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sys_app_config (
    config_key   VARCHAR(64) PRIMARY KEY,
    config_value JSONB       NOT NULL,
    updated_by   BIGINT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sys_model (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,
    provider    VARCHAR(64)  NOT NULL,     -- openai/anthropic/deepseek/qwen/glm/moonshot/ollama
    base_url    VARCHAR(255) NOT NULL,
    model_name  VARCHAR(128) NOT NULL,
    api_key_enc TEXT,                      -- AES-GCM 密文，接口返回时脱敏
    scene       VARCHAR(64)  NOT NULL DEFAULT 'chat_qa',   -- chat_qa / rewrite / embedding
    is_default  BOOLEAN      NOT NULL DEFAULT FALSE,
    enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    params      JSONB        NOT NULL DEFAULT '{"temperature":0.1,"top_p":0.9,"max_tokens":4096}'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Prompt 模板（版本化，支持灰度与回滚）
CREATE TABLE IF NOT EXISTS sys_prompt_template (
    id         BIGSERIAL PRIMARY KEY,
    scene      VARCHAR(64)  NOT NULL,      -- sql_generate / rewrite / intent / compose
    name       VARCHAR(128) NOT NULL,
    content    TEXT         NOT NULL,
    version    INT          NOT NULL DEFAULT 1,
    is_active  BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (scene, name, version)
);

CREATE TABLE IF NOT EXISTS sys_oper_log (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT,
    username   VARCHAR(64),
    log_type   VARCHAR(16)  NOT NULL,      -- login / oper
    action     VARCHAR(255) NOT NULL,
    method     VARCHAR(255),
    ip         VARCHAR(64),
    user_agent VARCHAR(512),
    status     VARCHAR(32)  NOT NULL,      -- 成功 / 失败-xxx / 部分成功
    cost_ms    INT,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_oper_log_created ON sys_oper_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_oper_log_user    ON sys_oper_log (user_id, created_at DESC);

-- ── 会话与问数 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_session (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT       NOT NULL,
    title          VARCHAR(128) NOT NULL DEFAULT '新对话',
    pinned         BOOLEAN      NOT NULL DEFAULT FALSE,
    msg_count      INT          NOT NULL DEFAULT 0,
    user_feedback  VARCHAR(32),             -- 有用 / 很满意
    admin_feedback VARCHAR(32),             -- 已关注
    source_files   JSONB        NOT NULL DEFAULT '[]'::jsonb,
    last_msg_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_session_user ON chat_session (user_id, last_msg_at DESC);

CREATE TABLE IF NOT EXISTS chat_message (
    id                BIGSERIAL PRIMARY KEY,
    session_id        BIGINT       NOT NULL REFERENCES chat_session (id) ON DELETE CASCADE,
    role              VARCHAR(16)  NOT NULL,   -- user / assistant / system
    content           TEXT         NOT NULL,
    payload           JSONB,                   -- {steps, tables, charts, sql, followups}
    rewritten_query   TEXT,
    intent            VARCHAR(32),
    model             VARCHAR(128),
    prompt_tokens     INT,
    completion_tokens INT,
    cost_ms           INT,
    trace_id          VARCHAR(64),
    error             TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON chat_message (session_id, id);

CREATE TABLE IF NOT EXISTS chat_message_feedback (
    id         BIGSERIAL PRIMARY KEY,
    message_id BIGINT      NOT NULL,
    session_id BIGINT      NOT NULL,
    user_id    BIGINT      NOT NULL,
    rating     VARCHAR(16) NOT NULL,           -- up / down / data_error
    comment    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 数据问题反馈单（反馈管理 ▸ 回复校对）
CREATE TABLE IF NOT EXISTS qa_feedback (
    id         BIGSERIAL PRIMARY KEY,
    question   TEXT         NOT NULL,
    user_id    BIGINT       NOT NULL,
    username   VARCHAR(64),
    ai_reply   TEXT,
    session_id BIGINT,
    message_id BIGINT,
    status     VARCHAR(16)  NOT NULL DEFAULT '待处理',   -- 待处理 / 已处理
    remark     TEXT,
    handled_by BIGINT,
    handled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_qa_fb_status ON qa_feedback (status, created_at DESC);

CREATE TABLE IF NOT EXISTS quick_question (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT,                          -- NULL = 系统预置
    question   VARCHAR(255) NOT NULL,
    category   VARCHAR(16)  NOT NULL,           -- recommend / favorite / recent
    hit_count  INT          NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_qq_user_cat ON quick_question (user_id, category);

-- 问数执行链路明细（可观测）
CREATE TABLE IF NOT EXISTS chat_query_trace (
    id         BIGSERIAL PRIMARY KEY,
    trace_id   VARCHAR(64) NOT NULL,
    message_id BIGINT,
    step       VARCHAR(32) NOT NULL,   -- rewrite/intent/plan/retrieve/sql/validate/execute/check/compose
    status     VARCHAR(16) NOT NULL,   -- success / fail / retry
    detail     JSONB,
    cost_ms    INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_trace_id ON chat_query_trace (trace_id);

COMMIT;
