"""Router dành riêng cho Cán bộ kiểm tra thẩm định tính toàn vẹn chuỗi sự kiện lô hàng (T-28 / SCRUM-44).

Chỉ tài khoản có vai trò cán bộ kiểm tra (`inspector`) hoặc quản trị viên (`admin`)
mới có quyền truy cập các endpoint này.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Batch, BatchEvent, InspectionLog, User
from app.schemas import (
    BatchEventResponse,
    InspectionLogResponse,
    InspectionResultResponse,
)
from app.security import compute_event_hash, require_inspector

router = APIRouter(
    prefix="/inspections",
    tags=["Kiểm định & Thẩm định tính toàn vẹn (Inspector Audit)"],
)


@router.post(
    "/verify/{identifier}",
    response_model=InspectionResultResponse,
    status_code=status.HTTP_200_OK,
    summary="Kiểm định tính toàn vẹn chuỗi sự kiện lô hàng (T-28 / SCRUM-44)",
    description=(
        "Chỉ dành cho cán bộ kiểm tra (`inspector`) hoặc vai trò cao hơn (`admin`).\n"
        "Nhập mã lô (code 8 ký tự hoặc ID số), hệ thống kiểm tra toàn bộ chuỗi cryptographic hash.\n"
        "Tự động ghi nhận log kèm thời điểm kiểm tra vào cơ sở dữ liệu để đối chiếu về sau."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Không có quyền cán bộ kiểm tra."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy lô nông sản."},
    },
)
def verify_batch_integrity(
    identifier: str = Path(..., min_length=1, max_length=50, description="Mã lô 8 ký tự hoặc ID số của lô nông sản."),
    current_user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
) -> InspectionResultResponse:
    """Thực hiện thẩm định tính toàn vẹn và ghi log kiểm định."""
    identifier_clean = identifier.strip()

    # Tìm Batch theo ID số hoặc theo mã code 8 ký tự
    batch: Batch | None = None
    if identifier_clean.isdigit():
        batch = db.get(Batch, int(identifier_clean))

    if batch is None:
        batch = db.scalar(select(Batch).where(Batch.code == identifier_clean))

    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản nào khớp với định danh '{identifier_clean}'.",
        )

    # Lấy toàn bộ các sự kiện của lô theo thứ tự thời gian tăng dần
    stmt = (
        select(BatchEvent)
        .where(BatchEvent.batch_id == batch.id)
        .order_by(BatchEvent.id.asc())
    )
    events = list(db.scalars(stmt).all())

    is_valid = True
    error_type: str | None = None
    tampered_index: int | None = None
    tampered_event_id: int | None = None
    details: str = ""
    expected_prev_hash = "0" * 64

    if not events:
        details = "Lô nông sản này chưa phát sinh sự kiện chuỗi nào. Chuỗi chưa có dữ liệu để kiểm tra."
    else:
        for idx, ev in enumerate(events):
            # 1. Kiểm tra tính liên kết chuỗi (previous_hash)
            if ev.previous_hash != expected_prev_hash:
                is_valid = False
                error_type = "BROKEN_CHAIN"
                tampered_index = idx
                tampered_event_id = ev.id
                details = (
                    f"Phát hiện đứt gãy liên kết chuỗi tại sự kiện #{idx + 1} (ID #{ev.id} - '{ev.event_type}'). "
                    f"Giá trị previous_hash ({ev.previous_hash[:16]}...) không khớp với hash sự kiện đứng trước "
                    f"({expected_prev_hash[:16]}...). Bản ghi có thể đã bị xoá hoặc chèn bất hợp pháp!"
                )
                break

            # 2. Tính lại mã băm SHA-256 từ payload thực tế để phát hiện can thiệp sửa lén nội dung
            recomputed = compute_event_hash(
                event_type=ev.event_type,
                payload=ev.payload,
                actor=ev.actor,
                organization=ev.organization,
                timestamp=ev.timestamp,
                previous_hash=ev.previous_hash,
            )
            if recomputed != ev.hash:
                is_valid = False
                error_type = "TAMPERED_PAYLOAD"
                tampered_index = idx
                tampered_event_id = ev.id
                details = (
                    f"Phát hiện sai lệch mã băm nội dung tại sự kiện #{idx + 1} (ID #{ev.id} - '{ev.event_type}'). "
                    f"Nội dung dữ liệu (payload/thông tin) đã bị chỉnh sửa lén sau khi ghi nhận! "
                    f"Hash gốc: {ev.hash[:16]}..., Hash tính lại: {recomputed[:16]}..."
                )
                break

            expected_prev_hash = ev.hash

        if is_valid:
            details = (
                f"Chuỗi bản ghi gồm {len(events)} sự kiện đạt tiêu chuẩn toàn vẹn mật mã học 100%. "
                f"Không phát hiện bất kỳ sự can thiệp hoặc sửa đổi trái phép nào."
            )

    now_iso = datetime.now(timezone.utc).isoformat()

    # Ghi log kiểm định kèm thời điểm để đối chiếu về sau (DoD & Ràng buộc kỹ thuật)
    inspection_log = InspectionLog(
        batch_id=batch.id,
        batch_code=batch.code,
        inspector=current_user.username,
        timestamp=now_iso,
        is_valid=is_valid,
        error_type=error_type,
        tampered_index=tampered_index,
        details=details,
    )
    db.add(inspection_log)
    db.commit()

    return InspectionResultResponse(
        batch_id=batch.id,
        batch_code=batch.code,
        product_name=batch.product_name,
        is_valid=is_valid,
        error_type=error_type,
        tampered_index=tampered_index,
        tampered_event_id=tampered_event_id,
        details=details,
        inspector=current_user.username,
        timestamp=now_iso,
        total_events=len(events),
        events=[BatchEventResponse.model_validate(e) for e in events],
    )


@router.get(
    "/logs",
    response_model=list[InspectionLogResponse],
    summary="Xem lịch sử các lần kiểm định để đối chiếu về sau (T-28 / SCRUM-44)",
    description="Tra cứu danh sách nhật ký kiểm định đã được ghi log, hỗ trợ lọc theo mã lô.",
)
def list_inspection_logs(
    batch_code: str | None = Query(None, description="Lọc lịch sử theo mã lô 8 ký tự."),
    limit: int = Query(50, ge=1, le=200, description="Số lượng bản ghi tối đa trả về."),
    current_user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
) -> list[InspectionLog]:
    """Lấy danh sách log kiểm định đã ghi nhận."""
    _ = current_user
    stmt = select(InspectionLog)
    if batch_code:
        stmt = stmt.where(InspectionLog.batch_code == batch_code.strip())

    stmt = stmt.order_by(InspectionLog.id.desc()).limit(limit)
    return list(db.scalars(stmt).all())


@router.post(
    "/simulate-tamper/{identifier}",
    summary="Giả lập can thiệp sửa lén dữ liệu để kiểm thử nghiệm thu (DoD / Demo)",
    description=(
        "Chỉ dùng cho mục đích kiểm thử và demo nghiệm thu AC:\n"
        "Cố tình sửa payload của một sự kiện trong database mà không cập nhật lại hash, "
        "nhằm kiểm chứng phản ứng cảnh báo đỏ của hệ thống."
    ),
)
def simulate_tampering(
    identifier: str = Path(..., description="Mã lô 8 ký tự hoặc ID số."),
    event_index: int = Query(0, ge=0, description="Chỉ số sự kiện muốn can thiệp sửa lén (mặc định 0)."),
    current_user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Giả lập sửa lén payload để test trường hợp bị can thiệp."""
    _ = current_user
    identifier_clean = identifier.strip()

    batch: Batch | None = None
    if identifier_clean.isdigit():
        batch = db.get(Batch, int(identifier_clean))
    if batch is None:
        batch = db.scalar(select(Batch).where(Batch.code == identifier_clean))

    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản '{identifier_clean}'.",
        )

    stmt = select(BatchEvent).where(BatchEvent.batch_id == batch.id).order_by(BatchEvent.id.asc())
    events = list(db.scalars(stmt).all())

    if not events:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lô chưa có sự kiện nào để giả lập can thiệp.",
        )

    target_idx = min(event_index, len(events) - 1)
    target_event = events[target_idx]

    # Cố tình sửa payload để làm sai lệch hash
    original_payload = target_event.payload
    target_event.payload = original_payload + " [DA BI CAN THIEP / SUA LEN GIA MAO]"
    db.commit()

    return {
        "message": f"Đã giả lập can thiệp sửa lén sự kiện #{target_idx + 1} (ID #{target_event.id}).",
        "batch_code": batch.code,
        "tampered_event_id": target_event.id,
        "original_payload": original_payload,
        "new_payload": target_event.payload,
    }
