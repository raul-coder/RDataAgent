# 应用层 chat / agent 模块设计方案拆解（RdataAgent / 经管之星）

> 接续 `docs/08-语义层设计方案拆解.md` 的拆解思路，本文聚焦**应用层**：
> 一次问数请求从 HTTP 进入到 SSE 吐完，中间经过哪些层、每层承担什么、为什么这么切。
>
> 核心源码：`server/app/api/v1/chat.py`、`server/app/services/chat_service.py`、`server/app/agent/*`

---

## 0. 一句话定位

- **`chat` 是编排层**：会话与消息的持久化、缓存策略、事件流输出、权限边界。它不产生任何智能，只负责「把一次问数安排妥当」。
- **`agent` 是推理层**：意图判断、语义层裁剪、多轮改写、SQL 生成/校验/执行、结论生成。它是一个**手写的确定性状态机**，不是通用 Agent 框架。

两者的接口只有一个函数：`AgentRuntime.run()` —— 一个 async generator，边跑边吐 `(SSEEvent, RunResult)`。

---

## 1. 分层架构与职责边界

```
┌─ API 层        api/v1/chat.py
│    · SSE 响应头（X-Accel-Buffering: no）
│    · 会话 CRUD / 问数日志 / 数据有误入口
│    · 只做参数校验与异常兜底，无业务逻辑
│
├─ 编排层        services/chat_service.py
│    · 建会话 → 落用户消息 → 取历史 → 查缓存 → 跑 Agent → 落助手消息 → 写缓存
│    · 缓存策略、trace_id、旁路任务（常问累积）
│
├─ 推理层        agent/runtime.py  （AgentRuntime.run 状态机）
│    └─ nodes/  intent / retrieve / rewrite / sql_generate / sql_validate
│               / sql_execute / chart_advisor / compose / result_ops
│
├─ 状态层        agent/context.py（Redis 会话上下文）+ agent/slots.py（槽位）
│
└─ 模型层        llm/router.py（降级链）+ llm/*_provider.py
```

**边界划得很清楚**：
- `api` 层不 import `agent`，只 import `chat_service`（唯一例外是异常兜底时的 `error_event`）。
- `chat_service` 不碰 SQL、不碰 Prompt，只管「流程 + 存储 + 缓存」。
- `runtime` 不碰 HTTP、不做持久化（结果通过 `RunResult` 回传，由 `chat_service` 落库）。

这条边界带来一个直接好处：**`AgentRuntime` 可以脱离 HTTP 单独测试**（`scripts/eval/` 下的评测脚本就是这么做的）。

---

## 2. 数据模型：四张表

```135:166:server/scripts/sql/001_schema_sys.sql
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
```

**两个设计值得注意：**

**① `payload JSONB` 是整个可解释链路的载体。** 5 步状态、SQL、结果表、图表配置、追问建议、槽位、澄清卡片全塞在这一列里。刷新页面直接回放，不需要重跑。代价是**消息表会膨胀**——一次问数的结果最多 200 行 × N 列，全部序列化进 JSONB。

**② 埋点字段直接落在消息行上**（`intent` / `model` / `prompt_tokens` / `completion_tokens` / `cost_ms` / `trace_id` / `error`），而不是另建日志表。这让「问数日志」页可以直接查 `chat_session` join `chat_message`，也支持按 `trace_id` 把一次请求的所有日志串起来。

**反馈分两级**，这是产品层面的一个好设计：

```168:176:server/scripts/sql/001_schema_sys.sql
CREATE TABLE IF NOT EXISTS chat_message_feedback (
    id         BIGSERIAL PRIMARY KEY,
    message_id BIGINT      NOT NULL,
    session_id BIGINT      NOT NULL,
    user_id    BIGINT      NOT NULL,
    rating     VARCHAR(16) NOT NULL,           -- up / down / data_error
    comment    TEXT,
```

- `chat_message_feedback` —— 轻量态度（点赞/点踩），一条消息一条，可覆盖更新。
- `qa_feedback` —— 正式工单（「数据有误」），带 `status` / `handled_by` / `handled_at`，进入管理员的回复校对队列。

点赞点踩是**信号**，工单是**待办**，两者分开才不会让工单队列被无效噪声淹没。

---

## 3. SSE 事件协议：11 类事件构成前端状态机

```14:21:server/app/agent/events.py
# 5 步可解释链路（对齐 demo 的 renderAaSteps）
STEP_TITLES = (
    "选择数据表&数据时效",
    "推理逻辑",
    "执行取数SQL",
    "展示取数结果",
    "执行结束",
)
```

