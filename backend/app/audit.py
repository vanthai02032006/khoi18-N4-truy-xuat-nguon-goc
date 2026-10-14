"""Nhật ký thao tác ghi ở middleware (S-31 / SCRUM-90).

Theo ghi chú ước lượng của story: *ghi ở middleware, không rải trong từng handler*.

- Mọi request thay đổi dữ liệu (POST/PUT/PATCH/DELETE) được ghi một dòng: người,
  tổ chức, thời điểm, địa chỉ IP, hành động, mã đối tượng và kết quả.
- Đăng nhập thành công/thất bại được ghi kèm lý do (thất bại do sai thông tin).
- Không ghi mật khẩu, token hay nội dung request. Bảng ``audit_logs`` chỉ-thêm.
- Ghi nhật ký lỗi thì KHÔNG làm hỏng request của người dùng.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

from fastapi import Request
from sqlalchemy import select
from starlette.responses import Response

from app.database import get_db
from app.models import AuditLog, User

logger = logging.getLogger("agritrace.audit")

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SKIP_PREFIXES = ("/docs", "/redoc", "/openapi.json", "/health")
MAX_PARSE_BYTES = 200_000

# (method, regex đường dẫn) -> (tên hành động, loại đối tượng)
ACTION_RULES: list[tuple[str, re.Pattern[str], str, str]] = [
    ("POST", re.compile(r"^/auth/login$"), "Đăng nhập", "user"),
    ("POST", re.compile(r"^/batches$"), "Tạo lô", "batch"),
    ("POST", re.compile(r"^/batches/merge$"), "Gộp lô", "batch"),
    ("POST", re.compile(r"^/batches/(\d+)/split$"), "Tách lô", "batch"),
    ("POST", re.compile(r"^/batches/(\d+)/consume$"), "Ghi nhận bán hết", "batch"),
    ("POST", re.compile(r"^/batches/(\d+)/location$"), "Chuyển địa điểm lô", "batch"),
    ("POST", re.compile(r"^/batches/(\d+)/incident$"), "Báo sự cố / mở thu hồi", "batch"),
    ("PUT", re.compile(r"^/batches/(\d+)$"), "Sửa lô", "batch"),
    ("DELETE", re.compile(r"^/batches/(\d+)$"), "Xoá lô", "batch"),
    ("POST", re.compile(r"^/handovers$"), "Tạo bàn giao", "handover"),
    ("POST", re.compile(r"^/handovers/(\d+)/accept$"), "Xác nhận bàn giao", "handover"),
    ("POST", re.compile(r"^/handovers/(\d+)/reject$"), "Từ chối bàn giao", "handover"),
    ("POST", re.compile(r"^/farms$"), "Tạo thửa", "farm"),
    ("PUT", re.compile(r"^/farms/(\d+)$"), "Sửa thửa", "farm"),
    ("DELETE", re.compile(r"^/farms/(\d+)$"), "Xoá thửa", "farm"),
    ("POST", re.compile(r"^/products$"), "Thêm sản phẩm", "product"),
    ("POST", re.compile(r"^/locations$"), "Khai báo địa điểm", "location"),
    ("POST", re.compile(r"^/imports/(products|farms)$"), "Nhập CSV", "import"),
    ("POST", re.compile(r"^/orders$"), "Mở lệnh kiểm tra/thu hồi", "order"),
]


def classify(method: str, path: str) -> tuple[str, str | None, str | None]:
    """Trả (tên hành động, loại đối tượng, mã đối tượng lấy từ đường dẫn)."""
    for m, pattern, name, obj_type in ACTION_RULES:
        if m == method:
            match = pattern.match(path)
            if match:
                obj_id = match.group(1) if match.groups() and match.group(1).isdigit() else None
                return name, obj_type, obj_id
    return f"{method} {path}", None, None


def client_ip(request: Request) -> str | None:
    for header in ("x-forwarded-for", "x-real-ip"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def basic_username(request: Request) -> str | None:
    """Đọc tên đăng nhập từ header Basic (KHÔNG giữ lại mật khẩu)."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(auth[6:]).decode("utf-8", "replace")
    except Exception:
        return None
    return raw.split(":", 1)[0][:50] or None


def _object_id_from_body(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    for key in ("batch_code", "id", "order_code"):
        if body.get(key) not in (None, ""):
            return str(body[key])[:80]
    for key in ("merged_batch", "new_batch", "batch"):
        nested = body.get(key)
        if isinstance(nested, dict):
            found = _object_id_from_body(nested)
            if found:
                return found
    return None


async def audit_middleware(request: Request, call_next):
    method = request.method.upper()
    path = request.url.path
    login = method == "POST" and path == "/auth/login"
    login_username: str | None = None
    if login:
        try:
            body = await request.body()  # Starlette lưu lại body cho handler đọc tiếp
            login_username = str(json.loads(body or b"{}").get("username", ""))[:50] or None
        except Exception:
            login_username = None

    response = await call_next(request)

    if method not in MUTATING_METHODS or path.startswith(SKIP_PREFIXES):
        return response

    # Đọc body phản hồi JSON nhỏ để lấy mã đối tượng vừa tạo, rồi trả lại nguyên vẹn.
    obj_from_body = None
    ctype = response.headers.get("content-type", "")
    if "application/json" in ctype:
        chunks = [chunk async for chunk in response.body_iterator]
        raw = b"".join(chunks)
        response = Response(
            content=raw,
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
            media_type=ctype,
        )
        if len(raw) <= MAX_PARSE_BYTES and response.status_code < 400:
            try:
                obj_from_body = _object_id_from_body(json.loads(raw))
            except Exception:
                obj_from_body = None

    try:
        _write(request, response.status_code, method, path, login, login_username, obj_from_body)
    except Exception:  # pragma: no cover - nhật ký không được làm hỏng request
        logger.exception("Không ghi được nhật ký thao tác cho %s %s", method, path)
    return response


def _write(request, status_code, method, path, login, login_username, obj_from_body) -> None:
    dep = request.app.dependency_overrides.get(get_db, get_db)
    gen = dep()
    db = next(gen)
    try:
        action, obj_type, obj_id = classify(method, path)
        username = login_username if login else basic_username(request)
        org = None
        if username:
            org = db.scalar(select(User.organization_id).where(User.username == username))
        success = status_code < 400
        detail = None
        if login:
            action = "Đăng nhập" if success else "Đăng nhập thất bại"
            if not success:
                detail = "Sai tên đăng nhập hoặc mật khẩu" if status_code == 401 else f"HTTP {status_code}"
        elif not success:
            detail = f"Bị từ chối: HTTP {status_code}"
        db.add(
            AuditLog(
                username=username,
                organization_id=org,
                action=action,
                method=method,
                path=path[:255],
                object_type=obj_type,
                object_id=obj_id or obj_from_body,
                status_code=status_code,
                success=success,
                ip_address=client_ip(request),
                detail=detail,
            )
        )
        db.commit()
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


__all__ = ["audit_middleware", "classify"]
