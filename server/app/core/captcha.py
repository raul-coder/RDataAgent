"""图形验证码（PNG）。

返回 (captcha_id, png_bytes)；答案存 Redis（TTL 5 分钟），一次性校验后立即删除。
Redis 降级时退回进程内字典（见 core/redis.py）。
"""

from __future__ import annotations

import io
import random
import uuid

# 去掉易混淆字符 0/O/1/I/l
CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
TTL = 300


def _key(captcha_id: str) -> str:
    return f"captcha:{captcha_id}"


def generate(width: int = 110, height: int = 40, length: int = 4) -> tuple[str, bytes]:
    """生成验证码，返回 (captcha_id, png_bytes)。"""
    from . import redis

    code = "".join(random.choice(CHARS) for _ in range(length))
    captcha_id = uuid.uuid4().hex
    redis.setex(_key(captcha_id), TTL, code.lower())
    return captcha_id, _render(code, width, height)


def _render(code: str, width: int, height: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont  # type: ignore

    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    font = None
    for size in (28, 26, 24):
        for path in (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ):
            try:
                font = ImageFont.truetype(path, size)
                break
            except Exception:  # noqa: BLE001
                continue
        if font:
            break

    # 干扰线
    for _ in range(5):
        x1, y1 = random.randint(0, width), random.randint(0, height)
        x2, y2 = random.randint(0, width), random.randint(0, height)
        draw.line((x1, y1, x2, y2), fill=(random.randint(180, 230),) * 3, width=1)

    # 字符（逐个旋转 + 随机颜色）
    step = width // (len(code) + 1)
    for i, ch in enumerate(code):
        cell = Image.new("RGBA", (30, 34), (255, 255, 255, 0))
        cdraw = ImageDraw.Draw(cell)
        cdraw.text(
            (4, 2),
            ch,
            font=font,
            fill=(random.randint(20, 90), random.randint(40, 120), random.randint(120, 220)),
        )
        cell = cell.rotate(random.randint(-22, 22), expand=True, fillcolor=(255, 255, 255, 255))
        img.paste(cell, (step * i + 4, (height - cell.height) // 2), cell)

    # 噪点
    for _ in range(80):
        draw.point(
            (random.randint(0, width), random.randint(0, height)),
            fill=(random.randint(150, 220),) * 3,
        )

    img = img.filter(ImageFilter.SMOOTH)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def verify(captcha_id: str, user_input: str) -> bool:
    """校验并立即失效（一次性）。"""
    from . import redis

    if not captcha_id or not user_input:
        return False
    key = _key(captcha_id)
    answer = redis.get(key)
    if answer is None:
        return False
    redis.delete(key)
    return answer == user_input.strip().lower()
