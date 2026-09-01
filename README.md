# 经管之星 · 智能问数平台

以「对话式问数（ChatBI）」为核心的企业级销售经营数据管理平台。用户用自然语言提问，系统自动完成
**选表 → 推理 → 生成并执行 SQL → 结构化呈现 → 支持追问** 的全链路，并把推理过程完整透明地展示出来。

- 需求基线：[docs/01-需求分析说明书.md](docs/01-需求分析说明书.md)
- 技术方案：[docs/02-技术方案.md](docs/02-技术方案.md)
- 开发计划：[docs/03-开发计划.md](docs/03-开发计划.md)
- 问数链路与启动流程：[docs/07-问数链路与启动流程.md](docs/07-问数链路与启动流程.md)（函数级排障手册）
- 原型参考：`docs/demo.html`（4595 行单文件原型，本项目由其逆向而来）

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| 前端 | React 18 + TypeScript + Vite 5 + Ant Design 5 |
| 后端 | Python 3.11 + FastAPI + SQLAlchemy 2.0 + Pydantic v2 |
| 存储 | PostgreSQL 15（业务/权限/语义层）+ Redis 7 + MinIO |
| 数据 | 自造（程序化生成，固定随机种子，可复现） |

---

## 快速开始

### 方式 A：本机直连（macOS / Homebrew，推荐本地调试）

```bash
# 1) 安装依赖服务（仅首次）
brew install postgresql@15 redis node python@3.11

# 2) 一键搭好环境：生成 .env → 装后端依赖 → 起服务 → 造数 → 建库装载
make setup-local

# 3) 启动应用
make api-dev       # 后端   http://127.0.0.1:8000/docs
make web-dev       # 前端   http://127.0.0.1:5180
```

首次使用需先创建数据库与账号（执行一次即可）：

```bash
/opt/homebrew/opt/postgresql@15/bin/psql -U $(whoami) -d postgres -h localhost <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='jingguan') THEN
    CREATE ROLE jingguan LOGIN PASSWORD 'jingguan' CREATEDB;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bi_readonly') THEN
    CREATE ROLE bi_readonly LOGIN PASSWORD 'bi_readonly';
  END IF;
END $$;
SQL
/opt/homebrew/opt/postgresql@15/bin/createdb -U $(whoami) -h localhost -O jingguan -E UTF8 jingguan
```

常用命令：

```bash
make test          # 后端全量测试（数据权限 + RBAC）
make status        # 查看服务/接口状态
make db-reset      # 清空业务数据后重新装载
make check-fewshots # 校验语义层 20 条 Few-shot SQL 是否都能执行
make services-stop # 停掉 PostgreSQL / Redis
```

### 方式 B：Docker Compose（团队一致环境）

```bash
make init          # env + docker up + 造数 + 建库装载
make up / make down
```

### 本地开发说明

- 造数脚本**零第三方依赖**，纯标准库实现，可独立于数据库运行与校验（`make verify`）
- 前端固定端口 **5180**（避开本机其它 Vite 项目默认的 5173），并显式绑定 IPv4
- 后端通过只读账号 `bi_readonly` 访问 `bi` schema 与 `sem_*` 元数据；
  该账号**写入会被数据库拒绝**，是问数安全的最后一道兜底

### 3. 演示账号

| 用户名 | 角色 | 密码 | 说明 |
| --- | --- | --- | --- |
| `admin` | 超级管理员 | `123456` | 全部菜单 + 全量数据 |
| `zhaoliu` | 管理员 | `123456` | 可管用户/权限/反馈 |
| `zhangsan` | 普通用户 | `123456` | **数据权限限定上海、浙江 2 个经营单元** |
| `huangjiu` | 数据查看员 | `123456` | 只读 |
| `xushi` | 审计员 | `123456` | 仅日志 |

---

## 本机环境验证记录（macOS / Homebrew）

