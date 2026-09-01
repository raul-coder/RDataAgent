"""台账接口（T5-3 / FR-D1）：在线查看 + 导出。

权限分两层，都在服务端强制（前端隐藏按钮只是体验层）：
    1. 菜单权限 ``lg:*:view`` —— 能否进入该台账
    2. 数据权限（菜单 × 经营单元）—— 能看到哪些经营单元的行，
       由 ledger_service 注入 SQL，与问数链路共用同一套闸门

所有查询最终以只读账号执行，即使权限注入被绕过也写不了库。
"""

from __future__ import annotations

import csv
import datetime
import io
import time
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import CurrentUser, get_current_user
from ...core.exceptions import AppException, ErrorCode, ForbiddenError, NotFoundError
from ...core.logging import get_logger
from ...core.response import ok
from ...db.session import get_db, get_readonly_db
from ...services import ledger_import, ledger_service, log_service

logger = get_logger(__name__)
router = APIRouter(prefix="/ledger", tags=["ledger"])

# 台账 key -> 菜单权限码（与 sys_menu.perm_code 一致）
PERM_OF: dict[str, str] = {
    "contract": "lg:commercial:view",
    "ppl": "lg:ppl:view",
    "goal": "lg:goal:view",
}


async def _auth_ledger(
    key: str,
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """按路径参数校验台账权限（key 由 FastAPI 从路径注入）。"""
    code = PERM_OF.get(key)
    if not code:
        raise NotFoundError(f"未知台账：{key}")
    if not user.has(code):
        logger.warning("台账权限拒绝 user=%s 需要=%s", user.username, code)
        raise ForbiddenError(f"缺少权限：{code}")
    return user


class FilterItem(BaseModel):
    column: str
    op: str = "eq"
    value: Any = None


class QueryIn(BaseModel):
    filters: list[FilterItem] = Field(default_factory=list)
    sort_by: Optional[str] = None
    sort_dir: str = "asc"
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=ledger_service.MAX_PAGE_SIZE)
    columns: Optional[list[str]] = None


@router.get("/tables", summary="台账列表")
async def tables(
    user: CurrentUser = Depends(get_current_user),
):
    """只返回当前用户有权限查看的台账。"""
    items = []
    for key, (_table, title, _menu) in ledger_service.LEDGERS.items():
        if user.has(PERM_OF[key]):
            items.append({"key": key, "title": title, "perm": PERM_OF[key]})
    return ok(items)


@router.get("/{key}/columns", summary="列定义（含筛选候选值）")
async def columns(
    key: str,
    with_values: bool = Query(False, description="是否附带枚举列的候选值"),
    db: AsyncSession = Depends(get_db),
    ro: AsyncSession = Depends(get_readonly_db),
    user: CurrentUser = Depends(_auth_ledger),
):
    data = await ledger_service.columns(db, ro, key, with_values=with_values)
    return ok(data)


@router.post("/{key}/query", summary="分页查询")
async def query(
    key: str,
    body: QueryIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ro: AsyncSession = Depends(get_readonly_db),
    user: CurrentUser = Depends(_auth_ledger),
):
    t0 = time.perf_counter()
    try:
        data = await ledger_service.query(
            db,
            ro,
            key,
            filters=[f.model_dump() for f in body.filters],
            sort_by=body.sort_by,
            sort_dir=body.sort_dir,
            page=body.page,
            page_size=body.page_size,
            columns=body.columns,
            unit_codes=ledger_service.visible_units(user, key),
        )
        cost_ms = int((time.perf_counter() - t0) * 1000)
        await log_service.record(
            db, action=f"台账查询-{ledger_service.LEDGERS[key][1]}", status="成功",
            user_id=user.id, username=user.username, method="POST /ledger/{key}/query",
            ip=(request.client.host if request.client else ""),
            cost_ms=cost_ms,
        )
        await db.commit()
        return ok(data)
    except Exception:  # noqa: BLE001
        await db.rollback()
        raise


