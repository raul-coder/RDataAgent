# 槽位（Slots）机制详解（RdataAgent / 经管之星）

> 「槽位」在语义层、改写、状态机、事件协议、前端状态里都出现过，但各处含义不完全一样。
> 本文从头到尾串一遍：**它是什么、从哪来、怎么合并、被谁消费、存在哪、有哪些坑**。
>
> 核心源码：`server/app/agent/slots.py`、`server/app/agent/nodes/rewrite.py`、`server/app/agent/context.py`、`server/app/agent/runtime.py`

---

## 0. 一句话：槽位是什么

**槽位 = 一次问数的「分析条件」的结构化快照。** 它把一句自然语言问题里蕴含的分析意图，拆成 9 个有名字的字段：

```
「2026年各经营单元收入排名，取前10」
        ↓ extract_slots
{
  metrics:    ["biz_income"],                   ← 收入
  dimensions: ["unit"],                         ← 各经营单元
  time_range: {"type":"year","value":2026},     ← 2026年
  order:      {"by":"biz_income","dir":"desc"}, ← 排名
  limit:      10,                               ← 前10
  subject:    null,
  filters:    [],
  compare:    null,
  chart_hint: null
}
```

**为什么要拆成结构，而不是把历史消息拼进 Prompt？** 源码里的解释很直接：

```1:7:server/app/agent/slots.py
"""槽位（Slots）：多轮对话的状态载体。

为什么用显式槽位而不是「把历史消息拼进 Prompt」：
    指代（"那北京呢"）、条件叠加（"只看政企"）、时间切换（"同比呢"）
    都需要**结构化地继承与覆盖**上一次的分析条件。
    槽位让这个过程可预测、可调试、可测试。
"""
```

关键区别：**「拼历史」只能让模型自己猜该继承什么；槽位让代码显式决定。**

| 方案 | 处理「那北京呢」 | 结果 |
| --- | --- | --- |
| 拼历史消息 | 模型读到「Q1: 2026年各经营单元收入排名 / Q2: 那北京呢」自行推断 | 可能继承时间、也可能忘；可能保留分组、也可能丢 |
| **槽位** | `merge` 明确定义：本轮只抽到 `subject`，其余从 `prev` 继承 | 稳定得到 `{time:2026, metrics:[biz_income], subject:北京代表处}` |

---

## 1. 为什么需要它：三种追问形态

槽位存在的唯一理由，是中文追问有三种完全不同的语义，必须区分对待：

| 追问形态 | 例子 | 需要的语义 | 槽位操作 |
| --- | --- | --- | --- |
| **指代消解** | 「那北京呢」 | 主体替换，其余继承 | `subject` 覆盖，`time_range`/`metrics` 继承 |
| **条件叠加** | 「只看政企」 | 新增筛选，主体保留 | `filters` 按 dim **追加** |
| **条件切换** | 「改成运营商」 | 同维度替换，其余保留 | `filters` 按 dim **覆盖** |

注意「叠加」与「切换」的区别——**同一个字段要同时支持两种语义**，这正是 `merge` 里 `filters` 用「按 dim 覆盖」而非整体替换的原因：

```91:101:server/tests/test_multiturn.py
def test_merge_filters_replaced_by_dim():
    prev = Slots(filters=[{"dim": "industry_cat", "op": "=", "value": "政企"}])
    cur = Slots(filters=[{"dim": "industry_cat", "op": "=", "value": "运营商"}])
    out = merge(prev, cur)
    assert out.filters == [{"dim": "industry_cat", "op": "=", "value": "运营商"}]


def test_merge_filters_accumulate():
    prev = Slots(filters=[{"dim": "industry_cat", "op": "=", "value": "政企"}])
    cur = Slots(filters=[{"dim": "unit", "op": "=", "value": "上海代表处"}])
    assert len(merge(prev, cur).filters) == 2
```

- 同 dim（`industry_cat`）→ 新值替换旧值（**切换**）
- 异 dim（`industry_cat` + `unit`）→ 两个条件共存（**叠加**）

---

## 2. 结构：9 个字段逐一拆解

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