| 事件 | 时机 | 前端动作 |
| --- | --- | --- |
| `meta` | **两次**：用户消息落库后、助手消息落库后 | 回填真实自增 id（见 §9） |
| `intent` | 意图判定完成 | 可选展示 |
| `step` | 5 步各推 RUNNING / DONE / FAIL | 渲染可解释链路（含耗时） |
| `slots` | 多轮改写生效时 | 展示当前生效的分析条件 |
| `clarify` | 槽位为空 | 弹出澄清候选卡片 |
| `sql` | 校验通过后 | 展示最终 SQL（含权限注入后的版本） |
| `table` | 执行完成 | 渲染结果表 |
| `chart` | 图表推荐完成 | 渲染 ECharts |
| `token` | 结论流式 | 增量拼接 |
| `result_op` | 走二次加工旁路 | 清空 steps，等待新 table/chart |
| `followups` | 结束前 | 追问建议 |
| `error` | 任一环节异常 | 展示错误（含 retry 次数） |
| `done` | 结束 | 收尾，刷新会话列表 |

**「5 步可解释链路」是产品契约，不是技术实现**。它在 `events.py` 里被硬编码为 `STEP_TITLES`，前端按 `index` 渲染——这个约定的价值在于：用户能看见 AI 在做什么，而不是面对一个转圈的 loading。

SSE 帧格式极简，没有 id / retry 字段：

```30:38:server/app/agent/events.py
@dataclass
class SSEEvent:
    event: str
    data: dict = field(default_factory=dict)

    def encode(self) -> str:
        """序列化为 SSE 帧。"""
        payload = json.dumps(self.data, ensure_ascii=False)
        return f"event: {self.event}\ndata: {payload}\n\n"
```

响应头里有一个生产环境才体会到的细节：

```55:63:server/app/api/v1/chat.py
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关闭 Nginx 缓冲，否则流式会攒批
        },
    )
```

没有 `X-Accel-Buffering: no`，Nginx 会把流式响应攒成一坨再吐，前端看到的是「等 10 秒然后一次性全出来」。

---

## 4. Agent 状态机：一条主链 + 三条旁路

`AgentRuntime.run()` 是 async generator，主链 5 步，但在不同位置有 3 个提前 return 的旁路：

```
                    intent.classify()
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
  chitchat/out_of_scope  result_ops      其余 6 类
   直接模板回复        复用结果集变换     走完整主链
   （旁路①）           （旁路②）              │
                                              ▼
                                    retrieve_schema（语义层裁剪）
                                              │
                                         rewrite() ──→ 槽位为空？→ clarify（旁路③）
                                              │
                                    ① 选表 & 数据时效  ─→ step(1)
                                    ② 生成 SQL        ─→ step(2)
                                    ③ 校验 + 执行     ─→ step(3)  ← 自愈重试 ×2
                                    ④ 展示结果        ─→ step(4)
                                    ⑤ 结论流式        ─→ step(5)
                                              │
                                    回写 session context
```

### 4.1 旁路①：闲聊 / 越界，零成本拒答

```129:136:server/app/agent/runtime.py
        # 闲聊 / 越界：直接回复，不查库
        if intent.intent in {"chitchat", "out_of_scope"}:
            result.content = intent.reply
            for i in range(0, len(intent.reply), 24):
                yield ev.token_event(intent.reply[i : i + 24]), result
```

`intent.classify()` 是**纯正则**，不查语义层、不调模型。这意味着「今天天气怎么样」这种问题在微秒级被拦掉，不产生任何 token 成本。9 类意图的判定顺序本身就是优先级设计（越界 > 二次加工 > 对比 > 归因 > 预警 > 占比 > 趋势 > 排名）。

### 4.2 旁路②：结果二次加工，不重跑 SQL

```140:153:server/app/agent/runtime.py
        # ── 结果二次加工：复用上轮结果集，不重跑 SQL ───────────────
        if intent.intent == "result_ops":
            cached = ctx_store.load_result(ctx.last_result_key) if ctx.last_result_key else None
            if not cached:
                # 常见诱因：Redis 重启/过期，或（多副本时）请求打到了另一个进程
                log_kv(logger, logging.WARNING, "二次加工无结果集可用",
                       session_id=self.session_id, op=intent.op,
                       last_result_key=ctx.last_result_key, question=question)
```

「按降序排序」「换成饼图」「只看前 5」这类请求**完全不碰数据库和模型**，直接对 Redis 里缓存的结果集做变换，毫秒级响应。这是本项目对「交互流畅度」最重要的一个优化——用户调图表时的体验差异是数量级的。

前提是 `last_result_key` 还在（TTL 2 小时），且有兜底文案。

### 4.3 旁路③：主动澄清，绝不臆测

```276:289:server/app/agent/nodes/rewrite.py
    # 完全没有分析对象，且也没有历史可继承 → 请求澄清（给出指标 + 示例问题）
    if merged.is_empty():
        options = [m["name"] for m in schema.metrics[:4]]
        options += [d["name"] for d in schema.dimensions[:2]]
        options.append("2026年各经营单元收入排名")
```

候选项**来自语义层**——取前 4 个指标名 + 前 2 个维度名 + 一个示例问题。这是语义层反哺交互设计的一个例子：澄清卡片的内容是运营配出来的，不是写死的。

### 4.4 主链的 5 步与自愈重试

