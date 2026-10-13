"""Middleware kiểm toán hệ thống (Audit Log Middleware).

Mục tiêu & Đặc tả nghiệp vụ:
- Ghi nhận tự động mọi thao tác quan trọng (đăng nhập, tạo lô, tách lô, v.v.)
- Đảm bảo thực thi ở tầng middleware, không rải rác trong từng handler.
- Ghi lại đầy đủ: người thực hiện (actor), tổ chức (organization), thời điểm (timestamp),
  địa chỉ IP (ip_address), thao tác (action), trạng thái (status), mã đối tượng (target_code)
  và lý do (reason) nếu thất bại.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import AuditLog, Batch

try:
    from app.tenant import get_tenant_org
except ImportError:
    def get_tenant_org() -> str:
        return "HTX Nông Nghiệp Số 4"


def get_client_ip(request: Request) -> str:
    """Trích xuất địa chỉ IP thực tế của client (hỗ trợ reverse proxy headers)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def extract_actor_from_auth_header(request: Request) -> Optional[str]:
    """Trích xuất username từ header HTTP Basic Authorization nếu có."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
            if ":" in decoded:
                return decoded.split(":", 1)[0]
        except Exception:
            pass
    return None


def resolve_batch_info(app: FastAPI, batch_id: int | None) -> tuple[str, str]:
    """Tra cứu mã lô (batch_code) và tổ chức sở hữu từ CSDL."""
    if not batch_id:
        return ("", get_tenant_org())

    override = getattr(app, "dependency_overrides", {}).get(get_db)
    if override:
        try:
            res = override()
            db: Session = next(res) if hasattr(res, "__next__") else res
            batch = db.get(Batch, batch_id)
            if batch:
                code = batch.batch_code or f"LOT-{batch.id}"
                org = getattr(batch, "current_holder_org", None) or get_tenant_org()
                return (code, org)
        except Exception:
            pass

    try:
        with SessionLocal() as db:
            batch = db.get(Batch, batch_id)
            if batch:
                code = batch.batch_code or f"LOT-{batch.id}"
                org = getattr(batch, "current_holder_org", None) or get_tenant_org()
                return (code, org)
    except Exception:
        pass

    return (f"LOT-{batch_id}", get_tenant_org())


def save_audit_log_entry(
    app: FastAPI,
    actor: str,
    organization: str,
    timestamp: str,
    ip_address: str,
    action: str,
    status: str,
    target_code: str,
    reason: Optional[str] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
) -> None:
    """Lưu một dòng nhật ký kiểm toán vào bảng audit_logs."""
    log_entry = AuditLog(
        actor=actor,
        organization=organization,
        timestamp=timestamp,
        ip_address=ip_address,
        action=action,
        status=status,
        target_code=target_code,
        reason=reason,
        method=method,
        path=path,
    )

    override = getattr(app, "dependency_overrides", {}).get(get_db)
    if override:
        try:
            res = override()
            if hasattr(res, "__next__"):
                db = next(res)
                db.add(log_entry)
                db.commit()
            elif isinstance(res, Session):
                res.add(log_entry)
                res.commit()
            return
        except Exception:
            pass

    try:
        with SessionLocal() as db:
            db.add(log_entry)
            db.commit()
    except Exception:
        pass


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Middleware tự động ghi nhật ký kiểm toán cho các thao tác quan trọng."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        norm_path = path.rstrip("/")
        method = request.method

        # Bỏ qua các endpoint không phải thao tác nghiệp vụ/xác thực (GET, docs, health, audit-logs chính nó)
        is_auth_endpoint = norm_path.endswith("/auth/login")
        is_mutating = method in ("POST", "PUT", "PATCH", "DELETE")
        is_ignored_path = any(
            norm_path.startswith(prefix)
            for prefix in ("/docs", "/redoc", "/openapi.json", "/health", "/audit-logs", "/admin/audit-logs")
        )

        if is_ignored_path or (not is_auth_endpoint and not is_mutating):
            return await call_next(request)

        # 1. Trích xuất thông tin ban đầu từ request
        client_ip = get_client_ip(request)
        actor = extract_actor_from_auth_header(request) or ""
        timestamp = datetime.now(timezone.utc).isoformat()
        org = request.headers.get("x-organization-id") or get_tenant_org()

        # 2. Đọc request body an toàn (để không làm nghẽn stream phía sau)
        body_bytes = await request.body()
        async def receive():
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        request = Request(request.scope, receive=receive)

        req_json: dict[str, Any] = {}
        if body_bytes:
            try:
                parsed = json.loads(body_bytes.decode("utf-8"))
                if isinstance(parsed, dict):
                    req_json = parsed
            except Exception:
                pass

        if not actor and "username" in req_json:
            actor = str(req_json["username"])

        # 3. Chuyển tiếp request đến handler
        response = await call_next(request)

        # 4. Đọc response body an toàn để phân tích kết quả thao tác
        resp_body_chunks = []
        async for chunk in response.body_iterator:
            resp_body_chunks.append(chunk)
        resp_body_bytes = b"".join(resp_body_chunks)

        new_response = Response(
            content=resp_body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

        resp_json: dict[str, Any] = {}
        if resp_body_bytes:
            try:
                parsed_resp = json.loads(resp_body_bytes.decode("utf-8"))
                if isinstance(parsed_resp, dict):
                    resp_json = parsed_resp
            except Exception:
                pass

        # 5. Phân tích nghiệp vụ & Xác định thông tin dòng nhật ký
        action = f"{method}_{norm_path.strip('/').replace('/', '_').upper()}"
        target_code = ""
        status_str = "SUCCESS" if response.status_code < 400 else "FAILED"
        reason: Optional[str] = None

        if norm_path.endswith("/auth/login") and method == "POST":
            action = "LOGIN"
            actor = actor or req_json.get("username", "") or "unknown"
            target_code = actor
            if response.status_code == 200:
                status_str = "SUCCESS"
                reason = None
            else:
                status_str = "FAILED"
                reason = str(resp_json.get("detail", "Sai tên đăng nhập hoặc mật khẩu."))

        elif norm_path.endswith("/batches") and method == "POST":
            action = "CREATE_BATCH"
            if response.status_code in (200, 201):
                status_str = "SUCCESS"
                target_code = str(resp_json.get("batch_code") or f"LOT-{resp_json.get('id', '')}")
                if resp_json.get("current_holder_org"):
                    org = str(resp_json["current_holder_org"])
            else:
                status_str = "FAILED"
                target_code = str(req_json.get("batch_code", ""))
                reason = str(resp_json.get("detail", "Tạo lô thất bại"))

        elif norm_path.endswith("/split") and "/batches/" in norm_path and method == "POST":
            action = "SPLIT_BATCH"
            # Tìm ID lô mẹ từ URL hoặc response
            parent_id: Optional[int] = None
            if "parent_batch_id" in resp_json:
                try:
                    parent_id = int(resp_json["parent_batch_id"])
                except Exception:
                    pass
            if not parent_id:
                parts = norm_path.split("/")
                if "split" in parts:
                    idx = parts.index("split")
                    if idx > 0 and parts[idx - 1].isdigit():
                        parent_id = int(parts[idx - 1])

            code, batch_org = resolve_batch_info(request.app, parent_id)
            target_code = code
            if batch_org:
                org = batch_org

            if response.status_code in (200, 201):
                status_str = "SUCCESS"
            else:
                status_str = "FAILED"
                reason = str(resp_json.get("detail", "Tách lô thất bại"))

        elif norm_path.endswith("/merge") and "/batches" in norm_path and method == "POST":
            action = "MERGE_BATCH"
            if response.status_code in (200, 201):
                status_str = "SUCCESS"
                merged = resp_json.get("merged_batch", {})
                target_code = str(merged.get("batch_code") or f"LOT-{merged.get('id', '')}")
            else:
                status_str = "FAILED"
                reason = str(resp_json.get("detail", "Gộp lô thất bại"))

        elif "/batches/" in norm_path and norm_path.endswith("/events") and method == "POST":
            action = "RECORD_EVENT"
            parts = norm_path.split("/")
            idx = parts.index("events") if "events" in parts else -1
            b_id = int(parts[idx - 1]) if idx > 0 and parts[idx - 1].isdigit() else None
            code, batch_org = resolve_batch_info(request.app, b_id)
            target_code = code
            if batch_org:
                org = batch_org
            status_str = "SUCCESS" if response.status_code in (200, 201) else "FAILED"
            if status_str == "FAILED":
                reason = str(resp_json.get("detail", "Ghi sự kiện thất bại"))

        else:
            # Các thao tác sửa/xoá khác (PUT / DELETE)
            if "/batches/" in norm_path and method in ("PUT", "DELETE"):
                parts = norm_path.split("/")
                b_id_str = parts[-1]
                b_id = int(b_id_str) if b_id_str.isdigit() else None
                code, batch_org = resolve_batch_info(request.app, b_id)
                target_code = code
                if batch_org:
                    org = batch_org
                action = f"{method}_BATCH"
            elif "/farms/" in norm_path and method in ("PUT", "DELETE"):
                parts = norm_path.split("/")
                target_code = f"FARM-{parts[-1]}"
                action = f"{method}_FARM"
            elif norm_path.endswith("/farms") and method == "POST":
                action = "CREATE_FARM"
                target_code = str(resp_json.get("name") or f"FARM-{resp_json.get('id', '')}")

            if response.status_code >= 400:
                reason = str(resp_json.get("detail", f"Thao tác {action} thất bại"))

        # Ghi nhận vào CSDL
        save_audit_log_entry(
            app=request.app,
            actor=actor or "unknown",
            organization=org or get_tenant_org(),
            timestamp=timestamp,
            ip_address=client_ip,
            action=action,
            status=status_str,
            target_code=target_code,
            reason=reason,
            method=method,
            path=path,
        )

        return new_response


__all__ = ["AuditLogMiddleware"]