| 字段 | 含义 | 抽取来源 | 举例 |
| --- | --- | --- | --- |
| `metrics` | 要算什么指标 | `sem_metric` 的 `name`/`code`/`aliases` | 「营收」→ `["biz_income"]` |
| `dimensions` | 按什么维度分组 | `sem_dimension` 的 `name`/`code`/`aliases` | 「各经营单元」→ `["unit"]` |
| `filters` | 筛选条件 | `sem_dimension.value_map` 取值命中 | 「政企」→ `[{dim:industry_cat,...}]` |
| `time_range` | 时间范围 | 7 条时间正则 `TIME_PATTERNS` | 「今年」→ `{type:year,value:2026}` |
| `compare` | 对比方式 | 同比/环比正则 | 「同比呢」→ `"yoy"` |
| `order` | 排序 | 最多/最高/排名→desc；最少/最低→asc | 「排名」→ `{by:biz_income,dir:desc}` |
| `limit` | 返回条数 | `TOP N` / `前 N 个` 正则 | 「前10」→ `10` |
| `subject` | 分析主体 | 取值命中 + **主体维度白名单** | 「北京」→ `"北京代表处"` |
| `chart_hint` | 图表建议 | **无来源，从未被赋值** | 恒为 `None` |

### 2.1 `subject` 与 `filters` 的分离：最容易误解的一处

同样命中了一个维度取值，为什么有的进 `subject`（主体）、有的只进 `filters`（筛选）？

```26:27:server/app/agent/nodes/rewrite.py
# 只有这些维度的取值才被视作「分析主体」（其余维度取值只作筛选）
SUBJECT_DIM_CODES = frozenset({"unit", "region", "customer", "sales", "product_line"})
```

```131:141:server/app/agent/nodes/rewrite.py
    # 主体 / 筛选：命中维度取值表
    # 只有「主实体」维度（经营单元、区域、客户、销售）的取值才构成分析主体；
    # 行业、产品线等维度取值一律只作为筛选条件叠加，避免覆盖上文主体。
    for d in schema.dimensions:
        # 时间由 time_range 单独建模，再生成 year 筛选会与「2026年」重复
        if d["code"] == "year":
            continue
        for v in _match_values(q, _flatten_values(d.get("value_map"))):
            s.filters.append({"dim": d["code"], "op": "=", "value": v})
            if d["code"] in SUBJECT_DIM_CODES:
                s.subject = v
```

**规则**：`unit`/`region`/`customer`/`sales`/`product_line` 的取值才算「分析主体」；`industry_cat`（行业）、`product_type`（产品类型）等一律只作筛选。

这解决了一个真实问题：**「只看政企」不能顶掉上文的「北京代表处」**——否则用户问完北京再问「只看政企」，主体被行业覆盖，语义完全错位。

### 2.2 `time_range` 单独建模，`year` 维度被跳过

```135:137:server/app/agent/nodes/rewrite.py
        # 时间由 time_range 单独建模，再生成 year 筛选会与「2026年」重复
        if d["code"] == "year":
            continue
```

若同时生成 `time_range={year:2026}` 和 `filters=[{dim:year,value:2026}]`，Prompt 里会出现重复的年份约束。时间单独建模，避免与筛选打架。

### 2.3 `chart_hint` 是死字段

`extract_slots` 从未给 `chart_hint` 赋值，它只在 `merge` 里被搬运：

```77:77:server/app/agent/slots.py
    out.chart_hint = cur.chart_hint or prev.chart_hint
```

即「恒为 None 且永远继承 None」。图表推荐实际由 `chart_advisor` 的确定性规则完成（见 `docs/09` §5.3），`chart_hint` 是**规划阶段留下、最终未启用的字段**。

---

## 3. 生命周期：一次问数中槽位流经的 8 个站点

```
  Redis  ctx:{session_id}
    │
  ① 载入    ctx_store.load(session_id)        runtime.py:103
    │          └─ reset_context → Slots()     runtime.py:113
    ▼
  prev = ctx.active_slots ─────────────┐
    │                                  │
  ② 抽取    extract_slots(question)    rewrite.py:145   ← 依赖语义层
    │                                  │
    ▼                                  │
  cur = Slots ─────────────────────────┤
                                       ▼
  ③ 合并    merge(prev, cur)           slots.py:139
    │
    ├──④ 判空   全空 → need_clarify    rewrite.py:390
    │
    ├──⑤ 改写   build_question(merged)  rewrite.py:286（续问分支①）
    │           _inherited_hint(...)    rewrite.py:422（分支②③）
    │
    ▼
  ⑥ 注入    to_prompt_hint(merged) → Prompt   runtime.py:228
    │
    ▼
  ⑦ 回写    ctx.active_slots = rw.merged      runtime.py:366
    │
    ▼
  Redis（TTL 7200s）→ 下一轮
```