```245:274:server/app/agent/runtime.py
        for attempt in range(MAX_RETRY + 1):
            try:
                final_sql = sql_validate.validate(
                    draft.sql,
                    allowed_tables,
                    unit_codes,
                    is_ranking=intent.intent == "ranking"
                    or sql_validate.looks_like_ranking(rw.rewritten),
                )
                result.sql = final_sql
                yield ev.sql_event(final_sql, result.data_sources), result
                exec_result = await sql_execute.execute_sql(self.ro, final_sql)
                break
            except sql_validate.SQLRejectedError as exc:
                ...
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("SQL 执行失败（第 %d/%d 次）：%s", attempt + 1, MAX_RETRY, exc)
                if attempt >= MAX_RETRY:
                    ...
                    return
                yield ev.error_event("SQL_RETRY", str(exc), attempt), result
                draft = await sql_generate.generate_sql(
                    ...,
                    retry_hint=f"上一次 SQL 执行报错：{exc}\n原 SQL：{draft.sql}\n请修正后重新输出。",
                )
```

**自愈重试的设计要点**：
- `SQLRejectedError`（安全/白名单拒绝）**不重试**——这是 SQL 本身有问题，重试只会再错一次，直接 FAIL。
- 其它异常（语法错、列名不存在、超时）**重试 2 次**，把 `原始报错 + 原 SQL` 一起喂回去。
- 重试时 `prev_sql=draft.sql`，让模型看到自己上一版写了什么。

文档里记录了这个设计的收益：一次执行成功率从 ~80% 提升到 ~93%。

**执行失败的回滚是个 PostgreSQL 专属陷阱**：

```85:92:server/app/agent/nodes/sql_execute.py
    except Exception:
        # 关键：PostgreSQL 中一条语句失败会中止整个事务，若不回滚，
        # 自愈重试时使用同一会话会直接抛 InFailedSQLTransactionError。
        try:
            await session.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            logger.warning("执行失败后回滚出错：%s", rb_exc)
        raise
```

少了这个 rollback，自愈重试的第二次必然失败——这是「自愈功能写了但没生效」的典型原因。

---

## 5. 十个节点逐个拆解

| 节点 | 是否调模型 | 产出 | 关键设计 |
| --- | --- | --- | --- |
| `intent` | ✗ 纯正则 | 意图 + op | 9 类，判定顺序即优先级 |
| `retrieve` | ✗ 查库 | `SchemaContext` | 语义层裁剪 + 推导 `allowed_tables` |
| `rewrite` | ✗ 规则优先 | 改写问题 + 槽位 | 指代消解 / 条件叠加 / 澄清 |
| `sql_generate` | ✓ | `SQLDraft` | Prompt 组装 + Few-shot 召回 |
| `sql_validate` | ✗ sqlglot | 可安全执行的 SQL | 5 道闸门 |
| `sql_execute` | ✗ 只读连接 | `QueryResult` | 超时/只读/截断 + SQL 级缓存 |
| `chart_advisor` | ✗ 确定性规则 | ECharts option | 零模型调用 |
| `compose` | ✓ 流式 | 结论文本 | 数值程序算好再注入 |
| `result_ops` | ✗ | 变换后的结果集 | 不重跑 SQL |
| —（`runtime`） | — | 事件流 | 5 步编排 + 自愈重试 |

**刻意的控制：只有 2 个节点调模型。** 意图、改写、图表、校验全部规则化——因为它们要么需要确定性（改写/校验），要么不值得花 token（意图/图表）。这直接决定了单次问数的成本与延迟。

### 5.1 `sql_execute`：四道防线 + 一个诚实的告警

```78:92:server/app/agent/nodes/sql_execute.py
    # 会话级只读与超时兜底（连接账号本身也是只读的）
    try:
        await session.execute(text(f"SET LOCAL statement_timeout = {int(settings.SQL_TIMEOUT_MS)}"))
        await session.execute(text("SET LOCAL default_transaction_read_only = on"))
        cursor = await session.execute(text(sql))
        columns = list(cursor.keys())
        raw_rows = cursor.fetchall()
    except Exception:
```

四道防线：**连接账号只读**（DBA 授权）+ **`SET LOCAL` 会话级只读** + **statement_timeout** + **行数上限**。`SET LOCAL` 用事务级设置，不影响连接复用。

截断处理体现了很强的产品意识：

```94:101:server/app/agent/nodes/sql_execute.py
    cap = settings.SQL_MAX_ROWS
    truncated = len(raw_rows) > cap
    if truncated:
        # 截断会让「前端看到的合计」小于「数据库真实合计」，
        # 是「两个数字对不上」的高频原因之一，必须显式告警而不是静默切一刀。
        log_kv(logger, logging.WARNING, "SQL 结果被截断", returned=cap,
               actual=len(raw_rows), cap=cap, sql=sql[:300])
```

「两个数字对不上」是 BI 系统最伤信任的问题。这里选择**显式标记 `truncated=True` 并告警**，让前端能提示用户，而不是悄悄切一刀。

