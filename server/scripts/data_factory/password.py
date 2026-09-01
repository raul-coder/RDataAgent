"""口令散列（零第三方依赖）。

格式：pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

选择 PBKDF2-SHA256 而非 bcrypt 的原因：造数脚本要求在「仅标准库」环境下可运行。
后端 app/core/security.py 同时支持校验本格式与 bcrypt 格式，
新口令在安装了 bcrypt 的环境下优先用 bcrypt。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os


def hash_password(password: str, iterations: int = 120000, salt: bytes = None) -> str:
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_b64, hash_b64 = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False