@router.post("/{key}/export", summary="导出 CSV / Excel（按当前筛选条件）")
async def export(
    key: str,
    body: QueryIn,
    request: Request,
    fmt: str = Query("csv", pattern="^(csv|xlsx)$", description="导出格式：csv / xlsx"),
    db: AsyncSession = Depends(get_db),
    ro: AsyncSession = Depends(get_readonly_db),
    user: CurrentUser = Depends(_auth_ledger),
):
    """导出与查询同源同条件，因此导出的内容不会多于页面上能看到的。"""
    headers, rows, head_meta, truncated = await ledger_service.export_rows(
        db,
        ro,
        key,
        filters=[f.model_dump() for f in body.filters],
        sort_by=body.sort_by,
        sort_dir=body.sort_dir,
        columns=body.columns,
        unit_codes=ledger_service.visible_units(user, key),
    )

    title = ledger_service.LEDGERS[key][1]

    if fmt == "xlsx":
        payload: bytes = _to_xlsx([m["cn_name"] for m in head_meta], rows).getvalue()
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{title}.xlsx"
    else:
        # UTF-8 BOM：Excel 直接打开 CSV 时中文不乱码
        buf = io.StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow([m["cn_name"] for m in head_meta])
        writer.writerows(rows)
        payload = buf.getvalue().encode("utf-8")
        media = "text/csv; charset=utf-8"
        filename = f"{title}.csv"

    await log_service.record(
        db, action=f"台账导出-{title}（{fmt}）", status="成功",
        user_id=user.id, username=user.username, method="POST /ledger/{key}/export",
        ip=(request.client.host if request.client else ""),
    )
    await db.commit()

    return StreamingResponse(
        iter([payload]),
        media_type=media,
        headers={
            "Content-Disposition":
                f"attachment; filename*=UTF-8''{_urlquote(filename)}",
            # 供前端提示：导出是否被上限截断（需 CORS 暴露，见 main.py）
            "X-Export-Rows": str(len(rows)),
            "X-Export-Truncated": "1" if truncated else "0",
        },
    )


def _urlquote(text: str) -> str:
    from urllib.parse import quote

    return quote(text)


# ── 导入 ────────────────────────────────────────────────────────
# 导入会写库，因此用独立的 import 权限，与「查看/导出」分开授权
IMPORT_PERM: dict[str, str] = {
    "contract": "lg:commercial:import",
    "ppl": "lg:ppl:import",
    "goal": "lg:goal:import",
}


async def _auth_import(
    key: str,
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    code = IMPORT_PERM.get(key)
    if not code:
        raise NotFoundError(f"未知台账：{key}")
    if not user.has(code):
        logger.warning("台账导入权限拒绝 user=%s 需要=%s", user.username, code)
        raise ForbiddenError(f"缺少权限：{code}")
    return user


@router.post("/{key}/import", summary="导入台账（Excel）")
async def import_ledger(
    key: str,
    file: UploadFile = File(...),
    mode: str = Query(
        "append", pattern="^(append|replace)$",
        description="append=追加；replace=清空后导入",
    ),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    ro: AsyncSession = Depends(get_readonly_db),
    user: CurrentUser = Depends(_auth_import),
):
    """导入 Excel。

    表头必须是数据字典里登记的中文列名——建议先点「导出」拿到文件当模板，
    填好再传。整批在一个事务里，任一行校验失败都会回滚。
    """
    ledger_service._table_of(key)   # 顺带校验 key 合法
    content = await file.read()
    if not content:
        raise AppException("上传文件为空", ErrorCode.BAD_REQUEST)

    t0 = time.perf_counter()
    result = await ledger_import.import_rows(db, ro, key, content, mode=mode)
    cost_ms = int((time.perf_counter() - t0) * 1000)

    title = ledger_service.LEDGERS[key][1]
    await log_service.record(
        db, action=f"台账导入-{title}（{mode}，{result['imported']} 行）",
        status="成功", user_id=user.id, username=user.username,
        method="POST /ledger/{key}/import",
        ip=(request.client.host if request.client else ""),
        cost_ms=cost_ms,
    )
    await db.commit()
    return ok({**result, "filename": file.filename, "cost_ms": cost_ms})


def _xlsx_value(value: Any) -> Any:
    """openpyxl 不认 Decimal 等类型，统一转成它能直接写入的类型。"""
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value
    return str(value)


def _to_xlsx(headers: list[str], rows: list[list[Any]]) -> io.BytesIO:
    """生成 xlsx 字节流。

    用 write_only 模式逐行写入：台账动辄上万行，普通模式会把整个
    DOM 树放在内存里，导出 5 万行时内存开销非常可观。
    """
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("数据")

    bold = Font(bold=True)
    header_cells = []
    for h in headers:
        cell = WriteOnlyCell(ws, value=h)
        cell.font = bold
        header_cells.append(cell)
    ws.append(header_cells)

    for r in rows:
        ws.append([_xlsx_value(v) for v in r])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
