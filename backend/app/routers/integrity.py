"""Router API Job S-12 kiểm tra tính toàn vẹn dữ liệu chuỗi cung ứng (SCRUM-136)."""

from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import (
    IntegrityCheckLogResponse,
    IntegrityCheckRunResponse,
    IntegrityViolationItem,
)
from app.security import require_admin, require_farmer
from app.services.integrity_service import (
    get_latest_integrity_check,
    list_integrity_checks,
    run_s12_integrity_check,
)

router = APIRouter(
    prefix="/integrity-check",
    tags=["Data Integrity S-12 (SCRUM-136)"],
)


@router.post(
    "/run",
    response_model=IntegrityCheckRunResponse,
    status_code=status.HTTP_200_OK,
    summary="Kích hoạt kiểm tra tính toàn vẹn Job S-12 (chỉ admin)",
    description=(
        "Chạy quy tắc kiểm định toàn vẹn dữ liệu S-12 (quy tắc nghiệp vụ đề xuất): "
        "quét foreign keys, kiểm tra bất thường sản lượng, tính hash-chain SHA-256 "
        "và phát hiện chu trình phả hệ. Ghi log cảnh báo mà không tự ý sửa/xoá dữ liệu."
    ),
)
def run_integrity_check_endpoint(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> IntegrityCheckRunResponse:
    log_entry, violations = run_s12_integrity_check(
        db,
        triggered_by=f"MANUAL_ADMIN_{current_user.username}",
    )
    items = [
        IntegrityViolationItem(
            category=v["category"],
            table=v["table"],
            record_id=str(v["record_id"]),
            message=v["message"],
        )
        for v in violations
    ]
    msg = (
        "Dữ liệu hoàn toàn nhất quán và toàn vẹn theo quy tắc S-12."
        if log_entry.status == "PASSED"
        else f"Phát hiện {log_entry.error_count} vi phạm tính toàn vẹn dữ liệu!"
    )
    return IntegrityCheckRunResponse(
        message=msg,
        result=log_entry,
        violations=items,
    )


@router.get(
    "/latest",
    response_model=IntegrityCheckLogResponse,
    status_code=status.HTTP_200_OK,
    summary="Xem kết quả kiểm tra toàn vẹn gần nhất",
)
def get_latest_check_endpoint(
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> IntegrityCheckLogResponse:
    _ = current_user
    latest = get_latest_integrity_check(db)
    if not latest:
        # Nếu chưa từng chạy, tự động kích hoạt một lần đầu
        latest, _ = run_s12_integrity_check(db, triggered_by="SYSTEM_INITIAL_RUN")
    return latest


@router.get(
    "/history",
    response_model=list[IntegrityCheckLogResponse],
    status_code=status.HTTP_200_OK,
    summary="Xem lịch sử các lần chạy Job S-12 (chỉ admin)",
)
def get_history_checks_endpoint(
    limit: int = 50,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[IntegrityCheckLogResponse]:
    _ = current_user
    return list_integrity_checks(db, limit=limit)
