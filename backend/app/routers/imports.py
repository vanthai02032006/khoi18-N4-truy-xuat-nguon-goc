"""Nhập danh mục sản phẩm và thửa từ tệp CSV (S-32 / SCRUM-99) - chỉ quản trị.

AC:
- Tệp đúng mẫu -> mọi dòng được tạo, báo số dòng thành công.
- Có dòng sai -> KHÔNG dòng nào được tạo, báo cáo nêu rõ dòng và cột sai.
NFR: giới hạn kích thước tệp; xử lý trong một giao dịch.

Gửi nội dung tệp làm thân request với ``Content-Type: text/csv`` (UTF-8, có hoặc
không có BOM). Dòng đầu là tiêu đề cột.
"""

from __future__ import annotations

import csv
import io
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PRODUCT_UNITS, Farm, Product, User
from app.security import require_admin

router = APIRouter(prefix="/imports", tags=["Nhập CSV (S-32)"])

MAX_BYTES = 1_000_000  # 1 MB
MAX_ROWS = 5_000

PRODUCT_COLUMNS = ("name", "unit", "description")
FARM_COLUMNS = ("name", "location", "area", "owner")


class ImportRowError(BaseModel):
    row: int  # số dòng trong tệp (dòng 1 là tiêu đề)
    column: str
    message: str


class ImportResult(BaseModel):
    created: int
    message: str


async def _read_rows(request: Request, required: tuple[str, ...]) -> list[tuple[int, dict[str, str]]]:
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Tệp vượt quá {MAX_BYTES // 1000} KB.")
    if not raw.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tệp CSV rỗng.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tệp phải được lưu dạng UTF-8.")
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in required if c not in header]
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"message": "Thiếu cột bắt buộc trong dòng tiêu đề.", "errors": [
                ImportRowError(row=1, column=c, message="Thiếu cột").model_dump() for c in missing
            ]},
        )
    rows = []
    for idx, rec in enumerate(reader, start=2):
        clean = {(k or "").strip(): (v or "").strip() for k, v in rec.items()}
        if not any(clean.values()):
            continue  # bỏ dòng trống
        rows.append((idx, clean))
        if len(rows) > MAX_ROWS:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Tối đa {MAX_ROWS} dòng mỗi lần nhập.")
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tệp không có dòng dữ liệu nào.")
    return rows


def _commit_all_or_nothing(db: Session, objects: list, errors: list[ImportRowError], label: str) -> ImportResult:
    if errors:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "message": f"Có {len(errors)} lỗi - không {label} nào được tạo. Sửa tệp rồi nhập lại.",
                "errors": [e.model_dump() for e in errors],
            },
        )
    try:
        db.add_all(objects)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Không lưu được dữ liệu nhập.") from exc
    return ImportResult(created=len(objects), message=f"Đã tạo {len(objects)} {label}.")


def _required(errors: list[ImportRowError], row: int, rec: dict, col: str, max_len: int) -> str | None:
    value = rec.get(col, "")
    if not value:
        errors.append(ImportRowError(row=row, column=col, message="Không được để trống"))
        return None
    if len(value) > max_len:
        errors.append(ImportRowError(row=row, column=col, message=f"Dài quá {max_len} ký tự"))
        return None
    return value


@router.post(
    "/products",
    response_model=ImportResult,
    status_code=status.HTTP_201_CREATED,
    summary="Nhập danh mục sản phẩm từ CSV (cột: name, unit, description)",
    openapi_extra={"requestBody": {"content": {"text/csv": {"schema": {"type": "string"}}}, "required": True}},
)
async def import_products(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ImportResult:
    rows = await _read_rows(request, ("name", "unit"))
    existing = {n.lower() for n in db.scalars(select(func.lower(Product.name))).all()}
    seen: dict[str, int] = {}
    errors: list[ImportRowError] = []
    objects: list[Product] = []
    for row, rec in rows:
        name = _required(errors, row, rec, "name", 255)
        unit = _required(errors, row, rec, "unit", 20)
        if unit and unit.lower() not in PRODUCT_UNITS:
            errors.append(ImportRowError(row=row, column="unit", message=f"Đơn vị '{unit}' không hợp lệ; chọn một trong: {', '.join(PRODUCT_UNITS)}"))
            unit = None
        if name:
            key = name.lower()
            if key in existing:
                errors.append(ImportRowError(row=row, column="name", message="Sản phẩm đã có trong danh mục"))
                name = None
            elif key in seen:
                errors.append(ImportRowError(row=row, column="name", message=f"Trùng với dòng {seen[key]} trong tệp"))
                name = None
            else:
                seen[key] = row
        description = rec.get("description") or None
        if description and len(description) > 500:
            errors.append(ImportRowError(row=row, column="description", message="Dài quá 500 ký tự"))
        if name and unit:
            objects.append(Product(name=name, unit=unit.lower(), description=description))
    return _commit_all_or_nothing(db, objects, errors, "sản phẩm")


@router.post(
    "/farms",
    response_model=ImportResult,
    status_code=status.HTTP_201_CREATED,
    summary="Nhập thửa/vùng trồng từ CSV (cột: name, location, area, owner)",
    openapi_extra={"requestBody": {"content": {"text/csv": {"schema": {"type": "string"}}}, "required": True}},
)
async def import_farms(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ImportResult:
    rows = await _read_rows(request, FARM_COLUMNS)
    errors: list[ImportRowError] = []
    objects: list[Farm] = []
    for row, rec in rows:
        name = _required(errors, row, rec, "name", 255)
        location = _required(errors, row, rec, "location", 255)
        owner = _required(errors, row, rec, "owner", 255)
        area_raw = rec.get("area", "")
        area: float | None = None
        try:
            area = float(area_raw.replace(",", "."))
            if area <= 0:
                errors.append(ImportRowError(row=row, column="area", message="Diện tích phải lớn hơn 0"))
                area = None
        except ValueError:
            errors.append(ImportRowError(row=row, column="area", message=f"'{area_raw}' không phải số"))
        if name and location and owner and area is not None:
            objects.append(Farm(name=name, location=location, area=area, owner=owner))
    return _commit_all_or_nothing(db, objects, errors, "thửa")
