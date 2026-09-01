"""语音兜底接口（FR-V1 语音转文字 / FR-V2 文字转语音）。

设计取舍：
    浏览器原生的 Web Speech API / SpeechSynthesis 已能覆盖绝大多数场景，
    且零成本、零延迟，所以前端以原生方案为主（见 web/src/hooks/useSpeech.ts）。
    本模块只做「浏览器不支持时」的兜底：只有在「模型配置」里登记了
    scene = speech_stt / speech_tts 的服务时才真正转发；
    否则明确返回 501「未配置」——静默降级会让用户以为功能坏了却查不到原因。

接入方式：
    在「系统管理 ▸ 模型配置」新增模型，场景填 speech_stt 或 speech_tts，
    Base URL 填 OpenAI 兼容服务地址，模型名填 whisper-1 / tts-1 等。
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.crypto import decrypt_secret
from ...core.deps import CurrentUser, get_current_user
from ...core.exceptions import AppException, ErrorCode
from ...core.logging import get_logger
from ...core.response import ok
from ...db.session import get_db
from ...models import SysModel

logger = get_logger(__name__)
router = APIRouter(prefix="/speech", tags=["speech"])

MAX_AUDIO_BYTES = 10 * 1024 * 1024   # 10 MB，约合 10 分钟录音
MAX_TTS_CHARS = 2000

STT_SCENE = "speech_stt"
TTS_SCENE = "speech_tts"


async def _pick(db: AsyncSession, scene: str) -> SysModel | None:
    """取该场景启用的服务（默认优先）。"""
    return (
        await db.execute(
            select(SysModel)
            .where(SysModel.scene == scene, SysModel.enabled.is_(True))
            .order_by(SysModel.is_default.desc(), SysModel.id)
            .limit(1)
        )
    ).scalars().first()


def _not_configured(scene: str, what: str) -> AppException:
    return AppException(
        f"未配置{what}：请在「系统管理 ▸ 模型配置」中新增场景为 {scene} 的模型；"
        f"未配置时前端会直接使用浏览器原生语音能力",
        ErrorCode.NOT_FOUND,
        501,
    )


@router.get("/status", summary="语音兜底服务是否可用")
async def status(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """供前端判断：是否需要回退到浏览器原生实现。"""
    stt_svc = await _pick(db, STT_SCENE)
    tts_svc = await _pick(db, TTS_SCENE)
    return ok({
        "stt": bool(stt_svc),
        "tts": bool(tts_svc),
        "stt_name": stt_svc.name if stt_svc else "",
        "tts_name": tts_svc.name if tts_svc else "",
    })


@router.post("/stt", summary="语音转文字（服务端兜底）")
async def stt(
    audio: UploadFile = File(..., description="音频文件（webm / wav / mp3）"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    svc = await _pick(db, STT_SCENE)
    if not svc:
        raise _not_configured(STT_SCENE, "语音识别服务")

    content = await audio.read()
    if not content:
        raise AppException("音频内容为空", ErrorCode.BAD_REQUEST)
    if len(content) > MAX_AUDIO_BYTES:
        raise AppException("音频文件过大（上限 10 MB）", ErrorCode.BAD_REQUEST)

    url = f"{svc.base_url.rstrip('/')}/audio/transcriptions"
    try:
        async with httpx.AsyncClient(timeout=90) as cli:
            resp = await cli.post(
                url,
                headers={"Authorization": f"Bearer {decrypt_secret(svc.api_key_enc or '')}"},
                files={
                    "file": (
                        audio.filename or "audio.webm",
                        content,
                        audio.content_type or "audio/webm",
                    )
                },
                data={"model": svc.model_name},
            )
    except Exception as exc:  # noqa: BLE001
        raise AppException(f"语音识别服务不可用：{exc}", ErrorCode.LLM_ERROR, 502) from exc

    if resp.status_code >= 400:
        logger.warning("STT 失败 HTTP %s：%s", resp.status_code, resp.text[:200])
        raise AppException(
            f"语音识别服务返回错误：HTTP {resp.status_code}",
            ErrorCode.LLM_ERROR, 502, detail={"body": resp.text[:200]},
        )

    text = (resp.json() or {}).get("text", "")
    return ok({"text": text})


@router.post("/tts", summary="文字转语音（服务端兜底）")
async def tts(
    text: str = Form(..., max_length=MAX_TTS_CHARS),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    svc = await _pick(db, TTS_SCENE)
    if not svc:
        raise _not_configured(TTS_SCENE, "语音合成服务")

    if not text.strip():
        raise AppException("待朗读文本为空", ErrorCode.BAD_REQUEST)

    url = f"{svc.base_url.rstrip('/')}/audio/speech"
    try:
        async with httpx.AsyncClient(timeout=90) as cli:
            resp = await cli.post(
                url,
                headers={
                    "Authorization": f"Bearer {decrypt_secret(svc.api_key_enc or '')}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": svc.model_name,
                    "input": text,
                    "voice": (svc.params or {}).get("voice", "alloy"),
                },
            )
    except Exception as exc:  # noqa: BLE001
        raise AppException(f"语音合成服务不可用：{exc}", ErrorCode.LLM_ERROR, 502) from exc

    if resp.status_code >= 400:
        logger.warning("TTS 失败 HTTP %s：%s", resp.status_code, resp.text[:200])
        raise AppException(
            f"语音合成服务返回错误：HTTP {resp.status_code}",
            ErrorCode.LLM_ERROR, 502, detail={"body": resp.text[:200]},
        )

    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "audio/mpeg"),
    )
