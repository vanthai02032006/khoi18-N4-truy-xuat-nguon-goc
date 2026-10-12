"""Router API sao lưu cơ sở dữ liệu và diễn tập khôi phục (SCRUM-138) - Chỉ Admin."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import (
    BackupLogResponse,
    BackupRehearsalRequest,
    BackupRehearsalResponse,
    BackupRunResponse,
)
from app.security import require_admin
from app.services.backup_service import (
    cleanup_old_backups,
    execute_database_backup,
    list_backups,
    rehearse_restore,
)

router = APIRouter(
    prefix="/backups",
    tags=["Backups & Disaster Recovery (SCRUM-138)"],
)


@router.post(
    "/run",
    response_model=BackupRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Kích hoạt sao lưu CSDL ngay lập tức (chỉ admin)",
    description=(
        "Sử dụng SQLite Online Backup API để sao lưu nhất quán CSDL hiện tại. "
        "File sao lưu được kiểm tra integrity và lưu vào thư mục lưu trữ độc lập. "
        "Tự động duy trì tối đa 14 bản sao lưu gần nhất."
    ),
)
def trigger_backup(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> BackupRunResponse:
    _ = current_user
    try:
        backup_log = execute_database_backup(db)
        all_backups = list_backups(db)
        return BackupRunResponse(
            message=f"Sao lưu CSDL thành công. Checksum SHA-256: {backup_log.sha256_checksum[:16]}...",
            backup=backup_log,
            retained_backups_count=len(all_backups),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Tiến trình sao lưu thất bại: {exc}",
        )


@router.get(
    "",
    response_model=list[BackupLogResponse],
    status_code=status.HTTP_200_OK,
    summary="Xem lịch sử và danh sách các bản sao lưu (chỉ admin)",
)
def get_backups_list(
    limit: int = 50,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[BackupLogResponse]:
    _ = current_user
    return list_backups(db, limit=limit)


@router.post(
    "/rehearsal",
    response_model=BackupRehearsalResponse,
    status_code=status.HTTP_200_OK,
    summary="Diễn tập khôi phục trên CSDL tạm (chỉ admin)",
    description=(
        "Khôi phục file bản sao lưu vào một CSDL SQLite tạm thời, kiểm tra "
        "PRAGMA integrity_check và đếm số lượng bản ghi của các bảng dữ liệu nghiệp vụ. "
        "Tuyệt đối không ghi đè CSDL đang hoạt động."
    ),
)
def trigger_restore_rehearsal(
    payload: BackupRehearsalRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> BackupRehearsalResponse:
    _ = current_user
    try:
        result = rehearse_restore(
            db,
            backup_id=payload.backup_id,
            backup_file_path=payload.backup_file_path,
        )
        return BackupRehearsalResponse(
            status=result["status"],
            tested_file=result["tested_file"],
            integrity_check_output=result["integrity_check_output"],
            tables_checked=result["tables_checked"],
            details=result["details"],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Diễn tập khôi phục thất bại: {exc}",
        )
