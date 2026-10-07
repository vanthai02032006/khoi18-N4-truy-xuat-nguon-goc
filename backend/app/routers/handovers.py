"""Router bàn giao quyền giữ lô nông sản (Handover) - luồng xác nhận / từ chối.

Endpoints:

- ``POST /handovers``                     : bên giao tạo phiếu bàn giao (``pending``).
- ``GET  /handovers``                     : tra cứu lịch sử bàn giao.
- ``GET  /handovers/{handover_id}``       : xem chi tiết một phiếu.
- ``POST /handovers/{handover_id}/accept``: **chỉ bên nhận** xác nhận tiếp nhận.
- ``POST /handovers/{handover_id}/reject``: **chỉ bên nhận** từ chối, kèm **lý do**.

Ba nguyên tắc nghiệp vụ quan trọng:

1. **Chỉ bên nhận được xác nhận/từ chối.** Kiểm tra ở **máy chủ** bằng dependency
   ``require_handover_receiver`` - dùng chung cho cả hai endpoint, nên không thể
   lách bằng cách gọi thẳng API (khác hẳn việc ẩn nút trên giao diện):
   không tồn tại → ``404``; không phải bên nhận → ``403``; phiếu không còn
   ``pending`` → ``400``. Thứ tự kiểm tra cố tình là *quyền trước, trạng thái
   sau* để không tiết lộ trạng thái phiếu cho người không có quyền.

2. **Xác nhận và từ chối là một giao dịch duy nhất.** Mỗi thao tác gom mọi thay
   đổi (trạng thái phiếu, quyền giữ lô, các dòng nhật ký) vào **một**
   ``db.commit()``; lỗi ở bất kỳ bước nào → ``db.rollback()`` huỷ toàn bộ, không
   để trạng thái nửa vời (đã đổi chủ nhưng chưa ghi được sự kiện).

3. **Xác nhận ghi đủ 2 sự kiện**: ``HANDOVER_ACCEPTED`` (bên nhận đồng ý nhận)
   và ``OWNER_CHANGED`` (quyền giữ lô đổi từ bên giao sang bên nhận). Từ chối
   ghi 1 sự kiện ``HANDOVER_REJECTED`` kèm **lý do** để lưu vết.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.events import record_event
from app.models import (
    EVENT_TYPE_HANDOVER_ACCEPTED,
    EVENT_TYPE_HANDOVER_PENDING,
    EVENT_TYPE_HANDOVER_REJECTED,
    EVENT_TYPE_OWNER_CHANGED,
    HANDOVER_STATUS_ACCEPTED,
    HANDOVER_STATUS_PENDING,
    HANDOVER_STATUS_REJECTED,
    Batch,
    Handover,
    User,
)
from app.schemas import (
    HandoverAccept,
    HandoverActionResponse,
    HandoverCreate,
    HandoverReject,
    HandoverResponse,
)
from app.security import get_current_user

router = APIRouter(
    prefix="/handovers",
    tags=["Handovers"],
)


def _to_response(handover: Handover, owner: str | None = None) -> HandoverResponse:
    """Chuyển ORM ``Handover`` -> ``HandoverResponse`` kèm chủ sở hữu hiện tại của lô."""
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
        current_batch_owner=owner,
    )


def require_handover_receiver(
    handover_id: int = Path(..., ge=1, description="ID phiếu bàn giao cần xử lý."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Handover:
    """Dependency: chỉ **bên nhận** của phiếu được đi tiếp.

    Dùng cho cả ``accept`` và ``reject`` nên quy tắc quyền chỉ tồn tại ở một chỗ.

    Args:
        handover_id: ID phiếu bàn giao (lấy từ đường dẫn).
        current_user: Tài khoản đang gọi API (đã xác thực bằng HTTP Basic).
        db: Session SQLAlchemy của request.

    Returns:
        Handover: Phiếu bàn giao đang ở trạng thái ``pending``.

    Raises:
        HTTPException: **404** nếu phiếu không tồn tại; **403** nếu phiếu không
            gắn tài khoản bên nhận hoặc người gọi không phải bên nhận;
            **400** nếu phiếu không còn ở trạng thái chờ xử lý.
    """
    handover = db.get(Handover, handover_id)
    if handover is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu bàn giao có id={handover_id}.",
        )

    # Kiểm tra quyền TRƯỚC khi kiểm tra trạng thái: người không có quyền không
    # được biết phiếu đang ở trạng thái nào.
    if handover.receiver_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Phiếu bàn giao #{handover_id} không gắn với tài khoản bên nhận nào, "
                "nên không có tài khoản nào được phép xác nhận hoặc từ chối."
            ),
        )
    if current_user.id != handover.receiver_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Chỉ bên nhận (tài khoản #{handover.receiver_id} - "
                f"{handover.receiver_name}) được phép xác nhận hoặc từ chối phiếu "
                f"bàn giao #{handover_id}."
            ),
        )

    if handover.status != HANDOVER_STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Phiếu bàn giao #{handover_id} không ở trạng thái chờ xử lý "
                f"(hiện tại: {handover.status})."
            ),
        )

    return handover


@router.post(
    "",
    response_model=HandoverResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo phiếu bàn giao lô nông sản",
    description=(
        "Tạo phiếu bàn giao lô sang bên nhận, trạng thái khởi tạo `pending`.\n\n"
        "- Lô **vẫn thuộc bên giao** trong suốt thời gian chờ.\n"
        "- Ghi sự kiện `HANDOVER_PENDING` vào nhật ký lô.\n"
        "- Mỗi lô chỉ có **tối đa 1 phiếu đang chờ** (ràng buộc ở cả tầng ứng "
        "dụng và CSDL)."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Lô đang có phiếu bàn giao chờ xử lý."},
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy lô nông sản."},
    },
)
def create_handover(
    payload: HandoverCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HandoverResponse:
    """Bên giao khởi tạo phiếu bàn giao cho một lô nông sản."""
    batch = db.get(Batch, payload.batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={payload.batch_id}.",
        )

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
                f"Lô nông sản #{payload.batch_id} đang có phiếu bàn giao chờ xử lý "
                f"(#{existing_pending.id}, bên nhận: {existing_pending.receiver_name}). "
                "Hãy xử lý phiếu hiện tại trước khi tạo phiếu mới."
            ),
        )

    # Lô chưa từng ghi nhận chủ sở hữu -> lấy theo chủ vùng trồng (hoặc chính
    # người tạo phiếu) để mọi lô đều có "tổ chức đang giữ" trước khi bàn giao.
    if not batch.current_owner:
        batch.current_owner = batch.farm.owner if batch.farm else current_user.username

    handover = Handover(
        batch_id=payload.batch_id,
        sender_id=current_user.id,
        sender_name=batch.current_owner,
        receiver_id=payload.receiver_id,
        receiver_name=payload.receiver_name.strip(),
        status=HANDOVER_STATUS_PENDING,
        notes=payload.notes.strip() if payload.notes else None,
    )
    db.add(handover)

    record_event(
        db,
        batch_id=payload.batch_id,
        event_type=EVENT_TYPE_HANDOVER_PENDING,
        actor=current_user.username,
        payload={
            "sender": handover.sender_name,
            "receiver": handover.receiver_name,
            "receiver_id": handover.receiver_id,
            "status": HANDOVER_STATUS_PENDING,
            "current_owner": batch.current_owner,
            "description": (
                f"Khởi tạo yêu cầu bàn giao lô #{payload.batch_id} "
                f"({batch.product_name}) từ [{handover.sender_name}] sang "
                f"[{handover.receiver_name}]. Lô vẫn thuộc bên giao cho tới khi "
                "bên nhận xác nhận."
            ),
        },
    )

    try:
        db.commit()
    except IntegrityError as exc:
        # Hai request tạo phiếu cùng lúc -> chỉ mục duy nhất một phần chặn lại.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Lô nông sản #{payload.batch_id} vừa phát sinh một phiếu bàn giao "
                "chờ xử lý khác. Vui lòng tải lại và thử lại."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể lưu phiếu bàn giao vào cơ sở dữ liệu.",
        ) from exc

    db.refresh(handover)
    db.refresh(batch)
    return _to_response(handover, batch.current_owner if batch else None)


@router.get(
    "",
    response_model=list[HandoverResponse],
    status_code=status.HTTP_200_OK,
    summary="Lấy lịch sử bàn giao",
    description="Tra cứu các phiếu bàn giao, lọc được theo lô (`batch_id`) và trạng thái (`status`).",
)
def list_handovers(
    batch_id: int | None = Query(None, gt=0, description="Lọc theo ID lô nông sản."),
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Lọc theo trạng thái: `pending` / `accepted` / `rejected`.",
    ),
    db: Session = Depends(get_db),
) -> list[HandoverResponse]:
    """Danh sách phiếu bàn giao, mới nhất trước."""
    stmt = select(Handover, Batch.current_owner).join(
        Batch, Batch.id == Handover.batch_id, isouter=True
    )
    if batch_id is not None:
        stmt = stmt.where(Handover.batch_id == batch_id)
    if status_filter:
        stmt = stmt.where(Handover.status == status_filter.lower().strip())

    stmt = stmt.order_by(Handover.created_at.desc(), Handover.id.desc())
    return [
        _to_response(handover, owner)
        for handover, owner in db.execute(stmt).all()
    ]


@router.get(
    "/{handover_id}",
    response_model=HandoverResponse,
    status_code=status.HTTP_200_OK,
    summary="Xem chi tiết một phiếu bàn giao",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy phiếu bàn giao."}},
)
def get_handover(
    handover_id: int = Path(..., ge=1, description="ID phiếu bàn giao."),
    db: Session = Depends(get_db),
) -> HandoverResponse:
    """Xem chi tiết một phiếu bàn giao kèm chủ sở hữu hiện tại của lô."""
    handover = db.get(Handover, handover_id)
    if handover is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu bàn giao có id={handover_id}.",
        )
    batch = db.get(Batch, handover.batch_id)
    return _to_response(handover, batch.current_owner if batch else None)


@router.post(
    "/{handover_id}/accept",
    response_model=HandoverActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Xác nhận tiếp nhận bàn giao (chỉ bên nhận)",
    description=(
        "Bên nhận xác nhận tiếp nhận lô. Trong **một giao dịch duy nhất**:\n\n"
        "1. Chuyển phiếu sang trạng thái `accepted`.\n"
        "2. Đổi **tổ chức đang giữ lô** (`current_owner`) sang bên nhận.\n"
        "3. Ghi **2 sự kiện** vào nhật ký lô: `HANDOVER_ACCEPTED` và `OWNER_CHANGED`.\n\n"
        "Lỗi ở bất kỳ bước nào sẽ rollback toàn bộ. Trường `recorded_events` trong "
        "kết quả cho biết các sự kiện đã ghi."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Phiếu không ở trạng thái chờ xử lý."},
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Người gọi không phải bên nhận của phiếu."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy phiếu bàn giao."},
    },
)
def accept_handover(
    payload: HandoverAccept | None = None,
    handover: Handover = Depends(require_handover_receiver),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HandoverActionResponse:
    """Xác nhận bàn giao: đổi quyền giữ lô và ghi đủ 2 sự kiện trong 1 transaction."""
    batch = db.get(Batch, handover.batch_id)
    old_owner = batch.current_owner if batch else handover.sender_name

    handover.status = HANDOVER_STATUS_ACCEPTED
    if payload is not None and payload.notes and payload.notes.strip():
        handover.notes = _append_note(handover.notes, f"Tiếp nhận: {payload.notes.strip()}")

    # Đổi "tổ chức đang giữ" của lô sang bên nhận.
    if batch is not None:
        batch.current_owner = handover.receiver_name

    # Sự kiện 1/2: bên nhận xác nhận tiếp nhận.
    record_event(
        db,
        batch_id=handover.batch_id,
        event_type=EVENT_TYPE_HANDOVER_ACCEPTED,
        actor=current_user.username,
        payload={
            "handover_id": handover.id,
            "sender": handover.sender_name,
            "receiver": handover.receiver_name,
            "receiver_id": handover.receiver_id,
            "status": HANDOVER_STATUS_ACCEPTED,
            "description": (
                f"Bên nhận [{handover.receiver_name}] đã xác nhận tiếp nhận lô "
                f"#{handover.batch_id} (phiếu bàn giao #{handover.id})."
            ),
        },
    )
    # Sự kiện 2/2: quyền giữ lô đổi chủ.
    record_event(
        db,
        batch_id=handover.batch_id,
        event_type=EVENT_TYPE_OWNER_CHANGED,
        actor=current_user.username,
        payload={
            "handover_id": handover.id,
            "old_owner": old_owner,
            "new_owner": handover.receiver_name,
            "description": (
                f"Quyền giữ lô #{handover.batch_id} chuyển từ [{old_owner}] sang "
                f"[{handover.receiver_name}]."
            ),
        },
    )

    _commit_or_rollback(db, "Không thể xác nhận tiếp nhận bàn giao.")

    db.refresh(handover)
    if batch is not None:
        db.refresh(batch)
    return HandoverActionResponse(
        **_to_response(handover, batch.current_owner if batch else None).model_dump(),
        recorded_events=[EVENT_TYPE_HANDOVER_ACCEPTED, EVENT_TYPE_OWNER_CHANGED],
    )


@router.post(
    "/{handover_id}/reject",
    response_model=HandoverActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Từ chối tiếp nhận bàn giao (chỉ bên nhận)",
    description=(
        "Bên nhận từ chối tiếp nhận lô. **Lý do là bắt buộc** (`reason`, rỗng → `422`).\n\n"
        "Trong **một giao dịch duy nhất**: phiếu chuyển sang `rejected`, lý do được "
        "lưu ở `notes` và trong sự kiện `HANDOVER_REJECTED`, còn **tổ chức đang giữ "
        "lô giữ nguyên** ở bên giao."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Phiếu không ở trạng thái chờ xử lý."},
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Người gọi không phải bên nhận của phiếu."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy phiếu bàn giao."},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Thiếu lý do từ chối."},
    },
)
def reject_handover(
    payload: HandoverReject,
    handover: Handover = Depends(require_handover_receiver),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HandoverActionResponse:
    """Từ chối bàn giao: giữ nguyên quyền giữ lô và lưu vết lý do từ chối."""
    batch = db.get(Batch, handover.batch_id)

    handover.status = HANDOVER_STATUS_REJECTED
    # Lưu lý do vào notes để tra cứu nhanh ngay trên phiếu.
    handover.notes = _append_note(handover.notes, f"Từ chối: {payload.reason}")

    # KHÔNG đổi batch.current_owner: lô vẫn thuộc bên giao.
    record_event(
        db,
        batch_id=handover.batch_id,
        event_type=EVENT_TYPE_HANDOVER_REJECTED,
        actor=current_user.username,
        payload={
            "handover_id": handover.id,
            "sender": handover.sender_name,
            "receiver": handover.receiver_name,
            "status": HANDOVER_STATUS_REJECTED,
            "reason": payload.reason,
            "current_owner": batch.current_owner if batch else handover.sender_name,
            "description": (
                f"Bên nhận [{handover.receiver_name}] đã từ chối tiếp nhận lô "
                f"#{handover.batch_id} (phiếu bàn giao #{handover.id}). "
                f"Lý do: {payload.reason}. Quyền giữ lô vẫn thuộc "
                f"[{batch.current_owner if batch else handover.sender_name}]."
            ),
        },
    )

    _commit_or_rollback(db, "Không thể cập nhật trạng thái từ chối bàn giao.")

    db.refresh(handover)
    if batch is not None:
        db.refresh(batch)
    return HandoverActionResponse(
        **_to_response(handover, batch.current_owner if batch else None).model_dump(),
        recorded_events=[EVENT_TYPE_HANDOVER_REJECTED],
    )


def _append_note(current: str | None, addition: str) -> str:
    """Nối thêm ghi chú vào cuối ``notes``, giữ lại nội dung cũ nếu có.

    Cắt bớt để không vượt ``notes VARCHAR(500)`` - ưu tiên giữ phần ghi chú mới
    nhất vì đó là lý do/ghi chú của lần xử lý hiện tại.
    """
    if not current:
        return addition[:500]
    combined = f"{current} | {addition}"
    return combined if len(combined) <= 500 else combined[-500:]


def _commit_or_rollback(db: Session, error_detail: str) -> None:
    """Commit transaction hiện tại; lỗi thì rollback sạch rồi trả HTTP 500.

    Toàn bộ thay đổi của thao tác (trạng thái phiếu, quyền giữ lô, các sự kiện)
    nằm trong cùng một transaction nên chỉ có hai kết cục: **tất cả** hoặc
    **không có gì**.
    """
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail,
        ) from exc


__all__ = [
    "accept_handover",
    "create_handover",
    "get_handover",
    "list_handovers",
    "reject_handover",
    "require_handover_receiver",
    "router",
]