外加一个旁路：**缓存命中时不走 ②~⑤，但仍要补槽位**（`chat_service.py:262` → `_sync_context_after_cache`），否则下一轮追问丢失本轮条件。

---

## 4. 抽取：`extract_slots` 如何从语义层「读出」槽位

```113:116:server/app/agent/nodes/rewrite.py
def extract_slots(
    question: str, schema: SchemaContext, default_year: int = 2026
) -> Slots:
    """从问题中抽取槽位（基于语义层的别名与取值表）。"""
```

**这是槽位机制与语义层的唯一接口**——抽取完全不碰数据库表结构，只认 `SchemaContext`（已裁剪过的语义层子集）。

### 4.1 指标与维度：靠 `aliases` 做字面包含匹配

```120:129:server/app/agent/nodes/rewrite.py
    # 指标（含别名）
    for m in schema.metrics:
        names = [m["name"], m["code"], *(m.get("aliases") or [])]
        if any(n and n in q for n in names):
            s.metrics.append(m["code"])
    # 维度（含别名）
    for d in schema.dimensions:
        names = [d["name"], d["code"], *(d.get("aliases") or [])]
        if any(n and n in q for n in names):
            s.dimensions.append(d["code"])
```

「营收」「签约额」「销售额」都是 `biz_income` 的别名，命中任一即归一。**别名表由运营在语义层管理页维护**——槽位抽取能力直接由语义层驱动，加一个同义词不需要改代码。

### 4.2 取值匹配：简称归一化 + 最长优先

用户说「北京」，库里是「北京代表处」：

```86:92:server/app/agent/nodes/rewrite.py
def _alias_keys(value: str) -> list[str]:
    """为一个取值生成可用于匹配的别名（含去掉机构后缀的简称）。"""
    keys = [value]
    short = ORG_SUFFIXES.sub("", value)
    if len(short) >= 2 and short != value:
        keys.append(short)
    return keys
```

```68:69:server/app/agent/nodes/rewrite.py
# 机构后缀：用户常把「北京代表处」说成「北京」
ORG_SUFFIXES = re.compile(r"(代表处|办事处|系统部|分公司|子公司|有限公司|公司|中心|事业部|部门)$")
```

匹配结果**按命中键长度降序**，保证「北京」优先匹配「北京代表处」而非其它更短取值：

```103:110:server/app/agent/nodes/rewrite.py
    # 去重后按「命中的键长度」降序，保证「北京」优先匹配到「北京代表处」而非更短的其它取值
    seen: set[str] = set()
    out: list[str] = []
    for _, v in sorted(hits, key=lambda x: -x[0]):
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out
```

`value_map` 还支持嵌套结构（`unit` 按区域分组：`{"华北":["北京代表处",...],"华东":[...]}`），由 `_flatten_values()` 统一摊平。

### 4.3 时间：7 条正则 + `default_year` 基准

```31:39:server/app/agent/nodes/rewrite.py
TIME_PATTERNS = (
    (re.compile(r"(20\d{2})\s*年"), "year"),
    (re.compile(r"今年|本年"), "this_year"),
    (re.compile(r"去年|上一年"), "last_year"),
    (re.compile(r"前年"), "year_before_last"),
    (re.compile(r"上季度"), "last_quarter"),
    (re.compile(r"本季度|这季度"), "this_quarter"),
    (re.compile(r"(?:近|前|过去)\s*(\d+)\s*个?\s*月"), "last_months"),
)
```

「今年」不是硬编码，而是 `default_year`（配置项 `settings.DEFAULT_YEAR`）——**换一年数据只要改一个配置**。

### 4.4 三个踩过的坑

**坑 1：时间跨度里的数字被当成条数**（有回归测试专门守着）

```41:47:server/app/agent/nodes/rewrite.py
# 时间跨度片段，如「最近3个月」「前6个月」「过去2年」。
# 抽条数时必须先从问题中剔除：否则「最近 3 个月」里的 3 会被当成返回条数，
# 给聚合 SQL 加上 LIMIT 3，把分组结果截断 ——
# 表现为「总收入」与「拆维度后合计」两个数字对不上。
TIME_SPAN_RE = re.compile(
    r"(?:最近|近|前|过去|上)\s*\d+\s*(?:个\s*)?(?:月|年|季|季度|周|天|日)"
)
```

