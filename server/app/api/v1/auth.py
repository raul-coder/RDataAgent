"""认证接口：登录 / 刷新 / 登出 / 当前用户 / 改密 / 验证码。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core import captcha, rate_limit
from ...core.deps import CurrentUser, get_current_user
from ...core.exceptions import AppException, ErrorCode
from ...core.response import ok
from ...db.session import get_db
from ...services import auth_service, log_service

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    captcha: Optional[str] = None
    captcha_id: Optional[str] = None


class RefreshReq(BaseModel):
    refresh_token: str


class ChangePwdReq(BaseModel):
    old_password: str
    new_password: str


@router.get("/captcha", summary="获取图形验证码")
async def get_captcha(request: Request):
    """失败次数达到阈值后前端才需要展示验证码。"""
    need = auth_service.need_captcha(request.query_params.get("username", ""))
    cid, png = captcha.generate()
    return ok({"captcha_id": cid, "image": f"data:image/png;base64,{_b64(png)}", "required": need})


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


@router.post("/login", summary="登录")
async def login(req: LoginReq, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        result = await auth_service.login(
            db,
            username=req.username,
            password=req.password,
            request=request,
            captcha_code=req.captcha,
            captcha_id=req.captcha_id,
        )
    except AppException:
        await db.commit()  # 保证失败审计日志落库
        raise
    await db.commit()
    return ok(result)


@router.post("/refresh", summary="刷新访问令牌")
async def refresh(req: RefreshReq, db: AsyncSession = Depends(get_db)):
    return ok(await auth_service.refresh(db, req.refresh_token))


@router.post("/logout", summary="登出")
async def logout(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    auth_service.logout(token)
    await log_service.record(
        db, user_id=user.id, username=user.username, log_type="login",
        action="用户退出登录", method="POST /api/v1/auth/logout",
        ip=rate_limit.client_ip(request), status="成功",
    )
    await db.commit()
    return ok({"logged_out": True})


@router.get("/me", summary="当前用户信息（含菜单与权限）")
async def me(user: CurrentUser = Depends(get_current_user)):
    return ok(
        {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname,
            "phone": user.phone,
            "email": user.email,
            "avatar": user.avatar,
            "status": user.status,
            "pwd_must_change": user.pwd_must_change,
            "role_ids": user.role_ids,
            "role_codes": user.role_codes,
            "perms": user.perms,
            "menus": user.menus,
            "data_perms": user.data_perms,
        }
    )


@router.post("/change-password", summary="修改密码")
async def change_password(
    req: ChangePwdReq,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await auth_service.change_password(db, user.id, req.old_password, req.new_password)
    await log_service.record(
        db, user_id=user.id, username=user.username, action="修改密码-个人信息",
        method="POST /api/v1/auth/change-password", status="成功",
    )
    await db.commit()
    return ok({"changed": True})