| 组件 | 版本 | 验证结果 |
| --- | --- | --- |
| PostgreSQL | 15.19 (Homebrew) | ✅ 库 `jingguan` 已建，装载 15,000 + 10,000 + 416 行 |
| Redis | 7.x | ✅ `PING` → `PONG` |
| Python | 3.11.16 | ✅ `server/.venv` 已装全部后端依赖 |
| Node | v26.8.1 | ✅ `npm run build` 通过（3097 模块 / 2.17s） |
| 前端 dev server | Vite 5.4.21 | ✅ `http://127.0.0.1:5180` HTTP 200，API 代理打通 |
| 后端 API | FastAPI | ✅ 4 个接口全部返回真实数据 |
| 只读隔离 | `bi_readonly` | ✅ `DELETE` 被拒；`SELECT` 正常（15,000 行） |

接口实跑：

```
GET /api/v1/health        -> env=local, data_as_of=2026-12-31
GET /api/v1/health/db     -> PostgreSQL 15.19
GET /api/v1/meta/overview -> fact_contract=15,000  sem_metric=18  sem_fewshot=20
GET /api/v1/meta/achieve  -> 16 个单元，北京 118.0% … 江西 50.0%(预警)
```

语义层 Few-shot 可执行性：**20/20 全部执行成功**，平均 13.6ms/条，返回行数全部合理
（`make check-fewshots` 可随时复跑，改动语义层后立即发现失效样本）。

> 真实数据库暴露了一个仅靠 CSV 校验无法发现的缺陷：`v_overall_achieve`
> 原先把「目标表」与「合同表」直接 JOIN，导致一行目标扇出成 N 行，
> `SUM(biz_goal)` 被放大 N 倍，完成率被算成 0.03%。
> 已改为 CTE 预聚合修复，并在 `db_init` 中增加「完成率必须落在 [30%, 130%]」的断言作为防线。

---

## 当前进度

### I0 · 地基与造数（已完成）

**增补**：`scripts/eval/check_fewshots.py`、db-init 装载后断言、只读账号授权。

### I1 · 登录与权限（已完成）

| 交付项 | 位置 |
| --- | --- |
| JWT 认证（登录/刷新/登出黑名单/改密） | `app/services/auth_service.py` + `api/v1/auth.py` |
| 图形验证码（PNG，失败即失效） | `app/core/captcha.py` |
| 失败锁定（5 次锁 10 分钟）+ 限流 | `auth_service.login` + `core/rate_limit.py` |
| RBAC 内核（权限加载/缓存/失效） | `services/perm_service.py`、`core/deps.py` |
| **数据权限注入器** | `security/sql_guard.py`（16 项单测） |
| 用户 / 角色 / 菜单 / 权限 / 日志 API | `api/v1/{users,roles,menus,permissions,logs}` |
| 操作日志落库与查询 | `services/log_service.py` |
| 前端：无感刷新、登录页、菜单树、路由守卫 | `services/http.ts`、`stores/authStore.ts`、`router/guards.tsx` |
| 前端：5 个管理页面 + 个人信息 | `pages/System/*`、`pages/Profile` |

**UC-5 权限隔离验证（zhangsan，真实数据库）**

```
菜单     : 智能问数 / 商业市场台账 / PPL明细台账 / 整体目标台账 / 数据台账
数据权限 : {1: [SH, ZJ], 13: [SH, ZJ], 14: [SH, ZJ], 15: [SH, ZJ]}
接口     : GET /users -> 403 缺少权限 sys:user:view
问数 SQL : 注入 f.unit_code IN ('SH','ZJ') 后，10 个单元 → 精确 2 个单元
```

**真机发现并修复的 3 个缺陷**
1. **`ops_to_perms` 前缀截取错误**：`:view` 判为 5 字符，把 `sys:log:view` 推导成 `sys:logexport`，应为 `[:-4]`
2. **sqlglot 30.x 把 FROM 的 key 改为 `from_`**（旧版为 `from`），导致注入点全部定位失败
3. **`find_all` 包含自身**：叶子判断把根节点误判为「非叶子」，注入被跳过

### I2 · 智能问数最小闭环（已完成）

