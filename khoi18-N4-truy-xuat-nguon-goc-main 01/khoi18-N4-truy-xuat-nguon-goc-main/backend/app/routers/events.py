"""Router tra cứu nhật ký sự kiện vòng đời lô nông sản (Traceability Events - Task T-25).

Cung cấp các endpoint công khai để người tiêu dùng và các đối tác trong chuỗi cung ứng
có thể tra cứu toàn bộ dòng sự kiện truy xuất nguồn gốc của lô nông sản.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Batch, Event
from app.schemas import EventResponse

router = APIRouter(
    prefix="/events",
    tags=["Events"],
)


@router.get(
    "",
    response_model=list[EventResponse],
    status_code=status.HTTP_200_OK,
    summary="Lấy danh sách nhật ký sự kiện",
    description="Tra cứu các sự kiện trong hệ thống, có thể lọc theo `batch_id` hoặc `event_type`.",
)
def list_events(
    batch_id: int | None = Query(None, description="Lọc sự kiện theo ID lô nông sản."),
    event_type: str | None = Query(None, description="Lọc theo loại sự kiện (HANDOVER_PENDING, HANDOVER_ACCEPTED, ...)."),
    db: Session = Depends(get_db),
) -> list[Event]:
    """Danh sách sự kiện vòng đời nông sản."""
    stmt = select(Event)
    if batch_id is not None:
        stmt = stmt.where(Event.batch_id == batch_id)
    if event_type:
        stmt = stmt.where(Event.event_type == event_type.strip())

    stmt = stmt.order_by(Event.created_at.desc(), Event.id.desc())
    return list(db.scalars(stmt).all())


@router.get(
    "/batch/{batch_id}",
    response_model=list[EventResponse],
    status_code=status.HTTP_200_OK,
    summary="Tra cứu dòng sự kiện truy xuất nguồn gốc của một lô nông sản",
    description="Trả về toàn bộ tiến trình sự kiện từ khi khởi tạo lô đến các lần bàn giao.",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Lô nông sản không tồn tại."},
    },
)
def get_batch_events(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần tra cứu."),
    db: Session = Depends(get_db),
) -> list[Event]:
    """Lấy toàn bộ lịch sử sự kiện của một lô nông sản theo thứ tự thời gian."""
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )

    stmt = (
        select(Event)
        .where(Event.batch_id == batch_id)
        .order_by(Event.created_at.asc(), Event.id.asc())
    )
    return list(db.scalars(stmt).all())