```170:181:server/app/agent/nodes/rewrite.py
    # 条数：时间已由 time_range 单独建模，先剔除时间片段再抽，
    # 避免「最近 3 个月」的 3 被误当成条数（见 TIME_SPAN_RE 注释）。
    q_for_limit = TIME_SPAN_RE.sub("", q)
    for pat in LIMIT_PATTERNS:
        m = pat.search(q_for_limit)
```

对应的回归测试同时验证了正反两面——既要「最近 3 个月」不抽 limit，又要「前 3 个产品线」照常抽：

```174:198:server/tests/test_multiturn.py
# 回归：曾把「最近 3 个月」的 3 抽成 limit=3，给按月聚合的 SQL 加上 LIMIT 3，
# 砍掉最后一个月，导致「总收入」比「拆维度后合计」少一截。
@pytest.mark.parametrize("q", [
    "最近 3 个月的收入情况", "最近3个月收入", "前3个月的收入",
    "近6个月各代表处收入", "近12个月的收入",
])
def test_limit_not_taken_from_time_span(q):
    """时间跨度里的数字只进 time_range，不能同时被当成返回条数。"""
    s = extract_slots(q, SCHEMA)
    assert s.time_range, f"{q} 应识别出时间范围"
    assert s.limit is None, f"{q} 不应抽出 limit，实际为 {s.limit}"


@pytest.mark.parametrize("q,limit", [
    ("收入最高的前3个产品线", 3), ("TOP5 代表处", 5), ("前5名代表处的收入", 5),
])
def test_limit_still_extracted_for_topn(q, limit):
    """真正的 TOP N 语义不受影响。"""
    assert extract_slots(q, SCHEMA).limit == limit
```

正则里也有同样的防守——「3个产品线」算条数，「3个月」不算：

```49:54:server/app/agent/nodes/rewrite.py
LIMIT_PATTERNS = (
    re.compile(r"(?:TOP|top)\s*(\d+)"),
    re.compile(r"前\s*(\d+)\s*(?:个|名|条)?"),
    # 「3个产品线」算条数，「3个月」不算
    re.compile(r"(\d+)\s*个(?!\s*(?:月|年|季|季度|周|天|日))"),
)
```

**坑 2：槽位抽错的表现是「数字对不上」而不是报错**，所以必须留全量日志：

```192:199:server/app/agent/nodes/rewrite.py
    # 槽位是多轮对话与 SQL 生成的唯一输入，抽错一个字段就会表现为「数字对不上」。
    # 这里必须把完整结果留在日志里——排查时先看 limit 是否被时间跨度污染。
    log_kv(
        logger, logging.DEBUG, "槽位抽取完成",
        question=q, metrics=s.metrics, dimensions=s.dimensions,
        time_range=s.time_range, limit=s.limit, compare=s.compare,
        subject=s.subject, filters=s.filters, order=s.order,
    )
```

**坑 3：抽取是纯字面匹配，无分词、无语义理解。** 「今年卖得怎么样」抽不到 `metrics`（没有「收入」二字）→ 槽位为空 → 触发澄清。这是槽位机制的**已知边界**，靠 `is_continuation` 的「≤6 字视为续问」部分缓解。

---

## 5. 合并：`merge` 的覆盖语义

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

    out.time_range = dict(cur.time_range) if cur.time_range else dict(prev.time_range)
    out.compare = cur.compare or prev.compare
    out.order = dict(cur.order) if cur.order else (dict(prev.order) if prev.order else None)
    out.limit = cur.limit if cur.limit is not None else prev.limit
    out.subject = cur.subject or prev.subject
    out.chart_hint = cur.chart_hint or prev.chart_hint
    return out
