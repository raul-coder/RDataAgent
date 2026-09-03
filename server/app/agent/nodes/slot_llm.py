"""槽位抽取的 LLM 兜底（技术方案 §4.2「规则优先，LLM 兜底」的实现）。

为什么需要这一层
────────────────
``rewrite.extract_slots`` 是**字面包含匹配**：快、确定、零成本、可单测，
但它覆盖不到下面几类问法（此时规则层要么抽空、要么抽错）：

  · 同义改写   「今年卖得怎么样」→ 不含任何指标别名，槽位全空 → 只能澄清
  · 否定       「不含政企」→ 仍匹配到「政企」，反而加上了一个反向的筛选
  · 取值歧义   「渠道部」既是经营单元也是行业（种子数据里两个 value_map 都有）
  · 同维度多值 「北京和上海」→ 抽出两条 unit 筛选，合并时互相覆盖
  · 数值区间   「收入 3000 万到 5000 万」→ 槽位结构根本无法表达

这些交给轻量模型补一次结构化抽取即可覆盖。

三条硬约束（决定了这层不会把系统搞坏）
────────────────────────────────────
1. **规则结果优先**：LLM 只补规则没抽到的字段，以及被判定为「可疑」的字段，
   绝不无条件覆盖规则已经高置信命中的结果；
2. **输出必须过语义层白名单**：编造的指标 code / 维度 code / 取值一律丢弃，
   LLM 拿不到「发明指标」的能力，它的选择空间被语义层框死；
3. **失败静默回落**：未开启 / 无可用远程模型 / 超时 / 解析失败 / 校验后为空，
   一律返回 None，调用方沿用规则结果——行为与改造前完全一致。

因此这层的定位是**增益**而非**依赖**：关掉 settings.SLOT_LLM_ENABLED，
整个问数链路的表现会退化到改造前的水平，但不会坏。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ...core.config import settings
from ...core.logging import get_logger, log_kv
from ...llm.provider import LLMMessage, extract_json
from ...llm.router import complete
from ..slots import Slots, flatten_values, has_negation, match_values
from .retrieve import SchemaContext

logger = get_logger(__name__)

# ── 可疑信号：命中即说明规则层的字面匹配可能不可靠 ──────────────────
# 数值区间 / 比较：槽位结构（只有 = / != / in）无法表达
RANGE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:万|亿|个|家|台)?\s*(?:到|至|~|-|—)\s*\d+(?:\.\d+)?")
COMPARE_RE = re.compile(r"(?:大于|超过|高于|多于|小于|低于|少于|以上|以下|至少|至多)\s*\d")

# 各字段对「理解了这个问题」的贡献度。指标最重要——它决定查什么数。
_FIELD_WEIGHTS = ("metrics", "dimensions", "subject", "filters")
_WEIGHTS = {"metrics": 0.45, "dimensions": 0.25, "subject": 0.20, "filters": 0.10}

_VALID_OPS = ("=", "!=", "in")

#: 模型输出中可能出现的所有槽位字段（用于判断「是否给了有效内容」）
_SLOT_KEYS = ("metrics", "dimensions", "filters", "subject",
              "time_range", "compare", "order", "limit")

#: 单个维度最多把多少个取值喂给模型（防止 value_map 过大撑爆 Prompt）
_MAX_VALUES_PER_DIM = 40

SYSTEM = """你是经营数据分析系统的「槽位抽取器」：把用户的中文问题转成结构化查询条件。

# 硬性约束
1. 只能使用下方列出的指标 code、维度 code 与维度取值，**禁止编造**。
   拿不准的字段留空（null / [] / {{}}）——宁可少填，也不要错填。
2. 用户没提到的条件不要补：例如没说时间就不要填 time_range。
3. 默认年份为 {default_year}；只有用户**明确**提到时间时才填 time_range。
4. 否定用 op="!="：如「不含政企」→ {{"dim":"industry_cat","op":"!=","value":"政企"}}。
5. 同一维度多个取值用 op="in"，value 为数组：
   如「北京和上海」→ {{"dim":"unit","op":"in","value":["北京代表处","上海代表处"]}}。
