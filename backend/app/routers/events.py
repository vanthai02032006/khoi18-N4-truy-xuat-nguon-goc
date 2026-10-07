"""API ghi nhận và kiểm tra chuỗi sự kiện bất biến của lô hàng (SCRUM-39 / SCRUM-44).

Đặc tính kỹ thuật:
- Chỉ cho phép ghi thêm (Append-only).
- Liên kết mật mã dạng chuỗi (Cryptographic hash-chain).
- Tự động kiểm tra tính toàn vẹn và phát hiện sửa lén.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Batch, BatchEvent, User
from app.schemas import BatchEventCreate, BatchEventResponse, BatchTimelineResponse
from app.security import compute_event_hash, get_current_user, require_farmer
from app.tenant import scope_query_by_tenant

router = APIRouter(prefix="/batches", tags=["Chuỗi sự kiện & Truy xuất (Batch Events)"])


@router.post(
    "/{batch_id}/events",
    response_model=BatchEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ghi thêm sự kiện vào chuỗi bản ghi lô hàng (Append-only)",
)
def record_batch_event(
    batch_id: int,
    data: BatchEventCreate,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchEvent:
    """Ghi thêm một sự kiện mới vào lô hàng.

    Tự động lấy hash của sự kiện trước đó làm previous_hash và sinh mã hash SHA-256
    mới bảo vệ tính toàn vẹn.
    """
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản với ID #{batch_id}.",
        )

    # Lấy sự kiện cuối cùng để lấy previous_hash
    stmt = (
        select(BatchEvent)
        .where(BatchEvent.batch_id == batch_id)
        .order_by(BatchEvent.id.desc())
        .limit(1)
    )
    last_event = db.scalars(stmt).first()
    prev_hash = last_event.hash if last_event else "0" * 64

    now_iso = datetime.now(timezone.utc).isoformat()
    actor_name = current_user.username
    event_hash = compute_event_hash(
        event_type=data.event_type,
        payload=data.payload,
        actor=actor_name,
        organization=data.organization,
        timestamp=now_iso,
        previous_hash=prev_hash,
    )

    new_event = BatchEvent(
        batch_id=batch_id,
        event_type=data.event_type,
        payload=data.payload,
        actor=actor_name,
        organization=data.organization,
        timestamp=now_iso,
        hash=event_hash,
        previous_hash=prev_hash,
    )
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    return new_event


@router.get(
    "/{batch_id}/events",
    response_model=BatchTimelineResponse,
    summary="Lấy dòng thời gian sự kiện và xác thực tính toàn vẹn (Audit Chain)",
)
def get_batch_timeline(
    batch_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BatchTimelineResponse:
    """Truy xuất toàn bộ chuỗi sự kiện của lô hàng và quét phát hiện sửa lén dữ liệu."""
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản với ID #{batch_id}.",
        )

    stmt = select(BatchEvent).where(BatchEvent.batch_id == batch_id)
    # Tự động lọc theo tổ chức nếu có yêu cầu cách ly dữ liệu (SCRUM-28)
    stmt = scope_query_by_tenant(stmt, BatchEvent).order_by(BatchEvent.id.asc())
    events = list(db.scalars(stmt).all())

    # Quét tính toàn vẹn của chuỗi hash (SCRUM-44)
    is_valid = True
    tampered_index = None
    expected_prev_hash = "0" * 64

    for idx, ev in enumerate(events):
        # 1. Kiểm tra previous_hash có khớp không
        if ev.previous_hash != expected_prev_hash:
            is_valid = False
            tampered_index = idx
            break

        # 2. Tính lại hash của chính bản ghi xem có bị sửa lén payload không
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
            tampered_index = idx
            break

        expected_prev_hash = ev.hash

    return BatchTimelineResponse(
        batch_id=batch_id,
        is_valid=is_valid,
        tampered_index=tampered_index,
        events=events,
    )