```

| 字段 | 合并规则 | 语义 |
| --- | --- | --- |
| `metrics` | `cur or prev`（**整体替换**） | 本轮说了指标就换掉，没说就继承 |
| `dimensions` | `cur or prev`（**整体替换**） | 同上 |
| `filters` | **按 dim 覆盖**（字典合并） | 同 dim 替换，异 dim 共存 |
| `time_range` | `cur or prev` | 切换年份 / 继承 |
| `compare` | `cur or prev` | 加同比 / 继承 |
| `order` | `cur or prev` | 改排序 / 继承 |
| `limit` | `cur is not None ? cur : prev` | 唯一用 `is not None` 判断的 |
| `subject` | `cur or prev` | 换主体 / 继承 |
| `chart_hint` | `cur or prev` | 恒 None |

**两个细节值得注意：**

**① `metrics`/`dimensions` 是整体替换，不是并集。** 若 Q1 说「收入」、Q2 说「回款」，合并结果是 `["biz_payment"]` 而非 `["biz_income","biz_payment"]`。这是刻意的设计（换指标是常见语义），但也意味着**无法表达「同时看收入和回款」**——除非用户在一句话里同时提到两个指标的别名。

**② `limit` 用 `is not None` 而其它用 truthy 判断。** 因为 `limit=0` 与 `limit=None` 语义不同（虽然当前正则不会产生 0）。这是合并函数里唯一做了显式 `None` 区分的字段。

---

## 6. 消费：槽位被用在 4 个地方

### 6.1 改写问题（分支①：续问 → 展开成完整句子）

```211:243:server/app/agent/nodes/rewrite.py
def build_question(slots: Slots, schema: SchemaContext) -> str:
    """把槽位还原成一句完整的中文问题。"""
    metric_names = {m["code"]: m["name"] for m in schema.metrics}
    dim_names = {d["code"]: d["name"] for d in schema.dimensions}

    parts: list[str] = []
    t = slots.time_range or {}
    if t.get("type") == "year":
        parts.append(f"{t.get('value')}年")
    elif t.get("type") == "quarter":
        parts.append(str(t.get("value")))
    elif t.get("type") == "last_months":
        parts.append(f"近{t.get('value')}个月")

    if slots.subject:
        parts.append(slots.subject)
    ...
```

「那北京呢」→「2026年北京代表处商业收入按经营单元统计，按指标降序排名」。**槽位回写成完整句子**是必要的，因为模型需要一句自足的话才能生成 SQL。

### 6.2 注入 Prompt 作为强制约束（分支②③）

```108:111:server/app/agent/slots.py
def to_prompt_hint(slots: Slots, *, metric_names: dict | None = None) -> str:
    """渲染成注入 Prompt 的上下文提示。"""
    text = describe(slots, metric_names=metric_names)
    return f"【继承的上文分析条件】{text}\n请把这些条件一并体现在 SQL 的 WHERE 中。" if text else ""
```

在 `sql_generate` 里，这段提示被放在**问题之后、语义层之前**：

```71:76:server/app/agent/nodes/sql_generate.py
    user_parts = [f"# 当前问题\n{question}"]
    if slot_hint:
        user_parts.append("")
        user_parts.append(slot_hint)
    user_parts.append("")
    user_parts.append(schema.render())
```

顺序是刻意的：**先说要查什么 → 再立上下文约束 → 最后给可用表达式**。

### 6.3 触发澄清（槽位全空）

```276:289:server/app/agent/nodes/rewrite.py
    # 完全没有分析对象，且也没有历史可继承 → 请求澄清（给出指标 + 示例问题）
    if merged.is_empty():
        options = [m["name"] for m in schema.metrics[:4]]
        options += [d["name"] for d in schema.dimensions[:2]]
        options.append("2026年各经营单元收入排名")
```

**候选选项直接来自语义层**——取前 4 个指标名 + 前 2 个维度名。语义层运营得好，澄清卡片就好用。

### 6.4 前端展示（`slots` 事件）

```213:214:server/app/agent/runtime.py
        if rw.rewritten != question:
            yield ev.slots_event(describe(rw.merged, metric_names={m["code"]: m["name"] for m in schema.metrics}), rw.merged.to_dict()), result