### 5.2 `compose`：让模型只做表述，不做计算

```3:5:server/app/agent/nodes/compose.py
关键设计：**数值由程序算好再注入 Prompt**（见技术方案 §4.3⑨），
让模型只做「表述」不做「计算」，杜绝心算错误。
模型不可用时回退到程序化模板，保证链路不中断。
```

统计摘要由 `sql_execute.summarize()` 程序算出（sum/max/min/avg + 前 12 行预览），模型只负责把这些数字组织成中文结论。System Prompt 第 1 条就是硬约束：

```21:31:server/app/agent/nodes/compose.py
规则：
1. 只使用「统计摘要」中给出的数值，禁止自行计算或编造数字。
...
7. 若上下文提供了「数据权限说明」，**必须在结论末尾补一句提示**，
   例如"注：以上结果已按您的数据权限范围过滤（仅含：上海代表处、浙江代表处）。"
8. 结果为空且存在「数据权限说明」时，要说明这是**权限范围所致**，
   不要让用户误以为数据不存在或名称写错（禁止出现"名称是否准确"这类猜测）。
```

第 8 条尤其关键——**用户查不到数据时的错误归因会直接造成伤害**。程序化兜底同样遵守：

```131:139:server/app/agent/nodes/compose.py
    if n == 0:
        if permission_note:
            # 有权限限制时，空结果几乎一定是权限所致，
            # 不能再让用户去"检查名称是否准确"
            return (
                f"在「{question}」的条件下**没有查询到数据**。\n\n"
                f"本次查询{permission_note}，因此未返回任何记录。\n"
                "如需查看其他经营单元的数据，请联系管理员开通数据权限。"
            )
```

还有一个针对推理型模型的兜底：

```115:117:server/app/agent/nodes/compose.py
    if not got:
        # 推理型模型可能把预算全用在思维链上，导致正文为空 —— 交给程序化摘要兜底
        raise RuntimeError("模型未产出正文内容（可能被思维链耗尽预算）")
```

### 5.3 `chart_advisor`：确定性规则，零模型调用

```1:9:server/app/agent/nodes/chart_advisor.py
"""图表推荐：确定性规则（不额外消耗模型调用）。

优先级：
    单值 → 指标卡
    时间维度 ≥3 点 → 折线图
    占比列（列名含「占比」或百分比）→ 饼图
    排名类（≤10 行且有数值序）→ 横向条形图
    双数值列 → 分组柱状图
    其它 → 表格
"""
```

图表类型判断本质是个规则问题，没有理由花一次模型调用。模型可以在生成 SQL 时给 `chart` 建议（hint），但**最终判定权在规则**：

```31:38:server/app/agent/nodes/chart_advisor.py
    # 单值一律用指标卡（模型可能会误判为 bar，画出来毫无意义）
    if len(rows) == 1 and len(columns) == 1:
        return _metric_card(columns, rows)

    # 模型显式指定且合法
    declared = str(hint.get("type") or "").lower()
    if declared in {"bar", "line", "pie", "table", "metric"} and declared != "auto":
        return _build(declared, columns, rows, hint)
```

---

## 6. 多轮对话：Slots + Context + Rewrite 三件套

这是整个应用层设计密度最高的部分。核心判断写在 `slots.py` 开头：

```1:7:server/app/agent/slots.py
"""槽位（Slots）：多轮对话的状态载体。

为什么用显式槽位而不是「把历史消息拼进 Prompt」：
    指代（"那北京呢"）、条件叠加（"只看政企"）、时间切换（"同比呢"）
    都需要**结构化地继承与覆盖**上一次的分析条件。
    槽位让这个过程可预测、可调试、可测试。
"""
```

### 6.1 `Slots`：9 个字段承载全部分析条件

```15:25:server/app/agent/slots.py
@dataclass
class Slots:
    metrics: list[str] = field(default_factory=list)          # ["biz_income"]
    dimensions: list[str] = field(default_factory=list)       # ["unit"]
    filters: list[dict] = field(default_factory=list)         # [{"dim":"industry_cat","op":"=","value":"政企"}]
    time_range: dict = field(default_factory=dict)            # {"type":"year","value":2026}
    compare: Optional[str] = None                             # yoy / mom / qoq / None
    order: Optional[dict] = None                              # {"by":"biz_income","dir":"desc"}
    limit: Optional[int] = None
    subject: Optional[str] = None                             # 当前分析主体，如 "北京代表处"
    chart_hint: Optional[str] = None
```

### 6.2 `merge`：覆盖语义的精确定义

```54:78:server/app/agent/slots.py
def merge(prev: Slots, cur: Slots) -> Slots:
    """合并槽位：本轮显式命中的覆盖历史，未命中的继承历史。

    过滤条件按维度去重（同一维度的新条件替换旧的），实现「条件叠加」。
    """
    out = Slots()

    out.metrics = cur.metrics or list(prev.metrics)
    out.dimensions = cur.dimensions or list(prev.dimensions)

    # 过滤条件：按 dim 维度覆盖
    merged: dict[str, dict] = {}
    for f in prev.filters:
        merged[str(f.get("dim"))] = dict(f)
    for f in cur.filters:
        merged[str(f.get("dim"))] = dict(f)
    out.filters = list(merged.values())
```