| 交付项 | 位置 |
| --- | --- |
| LLM 抽象层（LiteLLM 真实模型 + 检索式兜底） | `app/llm/` |
| Agent 节点：Schema 检索 / SQL 生成 / 校验 / 执行 / 图表 / 结论 | `app/agent/nodes/` |
| AgentRuntime 编排 + 5 步可解释链路 + SSE 事件 | `app/agent/runtime.py`、`events.py` |
| 会话服务与 SSE 接口 | `services/chat_service.py`、`api/v1/chat.py` |
| 前端：会话列表 / 对话区 / AI 回答卡片 / 输入区 / 数据源选择 | `pages/AiQa/` |

**基线准确率（`make check-pipeline`，真实模型）**

```
成功率 9/10 = 90%    耗时 P50 59.7s（推理型模型，见下方说明）
```

**真机发现并修复的 6 个缺陷**
1. **检索式兜底喂错输入**：拿拼接后的长 Prompt 去做样本匹配，相似度被稀释到 0.01 —— 必须为 Provider 显式传入原始问题
2. **维表被安全校验误杀**：表白名单只有事实表，`bi.dim_unit` 被拒 —— 改为从 `join_sql` 提取维表一并放行
3. **litellm 缺 provider 前缀**：自定义 `base_url` 时 `deepseek-v4-pro-0813` 报 `LLM Provider NOT provided` —— 自动加 `openai/` 前缀
4. **推理型模型 token 预算**：`max_tokens=1024` 全被 `reasoning_content` 吃光，正文为空且句子被截断 —— 提高到 8192 并增加"正文为空则降级"的防护
5. **饼图分支引用未定义变量 `s`**：占比类问题直接 500 —— 已修复并补回归测试
6. **CTE 被当成物理表拦截**：模型写 `WITH goal_agg AS (...)` 时校验报"表不在白名单" —— CTE 属虚拟表，必须放行；数据权限会注入到 CTE 内部（离表最近的一层）

**性能说明与模型选型**：模型名是否可用取决于网关托管范围，不能凭经验推断，
应实测。本项目提供两个选型工具（见下方命令）：

```
                        SQL 质量   4 条 SQL 生成总耗时   端到端 P50
deepseek-v4-flash        4/4           20.7s            15.3s   ← 当前配置
qwen3.8-max              4/4           94.6s              —
deepseek-v4-pro-0813     4/4          139.2s             ~60s
```

三者质量一致（4/4），但 `deepseek-v4-flash` 快 6.7 倍。
当前配置：主模型 `deepseek-v4-flash`，降级链 `qwen3.8-max → deepseek-v4-pro-0813`。

> 注意：推理型模型的思维链会占用 token 预算，因此 `LLM_MAX_TOKENS` 需给足
> （默认 8192）；若改用其他模型，建议跑一遍 `make compare-models` 复核质量。

**新增命令**

```bash
make check-pipeline   # 问数链路端到端基线（10 条问题，统计成功率与耗时）
make test             # 后端全量单测
```

### I3 · 多轮对话与准确率调优（已完成）

| 交付项 | 位置 |
| --- | --- |
| 槽位模型 + 合并策略 | `app/agent/slots.py` |
| 会话上下文（Redis，含长对话摘要） | `app/agent/context.py` |
| 上下文改写（指代/时间/条件 + 澄清反问） | `app/agent/nodes/rewrite.py` |
| 9 类意图识别（含结果二次加工、越界拒答） | `app/agent/nodes/intent.py` |
| 结果二次加工（排序/改图/截取/导出，不重跑 SQL） | `app/agent/nodes/result_ops.py` |
| 100 条评测集 + 评测器 | `scripts/eval/{cases.json,run_eval.py}` |
| Few-shot 20 → 50 | `scripts/sql/006_seed_fewshot_extra.sql` |
| 前端：澄清卡片 / 上下文提示 / 结果操作条 / CSV 导出 | `pages/AiQa/` |

**UC-2 多轮对话验证（`make check-multiturn`，5/5 通过）**