6. 数值区间 / 大小比较（如「收入 3000 万到 5000 万」）本结构**不表达**，
   filters 留空即可，不要臆造一个等值条件。
7. 主体（subject）填「问的是哪个经营单元 / 区域 / 客户 / 销售 / 产品线」的取值，
   必须是维度取值表里出现过的完整名称（如「北京代表处」而非「北京」）。
8. 只输出 JSON，不要任何解释文字。"""

OUTPUT_SPEC = """请输出严格 JSON：
{"metrics":["指标code"],"dimensions":["维度code"],
 "filters":[{"dim":"维度code","op":"=|!=|in","value":"取值或取值数组"}],
 "subject":"主体取值或null","time_range":{"type":"year|last_months|quarter","value":0}或null,
 "compare":"yoy|mom|qoq"或null,"order":{"by":"指标code","dir":"desc|asc"}或null,
 "limit":正整数或null}"""


@dataclass
class ExtractionAssessment:
    """规则层抽取结果的自我评估。"""

    confidence: float = 0.0
    issues: list[str] = field(default_factory=list)
    #: 规则层**拿不准**的字段。只有这些字段允许被 LLM 结果覆盖，
    #: 其余字段一律沿用规则结果（规则命中是高置信的字面匹配，不该被模型改坏）。
    suspicious: set[str] = field(default_factory=set)

    def needs_llm(self, threshold: Optional[float] = None) -> bool:
        """是否需要 LLM 兜底。

        两个触发条件任一成立即可：
          · 置信度低于阈值（规则层基本没抽到东西）
          · 存在可疑信号（抽到了，但可能是错的——否定 / 歧义 / 多值 / 区间）
        """
        th = settings.SLOT_LLM_THRESHOLD if threshold is None else threshold
        return bool(self.issues) or self.confidence < th


def assess(question: str, slots: Slots, schema: SchemaContext) -> ExtractionAssessment:
    """评估规则层抽取结果的可信度，并标出「可疑」字段。

    纯函数、无 IO、可直接单测——这是选「显式槽位」而非「拼历史消息」
    带来的可测试性收益。
    """
    q = question or ""
    issues: list[str] = []
    suspicious: set[str] = set()

    # 完全没抽到分析对象：规则层彻底失效，交给模型
    if not any((slots.metrics, slots.dimensions, slots.subject, slots.filters)):
        issues.append("未识别到指标/维度/主体")
        return ExtractionAssessment(0.0, issues, suspicious)

    confidence = min(1.0, sum(_WEIGHTS[f] for f in _FIELD_WEIGHTS if getattr(slots, f)))

    # 否定：规则层用「否定词紧邻取值」的位置规则判定，能覆盖「不含政企」这类
    # 常见写法，但覆盖不到「不要政企和运营商」（否定词只紧邻第一个取值）。
    # 因此仍标为可疑——模型能正确处理并列否定。
    if has_negation(q):
        issues.append("含否定词")
        suspicious |= {"filters", "subject"}
        confidence *= 0.5

    if RANGE_RE.search(q) or COMPARE_RE.search(q):
        issues.append("含数值区间/比较")
        suspicious |= {"filters"}
        confidence *= 0.6

    # 取值歧义：同一个取值同时命中多个维度（如「渠道部」既是经营单元又是行业）。
    # 规则层已把它收敛成一个维度（见 rewrite._resolve_ambiguity），但那是按优先级
    # 猜的——真正的消歧该由模型根据问句意图判断，因此标为可疑让模型覆盖。
    ambiguous = _ambiguous_values(q, schema)
    if ambiguous:
        issues.append("取值歧义：" + "、".join(f"{v}({'/'.join(sorted(d))})" for v, d in ambiguous.items()))
        suspicious |= {"filters", "subject"}
        confidence *= 0.6

    # 同维度多值：规则层会抽出多条同 dim 筛选，合并时互相覆盖
    multi = _multi_value_dims(slots)
    if multi:
        issues.append("同维度多值：" + "、".join(sorted(multi)))
        suspicious |= {"filters"}
        confidence *= 0.7

    return ExtractionAssessment(round(confidence, 3), issues, suspicious)


def _ambiguous_values(question: str, schema: SchemaContext) -> dict[str, set[str]]:
    """问句中同时命中多个维度的取值（说明存在歧义）。

    从问句文本检测而非从 slots 反推：规则层已把歧义收敛成单一维度，
    slots 里看不出「这个取值本来也可能指别的维度」。
    """
    by_value: dict[str, set[str]] = {}
    for d in schema.dimensions:
        if d["code"] == "year":     # 时间由 time_range 单独建模
            continue
        for v in match_values(question or "", flatten_values(d.get("value_map"))):
            by_value.setdefault(v, set()).add(str(d["code"]))
    return {v: dims for v, dims in by_value.items() if len(dims) > 1}


def _multi_value_dims(slots: Slots) -> set[str]:
    """同一维度上命中了多个取值。

    两种形态都要识别：
      · 多条同 dim 筛选（规则层合并前的形态）
      · 单条 op="in" 且 value 为多元素（规则层合并后的形态）

    为什么合并成 in 之后仍要标记：合并只是「不会互相覆盖」的兜底，
    不代表猜对了。例如「PPL中高风险机会」会把「中」也当成风险等级取值，
    得到 in[中, 高]——比只取「中」好，但仍需模型判断真实意图。
    """
    counts: dict[str, int] = {}
    for f in slots.filters:
        d = str(f.get("dim"))
        value = f.get("value")
        if str(f.get("op")) == "in" and isinstance(value, list):
            counts[d] = counts.get(d, 0) + len(value)
        else:
            counts[d] = counts.get(d, 0) + 1
    return {d for d, n in counts.items() if n > 1}


def combine(rule: Slots, llm: Slots, suspicious: set[str]) -> Slots:
    """合并规则结果与 LLM 结果。

    规则优先，LLM 只补两处：
      1. 规则没抽到的字段（空位）
      2. 被判定为「可疑」的字段（否定 / 歧义 / 多值 / 区间）

    其余字段即使 LLM 给了不同答案也沿用规则结果——规则是字面命中，
    不存在幻觉；让模型去改一个已经正确的结果只会引入不确定性。
    """
    out = Slots()

    def pick(name: str, rule_val: Any, llm_val: Any) -> Any:
        if name in suspicious and _filled(llm_val):
            return llm_val
        return rule_val if _filled(rule_val) else llm_val

    out.metrics = pick("metrics", list(rule.metrics), list(llm.metrics))
    out.dimensions = pick("dimensions", list(rule.dimensions), list(llm.dimensions))
    out.filters = pick("filters", list(rule.filters), list(llm.filters))
    out.subject = pick("subject", rule.subject, llm.subject)
    out.time_range = pick("time_range", dict(rule.time_range), dict(llm.time_range))
    out.compare = pick("compare", rule.compare, llm.compare)
    out.order = pick(
        "order",
        dict(rule.order) if rule.order else None,
        dict(llm.order) if llm.order else None,
    )
    out.limit = pick("limit", rule.limit, llm.limit)
    return out


def _filled(v: Any) -> bool:
    """字段是否有实质内容。注意 limit=0 也算没填（无意义）。"""
    if v is None:
        return False
    if isinstance(v, (list, dict)):
        return bool(v)
    return True


# ══════════════════════════════════════════════════════════════════
# LLM 抽取
# ══════════════════════════════════════════════════════════════════
async def extract_slots_llm(
    question: str,
    schema: SchemaContext,
    providers: Optional[list],
    default_year: int,
) -> Optional[Slots]:
    """调用轻量模型补一次结构化抽取；任何异常都返回 None（静默回落）。"""
    if not settings.SLOT_LLM_ENABLED:
        return None

    # 只认真实模型：检索式兜底 Provider（TemplateProvider）只会匹配 Few-shot
    # 返回 SQL，让它填槽位得到的是无意义文本。
    remote = [p for p in (providers or []) if getattr(p, "is_remote", False)]
    if not remote:
        return None

    messages = _build_messages(question, schema, default_year)
    try:
        resp = await asyncio.wait_for(
            complete(
                remote,
                messages,
                question=question,
                temperature=0.0,   # 抽取任务要确定性，不要采样
                json_mode=True,
                max_tokens=settings.SLOT_LLM_MAX_TOKENS,
                validate=validate_slot_output,
            ),
            timeout=settings.SLOT_LLM_TIMEOUT_MS / 1000,
        )
    except asyncio.TimeoutError:
        logger.warning("槽位兜底抽取超时（%dms），回落规则结果", settings.SLOT_LLM_TIMEOUT_MS)
        return None
    except Exception as exc:  # noqa: BLE001
        # 兜底层的兜底：模型挂了不该影响问数主链路
        logger.warning("槽位兜底抽取失败，回落规则结果：%s", exc)
        return None

    payload = extract_json(resp.content)
    if not payload:
        logger.warning("槽位兜底抽取返回非 JSON，回落规则结果：%s", (resp.content or "")[:200])
        return None

    slots = validate(payload, schema)
    if slots is None:
        # 两种「没帮上忙」必须分开记录，否则排障时会误判方向：
        #   · 模型返回全空   → 模型能力/提示问题，调阈值没用
        #   · 内容不在白名单 → 模型编造了指标/取值，安全网正常拦截
        declared = any(payload.get(k) for k in _SLOT_KEYS)
        log_kv(
            logger, logging.INFO,
            "槽位兜底抽取：模型返回空槽位，已丢弃"
            if not declared else "槽位兜底抽取：输出未通过白名单校验，已丢弃",
            question=question, payload=payload,
        )
        return None

    log_kv(
        logger, logging.DEBUG, "槽位兜底抽取完成",
        question=question, slots=slots.to_dict(),
        model=resp.model, tokens=resp.total_tokens,
    )
    return slots


def validate_slot_output(text: str) -> Optional[str]:
    """兜底抽取的输出校验：至少要是可解析的 JSON。

    只校验结构，不校验内容。内容层面的白名单校验交给下面的 ``validate``：
    那里失败意味着「模型给了结构化结果但内容不在语义层内」——
    换一个模型通常也无济于事，直接回落规则结果更合适；
    而这里失败意味着模型连格式都没遵守，应当换模型再试。
    """
    if extract_json(text) is None:
        return "无法解析为 JSON"
    return None


def validate(payload: dict, schema: SchemaContext) -> Optional[Slots]:
    """按语义层白名单校验模型输出，剔除编造内容。

    这是本模块的安全核心：**模型只能在语义层已有的 code 与取值里做选择**。
    校验后无任何有效字段返回 None，等价于「模型没帮上忙」。
    """
    metric_codes = {str(m["code"]) for m in schema.metrics}
    dim_by_code = {str(d["code"]): d for d in schema.dimensions}
    out = Slots()

    for c in payload.get("metrics") or []:
        c = str(c).strip()
        if c in metric_codes and c not in out.metrics:
            out.metrics.append(c)

    for c in payload.get("dimensions") or []:
        c = str(c).strip()
        if c in dim_by_code and c not in out.dimensions:
            out.dimensions.append(c)

    for f in payload.get("filters") or []:
        if not isinstance(f, dict):
            continue
        code = str(f.get("dim") or "").strip()
        op = str(f.get("op") or "=").strip()
        if code not in dim_by_code or op not in _VALID_OPS:
            continue
        value = _clean_value(f.get("value"), dim_by_code[code].get("value_map"), op)
        if value is None:
            continue
        out.filters.append({"dim": code, "op": op, "value": value})

    subject = payload.get("subject")
    if isinstance(subject, str) and subject.strip():
        if _subject_known(schema, subject.strip()):
            out.subject = subject.strip()

    tr = payload.get("time_range")
    if isinstance(tr, dict) and tr.get("type") in ("year", "last_months", "quarter"):
        value = _clean_time_value(tr.get("type"), tr.get("value"))
        if value is not None:
            out.time_range = {"type": tr["type"], "value": value}

    if payload.get("compare") in ("yoy", "mom", "qoq"):
        out.compare = payload["compare"]

    order = payload.get("order")
    if isinstance(order, dict):
        by = str(order.get("by") or "").strip()
        direction = str(order.get("dir") or "desc").strip().lower()
        if direction not in ("asc", "desc"):
            direction = "desc"
        if by in metric_codes or by == "value":
            out.order = {"by": by, "dir": direction}

    try:
        limit = int(payload.get("limit"))
    except (TypeError, ValueError):
        limit = 0
    if limit > 0:
        out.limit = min(limit, 1000)

    return out if not out.is_empty() else None


def _clean_value(value: Any, value_map: Any, op: str) -> Any:
    """校验并规整筛选取值；不合法返回 None。

    维度没有声明 value_map 时（如 product model、sales、customer），
    语义层本身给不出取值清单，此时放行用户原话——无法校验就不硬拦，
    宁可交给后续 SQL 生成环节去处理。
    """
    known = flatten_values(value_map)
    if op == "in":
        raw = value if isinstance(value, list) else ([value] if value is not None else [])
        items = [str(v).strip() for v in raw if str(v).strip()]
        if known:
            items = [v for v in items if v in known]
        return list(dict.fromkeys(items)) or None

    if isinstance(value, list):
        return None
    v = str(value).strip() if value is not None else ""
    if not v:
        return None
    if known and v not in known:
        return None
    return v


def _subject_known(schema: SchemaContext, value: str) -> bool:
    """主体取值是否出现在某个维度的取值表中。"""
    maps = [flatten_values(d.get("value_map")) for d in schema.dimensions]
    declared = [m for m in maps if m]
    if not declared:
        return True  # 语义层未提供任何取值表，无法校验，放行
    return any(value in m for m in declared)


def _clean_time_value(kind: str, value: Any) -> Any:
    if kind in ("year", "last_months"):
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        # 年度做合理区间校验，避免模型吐出 3026 这类年份
        if kind == "year":
            return n if 2000 <= n <= 2100 else None
        return n if 1 <= n <= 120 else None
    return str(value).strip() or None


def _build_messages(question: str, schema: SchemaContext, default_year: int) -> list[LLMMessage]:
    metric_lines = []
    for m in schema.metrics:
        alias = "、".join(m.get("aliases") or []) or "无"
        caliber = f"；{m['caliber']}" if m.get("caliber") else ""
        metric_lines.append(f"- {m['code']}｜{m['name']}｜别名：{alias}{caliber}")

    dim_lines = []
    for d in schema.dimensions:
        alias = "、".join(d.get("aliases") or []) or "无"
        values = flatten_values(d.get("value_map"))
        if values:
            shown = "、".join(values[:_MAX_VALUES_PER_DIM])
            more = f" 等 {len(values)} 个" if len(values) > _MAX_VALUES_PER_DIM else ""
            value_txt = f"；取值：{shown}{more}"
        else:
            value_txt = "；取值：未登记（请填用户原话）"
        dim_lines.append(f"- {d['code']}｜{d['name']}｜别名：{alias}{value_txt}")

    user = "\n".join([
        f"# 用户问题\n{question}",
        "",
        "# 可用指标（只能用 code）",
        *(metric_lines or ["（无）"]),
        "",
        "# 可用维度（只能用 code 与登记过的取值）",
        *(dim_lines or ["（无）"]),
        "",
        OUTPUT_SPEC,
    ])
    return [
        LLMMessage(role="system", content=SYSTEM.format(default_year=default_year)),
        LLMMessage(role="user", content=user),
    ]
