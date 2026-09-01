"""系统数据生成：用户 / 角色 / 菜单 / 权限 / 应用配置 / 模型 / 日志 / 预置会话 / 反馈。

时间戳基于固定基准时间倒推，保证可重复（不使用 now()）。
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, List, Tuple

from .config import (
    DATA_AS_OF,
    DEFAULT_PASSWORD,
    DEMO_DATA_PERM_UNITS,
    OPERATIONS,
    PBKDF2_ITERATIONS,
    PRESET_PWD_MUST_CHANGE,
    ROLES,
    USERS,
)
from .password import hash_password
from .rng import RNG

BASE_TIME = dt.datetime(2026, 8, 30, 10, 0, 0)

ALL_OPS = [code for code, _ in OPERATIONS]

# 菜单树：(id, parent_id, name, path, type, perm_code, sort)
# type: M=目录 C=菜单
MENUS = (
    (1, 0, "智能问数", "/ai-qa", "C", "ai:qa", 1),
    (2, 0, "系统管理", "", "M", "", 2),
    (3, 2, "应用配置", "/system/app-config", "C", "sys:config:view", 1),
    (4, 2, "模型配置", "/system/model-config", "C", "sys:model:view", 2),
    (5, 2, "用户管理", "/system/users", "C", "sys:user:view", 3),
    (6, 2, "角色管理", "/system/roles", "C", "sys:role:view", 4),
    (7, 2, "菜单管理", "/system/menus", "C", "sys:menu:view", 5),
    (8, 2, "权限配置", "/system/permissions", "C", "sys:perm:view", 6),
    (9, 2, "操作日志", "/system/logs", "C", "sys:log:view", 7),
    (10, 0, "反馈管理", "", "M", "", 3),
    (11, 10, "回复校对", "/feedback/review", "C", "fb:review:view", 1),
    (12, 0, "数据台账", "", "M", "", 4),
    (13, 12, "商业市场台账", "/ledger/commercial", "C", "lg:commercial:view", 1),
    (14, 12, "PPL明细台账", "/ledger/ppl", "C", "lg:ppl:view", 2),
    (15, 12, "整体目标台账", "/ledger/goal", "C", "lg:goal:view", 3),
    (16, 0, "语义层管理", "", "M", "", 5),
    (17, 16, "指标管理", "/semantic/metrics", "C", "sem:metric:view", 1),
    (18, 16, "维度管理", "/semantic/dimensions", "C", "sem:dimension:view", 2),
    (19, 16, "口径规则", "/semantic/rules", "C", "sem:rule:view", 3),
    (20, 16, "样本管理", "/semantic/fewshots", "C", "sem:fewshot:view", 4),
)

# 角色 → 可见菜单（含目录）→ 操作权限
ROLE_MENUS: Dict[str, Dict[int, List[str]]] = {
    "SUPER_ADMIN": {m[0]: ALL_OPS for m in MENUS},
    "ADMIN": {
        m[0]: ALL_OPS
        for m in MENUS
        if m[0] <= 15
    },
    "NORMAL": {
        1: ["view", "export", "query", "filter", "refresh"],
        12: ["view"],
        13: ["view", "export", "filter", "query"],
        14: ["view", "export", "filter", "query"],
        15: ["view", "export", "filter", "query"],
    },
    "DATA_VIEWER": {
        1: ["view", "query", "filter", "refresh"],
        12: ["view"],
        13: ["view", "filter", "query"],
        14: ["view", "filter", "query"],
        15: ["view", "filter", "query"],
    },
    "AUDITOR": {
        1: ["view"],
        2: ["view"],
        9: ["view", "export"],
        10: ["view"],
        11: ["view"],
    },
}

# 需要施加数据权限（经营单元可见范围）的菜单
DATA_PERM_MENUS = (1, 13, 14, 15)

APP_CONFIG = (
    ("greeting", True),
    ("suggestions", True),
    ("tts", False),
    ("stt", False),
    ("hotRecommend", True),
    ("modelConfig", True),
    ("greetingText", "欢迎使用智能AI问数，您可以向我咨询经营数据、报表分析相关问题。"),
    ("hotThreshold", 3),
)

DEFAULT_MODELS = (
    ("GPT-4o", "openai", "https://api.openai.com/v1", "gpt-4o"),
    ("GPT-4o-mini", "openai", "https://api.openai.com/v1", "gpt-4o-mini"),
    ("Claude 3.5 Sonnet", "anthropic", "https://api.anthropic.com/v1", "claude-3-5-sonnet-20241022"),
    ("DeepSeek-V3", "deepseek", "https://api.deepseek.com/v1", "deepseek-chat"),
    ("Qwen2.5-72B", "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen2.5-72b-instruct"),
    ("GLM-4-Plus", "glm", "https://open.bigmodel.cn/api/paas/v4", "glm-4-plus"),
    ("Moonshot-v1", "moonshot", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
)

RECOMMEND_QUESTIONS = (
    "2026年各经营单元收入排名",
    "政企行业收入3000万-5000万数据",
    "产品线同比趋势分析",
    "高风险项目有哪些",
    "北京代表处今年达成情况",
    "各产品线收入占比饼图",
)
RECENT_QUESTIONS = ("政企行业收入3000万-5000万数据", "北京代表处今年达成情况")

OPER_LOG_ACTIONS = (
    "用户登录系统",
    "修改应用配置-开场白文案",
    "导出报表-经营单元达成",
    "修改用户权限-角色管理",
    "导入台账-PPL明细",
    "删除历史会话记录",
    "清空对话记录",
    "修改模型配置-智能问数模型",
    "导出报表-行业达成",
    "查看问数日志",
    "处理数据反馈-回复校对",
)
OPER_LOG_IPS = ("192.168.1.100", "192.168.1.101", "10.0.0.5", "10.0.0.6", "10.0.0.7")


def _ts(minutes_ago: int = 0, days_ago: int = 0) -> str:
    t = BASE_TIME - dt.timedelta(minutes=minutes_ago, days=days_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _wan(cents: int) -> str:
    v = int(cents)
    return f"{v // 100000:.2f}" if v >= 100000 else f"{v / 100:.2f}"


# ── 预置会话：问题 → 依据真实造数结果生成的 Markdown 回答 ───────────────
def _build_preset_sessions(plan: Dict[Tuple[str, int], Dict[str, Any]], ctx: Dict[str, Any]):
    """构造 8 条预置会话（标题与 Demo 的 _qaSessions 保持一致）。"""
    unit_by_code = ctx["unit_by_code"]
    rows_2026 = [
        (unit_by_code[c]["name"], v["income_cents"], v["biz_goal_cents"], v["achieve_rate"])
        for (c, y), v in plan.items()
        if y == 2026
    ]
    rows_2026.sort(key=lambda r: -r[1])

    def fmt_income(c):
        return f"{c / 100:,.0f}"

    top5 = rows_2026[:5]
    ranking_lines = "\n".join(
        f"{i}. **{n}**：{fmt_income(inc)} 万元，完成率 {r * 100:.1f}%"
        for i, (n, inc, g, r) in enumerate(top5, start=1)
    )
    bottom = rows_2026[-3:]
    warning_lines = "\n".join(
        f"- **{n}**：完成率 {r * 100:.1f}%，缺口 {fmt_income(max(0, g - inc))} 万元"
        for n, inc, g, r in bottom
    )
    bj = next((r for r in rows_2026 if r[0] == "北京代表处"), None)
    jx = next((r for r in rows_2026 if r[0] == "江西办事处"), None)
    total_income = sum(r[1] for r in rows_2026)
    total_goal = sum(r[2] for r in rows_2026)

    sessions = [
        (
            "经营单元收入&完成率分析",
            "2026年各经营单元收入&完成率分析",
            f"2026 年（数据截止 {DATA_AS_OF}）各经营单元收入排名如下：\n\n{ranking_lines}\n\n"
            f"整体：收入 {fmt_income(total_income)} 万元 / 目标 {fmt_income(total_goal)} 万元，"
            f"整体完成率 **{total_income / total_goal * 100:.1f}%**。",
            "有用",
        ),
        (
            "政企行业收入筛选",
            "帮我筛选政企行业收入在3000万到5000万之间的经营单元",
            "在「行业大类 = 政企」且收入区间 3,000 万 ~ 5,000 万元条件下，命中 6 个经营单元：\n\n"
            "| 经营单元 | 收入(万元) | 占比 |\n| --- | --- | --- |\n"
            "| 山东代表处 | 4,182 | 21.4% |\n| 湖北办事处 | 3,905 | 20.0% |\n"
            "| 福建代表处 | 3,641 | 18.6% |\n| 安徽办事处 | 3,517 | 18.0% |\n"
            "| 陕西办事处 | 3,264 | 16.7% |\n| 渠道部 | 3,102 | 15.9% |\n\n"
            "合计 21,611 万元，占政企行业总收入的 68.2%。",
            "有用",
        ),
        (
            "产品型号销售统计",
            "2026年销售最多的3个产品型号",
            "2026 年销量（台套数）排名前 3 的产品型号：\n\n"
            "1. **R5300 G5**：12,480 台，收入 9,984 万元\n"
            "2. **R5500 G5**：9,132 台，收入 8,219 万元\n"
            "3. **R2200 G5**：7,905 台，收入 4,743 万元\n\n"
            "三者合计占全年台套数的 58.4%，是主力出货型号。",
            "有用",
        ),
        (
            "产品线同比趋势分析",
            "2026年各产品线同比趋势分析",
            "2026 年 vs 2025 年各产品线收入同比：\n\n"
            "| 产品线 | 2025(万元) | 2026(万元) | 同比 |\n| --- | --- | --- | --- |\n"
            "| 通用计算 | 58,320 | 62,510 | +7.2% |\n"
            "| 智能计算 | 30,240 | 39,070 | +29.2% |\n"
            "| 商业解决方案 | 19,440 | 28,620 | +47.2% |\n\n"
            "**结论**：智能计算与商业解决方案是主要增长引擎，通用计算增速放缓。",
            "很满意",
        ),
        (
            "月度合同金额趋势",
            "2026年每月的合同金额趋势",
            "2026 年合同金额呈明显的「前低后高」季节性：\n\n"
            "- Q1：21,880 万元（占 19.9%）\n- Q2：26,540 万元（占 24.1%）\n"
            "- Q3：27,910 万元（占 25.4%）\n- Q4：33,760 万元（占 30.6%）\n\n"
            "峰值出现在 11 月（12,140 万元），谷值在 2 月（5,320 万元）。",
            "",
        ),
        (
            "高风险项目统计",
            "目前有多少高风险项目",
            "当前高风险项目共 **1,286 个**，涉及合同金额 42,318 万元，占全年收入的 18.2%。\n\n"
            "分布特征：\n- 金额 > 500 万元的项目占高风险总数的 63%\n"
            "- 落地时间集中在 Q4 的项目占 47%（年末冲刺导致交付风险上升）\n"
            "- 涉及经营单元 TOP3：北京代表处、上海代表处、浙江代表处",
            "有用",
        ),
        (
            "北京代表处达成",
            "北京代表处今年达成情况",
            (
                f"北京代表处 2026 年：收入 **{fmt_income(bj[1])} 万元** / "
                f"目标 {fmt_income(bj[2])} 万元，完成率 **{bj[3] * 100:.1f}%**，超额达成。\n\n"
                "拆解：\n- 通用计算 6,120 万元（38.4%）\n- 智能计算 5,480 万元（34.4%）\n"
                "- 商业解决方案 4,340 万元（27.2%）\n\n"
                "建议在 Q4 维持智能计算的投入节奏，商解目标还有提升空间。"
                if bj
                else "暂无北京代表处数据。"
            ),
            "",
        ),
        (
            "江西办事处预警",
            "江西办事处有什么经营预警",
            (
                f"江西办事处 2026 年触发 **低达成预警**：完成率 {jx[3] * 100:.1f}%，"
                f"收入 {fmt_income(jx[1])} 万元，目标 {fmt_income(jx[2])} 万元，"
                f"缺口 {fmt_income(max(0, jx[2] - jx[1]))} 万元。\n\n"
                "预警项：\n1. 完成率低于 60% 红线\n2. Q4 在途商机金额不足，回补难度大\n"
                "3. 高风险项目占比 21.4%，高于均值\n\n建议：盘点在途商机、加快交付确认。"
                if jx
                else "暂无江西办事处数据。"
            ),
            "有用",
        ),
    ]
    return sessions


def generate_system(
    w: Any,
    rng: RNG,
    ctx: Dict[str, Any],
    plan: Dict[Tuple[str, int], Dict[str, Any]],
    scale: float = 1.0,
) -> Dict[str, Any]:
    pwd_hash = hash_password(DEFAULT_PASSWORD, PBKDF2_ITERATIONS, salt=b"jingguan_demo_sl")

    # ── 角色 ────────────────────────────────────────────────────────
    w.table(
        "sys_role",
        ["id", "code", "name", "description", "is_builtin", "created_at", "updated_at"],
    )
    role_id_by_code = {}
    for i, (code, name, desc, builtin) in enumerate(ROLES, start=1):
        role_id_by_code[code] = i
        w.row("sys_role", [i, code, name, desc, "true" if builtin else "false", _ts(600), _ts(60)])

    # ── 用户 ────────────────────────────────────────────────────────
    w.table(
        "sys_user",
        ["id", "username", "password_hash", "nickname", "phone", "email", "avatar",
         "status", "valid_until", "last_login_at", "last_login_ip", "pwd_must_change",
         "created_at", "updated_at", "deleted_at"],
    )
    w.table("sys_user_role", ["user_id", "role_id"])
    user_id_by_name = {}
    for i, (username, nickname, phone, role_code, valid_until, enabled) in enumerate(USERS, start=1):
        user_id_by_name[username] = i
        w.row(
            "sys_user",
            [
                i, username, pwd_hash, nickname, phone, f"{username}@jingguan.com", "",
                1 if enabled else 0, valid_until,
                _ts(i * 37) if enabled else "",
                OPER_LOG_IPS[i % len(OPER_LOG_IPS)] if enabled else "",
                "true" if PRESET_PWD_MUST_CHANGE else "false",
                _ts(600 + i * 5), _ts(60 + i), "",
            ],
        )
        w.row("sys_user_role", [i, role_id_by_code[role_code]])

    # ── 菜单 ────────────────────────────────────────────────────────
    w.table(
        "sys_menu",
        ["id", "parent_id", "name", "path", "component", "icon", "sort_order",
         "type", "perm_code", "visible", "created_at"],
    )
    icons = {
        1: "robot", 2: "setting", 3: "control", 4: "cube", 5: "user", 6: "team",
        7: "menu", 8: "safety-certificate", 9: "file-text", 10: "flag",
        11: "exclamation-circle", 12: "database", 13: "table", 14: "table",
        15: "table", 16: "book", 17: "bar-chart", 18: "appstore", 19: "profile",
        20: "experiment",
    }
    for mid, parent, name, path, mtype, perm, sort in MENUS:
        w.row(
            "sys_menu",
            [mid, parent, name, path, path, icons.get(mid, ""), sort, mtype, perm, "true", _ts(700)],
        )

    # ── 角色-菜单（操作权限）───────────────────────────────────────
    w.table("sys_role_menu", ["role_id", "menu_id", "ops"])
    for code, rid in role_id_by_code.items():
        for mid, ops in sorted(ROLE_MENUS[code].items()):
            w.row("sys_role_menu", [rid, mid, json.dumps(ops, ensure_ascii=False)])

    # ── 数据权限（普通用户仅可见 2 个经营单元，用于演示权限隔离）───
    w.table("sys_role_data_perm", ["id", "role_id", "menu_id", "perm_type", "unit_codes"])
    dp_id = 0
    for code, rid in role_id_by_code.items():
        for mid in DATA_PERM_MENUS:
            if mid not in ROLE_MENUS[code]:
                continue
            units = list(DEMO_DATA_PERM_UNITS) if code == "NORMAL" else []
            for perm_type in ("view", "operate", "delete"):
                dp_id += 1
                w.row(
                    "sys_role_data_perm",
                    [dp_id, rid, mid, perm_type, json.dumps(units, ensure_ascii=False)],
                )

    # ── 应用配置 ────────────────────────────────────────────────────
    w.table("sys_app_config", ["config_key", "config_value", "updated_by", "updated_at"])
    for key, val in APP_CONFIG:
        w.row("sys_app_config", [key, json.dumps(val, ensure_ascii=False), 1, _ts(120)])

    # ── 模型 ────────────────────────────────────────────────────────
    w.table(
        "sys_model",
        ["id", "name", "provider", "base_url", "model_name", "api_key_enc", "scene",
         "is_default", "enabled", "params", "created_at"],
    )
    for i, (name, provider, base_url, model_name) in enumerate(DEFAULT_MODELS, start=1):
        w.row(
            "sys_model",
            [
                i, name, provider, base_url, model_name, "", "chat_qa",
                "true" if model_name == "deepseek-chat" else "false",
                "true",
                json.dumps({"temperature": 0.1, "top_p": 0.9, "max_tokens": 4096}),
                _ts(700),
            ],
        )

    # ── 操作日志 ────────────────────────────────────────────────────
    w.table(
        "sys_oper_log",
        ["id", "user_id", "username", "log_type", "action", "method", "ip",
         "user_agent", "status", "cost_ms", "created_at"],
    )
    log_total = int(round(500 * scale))
    usernames = [u[0] for u in USERS]
    for i in range(1, log_total + 1):
        username = usernames[rng.randint(0, len(usernames) - 1)]
        is_login = rng.bernoulli(0.45)
        action = "用户登录系统" if is_login else OPER_LOG_ACTIONS[rng.randint(1, len(OPER_LOG_ACTIONS) - 1)]
        if is_login and rng.bernoulli(0.08):
            status = "失败-密码错误"
        elif rng.bernoulli(0.04):
            status = "部分成功"
        else:
            status = "成功"
        w.row(
            "sys_oper_log",
            [
                i,
                user_id_by_name[username],
                username,
                "login" if is_login else "oper",
                action,
                "POST /api/v1/auth/login" if is_login else "POST /api/v1/oper",
                OPER_LOG_IPS[rng.randint(0, len(OPER_LOG_IPS) - 1)],
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                status,
                rng.randint(20, 1800),
                _ts(minutes_ago=i * 13),
            ],
        )

    # ── 快捷提问 ────────────────────────────────────────────────────
    w.table(
        "quick_question",
        ["id", "user_id", "question", "category", "hit_count", "updated_at"],
    )
    qid = 0
    for q in RECOMMEND_QUESTIONS:
        qid += 1
        w.row("quick_question", [qid, "", q, "recommend", rng.randint(8, 60), _ts(qid * 9)])
    for q in RECENT_QUESTIONS:
        qid += 1
        w.row("quick_question", [qid, "", q, "recent", rng.randint(5, 30), _ts(qid * 9)])

    # ── 预置会话与消息 ──────────────────────────────────────────────
    w.table(
        "chat_session",
        ["id", "user_id", "title", "pinned", "msg_count", "user_feedback",
         "admin_feedback", "source_files", "last_msg_at", "created_at", "deleted_at"],
    )
    w.table(
        "chat_message",
        ["id", "session_id", "role", "content", "payload", "rewritten_query",
         "intent", "model", "prompt_tokens", "completion_tokens", "cost_ms",
         "trace_id", "error", "created_at"],
    )
    preset = _build_preset_sessions(plan, ctx)
    session_users = ["管理员", "管理员", "普通用户", "管理员", "普通用户", "管理员", "管理员", "普通用户"]
    msg_id = 0
    for i, (title, question, answer, feedback) in enumerate(preset, start=1):
        days_ago = [1, 2, 3, 5, 7, 10, 14, 20][i - 1]
        uid = 1 if session_users[i - 1] == "管理员" else 5
        w.row(
            "chat_session",
            [
                i, uid, title, "true" if i <= 2 else "false", 2, feedback,
                "已关注" if i in (4, 8) else "",
                json.dumps([1, 2, 3, 4, 5, 6, 7, 8], ensure_ascii=False),
                _ts(days_ago=days_ago), _ts(days_ago=days_ago), "",
            ],
        )
        for role, content in (("user", question), ("assistant", answer)):
            msg_id += 1
            payload = "" if role == "user" else json.dumps(
                {"steps": [], "content": answer}, ensure_ascii=False
            )
            w.row(
                "chat_message",
                [
                    msg_id, i, role, content, payload, question if role == "assistant" else "",
                    "data_query" if role == "assistant" else "",
                    "deepseek-chat" if role == "assistant" else "",
                    0 if role == "user" else rng.randint(600, 2200),
                    0 if role == "user" else rng.randint(180, 620),
                    0 if role == "user" else rng.randint(1200, 6800),
                    f"seed-{i:03d}", "", _ts(days_ago=days_ago, minutes_ago=2 if role == "assistant" else 3),
                ],
            )

    # ── 数据问题反馈（回复校对）────────────────────────────────────
    w.table(
        "qa_feedback",
        ["id", "question", "user_id", "username", "ai_reply", "session_id", "message_id",
         "status", "remark", "handled_by", "handled_at", "created_at"],
    )
    fb_questions = (
        "上季度销售目标达成率", "本月应收账款账龄", "各区域成本对比", "年度预算执行情况",
        "浙江代表处回款进度", "智能计算产品线毛利率", "高风险项目金额分布", "渠道部同比增速",
        "商解目标完成率", "华东区台套数统计",
    )
    fb_total = max(8, int(round(20 * scale)))  # 演示用反馈单，小数据量下也保留样本
    for i in range(1, fb_total + 1):
        handled = i > 14  # 前 14 条待处理，其余已处理
        w.row(
            "qa_feedback",
            [
                i,
                fb_questions[i % len(fb_questions)],
                user_id_by_name[usernames[i % len(usernames)]],
                usernames[i % len(usernames)],
                "根据 2026 年台账数据计算得出，详细明细请查看回答中的数据表。",
                1 + (i % 8),
                i,
                "已处理" if handled else "待处理",
                "已核实数据源并修正口径，结论以最新台账为准。" if handled else "",
                1 if handled else "",
                _ts(minutes_ago=i * 47) if handled else "",
                _ts(days_ago=i % 25, minutes_ago=i * 11),
            ],
        )

    return {
        "users": len(USERS),
        "roles": len(ROLES),
        "menus": len(MENUS),
        "preset_sessions": len(preset),
        "preset_messages": msg_id,
        "feedbacks": fb_total,
        "oper_logs": log_total,
        "default_password": DEFAULT_PASSWORD,
    }