```
Q1 2026年各经营单元收入排名是多少   → 10 行
Q2 那北京呢                      → 改写「2026年北京代表处商业收入…」      ← 指代消解
Q3 它同比呢                      → 改写「2026年北京代表处收入同比…」      ← 时间切换
Q4 只看政企行业                   → 改写「2026年北京代表处行业大类为政企…」 ← 条件叠加（保留主体）
Q5 换成饼图                      → result_ops，24ms，图表=pie            ← 结果二次加工（不重跑 SQL）
```

**新增命令**

```bash
make check-multiturn   # 多轮对话端到端验证（UC-2）
make eval              # 100 条准确率评测（约 1.5 小时）
make eval-quick        # 抽样 20 条快速评测
```

**真机发现并修复的 5 个缺陷**
1. **`retrieve._dim()` 漏传 `value_map`** —— 维度取值表在运行时全为 None，主体/筛选抽取完全失效
2. **种子数据错位** —— `unit` 的 `value_map` 是 NULL，而「区域→单元」映射被错写在 `region` 上
3. **任何维度值都被当成分析主体** —— 「只看政企」会把主体从"北京代表处"覆盖成"政企"；只有主实体维度才构成主体
4. **续问判断顺序错误** —— 「那北京呢」因抽取到主体被误判为"自足"，继承条件未补全
5. **`InFailedSQLTransactionError`** —— SQL 执行失败后事务被中止，自愈重试复用同一会话导致后续全部失败（单次耗时一度达 542s）；已在失败时回滚

**准确率实测（`scripts/eval`，真实模型）**

```
ranking     12/12  100%
edge        10/10  100%   （越界拒答 / 澄清反问，约 10ms，不调模型）
UC-2 多轮   5/5    100%
数据权限隔离（zhangsan）通过
```

> 完整 100 条评测约需 1.5~2 小时（当前为推理型模型，单条约 40~110s），
> 可用 `make eval` 随时复跑；`make eval-quick` 抽样 20 条快速验证。

**已知限制**：模型输出存在波动（同一问题可能一次用 CTE、一次直连），
I3 已通过 50 条 Few-shot 与自愈重试显著收敛；切换为 `deepseek-v4-flash` 后
端到端 P50 约 15s（原 ~60s），距 NFR-P1 的「P95 ≤ 8s」仍有差距，
进一步压缩需精简 Prompt 或对结论生成改用更小的模型。

### I4 · 配置 · 反馈 · 日志（已完成）

| 交付项 | 位置 |
| --- | --- |
| 应用配置 API（6 项开关 + 文案 + 阈值，改完即时生效） | `services/config_service.py` + `api/v1/app_config.py` |
| 模型配置（CRUD / 测试连接 / 设默认 / 密钥加密） | `services/model_service.py` + `api/v1/models.py` |
| 反馈闭环（点赞点踩 / 数据有误 / 回复校对处理） | `api/v1/feedback.py` |
| 快捷提问（常问自动累积 / 推荐 / 收藏） | `services/quick_question_service.py` + `api/v1/quick_question.py` |
| 问数日志 + 操作日志导出（CSV） | `api/v1/chat.py`、`api/v1/menus.py` |
| 前端：应用配置页 / 模型配置页 / 回复校对页 | `pages/System/{AppConfig,ModelConfig,FeedbackReview}` |
| 前端：问数日志 Tab + 会话回放抽屉 | `pages/AiQa/LogList.tsx` |
| 前端：快捷提问面板（常问/推荐/收藏三 Tab） | `pages/AiQa/QuickPanel.tsx` |
| 前端：AI 卡片点赞点踩 | `pages/AiQa/messages/AiAnswerCard.tsx` |

```bash
make seed-i4      # 同步种子：禁用无 Key 模型 + 导入 .env 模型
make check-i4     # I4 接口冒烟
make check-uc4    # 反馈闭环端到端（UC-4）
```

**验证结果（真实数据库）**