```

**只在发生改写时才推送**——没改写就说明用户的问题本身是自足的，展示「继承的条件」反而是噪音。

---

## 7. 存储：在哪里、多久、什么情况下不更新

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

- **存在 Redis**，key 为 `ctx:{session_id}`，TTL **7200 秒（2 小时）**。
- **不在数据库**——`chat_message.payload.slots` 只是当轮快照，用于回放展示，不参与下一轮继承。
- Redis 不可用时降级为**进程内字典**（`core/redis.py`），此时**多 worker 部署下会话上下文不共享**，追问可能取不到 `prev_slots`。

**三种不更新槽位的情况：**

| 场景 | 是否更新槽位 | 说明 |
| --- | --- | --- |
| 完整取数链路 | ✅ `ctx.active_slots = rw.merged` | `runtime.py:366` |
| `result_ops` 旁路（排序/换图） | ❌ 只 `ctx_store.save(ctx)` | `runtime.py:161`——改图表不改变分析条件，符合直觉 |
| 缓存命中 | ⚠️ 单独补 | `chat_service.py:262` → `_sync_context_after_cache` |

第三处最容易被忽略，注释里给了具体故障场景：

```258:262:server/app/services/chat_service.py
    # 命中缓存跳过了 Agent，但会话上下文必须照常维护。
    # 否则下一轮追问读到的 prev_slots 为空，会丢掉本轮的筛选条件——
    # 例如 Q1「高风险项目有哪些」命中缓存后，Q2「按产品线分拆看看」
    # 就丢失了 risk_level = '高'，变成统计全部项目。
    await _sync_context_after_cache(session.id, question, hit, first)
```

补的内容只有两样、且都不需要 LLM：`extract_slots()` 抽槽位 + 缓存结果集引用。并且**任何异常只告警不抛出**，不让上下文同步失败拖垮已经成功的问数。

---

## 8. 完整走查：三轮对话的槽位演变

以语义层种子数据（`005_seed_semantic.sql`）为准，走一遍真实的槽位流转。

### Q1：「2026年各经营单元收入排名」

`extract_slots` 抽取：

| 片段 | 命中 | 结果 |
| --- | --- | --- |
| 「收入」 | `biz_income.aliases` 含「收入」 | `metrics=["biz_income"]` |
| 「各经营单元」 | `unit.name` = 经营单元 | `dimensions=["unit"]` |
| 「2026年」 | `TIME_PATTERNS[0]` | `time_range={type:year,value:2026}` |
| 「排名」 | `re.search(r"最多\|最高\|排名\|TOP\|前\s*\d")` | `order={by:biz_income,dir:desc}` |

`prev` 为空 → `merged == cur`。
**分支判定**：`cur.metrics` 非空 → 走**分支②（自足问题）**，原样使用，无继承提示。

```
rewritten = "2026年各经营单元收入排名"（未改写，不推送 slots 事件）
```

### Q2：「那北京呢」

抽取：

| 片段 | 命中 | 结果 |
| --- | --- | --- |
| 「北京」 | `unit.value_map` → 「北京代表处」（简称归一化） | `filters=[{dim:unit,value:"北京代表处"}]` |
| — | `unit ∈ SUBJECT_DIM_CODES` | `subject="北京代表处"` |
| 其余 | 无指标/维度/时间词 | 均空 |

`merge(prev, cur)`：

| 字段 | prev | cur | merged | 来源 |
| --- | --- | --- | --- | --- |
| `metrics` | `[biz_income]` | `[]` | `[biz_income]` | 继承 |
| `dimensions` | `[unit]` | `[]` | `[unit]` | 继承 |
| `time_range` | `{year:2026}` | `{}` | `{year:2026}` | 继承 |
| `order` | `{desc}` | `None` | `{desc}` | 继承 |
| `filters` | `[]` | `[unit=北京代表处]` | `[unit=北京代表处]` | 本轮 |
| `subject` | `None` | `"北京代表处"` | `"北京代表处"` | 本轮 |

**分支判定**：`is_continuation("那北京呢")` → 含标记「那」→ **分支①（续问）**，用 `build_question(merged)` 展开：

```
rewritten = "2026年北京代表处商业收入按经营单元统计，按指标降序排名"
```

> 注：`dimensions=["unit"]` 被继承，所以展开句里带上了「按经营单元统计」。在主体已锁定为单个代表处时略显冗余——见 §10 改进建议第 2 条。

### Q3：「只看政企」

抽取：

| 片段 | 命中 | 结果 |
| --- | --- | --- |
| 「政企」 | `industry_cat.value_map` = `["政企","运营商","商业市场","渠道部"]` | `filters=[{dim:industry_cat,value:"政企"}]` |
| — | `industry_cat ∉ SUBJECT_DIM_CODES` | **不设 subject** ← 关键 |

`merge`：

| 字段 | prev | cur | merged | 语义 |
| --- | --- | --- | --- | --- |
| `subject` | `北京代表处` | `None` | `北京代表处` | **保留**（未被行业顶掉） |
| `filters` | `[unit=北京代表处]` | `[industry_cat=政企]` | **两条共存** | **条件叠加** |

**分支判定**：`is_continuation("只看政企")` —— 不含续问标记，但 `len("只看政企") == 4 ≤ 6` → **视为续问**，走分支①：

```
rewritten = "2026年北京代表处行业大类为政企商业收入按经营单元统计，按指标降序排名"
```

这一轮同时演示了槽位机制的三个核心价值：**主体保留**（北京没丢）、**条件叠加**（政企加进来）、**其余继承**（时间、指标、排序全在）。

### 三种追问形态的对照

| 追问 | `metrics` | `time_range` | `subject` | `filters` | 形态 |
| --- | --- | --- | --- | --- | --- |
| Q1 自足 | 本轮 | 本轮 | — | — | — |
| Q2「那北京呢」 | 继承 | 继承 | **覆盖** | 新增 unit | 指代消解 |
| Q3「只看政企」 | 继承 | 继承 | **保留** | **追加** industry_cat | 条件叠加 |
| Q3'「改成运营商」 | 继承 | 继承 | 保留 | **覆盖** industry_cat | 条件切换 |

---

## 9. 与语义层的依赖关系

**槽位抽取的准确率 100% 由语义层决定。** 三处硬依赖：

| 槽位字段 | 依赖的语义层字段 | 缺失后果 |
| --- | --- | --- |
| `metrics` | `sem_metric.name` / `code` / `aliases` | 用户说的同义词抽不到指标 → 澄清 |
| `dimensions` | `sem_dimension.name` / `code` / `aliases` | 分组维度识别不了 |
| `filters` / `subject` | `sem_dimension.value_map` | **指代消解完全失效** |

第三处的依赖最重，代码里专门写了注释：

```189:199:server/app/agent/nodes/retrieve.py
def _dim(d: SemDimension) -> dict:
    # value_map 是多轮改写抽取「主体 / 筛选」的依据，缺了它指代消解会完全失效
    return {
        ...
        "value_map": d.value_map,
    }
