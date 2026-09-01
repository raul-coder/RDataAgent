"""问数相关请求 / 响应模型。"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class CompletionReq(BaseModel):
    session_id: Optional[int] = Field(None, description="为空时自动创建新会话")
    content: str = Field(..., min_length=1, max_length=2000)
    source_ids: Optional[list[int]] = Field(None, description="选中的数据源；为空表示全部")
    model_id: Optional[int] = None


class SessionCreateReq(BaseModel):
    title: str = "新对话"


class SessionRenameReq(BaseModel):
    title: str = Field(..., min_length=1, max_length=128)


class SessionPinReq(BaseModel):
    pinned: bool


class MessageFeedbackReq(BaseModel):
    rating: str = Field(..., pattern="^(up|down|data_error)$")
    comment: str = ""


class DataErrorReq(BaseModel):
    message_id: int
    comment: str = ""