```
make check-i4    28/28 通过
make check-uc4   10/10 通过（zhangsan 反馈 → admin 处理 → 状态实时变更）
UC-3 数据权限隔离 顺带验证：zhangsan 问数返回 2 个单元
```

**两处需要留意的适配**

1. **模型路由改为「数据库优先」**：`build_providers` 现在优先取 `sys_model`
   中启用的模型，取不到才回退 `.env`，因此在模型配置页切换默认模型无需重启。
   造数写入的 7 个 Demo 模型没有 API Key，若直接启用会让降级链逐个超时，
   因此 `make seed-i4` 会把它们置为禁用（保留可见，不进入降级链）。
2. **应用配置的键沿用造数脚本的扁平驼峰格式**（`greeting` / `greetingText` /
   `hotThreshold`），而非嵌套对象——造数会重新生成这些数据，
   适配数据比改数据更稳。6 张卡片的展示结构由后端 `/app-config/schema` 下发。

**验证中发现并修复的 3 个缺陷**

1. **受限用户查无权数据时结论误导用户**（UC-3 验收未达标）。
   zhangsan（仅授权上海/浙江）问「北京代表处今年达成情况」，SQL 权限注入正确、
   返回 0 行，但结论说"北京代表处今年无达成数据，建议检查名称是否准确"——
   用户会以为数据不存在。已在 `compose` 节点注入数据权限说明，
   现在会明确告知"不在您的数据权限范围内（仅含：上海代表处、浙江代表处）"，
   有结果时也会在结论末尾补一句过滤提示。
2. **「数据有误」按钮在新建会话中完全失效**。
   后端在「用户消息落库」「AI 回答落库」两个时机会各发一次 `meta` 事件，
   但前端 `case 'meta'` 是空实现，AI 消息 id 一直停留在本地临时负数，
   而反馈入口有 `m.id > 0` 的守卫，导致点击后无任何反应。
   已给 meta 事件加 `role` 字段，前端据此把真实自增 id 回填到本地消息。
3. **反馈单 `question` 为空、`ai_reply` 存成了用户问题**。
   根因同上：传的是用户消息 id 而非 AI 回答 id，
   导致「该回答之前最近的用户提问」查不到。修复后快照与原文均正确
   （`check_uc4` 已对两个 meta 的 id 差异增加断言防回归）。

> 附带调整：本地/演示环境（`ENV != prod`）的限流额度放宽 6 倍。
> 原额度（登录 10 次/10 分钟）会让 UC-3 / UC-4 这类连续验证脚本跑到一半被 429 打断；
> 生产额度保持不变。

3. **常问只收录能独立使用的完整问题**。
   「换成饼图」「按降序排序」属于结果二次加工操作，「那北京呢」「它同比呢」
   依赖上文补全省略成分——单独点进去会查出一个莫名其妙的结果，
   因此都不纳入频次统计（见 `quick_question_service` 的两条过滤规则）。
   推荐项与收藏不受影响，二者都是用户主动维护的完整问题。

### I5 · 增强与打磨（已完成）

| 交付项 | 位置 |
| --- | --- |
| 台账查看页 ×3（列筛选 / 排序 / 分页 / 列显隐） | `pages/Ledger/`、`services/ledger_service.py`、`api/v1/ledger.py` |
| 数据字典（列中文名 / 口径 / 维表关联） | `scripts/sql/007_dict_column.sql`、`sem_dict_column` |
| 台账导入（Excel 解析 + 逐格校验 + 事务落库） | `services/ledger_import.py` |
| 结果导出 CSV / Excel（服务端生成） | `api/v1/ledger.py` |
| 语音 STT / TTS（受应用配置开关控制） | `hooks/useSpeech.ts`、`api/v1/speech.py` |
| 语义层管理（指标 / 维度 / 口径规则 / Few-shot） | `pages/Semantic/`、`api/v1/semantic.py` |
| 问数结果缓存 + 运行看板 | `services/qa_cache.py`、`api/v1/stats.py` |
| 前端路由懒加载 | `router/index.tsx` |

