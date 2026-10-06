"""Router quản lý quy trình bàn giao lô nông sản (Handover) & Ràng buộc toàn vẹn.

Nghiệp vụ cốt lõi:
- Theo dõi trạng thái bàn giao: chờ xử lý (pending) / đã nhận (accepted) / từ chối (rejected).
- **Ràng buộc CSDL (Unique Constraint):** mỗi lô nông sản chỉ có tối đa 1 bàn giao đang ở trạng thái chờ xử lý.
  Chặn mọi hành vi cố tình tạo bàn giao thứ hai khi đang có bàn giao chờ.
- **Quyền quản lý:** Khi tạo bàn giao ở trạng thái chờ (pending), lô nông sản **vẫn thuộc quyền quản lý của bên giao**.
  Chỉ khi bên nhận bấm tiếp nhận (accepted) thì quyền quản lý mới chuyển sang bên nhận.
- **Ghi sự kiện (Task T-25):** Mọi thao tác bàn giao đều được tự động ghi lại vào nhật ký sự kiện
  qua hàm ``record_event`` phục vụ truy xuất nguồn gốc minh bạch.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.events import (
    EVENT_TYPE_HANDOVER_ACCEPTED,
    EVENT_TYPE_HANDOVER_PENDING,
    EVENT_TYPE_HANDOVER_REJECTED,
    record_event,
)
from app.models import (
    HANDOVER_STATUS_ACCEPTED,
    HANDOVER_STATUS_PENDING,
    HANDOVER_STATUS_REJECTED,
    Batch,
    Handover,
    User,
)
from app.schemas import HandoverAction, HandoverCreate, HandoverResponse
from app.security import require_farmer

router = APIRouter(
    prefix="/handovers",
    tags=["Handovers"],
)


def _to_handover_response(handover: Handover) -> HandoverResponse:
    """Helper chuyển đổi ORM Handover thành HandoverResponse kèm thông tin chủ sở hữu hiện tại."""
    current_owner = handover.batch.current_owner if handover.batch else None
    return HandoverResponse(
        id=handover.id,
        batch_id=handover.batch_id,
        sender_id=handover.sender_id,
        sender_name=handover.sender_name,
        receiver_id=handover.receiver_id,
        receiver_name=handover.receiver_name,
        status=handover.status,
        notes=handover.notes,
        created_at=handover.created_at,
        updated_at=handover.updated_at,
        current_batch_owner=current_owner,
    )


@router.post(
    "",
    response_model=HandoverResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo yêu cầu bàn giao lô nông sản",
    description=(
        "Tạo một yêu cầu bàn giao lô nông sản sang đối tác/bên tiếp nhận.\n\n"
        "- Trạng thái khởi tạo mặc định là **`pending` (chờ xử lý)**.\n"
        "- **Lô vẫn thuộc quyền quản lý của bên giao** trong suốt thời gian chờ.\n"
        "- Tự động ghi nhận sự kiện `HANDOVER_PENDING` vào nhật ký truy xuất nguồn gốc (T-25).\n"
        "- **Chặn tạo bàn giao thứ hai** nếu lô đang có một yêu cầu bàn giao chờ xử lý."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "Lô nông sản đang có bàn giao chờ hoặc dữ liệu không hợp lệ.",
        },
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Không có quyền thực hiện."},
        status.HTTP_404_NOT_FOUND: {"description": "Lô nông sản không tồn tại."},
    },
)
def create_handover(
    payload: HandoverCreate,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> HandoverResponse:
    """Khởi tạo yêu cầu bàn giao mới cho một lô nông sản."""
    # Bước 1: Kiểm tra lô nông sản tồn tại
    batch = db.get(Batch, payload.batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={payload.batch_id}.",
        )

    # Bước 2: Kiểm tra chặn tạo bàn giao thứ 2 khi đang có bàn giao chờ (Application-level check)
    existing_pending = db.scalar(
        select(Handover).where(
            Handover.batch_id == payload.batch_id,
            Handover.status == HANDOVER_STATUS_PENDING,
        )
    )
    if existing_pending is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Lô nông sản #{payload.batch_id} đang có một yêu cầu bàn giao ở trạng thái "
                f"chờ xử lý (Mã bàn giao #{existing_pending.id}, người nhận: {existing_pending.receiver_name}). "
                f"Không thể tạo thêm yêu cầu bàn giao mới cho đến khi yêu cầu hiện tại được tiếp nhận hoặc từ chối."
            ),
        )

    # Bước 3: Xác định bên giao (Sender)
    sender_name = payload.sender_name
    if not sender_name:
        sender_name = batch.current_owner or (batch.farm.owner if batch.farm else current_user.username)

    # Đảm bảo lô có ghi nhận chủ sở hữu hiện tại nếu chưa có
    if not batch.current_owner:
        batch.current_owner = sender_name

    # Lô vẫn thuộc quyền quản lý bên giao -> batch.current_owner giữ nguyên
    handover = Handover(
        batch_id=payload.batch_id,
        sender_id=current_user.id if current_user else None,
        sender_name=sender_name,
        receiver_id=payload.receiver_id,
        receiver_name=payload.receiver_name,
        status=HANDOVER_STATUS_PENDING,
        notes=payload.notes,
    )
    db.add(handover)

    # Bước 4: Ghi sự kiện qua hàm ở T-25
    record_event(
        db=db,
        batch_id=payload.batch_id,
        event_type=EVENT_TYPE_HANDOVER_PENDING,
        actor_name=sender_name,
        actor_id=current_user.id if current_user else None,
        description=(
            f"Khởi tạo yêu cầu bàn giao lô nông sản #{payload.batch_id} ({batch.product_name}) "
            f"từ bên giao [{sender_name}] sang bên nhận [{payload.receiver_name}]. "
            f"Trạng thái: Chờ xử lý. Quyền quản lý lô hàng hiện vẫn thuộc bên giao [{batch.current_owner}]."
        ),
        metadata_info={
            "batch_id": payload.batch_id,
            "sender": sender_name,
            "receiver": payload.receiver_name,
            "status": HANDOVER_STATUS_PENDING,
            "notes": payload.notes,
            "current_owner": batch.current_owner,
        },
    )

    # Bước 5: Lưu vào CSDL kèm xử lý ràng buộc Unique Index nếu có xung đột đồng thời
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Ràng buộc CSDL: Lô nông sản #{payload.batch_id} đã có một bàn giao "
                f"đang chờ xử lý. Không thể tạo bàn giao trùng lặp."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể lưu yêu cầu bàn giao vào cơ sở dữ liệu.",
        ) from exc

    db.refresh(handover)
    db.refresh(batch)
    return _to_handover_response(handover)


@router.get(
    "",
    response_model=list[HandoverResponse],
    status_code=status.HTTP_200_OK,
    summary="Lấy danh sách các yêu cầu bàn giao",
    description="Tra cứu danh sách bàn giao, hỗ trợ lọc theo mã lô (`batch_id`) hoặc trạng thái (`status`).",
)
def list_handovers(
    batch_id: int | None = Query(None, description="Lọc theo ID lô nông sản."),
    status_filter: str | None = Query(None, alias="status", description="Lọc theo trạng thái (pending/accepted/rejected)."),
    db: Session = Depends(get_db),
) -> list[HandoverResponse]:
    """Danh sách các yêu cầu bàn giao."""
    stmt = select(Handover)
    if batch_id is not None:
        stmt = stmt.where(Handover.batch_id == batch_id)
    if status_filter:
        stmt = stmt.where(Handover.status == status_filter.lower().strip())

    stmt = stmt.order_by(Handover.created_at.desc(), Handover.id.desc())
    handovers = list(db.scalars(stmt).all())
    return [_to_handover_response(h) for h in handovers]


@router.get(
    "/{handover_id}",
    response_model=HandoverResponse,
    status_code=status.HTTP_200_OK,
    summary="Xem chi tiết một yêu cầu bàn giao",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy yêu cầu bàn giao."}},
)
def get_handover(
    handover_id: int = Path(..., ge=1, description="ID yêu cầu bàn giao."),
    db: Session = Depends(get_db),
) -> HandoverResponse:
    """Xem chi tiết một yêu cầu bàn giao."""
    handover = db.get(Handover, handover_id)
    if handover is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy yêu cầu bàn giao có id={handover_id}.",
        )
    return _to_handover_response(handover)


@router.post(
    "/{handover_id}/accept",
    response_model=HandoverResponse,
    status_code=status.HTTP_200_OK,
    summary="Tiếp nhận bàn giao lô nông sản",
    description=(
        "Xác nhận đã tiếp nhận lô nông sản. Cập nhật trạng thái thành `accepted`, "
        "chuyển quyền quản lý lô sang bên nhận, và ghi sự kiện `HANDOVER_ACCEPTED` (T-25)."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Yêu cầu bàn giao không ở trạng thái chờ."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy yêu cầu bàn giao."},
    },
)
def accept_handover(
    payload: HandoverAction | None = None,
    handover_id: int = Path(..., ge=1, description="ID yêu cầu bàn giao cần tiếp nhận."),
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> HandoverResponse:
    """Bên nhận xác nhận tiếp nhận bàn giao thành công."""
    handover = db.get(Handover, handover_id)
    if handover is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy yêu cầu bàn giao có id={handover_id}.",
        )

    if handover.status != HANDOVER_STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Yêu cầu bàn giao #{handover_id} không ở trạng thái chờ xử lý (hiện tại: {handover.status}).",
        )

    action_notes = payload.notes if payload else None
    handover.status = HANDOVER_STATUS_ACCEPTED
    if action_notes:
        handover.notes = (f"{handover.notes} | " if handover.notes else "") + f"Tiếp nhận: {action_notes}"

    # Chuyển quyền quản lý lô sang bên nhận
    batch = handover.batch
    if batch:
        old_owner = batch.current_owner
        batch.current_owner = handover.receiver_name
    else:
        old_owner = handover.sender_name

    # Ghi sự kiện tiếp nhận qua hàm T-25
    record_event(
        db=db,
        batch_id=handover.batch_id,
        event_type=EVENT_TYPE_HANDOVER_ACCEPTED,
        actor_name=handover.receiver_name,
        actor_id=current_user.id if current_user else None,
        description=(
            f"Bên nhận [{handover.receiver_name}] đã xác nhận tiếp nhận lô nông sản #{handover.batch_id}. "
            f"Quyền quản lý lô hàng chính thức được chuyển giao từ [{old_owner}] sang [{handover.receiver_name}]."
        ),
        metadata_info={
            "handover_id": handover.id,
            "batch_id": handover.batch_id,
            "sender": handover.sender_name,
            "receiver": handover.receiver_name,
            "status": HANDOVER_STATUS_ACCEPTED,
            "notes": action_notes,
            "new_owner": handover.receiver_name,
        },
    )

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể cập nhật trạng thái tiếp nhận bàn giao.",
        ) from exc

    db.refresh(handover)
    if batch:
        db.refresh(batch)
    return _to_handover_response(handover)


@router.post(
    "/{handover_id}/reject",
    response_model=HandoverResponse,
    status_code=status.HTTP_200_OK,
    summary="Từ chối bàn giao lô nông sản",
    description=(
        "Từ chối tiếp nhận lô nông sản. Cập nhật trạng thái thành `rejected`, "
        "lô nông sản vẫn giữ nguyên thuộc bên giao, và ghi sự kiện `HANDOVER_REJECTED` (T-25)."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Yêu cầu bàn giao không ở trạng thái chờ."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy yêu cầu bàn giao."},
    },
)
def reject_handover(
    payload: HandoverAction | None = None,
    handover_id: int = Path(..., ge=1, description="ID yêu cầu bàn giao cần từ chối."),
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> HandoverResponse:
    """Bên nhận từ chối tiếp nhận bàn giao."""
    handover = db.get(Handover, handover_id)
    if handover is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy yêu cầu bàn giao có id={handover_id}.",
        )

    if handover.status != HANDOVER_STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Yêu cầu bàn giao #{handover_id} không ở trạng thái chờ xử lý (hiện tại: {handover.status}).",
        )

    action_notes = payload.notes if payload else None
    handover.status = HANDOVER_STATUS_REJECTED
    if action_notes:
        handover.notes = (f"{handover.notes} | " if handover.notes else "") + f"Từ chối: {action_notes}"

    batch = handover.batch
    # Lô vẫn thuộc quyền quản lý của bên giao

    # Ghi sự kiện từ chối qua hàm T-25
    record_event(
        db=db,
        batch_id=handover.batch_id,
        event_type=EVENT_TYPE_HANDOVER_REJECTED,
        actor_name=handover.receiver_name,
        actor_id=current_user.id if current_user else None,
        description=(
            f"Bên nhận [{handover.receiver_name}] đã từ chối tiếp nhận bàn giao lô nông sản #{handover.batch_id}. "
            f"Lý do/Ghi chú: {action_notes or 'Không nêu'}. "
            f"Lô hàng vẫn thuộc quyền quản lý của bên giao [{batch.current_owner if batch else handover.sender_name}]."
        ),
        metadata_info={
            "handover_id": handover.id,
            "batch_id": handover.batch_id,
            "sender": handover.sender_name,
            "receiver": handover.receiver_name,
            "status": HANDOVER_STATUS_REJECTED,
            "notes": action_notes,
            "current_owner": batch.current_owner if batch else handover.sender_name,
        },
    )

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể cập nhật trạng thái từ chối bàn giao.",
        ) from exc

    db.refresh(handover)
    if batch:
        db.refresh(batch)
    return _to_handover_response(handover)