```

目前 `model`（产品型号）、`sales`（销售）、`customer`（客户）三个维度的 `value_map` 是 `NULL`——**问这些维度的具体取值时抽不到筛选条件**，只能靠模型从语义层展示表达式里推断。

还有一个反向依赖：语义层的维度定义会直接影响槽位质量。种子数据里有一处刻意的规避：

```129:132:server/scripts/sql/005_seed_semantic.sql
-- 以下三项由覆盖度体检补充（原语义层未暴露，模型只能靠猜）。
-- 别名刻意不含裸词「项目」：避免「高风险项目有哪些」等项目类问法被误判为按项目名分组。
(14, 'project_name', '项目名称', '["项目名称","项目名"]'::jsonb,
```

`project_name` 的别名**不含裸词「项目」**，否则「高风险项目有哪些」会命中 `dimensions=["project_name"]`，把「列出明细」误判成「按项目名分组」。这是**通过调整语义层来修正槽位抽取**的典型手法——改元数据比改代码便宜。

---

## 10. 边界与已知问题

### 10.1 `metrics`/`dimensions` 是整体替换，无法表达并列

`merge` 用的是 `cur.metrics or prev.metrics`，不是并集。用户无法分两轮说「看收入」「再看回款」然后得到两者并存的查询。

### 10.2 抽取是纯字面匹配，无语义理解

「今年卖得怎么样」不含任何指标别名词 → 槽位为空 → 触发澄清。缓解手段只有「≤6 字视为续问」这一条。

### 10.3 展开句会带上冗余的分组维度

Q2 的 `dimensions=["unit"]` 被继承，导致 `build_question` 产出「2026年北京代表处商业收入**按经营单元统计**」——主体已是单个代表处时，这个分组是冗余的。虽不影响 SQL 正确性（模型会看到 `subject` 和 `filters` 里的 `unit=北京代表处`），但展开句的可读性受影响。

### 10.4 `result_ops` 不更新槽位（符合直觉，但有个副作用）

```157:158:server/app/agent/runtime.py
            ctx.last_result_key = ctx_store.cache_result(self.session_id, new_payload)
            ctx_store.save(ctx)
```

改图表/排序时不更新 `active_slots` 是对的（分析条件没变）。但**「只看前 5 个」这类 `topn` 操作也不更新 `limit`**——若用户随后追问「同比呢」，`limit` 不会被继承，新查询会回到默认条数。

### 10.5 前端收了 `slots` 但没渲染

```270:271:web/src/pages/AiQa/index.tsx
        case 'slots':
          patchPayload(sid, { slots: data.slots ?? {} });
```

`slots` 事件被存进 `payload.slots`（`chatStore.ts:41`），但 `AiAnswerCard.tsx` **从未渲染它**——只渲染了 `p.rewritten`：

```73:79:web/src/pages/AiQa/messages/AiAnswerCard.tsx
      {p?.rewritten && p.rewritten !== message.content && (
        <div className={styles.context}>
          <Text type="secondary" style={{ fontSize: 13 }}>
            已结合上文理解为：{p.rewritten}
          </Text>
        </div>
      )}
```

即：后端精心计算的槽位结构，**前端只用了它的「改写后问句」这一个产物**。结构化槽位白算了一半——它本可以渲染成可点击的条件 chip（点掉「政企」即可去掉该筛选），这是槽位机制最有想象力的一个未兑现用途。

### 10.6 缓存命中时的槽位是「打折」的

`_sync_context_after_cache` 只调用 `extract_slots` + `merge`，**不经过 `rewrite` 的三个分支**。因此缓存命中的轮次不会触发澄清、不会做续问展开，槽位质量略低于正常链路。这是可接受的取舍（缓存命中本来就意味着问题已被完整理解过一次）。

### 10.7 Redis 降级时多 worker 不共享上下文

`core/redis.py` 降级为进程内字典后，多副本部署下 `prev_slots` 可能取到空值 → 表现为「追问丢了上下文」。这是部署层面的约束，需在架构上保证 Redis 可用。

---

## 11. 速查表

| 问题 | 答案 |
| --- | --- |
| 槽位定义在哪 | `server/app/agent/slots.py:27`（`class Slots`） |
| 怎么抽取 | `rewrite.py:145 extract_slots()` —— 基于语义层 aliases / value_map |
| 怎么合并 | `slots.py:139 merge()` —— 按字段覆盖；`filters` 按 `(dim, op)` 覆盖，`op="in"` 取**并集** |
| 存在哪 | Redis `ctx:{session_id}.active_slots`，TTL 7200s（**不在数据库**） |
| 何时回写 | `runtime.py:366`（完整链路）；`chat_service.py:262`（缓存命中补） |
| 何时清空 | `runtime.py:113` `reset_context`（会话前两轮）；TTL 到期 |
| 怎么进 Prompt | `slots.py:211 to_prompt_hint()` → `sql_generate.py:103 slot_hint` |
| 怎么给前端 | `events.py:81 slots_event()`，**仅在发生改写时推送** |
| 前端用在哪 | `pages/AiQa/index.tsx:270` 存入 `payload.slots`（**当前未渲染**） |
| 主体 vs 筛选 | `SUBJECT_DIM_CODES`（`rewrite.py:38`）—— 5 个主实体维度才算主体 |
| 单元测试 | `tests/test_multiturn.py` 30 例（改写 8 / 合并 2 / 否定 6 / 消歧 4 / limit 回归 8 / 意图与 result_ops 8）；LLM 兜底见 `tests/test_slot_llm.py` 29 例 |

---

## 12. 一句话总结各模块的「槽位」

| 模块 | 语境下的「槽位」指什么 |
| --- | --- |
| **语义层** | 槽位抽取的**输入源**——`aliases` 决定指标/维度能否命中，`value_map` 决定取值能否识别 |
| **`slots.py`** | 槽位的**数据结构 + 合并规则**（`Slots` / `merge` / `describe` / `to_prompt_hint`） |
| **`rewrite.py`** | 槽位的**抽取与消费**——`extract_slots` 产出，`build_question` / `_inherited_hint` 消费 |
| **`context.py`** | 槽位的**持久化载体**——`SessionContext.active_slots` |
| **`runtime.py`** | 槽位的**调度**——载入 → 改写 → 注入 Prompt → 回写 |
| **`events.py`** | 槽位的**传输格式**——`slots_event(text, slots)` |
| **`chatStore.ts`** | 槽位的**前端存储**——`payload.slots`（当前未渲染） |

**本质上，槽位就是「对话状态机」的状态，而这个状态机是手写的、显式的、可单测的**——这是它区别于「把历史消息拼进 Prompt」的根本所在。