```bash
make check-ledger    # 台账接口（22 项，含权限隔离与注入用例）
make check-semantic  # 语义层 CRUD / 权限 / 样本 SQL 校验（14 项）
make bench-qa        # 问数并发压测
make warmup-cache    # 预热问数缓存（演示/压测前跑）
```

**验证结果**

```
make check-ledger   22/22    台账页分页 54ms（验收要求 < 500ms）
make check-semantic 14/14    前 20 条 Few-shot SQL 全部可执行
make check-uc3       9/9     受限用户仅见授权单元，结论明确提示过滤范围
make check-uc4      11/11
```

**性能优化：缓存是唯一有效的手段**

索引与视图优化对问数延迟几乎没有帮助——耗时的大头是 LLM 调用。
压测对比（10 并发 × 2 轮）：

```
冷缓存：P50 10.3s   P95 24.0s   吞吐 0.60 次/秒
预热后：P50  0.1s   P95  0.1s   吞吐 111  次/秒   ← 约 200 倍
```

因此实现了问数结果缓存（`services/qa_cache.py`），并配套 `make warmup-cache`
用 Few-shot 的真实问法预热——这些本来就是用户最可能问的问题。

> **缓存的安全前提**：键里必须带数据权限（`units`）。
> 否则 A 用户问过的问题会把结果串给权限不同的 B 用户，
> 这是缓存最典型的越权坑，而且很难被发现。已用 zhangsan 做过交叉验证。

**两处偏离需求原文的说明**

1. **没上 OTel + Grafana**。需求要求「看板可见问数成功率与耗时」，
   但 Prometheus + Grafana 对单机演示意味着多套中间件要维护；
   而要看的三个指标（成功率 / 耗时分位 / 缓存命中）库里本来就有
   （`chat_message.cost_ms`、`error`、`payload.cached`）。
   因此用 SQL 直接统计（`/api/v1/stats/qa`），零额外依赖，口径日后可平移到 OTel。
2. **语音走浏览器原生 API**。Web Speech API / SpeechSynthesis 零成本零延迟，
   Chrome 上中文识别质量足够；服务端 Whisper / TTS 需要额外 Key 与音频带宽，
   接口预留为 `/api/v1/speech/*`，未配置时明确返回 501（不静默降级）。

### I6 · 验收与发布（已完成）

| 交付项 | 位置 |
| --- | --- |
| 全量回归（100 条评测 + 各模块接口回归） | `make eval`、`make check-*` |
| 安全用例集（越权 / 注入 / 鉴权边界） | `scripts/eval/check_security.py`（27 项） |
| 性能压测（10 / 50 并发） | `scripts/eval/bench_qa.py` |
| E2E 测试脚本 | `web/e2e/smoke.spec.ts`、`playwright.config.ts` |
| 部署文档 | `docs/04-部署文档.md` |
| 演示脚本（分角色） | `docs/05-演示脚本.md` |
| 验收报告 | `docs/06-验收报告.md` |
| 问数链路与启动流程（排障手册） | `docs/07-问数链路与启动流程.md` |

```bash
make check-security   # 安全 27 项
make bench-qa         # 性能压测
make warmup-cache     # 演示前预热（P95 24s → 0.1s）
```

**验收中发现并修复的 6 个缺陷**（功能测试都跑不出来，详见验收报告第四节）

1. **JWT 令牌不唯一** —— payload 只有 `sub/type/exp`，同一秒内多次登录生成**完全相同**的
   令牌，导致"登出后立刻重登"拿到与刚拉黑的一样的令牌，一登录就失效。已加 `jti`。
2. **问数日志越权** —— `/chat/logs` 无用户过滤，普通用户能翻到全公司问数记录。
   已改为管理员看全量、普通用户只看自己的。
3. **缓存固化错误结果** —— 0 行的结果也进缓存，而 0 行往往是模型没理解对，
   会让错误固化整个 TTL 且看起来毫无异常（耗时 20ms）。已改为只缓存有结果的。