**「按 dim 覆盖」是条件叠加的关键**：Q1 说「政企行业」，Q2 说「只看北京」——两个筛选条件共存（不同 dim）；Q2 若说「换成运营商」——覆盖掉 `industry_cat` 的旧值（同 dim）。这个语义用「拼历史消息」根本无法实现。

### 6.3 `SessionContext`：Redis 中的会话状态

```23:31:server/app/agent/context.py
@dataclass
class SessionContext:
    session_id: int
    active_slots: Slots = field(default_factory=Slots)
    last_result_key: Optional[str] = None   # 上轮结果集缓存 key（结果二次加工用）
    last_sql: str = ""
    turn_count: int = 0
    summary: str = ""                        # 长对话压缩后的摘要
    updated_at: float = field(default_factory=time.time)
```

- **存 Redis，TTL 2 小时**；Redis 不可用自动降级为进程内字典（`core/redis.py`），功能不中断。
- `last_result_key` 指向独立的结果集缓存（`rs:{session_id}:{timestamp}`），供 `result_ops` 复用。
- `summary` 是长对话压缩的占位——`SUMMARY_AFTER_TURNS = 6` 与 `needs_summary()` 已定义，但**当前 `_build_history` 仍是简单截断最近 6 轮**，`summary` 字段只在 runtime 里增量维护、尚未真正替代历史注入。

### 6.4 三层上下文如何协同

```
ctx.active_slots  ──merge──> rw.merged ──to_prompt_hint──> 进 Prompt 作为强制约束
ctx.last_sql      ──────────> prev_sql ─────────────────> 进 Prompt 供多轮参考
ctx.last_result_key ────────> result_ops 旁路 ──────────> 不查库直接变换
```

`rewrite` 的三个分支决定了如何组合这些上下文：

```291:308:server/app/agent/nodes/rewrite.py
    # ① 续问优先：即便抽取到了主体（如「那北京呢」），也需要补全继承条件后才可执行
    if is_continuation(question):
        expanded = build_question(merged, schema)
        logger.info("指代消解：「%s」→「%s」", question, expanded)
        return RewriteResult(rewritten=expanded, current=cur, merged=merged)

    # ② 自足问题（本轮含指标或维度）→ 原样使用，仅把继承条件附在后面作为约束
    if cur.metrics or cur.dimensions:
        hint = _inherited_hint(prev_slots, cur, schema)
        rewritten = f"{question}（继承：{hint}）" if hint else question
        ...
    # ③ 条件叠加：本轮只有筛选/修饰，把继承条件补全后附加到原问题
```

**续问走「槽位回写成完整句子」**（`build_question`），自足问题走「原问题 + 继承提示」。这个区分是对的：续问本身信息不足，必须补全成独立可执行的句子；自足问题保留用户原话，只补充约束。

---

## 7. 降级与容错：七层防线

| 层 | 机制 | 触发条件 |
| --- | --- | --- |
| 1 | Redis 内存兜底 | Redis 连接失败（`core/redis.py`） |
| 2 | 模型降级链 | 主模型失败 → fallback → TemplateProvider |
| 3 | 检索式生成 | 无任何可用 Key |
| 4 | SQL 自愈重试 | 执行报错（最多 2 次） |
| 5 | 结论程序化摘要 | 结论生成失败 / 模型返回空 |
| 6 | 图表规则推荐 | 模型未给出合法 chart hint |
| 7 | SSE 异常兜底 + 前端 abort | 流中断 / 用户主动停止 |

### 7.1 模型降级链

```52:60:server/app/llm/router.py
async def build_providers(db: AsyncSession) -> list[LLMProvider]:
    """构造降级链。

    优先级：**数据库「模型配置」> 环境变量 .env**。
    管理员在系统管理页新增/切换默认模型后无需重启即可生效；
    未配置任何模型时才回退到 .env，保证开箱即用。

    数据库读取失败不能拖垮问数，因此捕获后静默回退。
    """
```

降级链末尾**永远挂一个 `TemplateProvider`**：

```85:86:server/app/llm/router.py
    providers.append(TemplateProvider(await load_fewshots(db)))
    return providers
```

这保证了 `complete()` 在极端情况下仍有产出。而 `degraded` 标记会一路传到前端：

```112:113:server/app/agent/runtime.py
        result.degraded = not any(getattr(p, "is_remote", False) for p in providers)
```

### 7.2 一个刻意的分工：TemplateProvider 只生成 SQL

```88:96:server/app/agent/nodes/compose.py
    """流式生成结论文本。

    只使用「真实模型」Provider：检索式兜底 Provider 只能产出 SQL，
    拿它写结论会得到无意义文本，因此这里直接抛错交给程序化摘要兜底。
    """
    remote = [p for p in providers if getattr(p, "is_remote", False)]
    if not remote:
        raise RuntimeError("无可用真实模型，结论生成走程序化摘要")
```

