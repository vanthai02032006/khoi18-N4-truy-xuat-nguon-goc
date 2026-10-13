"""Router tra cứu nhật ký kiểm toán hệ thống (Audit Logs) dành cho Quản trị viên (Sprint 5+).

Chỉ tài khoản có vai trò ``admin`` mới được xem danh sách và chi tiết nhật ký thao tác
để làm rõ trách nhiệm khi có tranh cãi, khiếu nại hoặc kiểm toán tuân thủ.
"""

from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, User
from app.schemas import AuditLogResponse
from app.security import require_admin

router = APIRouter(tags=["Nhật ký kiểm toán (Audit Logs)"])


@router.get(
    "/audit-logs",
    response_model=list[AuditLogResponse],
    status_code=status.HTTP_200_OK,
    summary="Xem nhật ký kiểm toán thao tác hệ thống (chỉ admin)",
    description=(
        "Trả về danh sách nhật ký kiểm toán ghi nhận ai đã làm gì (đăng nhập, tạo lô, tách lô, v.v.), "
        "thời điểm, địa chỉ IP và mã đối tượng. Chỉ quản trị viên (`role=admin`) mới có quyền truy cập."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa xác thực hoặc sai thông tin đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Không có quyền quản trị viên (yêu cầu role=admin)."},
    },
)
@router.get(
    "/admin/audit-logs",
    response_model=list[AuditLogResponse],
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def list_audit_logs(
    limit: int = Query(100, ge=1, le=1000, description="Số lượng dòng tối đa."),
    offset: int = Query(0, ge=0, description="Vị trí bắt đầu."),
    action: Optional[str] = Query(None, description="Lọc theo thao tác (LOGIN, CREATE_BATCH, SPLIT_BATCH, ...)."),
    actor: Optional[str] = Query(None, description="Lọc theo người thực hiện (username)."),
    status_filter: Optional[str] = Query(None, alias="status", description="Lọc theo trạng thái (SUCCESS, FAILED)."),
    target_code: Optional[str] = Query(None, description="Lọc theo mã đối tượng."),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AuditLog]:
    """Lấy danh sách nhật ký kiểm toán hệ thống."""
    stmt = select(AuditLog).order_by(AuditLog.id.asc())

    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
    if status_filter:
        stmt = stmt.where(AuditLog.status == status_filter)
    if target_code:
        stmt = stmt.where(AuditLog.target_code == target_code)

    stmt = stmt.offset(offset).limit(limit)
    return list(db.scalars(stmt).all())


@router.get(
    "/audit-logs/{log_id}",
    response_model=AuditLogResponse,
    status_code=status.HTTP_200_OK,
    summary="Xem chi tiết một dòng nhật ký kiểm toán",
)
def get_audit_log_detail(
    log_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AuditLog:
    """Xem chi tiết một dòng nhật ký theo ID."""
    log_entry = db.get(AuditLog, log_id)
    if not log_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy dòng nhật ký kiểm toán #{log_id}.",
        )
    return log_entry


__all__ = ["router"]
