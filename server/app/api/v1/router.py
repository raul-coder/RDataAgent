"""API v1 路由聚合。"""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    app_config, auth, chat, feedback, ledger, menus, models,
    quick_question, roles, semantic, speech, stats, users,
)
from .health import router as health_router
from .menus import logs_router, options_router, router as menus_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(roles.router)
api_router.include_router(menus_router)
api_router.include_router(options_router)
api_router.include_router(semantic.router)      # 只读：数据源 / 指标 / 维度
api_router.include_router(semantic.mgmt)        # 语义层管理：增删改查
api_router.include_router(chat.router)
api_router.include_router(app_config.router)
api_router.include_router(models.router)
api_router.include_router(feedback.router)
api_router.include_router(quick_question.router)
api_router.include_router(ledger.router)
api_router.include_router(speech.router)
api_router.include_router(stats.router)
api_router.include_router(logs_router)
