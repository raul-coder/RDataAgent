"""敏感字段加解密（模型 API Key 落库用）。

用对称加密（Fernet）而非单向散列，是因为调用模型时**必须还原出明文 Key**。
密钥由 ``SECRET_KEY`` 派生，因此更换 SECRET_KEY 会使历史密文失效——
这是有意为之的权衡：密钥泄露时改 SECRET_KEY 即可让旧密文全部作废。
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    """加密为可安全落库 / 出库的字符串。空值原样返回。"""
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """解密；密文损坏或密钥变更时返回空串（不抛异常，避免拖垮接口）。"""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""


def mask_secret(plain: str) -> str:
    """脱敏展示：保留前 3 后 4 位，中间打码。"""
    if not plain:
        return ""
    if len(plain) <= 9:
        return plain[0] + "***" if plain else ""
    return f"{plain[:3]}{'*' * (len(plain) - 7)}{plain[-4:]}"