降级不是「无脑一路降到底」，而是**按能力降级**：检索式 Provider 能写出正确 SQL（因为复用人工校验样本），但写不出像样的结论——所以结论环节宁可抛错走程序化模板。

---

## 8. 缓存体系：三层，各有各的失效规则

| 层 | Key | TTL | 失效规则 |
| --- | --- | --- | --- |
| **问数结果缓存** `qa:cache:` | 问题 + 数据源 + **数据权限** | `QA_CACHE_TTL` | 仅首轮；不缓存 0 行；切模型时 `clear()` |
| **SQL 结果缓存** `sqlcache:` | SQL 归一化后 md5 | 300s | 自动过期 |
| **会话上下文** `ctx:` / `rs:` | session_id | 7200s | 自动过期；`reset_context` 清空 |

### 8.1 问数缓存的三个「不」

```390:404:server/app/services/chat_service.py
    # 3) 缓存命中则直接回放，整段跳过 LLM
    #    只缓存首轮问题：多轮追问的语义依赖上文，
    #    「那北京呢」在不同上下文里含义不同，缓存会导致误命中
    unit_codes = _visible_units(user)
    cache_key = qa_cache.build_key(question, source_ids, unit_codes) if not history else ""
    # 缓存只在首轮生效（追问语义依赖上文），这里必须留痕：
    # 「改了语义层但结果没变」类问题，第一嫌疑就是命中了旧缓存。
```

**① 不缓存多轮追问**——「那北京呢」在不同上下文里含义不同，缓存必错。
**② 缓存键必须含数据权限**：

```8:11:server/app/services/qa_cache.py
安全前提（务必注意）：
    缓存键**必须包含数据权限**。否则 A 用户问过的问题，会把结果串给
    权限范围不同的 B 用户——这是缓存最典型的越权坑，而且很难被发现。
```

**③ 不缓存 0 行结果**——这个理由写得很实在：

```489:498:server/app/services/chat_service.py
    # 5) 写入缓存：只缓存成功的首轮问题
    #    刻意**不缓存 0 行结果**——0 行往往是模型没理解对（维度取值猜错、
    #    选错了表），缓存它会让这个错误固化整个 TTL，后续同样的问题一直错，
    #    而且错得毫无征兆（耗时 20ms，看起来像是"很快查到了"）。
    if (
        cache_key
        and not final.error
        and not final.degraded
        and final.total > 0
    ):
```

### 8.2 缓存命中后仍要同步会话上下文

这是最容易漏的一环，注释里给了具体故障场景：

```258:262:server/app/services/chat_service.py
    # 命中缓存跳过了 Agent，但会话上下文必须照常维护。
    # 否则下一轮追问读到的 prev_slots 为空，会丢掉本轮的筛选条件——
    # 例如 Q1「高风险项目有哪些」命中缓存后，Q2「按产品线分拆看看」
    # 就丢失了 risk_level = '高'，变成统计全部项目。
    await _sync_context_after_cache(session.id, question, hit, first)
```

补的内容只有两样、且都不需要 LLM：`extract_slots()` 纯规则抽槽位 + 缓存结果集引用。并且**任何异常只告警不抛出**：

```319:320:server/app/services/chat_service.py
    except Exception as exc:  # noqa: BLE001
        logger.warning("缓存命中后同步会话上下文失败（不影响本次问数）：%s", exc)
```

---

## 9. 前端消费：事件流 → 状态机

### 9.1 `useSSE`：为什么不用原生 EventSource

```15:18:web/src/hooks/useSSE.ts
/**
 * SSE 客户端（POST + 自定义 header，原生 EventSource 不支持）。
 * 返回 send / abort。
 */
```

原生 `EventSource` 只支持 GET、不能带 `Authorization` 头。这里用 `@microsoft/fetch-event-source`，拿到 POST + Bearer + `AbortController`（支持「停止生成」）。

### 9.2 `chatStore`：以「最后一条助手消息」为操作锚点

```59:78:web/src/stores/chatStore.ts
interface ChatState {
  sessions: SessionBrief[];
  currentId: number | null;
  messages: Record<number, ChatMessage[]>;
  streaming: boolean;
  sourceIds: number[];
  ...
  updateLastAssistant: (id: number, patch: Partial<ChatMessage>) => void;
  appendContent: (id: number, delta: string) => void;
  patchLastPayload: (id: number, patch: Record<string, unknown>) => void;
```

`messages: Record<sessionId, ChatMessage[]>` 按会话分片，切换会话不丢状态。所有更新操作（`appendContent` / `patchLastPayload` / `updateLastAssistant`）都**倒序查找最后一条 assistant 消息**打补丁——这与后端「事件流只描述增量」的协议完全对齐。

### 9.3 三个真实踩过的坑

