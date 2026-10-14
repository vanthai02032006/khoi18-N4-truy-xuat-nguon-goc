"""Xem nhật ký thao tác (S-31 / SCRUM-90) - chỉ quản trị hệ thống."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, User
from app.security import require_admin

router = APIRouter(prefix="/audit-logs", tags=["Nhật ký thao tác (S-31)"])


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    username: str | None
    organization_id: str | None
    action: str
    method: str
    path: str
    object_type: str | None
    object_id: str | None
    status_code: int
    success: bool
    ip_address: str | None
    detail: str | None


@router.get("", response_model=list[AuditLogResponse], summary="Nhật ký thao tác, mới nhất trước")
def list_audit_logs(
    username: str | None = Query(None, description="Lọc theo tên đăng nhập."),
    action: str | None = Query(None, description="Lọc theo tên hành động (chứa chuỗi)."),
    object_id: str | None = Query(None, description="Lọc theo mã đối tượng (mã lô, mã phiếu...)."),
    only_failed: bool = Query(False, description="Chỉ lấy thao tác bị từ chối / đăng nhập thất bại."),
    cursor: int | None = Query(None, ge=1, description="Lấy các dòng có id nhỏ hơn cursor."),
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AuditLog]:
    stmt = select(AuditLog)
    if username:
        stmt = stmt.where(AuditLog.username == username.strip())
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"%{action.strip()}%"))
    if object_id:
        stmt = stmt.where(AuditLog.object_id == object_id.strip())
    if only_failed:
        stmt = stmt.where(AuditLog.success.is_(False))
    if cursor:
        stmt = stmt.where(AuditLog.id < cursor)
    return list(db.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit)).all())
