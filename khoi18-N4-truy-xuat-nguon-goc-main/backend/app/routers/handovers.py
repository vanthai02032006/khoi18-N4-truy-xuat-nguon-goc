"""Router quản lý phiếu bàn giao nông sản giữa các tổ chức đối tác (T-35 / Task 3 / Task 5).

Endpoints:
- POST /handovers: Tạo phiếu bàn giao mới.
- GET  /handovers: Lấy lịch sử các đợt bàn giao.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Batch, Handover, User
from app.schemas import HandoverCreate, HandoverResponse
from app.security import require_farmer

router = APIRouter(
    prefix="/handovers",
    tags=["Handovers"],
)


@router.post(
    "",
    response_model=HandoverResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo phiếu bàn giao nông sản",
    description=(
        "Tạo phiếu bàn giao lô hàng cho tổ chức đối tác nhận. "
        "Bắt buộc chọn tổ chức nhận và lô hàng hợp lệ."
    ),
)
def create_handover(
    payload: HandoverCreate,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> Handover:
    """Tạo phiếu bàn giao nông sản cho đối tác."""
    _ = current_user

    # Kiểm tra lô hàng tồn tại
    batch = db.get(Batch, payload.batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={payload.batch_id}.",
        )

    # Giả định tổ chức hiện tại của user gửi là 1 (hoặc lấy theo tenant)
    sender_org_id = 1
    if payload.recipient_org_id == sender_org_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tổ chức nhận bàn giao không được trùng với tổ chức hiện tại của bạn.",
        )

    handover = Handover(
        batch_id=payload.batch_id,
        sender_org_id=sender_org_id,
        recipient_org_id=payload.recipient_org_id,
        recipient_org_name=payload.recipient_org_name.strip(),
        notes=payload.notes.strip() if payload.notes else None,
        status="PENDING",
        is_overdue=False,
    )
    db.add(handover)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể lưu phiếu bàn giao vào cơ sở dữ liệu.",
        ) from exc

    db.refresh(handover)
    return handover


@router.get(
    "",
    response_model=list[HandoverResponse],
    status_code=status.HTTP_200_OK,
    summary="Lấy lịch sử bàn giao nông sản",
    description="Trả về toàn bộ các phiếu bàn giao, sắp xếp theo ID giảm dần.",
)
def list_handovers(db: Session = Depends(get_db)) -> list[Handover]:
    """Lấy danh sách các phiếu bàn giao."""
    stmt = select(Handover).order_by(Handover.id.desc())
    return list(db.scalars(stmt).all())