4. **读他人会话返回 200** —— 归属校验其实生效了（查不到），但返回 `200 + null`；
   已改为 404（用 404 而非 403，避免泄露"该 ID 存在但你没权限"）。
5. **PPL 语义层覆盖缺失** —— `fact_ppl` 指标定义为 0、`stage` 维度完全没定义。
   已补 4 个指标 + stage 维度（6 个取值）+ 4 条 Few-shot。
6. **部署配置两处硬伤** —— web Dockerfile 里 `COPY ../deploy/...` 越出构建上下文
   （必定构建失败）；依赖缺 `greenlet`（SQLAlchemy asyncio 必需）与 `openpyxl`。

**两点必须说明的环境限制**

1. **模型免费配额在验收后段耗尽**（`Free quota exhausted`），最后一轮 100 条评测
   未能完整复跑。报告中的 83/100 是配额耗尽前跑完的那轮，其中 6 条失败是
   "模型调用失败"而非语义错误，扣除后约 88%。当前系统跑在降级链上
   （`template-fewshot`），对 Few-shot 覆盖的问法仍能正确返回数据。
2. **Docker 与 Playwright 均未在本机实测** —— 环境没有 Docker，
   浏览器下载也未完成。E2E 核心用例已用浏览器手动走通，脚本与配置已交付。

### I0 交付项（补记）

| 交付项 | 状态 | 位置 |
| --- | --- | --- |
| 仓库骨架与代码规范 | ✅ | `web/` `server/` `deploy/` |
| 全量 DDL（系统/会话/语义层/业务库） | ✅ | `server/scripts/sql/00*.sql` |
| 5+1 张统计报表视图 | ✅ | `server/scripts/sql/004_views.sql` |
| 造数脚本（零依赖、可复现） | ✅ | `server/scripts/data_factory/` |
| 一致性校验（20 万项断言） | ✅ | `server/scripts/data_factory/verify.py` |
| 语义层种子（18 指标/12 维度/15 规则/20 Few-shot） | ✅ | `server/scripts/sql/005_seed_semantic.sql` |
| 数据库装载脚本 | ✅ | `server/scripts/db_init.py` |
| FastAPI 骨架（配置/日志/安全/异常/DB） | ✅ | `server/app/` |
| React 骨架（设计令牌/路由/布局/登录页） | ✅ | `web/src/` |

---

## 造数说明

数据工厂采用「先定目标、再由目标反推收入」的建模方式，保证 `目标 → 收入 → 完成率` 主线自洽：

```
年度商业目标 = 8000 万 × 单元体量系数 × (1 + 增长)
计划收入     = 年度目标 × 完成率  r ~ Normal(0.85, 0.15) 截断到 [0.35, 1.25]
合同金额     = 计划收入按对数正态权重拆分（长尾：少数大单、多数小单）
分月收入     = 合同金额按 [50%, 30%, 20%] 在落地月起 3 个月内确认
分月回款     = 合同金额 × Beta(6,2) 回款比例，滞后落地月 1~3 个月
```

所有金额以「万元的百分之一」为**整数**参与运算，保证
`Σ分月 = Σ分季 = 年度`、`Σ合同 = 计划收入` **严格相等**（无浮点误差）。

### 数据规模

| 表 | 行数 |
| --- | --- |
| `bi.fact_contract` 商业市场台账 | 15,000 |
| `bi.fact_ppl` PPL 明细台账 | 10,000 |
| `bi.fact_goal` 目标台账 | 416（16 单元 × 2 年 × 13 行） |
| `bi.dim_*` 维度表 | 522 |
| `sys_*` / `chat_*` 系统与会话表 | 约 1,100 |

> **对 FR-M2 的有意偏离**：需求要求商业市场台账「≥ 50,000 行」，实际取 **15,000 行**。
> 原因是金额模型会打架——若摊到 5 万条合同，单均仅 4.6 万元，
> 低于一台服务器的单价（8 万元），会导致金额分布被下限钉住、台套数恒为 1，
> 数据明显失真、问数结果讲不通。取 1.5 万条时单均约 15 万元、中位约 9 万元，
> 符合企业级销售常识。详见 `data_factory/config.py` 的 `CONTRACT_TOTAL` 注释。
> 若确需 5 万行演示，把该常量调回即可（`make seed && make db-init --drop`）。