**① meta 事件发两次，用 `role` 区分，回填真实 id**

```200:217:web/src/pages/AiQa/index.tsx
        case 'meta': {
          // 后端在「用户消息落库」和「AI 回答落库」两个时机会各发一次 meta，
          // 用 role 区分，把真实自增 id 回填到本地临时（负数）id 上。
          // 若不回填，m.id 永远是负数，点赞点踩 / 数据有误会因 id <= 0 被拦掉。
```

前端先插入 `id: -Date.now()` 的占位消息（乐观更新，输入即显示），后端落库后用 meta 回填真实 id。**没有这一步，所有反馈功能都是废的**——前端的守卫 `m.id > 0 && void handleDataError(m.id)` 会全部跳过。

**② token 批量 flush，避免重渲染风暴**

```249:254:web/src/pages/AiQa/index.tsx
        case 'token':
          buffer.current += data.delta ?? '';
          if (timer.current === null) {
            timer.current = window.setTimeout(() => flush(sid), 80);
          }
          break;
```

后端每个 token 一个事件，若每收一个就 setState，一次回答会触发上千次重渲染。80ms 批量 flush 是标准做法。

**③ 自动滚动要在下一帧，且用户上翻时暂停跟随**

```81:89:web/src/pages/AiQa/index.tsx
  const scrollToBottom = useCallback((smooth = false) => {
    const el = messagesRef.current;
    if (!el) return;
    // 必须在下一帧再滚：调用方往往是刚 append 完内容，此刻 DOM 还没重排，
    // 直接滚只会滚到「上一帧的底部」，流式输出时会越差越多。
    requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    });
  }, []);
```

配合 `stickToBottom` ref：用户手动上翻时暂停跟随，否则想看历史回答的人会被流式输出不断拽回底部。

### 9.4 结果二次加工的前端映射

图表切换 / 排序按钮**不直接操作 DOM，而是重新发一个问题**：

```421:423:web/src/pages/AiQa/index.tsx
                  onSort={(dir) => void doAsk(dir === 'desc' ? '按降序排序' : '按升序排序')}
                  onChart={(t) => void doAsk(`换成${CHART_LABEL[t]}图`)}
                  onExport={() => exportCsv(m)}
```

这样「改图表」和「自然语言提问」走同一条链路，意图识别器统一处理——前端不需要为二次加工单独写一套状态。代价是**多一次 HTTP 往返**（虽然后端不查库不调模型）。

---

## 10. 关键设计决策与取舍

| # | 决策 | 收益 | 代价 / 风险 |
| --- | --- | --- | --- |
| 1 | 手写状态机而非 Agent 框架 | 全链路可控、可预测、可调试；无框架黑盒 | 新增能力要改 `runtime.py`；无自动工具编排 |
| 2 | 只有 2 个节点调模型 | 成本与延迟可控，规则部分零抖动 | 规则覆盖不到的长尾问法会失败 |
| 3 | 显式槽位而非拼历史 | 指代消解 / 条件叠加语义明确，可单测 | 槽位抽取漏一个字段就表现为「数字对不上」 |
| 4 | 5 步可解释链路作为产品契约 | 用户看得见过程，信任度高 | 步骤是硬编码的，链路结构变了要同步改前端 |
| 5 | `payload JSONB` 存全量结果 | 刷新即回放，无需重跑 | 消息表膨胀；无结果体积上限保护 |
| 6 | 缓存三层、各自定义失效 | P95 从 21s 降到 0.1s（文档实测） | 失效规则分散在 3 个文件，改语义层易漏 |
| 7 | 缓存键含数据权限 | 杜绝跨用户串数据 | 权限变更需等 TTL 或手动清 |
| 8 | 重试区分「拒绝」与「报错」 | 安全类错误不浪费重试预算 | — |
| 9 | 降级按能力而非一路到底 | TemplateProvider 只生 SQL，结论走模板 | 降级路径的行为差异需要前端理解 `degraded` |
| 10 | 前后端用「最后一条 assistant」对齐 | 事件协议极简（纯增量） | 并发多问场景会串（当前 `streaming` 锁已防住） |

---

## 11. 亮点与改进建议

### 亮点

1. **分层边界干净**。`AgentRuntime` 不依赖 HTTP 与持久化，`scripts/eval/` 下的评测脚本可以脱离服务直接驱动全链路——这让「改了语义层跑一遍评测」成为可行的工作流。

2. **规则与模型的分工极其克制**。10 个节点里只有 2 个调模型；意图、改写、图表、校验全部规则化。这既压了成本，也让 80% 的链路行为**可预测、可单测**。

3. **容错设计有层次感**。`SQLRejectedError` 不重试而其它错误重试；TemplateProvider 能写 SQL 但不写结论；缓存命中仍同步会话上下文——每一处降级都回答了「这一步最合理的退路是什么」。

4. **注释质量极高**。几乎每个非常规决策都附了「为什么 + 故障场景」，例如「不缓存 0 行，因为错得毫无征兆（耗时 20ms）」、「PostgreSQL 失败事务不回滚会导致自愈重试必然失败」。这是本仓库最难复制的资产。

