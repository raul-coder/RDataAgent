"""应用配置服务：6 张配置卡片的读取与持久化。

存储形态沿用了造数脚本已生成的扁平键值对（``config_key`` → 标量），
而不是嵌套对象。这样单个开关可以直接改表，且不会牵动造数流程。

6 张卡片与键的对应关系（对应需求 FR-S1）：
    对话开场白      greeting(开关) + greetingText(文案)
    下一步问题建议  suggestions
    文字转语音      tts
    语音转文字      stt
    模型配置        modelConfig
    常问设置        hotRecommend(开关) + hotThreshold(频次阈值)
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import SysAppConfig

# 默认值；键名与造数脚本保持一致（驼峰），新增配置项只需在此登记
DEFAULTS: Dict[str, Any] = {
    "greeting": True,
    "greetingText": (
        "你好，我是经管之星。我可以帮你分析经营数据，"
        "试试问我「2026年各经营单元收入排名」或「北京代表处今年达成情况」。"
    ),
    "suggestions": True,
    "tts": False,          # 语音为 I5 增强项，默认关闭，避免按钮点了没反应
    "stt": False,
    "modelConfig": True,
    "hotRecommend": True,
    "hotThreshold": 3,
}

# 6 张卡片的展示结构：页面按此渲染，读写仍然是上面的扁平键
CARDS: List[Dict[str, Any]] = [
    {
        "key": "greeting",
        "label": "对话开场白",
        "desc": "控制智能问数欢迎页是否展示开场白及其文案",
        "fields": [
            {"key": "greeting", "label": "启用", "kind": "switch"},
            {"key": "greetingText", "label": "开场白文案", "kind": "textarea"},
        ],
    },
    {
        "key": "suggestions",
        "label": "下一步问题建议",
        "desc": "回答底部是否生成 3 条延伸追问建议",
        "fields": [{"key": "suggestions", "label": "启用", "kind": "switch"}],
    },
    {
        "key": "tts",
        "label": "文字转语音",
        "desc": "控制 AI 回答的语音播放按钮是否显示",
        "fields": [{"key": "tts", "label": "启用", "kind": "switch"}],
    },
    {
        "key": "stt",
        "label": "语音转文字",
        "desc": "控制输入区麦克风按钮是否显示",
        "fields": [{"key": "stt", "label": "启用", "kind": "switch"}],
    },
    {
        "key": "modelConfig",
        "label": "模型配置",
        "desc": "是否在系统管理中开放模型配置入口",
        "fields": [{"key": "modelConfig", "label": "启用", "kind": "switch"}],
    },
    {
        "key": "hotRecommend",
        "label": "常问设置",
        "desc": "按提问频次自动生成常问问题，达到阈值才进入快捷面板",
        "fields": [
            {"key": "hotRecommend", "label": "启用", "kind": "switch"},
            {"key": "hotThreshold", "label": "频次阈值", "kind": "number"},
        ],
    },
]

# 卡片键 → 该卡片包含的存储键，用于保存时只更新涉及的项
CARD_KEYS: Dict[str, List[str]] = {
    c["key"]: [f["key"] for f in c["fields"]] for c in CARDS
}


async def get_all(db: AsyncSession) -> Dict[str, Any]:
    """返回全部配置，未落库的键用默认值补齐。"""
    rows = (await db.execute(select(SysAppConfig))).scalars().all()
    stored = {r.config_key: r.config_value for r in rows}
    return {k: stored.get(k, v) for k, v in DEFAULTS.items()}


async def get_one(db: AsyncSession, key: str) -> Any:
    if key not in DEFAULTS:
        return None
    row = (
        await db.execute(select(SysAppConfig).where(SysAppConfig.config_key == key))
    ).scalar_one_or_none()
    return row.config_value if row is not None else DEFAULTS[key]


async def get_flag(db: AsyncSession, key: str, default: bool = False) -> bool:
    """读取布尔开关，供问数链路判断某能力是否开启。"""
    value = await get_one(db, key)
    return bool(value) if value is not None else default


async def get_int(db: AsyncSession, key: str, default: int = 0) -> int:
    value = await get_one(db, key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def set_many(
    db: AsyncSession, items: Dict[str, Any], user_id: int | None
) -> List[str]:
    """批量 upsert；只接受已登记的键，返回实际保存的键。"""
    saved: List[str] = []
    for key, value in items.items():
        if key not in DEFAULTS:
            continue
        # 数字字段做一次类型收敛，避免前端传字符串导致阈值比较失效
        if key == "hotThreshold":
            try:
                value = max(1, int(value))
            except (TypeError, ValueError):
                continue
        await db.execute(
            pg_insert(SysAppConfig)
            .values(config_key=key, config_value=value, updated_by=user_id)
            .on_conflict_do_update(
                index_elements=["config_key"],
                set_={"config_value": value, "updated_by": user_id},
            )
        )
        saved.append(key)
    if saved:
        await db.flush()
    return saved


async def ensure_seeded(db: AsyncSession) -> int:
    """补齐缺失的默认键；返回新增条数。"""
    existing = {r[0] for r in (await db.execute(select(SysAppConfig.config_key))).all()}
    created = 0
    for key, value in DEFAULTS.items():
        if key in existing:
            continue
        db.add(SysAppConfig(config_key=key, config_value=value))
        created += 1
    if created:
        await db.flush()
    return created