**合同金额分布（I3 修正后）**

```
合同数 15,000 | 合计 230,750 万元
平均 15.4 万 | 中位 8.2 万 | P75 16.8 万 | P90 31.5 万 | P99 101 万 | 最大 1,772 万
台套数 平均 3.6 | 中位 2 | P90 7
>100万: 151 条   >500万: 19 条   >1000万: 4 条（大单合计占营收 16%）
```

> 最初设为 5 万条合同，但那样单均仅 4.6 万元 —— 低于一台服务器的单价（8 万元），
> 金额分布被下限钉住、台套数恒为 1，明显失真。改为 1.5 万条后分布真实，
> 且「金额大于 500 万的合同」这类问法才有数据。

### 刻意制造的分析素材

- **超额标杆**：北京代表处 完成率 118%
- **低达成预警**：江西办事处 50%、山西办事处 55%
- **同比下滑**：辽宁办事处（2026 完成率下调 18 个百分点）、安徽、湖北、渠道部、陕西
- **结构热点**：智能制造行业 2026 需求爆发（权重 ×1.45）
- **增长引擎**：智能计算 +29.2%、商业解决方案 +47.2%，通用计算仅 +7.2%
- **高风险项目**：占比 10.7%，且金额越大、落地越晚风险越高

### 校验结果（实跑）

```
[L1] 合同行内自洽：15,000 条 × 4 项 = 60,000 项断言通过
[L2] Σ合同收入 = 计划收入：32 个「单元×年度」全部命中，最大偏差 0 cents
[L2] 目标台账：32 个「单元×年度」月度之和 = 年度，全部通过
[L3] 整体完成率 84.5%，落在合理区间
[L3] 风险分布 低29,004 / 中15,642 / 高5,354，高风险占比 10.7%
[L3] 达成样本齐备：预警单元 7 个，超额单元 2 个
[L3] 同比增长：2025 108,386 万元 → 2026 122,365 万元（+12.9%）
[L3] 同比下滑单元 5 个：['AH', 'HB', 'LN', 'QDB', 'SN']
[L3] 参照完整性：5 个外键维度全部无孤儿
[L3] PPL 10,000 条，6 个阶段齐备
[OK] 全部校验通过
```

---

## 目录结构

```
RdataAgent/
├── docs/                         需求 / 技术方案 / 开发计划 / 原型 demo.html
├── deploy/
│   ├── docker-compose.yml        Postgres + Redis + MinIO + API + Web
│   ├── nginx/nginx.conf          反向代理（SSE 关闭缓冲）
│   └── postgres/initdb/          只读账号 bi_readonly
├── server/
│   ├── app/                      FastAPI 应用
│   │   ├── core/                 配置 / 日志 / 安全 / 异常
│   │   ├── db/session.py         主库（读写）+ 业务库（只读）双引擎
│   │   └── api/v1/               健康检查 / 元信息
│   └── scripts/
│       ├── data_factory/         造数（零依赖、可复现、自带校验）
│       ├── sql/                  DDL + 视图 + 语义层种子
│       └── db_init.py            建表 + COPY 装载 + 装载后校验
├── web/                          React 前端
└── Makefile                      常用命令入口
```

---

## 常用命令

```bash
make help          # 查看全部命令
make verify        # 仅跑造数一致性校验（不重新生成）
make db-reset      # 清空业务数据后重新装载
make down          # 停止所有服务
make logs          # 查看服务日志
```

## 安全提示

- `.env` 中的 `SECRET_KEY` / `AES_KEY` **必须**在生产环境替换为随机值；
- `BI_READONLY_URL` 应指向只读账号连接，即使 SQL 校验被绕过也无法写库；
- 模型 API Key 在库中以 AES-GCM 密文存储，接口返回时脱敏。
