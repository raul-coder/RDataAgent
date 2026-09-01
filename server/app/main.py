"""经管之星 · 后端入口。"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .api.v1.router import api_router
from .core.config import settings
from .core.exceptions import register_exception_handlers
from .core.logging import get_logger, new_trace_id, set_trace_id, setup_logging
from .db.session import dispose_engines, init_engines

logger = get_logger("jingguan")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    setup_logging(level="DEBUG" if settings.DEBUG else "INFO", json_format=not settings.DEBUG)
    init_engines()
    logger.info("%s %s 启动（env=%s）", settings.APP_NAME, settings.APP_VERSION, settings.ENV)
    yield
    await dispose_engines()
    logger.info("服务已停止")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="企业级销售经营数据管理平台 · 智能问数",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # 默认不暴露自定义响应头，axios 就读不到；
        # 导出接口用它回传"是否被上限截断"，前端据此提示用户
        expose_headers=["X-Export-Rows", "X-Export-Truncated"],
    )

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):  # noqa: ANN001, ANN202
        trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
        set_trace_id(trace_id)
        start = time.perf_counter()
        response = await call_next(request)
        cost_ms = int((time.perf_counter() - start) * 1000)
        response.headers["X-Trace-Id"] = trace_id
        response.headers["X-Response-Time"] = f"{cost_ms}ms"
        if request.url.path.startswith(settings.API_PREFIX):
            logger.info("%s %s -> %s (%dms)", request.method, request.url.path, response.status_code, cost_ms)
        return response

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.API_PREFIX)
    return app


app = create_app()