5. **可解释性是产品契约而非附加功能**。`STEP_TITLES` 硬编码、5 步每步带 `cost_ms`，前端按 index 渲染——过程本身成了卖点。

### 改进建议（按性价比排序）

1. **`_visible_units` 逻辑重复**。`chat_service.py:28` 与 `runtime.py:404` 各写了一份，注释也承认「保持一致」。这是权限逻辑，**重复实现是安全隐患**——两处漂移会导致缓存键与实际过滤范围不一致，正好绕开 §8.1 的越权防护。应抽到 `core/deps` 或 `security/` 下单一实现。

2. **缓存键缺语义层版本**（承接 `docs/08` 第 1 条）。当前改口径后旧答案仍在缓存里，且日志只打了 `cache_key` 而不打语义层摘要，排查困难。建议：缓存 value 里存一份语义层指纹，读取时校验，不匹配则视为未命中。

3. **`summary` 字段已定义但未投入使用**。`SUMMARY_AFTER_TURNS = 6` 与 `needs_summary()` 都在，`summary` 也在 runtime 里增量维护（`_update_summary` 保留最近 8 行），但 `_build_history` 仍是简单截断最近 `MAX_HISTORY_TURNS*2` 条消息，**`summary` 从未注入 Prompt**。要么接上（长对话下能省 token 并提升连贯性），要么删掉避免误导。

4. **结果集缓存只有 2 小时 TTL，且无体积上限**。`ctx_store.cache_result` 把最多 5000 行 × N 列塞进 Redis 单个 key，多会话并发时内存压力不可控。建议加行数/字节上限，超限则只缓存 `result_ops` 真正需要的部分（当前排序/截取最多用到前 N 行，但换图/导出需要全量——可按需降级）。

5. **`messages` 全量加载无分页保护**。`fetchSessionDetail` 一次拉 50 条消息，每条都带完整 `payload`（含最多 200 行结果）。历史会话翻页时响应体会很大。建议列表接口只返回摘要（不含 `payload`），展开时再单独取。

6. **二次加工走「重新提问」多一次往返**。前端 `onSort` / `onChart` 是 `doAsk('按降序排序')`，虽然后端不查库，但仍要走完整的 SSE 建连 + 意图识别。可考虑前端直接本地变换（结果集已在内存），或加一个轻量的非流式接口。

7. **`reset_context=(session.msg_count or 0) <= 2` 这个判据偏脆弱**。用消息数推断「是否是新一轮」，在用户连续快速提问或消息落库时序异常时可能误判。更稳的做法是让前端显式传 `new_turn` 标记，或前端新建会话时调一次 `context.clear()`。

---

## 附：一次问数的完整调用链

```
POST /api/v1/chat/completions
  └─ chat.py:completions            new_trace_id(); StreamingResponse
     └─ chat_service.stream_answer
        ├─ get_session / create_session
        ├─ 落 user 消息 ──────────────→ yield meta(role=user, message_id)
        ├─ quick_question.record       （旁路，失败不影响）
        ├─ _build_history / _last_sql
        ├─ qa_cache.get ───── 命中 →── _replay_cached
        │                              └─ _sync_context_after_cache
        └─ AgentRuntime.run
           ├─ build_providers          （DB 模型配置 > .env，末尾挂 TemplateProvider）
           ├─ intent.classify ────────→ yield intent  [旁路①]
           ├─ retrieve_schema ────────→ allowed_tables
           ├─ [result_ops] ───────────→ result_ops.apply  [旁路②]
           ├─ rewrite ────────────────→ yield slots / clarify  [旁路③]
           ├─ ① step(1) 选表 & 数据时效
           ├─ ② step(2) sql_generate ─→ Few-shot Top-3 + schema.render()
           ├─ ③ step(3) sql_validate → sql_execute    ← 自愈重试 ×2
           ├─ ④ step(4) table + chart_advisor
           └─ ⑤ step(5) compose.stream_answer  → token*
              └─ 回写 context（slots / last_sql / last_result_key）
        ├─ 落 assistant 消息 ─────────→ yield meta(role=assistant, message_id)
        └─ qa_cache.put               （仅首轮 + 无错误 + 未降级 + 行数 > 0）
           yield followups → yield done
```

## 附：相关命令

```bash
make check-pipeline          # 问数链路端到端基线测试（需后端已启动）
make check-multiturn         # 多轮对话端到端验证（UC-2）
make check-uc3               # 数据权限隔离验证
make check-uc4               # 反馈闭环端到端验证
make check-security          # 越权/注入/鉴权边界（27 项）
make check-i4                # 配置/模型/反馈/快捷提问/日志 冒烟
make eval-quick              # 抽样 20 条快速评测
make bench-qa                # 问数并发压测（10 并发 × 2 轮）
make warmup-cache            # 预热问数缓存（P95 21s → 0.1s）
```


