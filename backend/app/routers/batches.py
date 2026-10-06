"""Router quản lý lô nông sản (Batch).

Quan hệ: ``Farm 1 ---- N Batch``.

Cung cấp **đầy đủ CRUD** (hoàn thiện ở Sprint 5):

- ``POST   /batches``            : tạo lô nông sản (kiểm tra ``farm_id`` tồn tại).
- ``GET    /batches``            : lấy danh sách lô.
- ``GET    /batches/{batch_id}`` : xem chi tiết một lô.
- ``PUT    /batches/{batch_id}`` : cập nhật lô (có thể đổi sang vùng trồng khác).
- ``DELETE /batches/{batch_id}`` : xoá lô (chỉ admin).

**Phân quyền (Sprint 4):** ``POST``/``PUT`` dùng dependency ``require_farmer``
-> đăng nhập bằng role ``farmer`` hoặc ``admin`` (401 nếu chưa đăng nhập,
403 nếu sai vai trò); ``DELETE`` dùng ``require_admin`` -> chỉ admin. Hai endpoint
``GET`` giữ nguyên như trước (không yêu cầu đăng nhập) vì phục vụ tra cứu nguồn
gốc công khai.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Batch, BatchEvent, Farm, User
from app.schemas import (
    BatchCreate,
    BatchResponse,
    BatchUpdate,
    DeleteResponse,
    BatchEventCreate,
    BatchEventResponse,
    BatchEventVerifyResponse,
    BatchSplitRequest,
    BatchSplitResponse,
    BatchMergeRequest,
    BatchMergeResponse,
)
from app.security import require_admin, require_farmer
from app.batch_split import split_batch
from app.batch_merge import merge_batches_transaction
from app.event_chain import (
    record_batch_event,
    verify_batch_events_integrity,
    verify_batch_chain,
    build_10_events_for_batch,
    simulate_tamper_event,
    reset_batch_events,
)

router = APIRouter(
    prefix="/batches",
    tags=["Batches"],
)


@router.post(
    "",
    response_model=BatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo lô nông sản",
    description=(
        "Tạo một lô nông sản thuộc về một vùng trồng. "
        "Nếu `farm_id` không tồn tại, API trả về `404 Not Found`.\n\n"
        "**Phân quyền:** đăng nhập với role `farmer` hoặc `admin` (yêu cầu header "
        "`Authorization: Basic ...`)."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Chưa đăng nhập.",
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "Vai trò không được phép.",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Vùng trồng (farm_id) không tồn tại.",
        },
    },
)
def create_batch(
    payload: BatchCreate,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> Batch:
    """Tạo lô nông sản mới.

    Args:
        payload: Dữ liệu lô đã được Pydantic validate.
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Batch: Bản ghi lô vừa tạo (HTTP 201).

    Raises:
        HTTPException: 401/403 nếu chưa đăng nhập hoặc sai vai trò;
            404 nếu ``farm_id`` không tồn tại;
            500 nếu ghi database thất bại (đã rollback).
    """
    _ = current_user  # bắt buộc khai báo để dependency kiểm tra quyền chạy

    # Bước 1: kiểm tra toàn vẹn tham chiếu - vùng trồng phải tồn tại.
    farm = db.get(Farm, payload.farm_id)
    if farm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vùng trồng có id={payload.farm_id}.",
        )

    # Bước 2: lưu lô nông sản.
    batch_dict = payload.model_dump()
    if not batch_dict.get("batch_code"):
        from sqlalchemy import func
        from app.batch_split import generate_batch_code
        existing_count = (
            db.scalar(
                select(func.count())
                .select_from(Batch)
                .where(Batch.farm_id == payload.farm_id)
            )
            or 0
        )
        batch_dict["batch_code"] = generate_batch_code(
            farm_id=payload.farm_id,
            harvest_date=payload.harvest_date,
            parent_id=payload.parent_id,
            sequence=existing_count + 1,
        )
    if not batch_dict.get("owner"):
        batch_dict["owner"] = current_user.username

    batch = Batch(**batch_dict)
    db.add(batch)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể lưu lô nông sản vào cơ sở dữ liệu.",
        ) from exc

    db.refresh(batch)
    return batch


@router.get(
    "",
    response_model=list[BatchResponse],
    status_code=status.HTTP_200_OK,
    summary="Lấy danh sách lô nông sản",
    description="Trả về toàn bộ lô nông sản, sắp xếp theo `id` tăng dần.",
)
def list_batches(db: Session = Depends(get_db)) -> list[Batch]:
    """Lấy danh sách lô nông sản.

    Args:
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        list[Batch]: Danh sách lô (rỗng nếu chưa có dữ liệu).
    """
    return list(db.scalars(select(Batch).order_by(Batch.id)).all())


@router.get(
    "/{batch_id}",
    response_model=BatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Xem chi tiết một lô nông sản",
    description="Trả về thông tin chi tiết của lô theo `id`. Trả `404` nếu không tồn tại.",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Không tìm thấy lô nông sản.",
        },
    },
)
def get_batch(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần xem."),
    db: Session = Depends(get_db),
) -> Batch:
    """Lấy chi tiết một lô nông sản theo ``id``.

    Args:
        batch_id: ID của lô cần tìm.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Batch: Bản ghi lô tương ứng (HTTP 200).

    Raises:
        HTTPException: 404 nếu không tìm thấy lô.
    """
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )
    return batch


@router.put(
    "/{batch_id}",
    response_model=BatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Cập nhật lô nông sản",
    description=(
        "Cập nhật (thay thế) thông tin lô theo `id`. Client gửi đầy đủ các trường "
        "như khi tạo mới; `farm_id` mới cũng phải tồn tại. Trả `404` nếu lô "
        "**hoặc** vùng trồng không tồn tại.\n\n"
        "**Phân quyền:** đăng nhập với role `farmer` hoặc `admin`."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Vai trò không được phép."},
        status.HTTP_404_NOT_FOUND: {
            "description": "Không tìm thấy lô nông sản hoặc vùng trồng (farm_id).",
        },
    },
)
def update_batch(
    payload: BatchUpdate,
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần sửa."),
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> Batch:
    """Cập nhật thông tin lô nông sản theo ``id``.

    Args:
        payload: Dữ liệu mới đã được Pydantic validate (đủ 4 trường).
        batch_id: ID lô cần sửa.
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Batch: Bản ghi lô sau khi cập nhật (HTTP 200).

    Raises:
        HTTPException: 401/403 nếu chưa đăng nhập hoặc sai vai trò;
            404 nếu không tìm thấy lô hoặc ``farm_id`` mới;
            500 nếu ghi database thất bại (đã rollback).
    """
    _ = current_user

    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )

    # Kiểm tra lại toàn vẹn tham chiếu: vùng trồng (mới) phải tồn tại.
    farm = db.get(Farm, payload.farm_id)
    if farm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vùng trồng có id={payload.farm_id}.",
        )

    for field, value in payload.model_dump().items():
        setattr(batch, field, value)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể cập nhật lô nông sản trong cơ sở dữ liệu.",
        ) from exc

    db.refresh(batch)
    return batch


@router.delete(
    "/{batch_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Xoá lô nông sản (chỉ admin)",
    description=(
        "Xoá một lô nông sản theo `id`.\n\n"
        "**Phân quyền:** chỉ `role = admin` được xoá (dùng `require_admin`). "
        "Farmer gọi sẽ nhận `403 Forbidden` - giao diện cũng ẩn nút Xoá với farmer."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Đã đăng nhập nhưng không phải admin."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy lô nông sản."},
    },
)
def delete_batch(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần xoá."),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> DeleteResponse:
    """Xoá một lô nông sản (chỉ admin).

    Args:
        batch_id: ID lô cần xoá.
        current_user: Tài khoản admin đã được ``require_admin`` kiểm tra quyền.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        DeleteResponse: Thông báo kết quả xoá (HTTP 200).

    Raises:
        HTTPException: 401 nếu chưa đăng nhập; 403 nếu không phải admin;
            404 nếu không tìm thấy lô;
            500 nếu xoá trong database thất bại (đã rollback).
    """
    _ = current_user

    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )

    # Lưu lại tên sản phẩm để viết thông báo (sau khi xoá không đọc được nữa).
    product_name = batch.product_name

    db.delete(batch)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể xoá lô nông sản khỏi cơ sở dữ liệu.",
        ) from exc

    return DeleteResponse(
        message=f"Đã xoá lô nông sản #{batch_id} ({product_name}).",
        deleted_id=batch_id,
        # Xoá lô không kéo theo bản ghi nào khác -> null.
        deleted_batches=None,
    )


# ==============================================================================
# SỰ KIỆN CHUỖI CUNG ỨNG & KIỂM TRA TÍNH TOÀN VẸN (T-28 / T-31 / SCRUM-47)
# ==============================================================================


@router.get(
    "/{batch_id}/events",
    response_model=list[BatchEventResponse],
    status_code=status.HTTP_200_OK,
    summary="Lấy danh sách sự kiện dòng thời gian của lô (T-31)",
    description=(
        "Trả về danh sách sự kiện trong chuỗi cung ứng của lô theo thứ tự sequence tăng dần. "
        "Mỗi mốc sự kiện gồm: loại sự kiện, thời điểm, tổ chức thực hiện, dữ liệu và mã băm SHA-256."
    ),
)
def get_batch_events(
    batch_id: int = Path(..., ge=1, description="ID của lô nông sản"),
    db: Session = Depends(get_db),
) -> list[BatchEvent]:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )

    events = (
        db.query(BatchEvent)
        .filter(BatchEvent.batch_id == batch_id)
        .order_by(BatchEvent.sequence.asc(), BatchEvent.id.asc())
        .all()
    )
    return events


@router.post(
    "/{batch_id}/events",
    response_model=BatchEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ghi nhận sự kiện chuỗi cung ứng mới (T-31)",
    description=(
        "Ghi nhận thêm một mốc sự kiện vào dòng thời gian của lô nông sản. "
        "Hệ thống tự động liên kết prev_hash và tính mã băm SHA-256 bảo đảm tính toàn vẹn."
    ),
)
def add_batch_event(
    batch_id: int = Path(..., ge=1, description="ID của lô nông sản"),
    payload: BatchEventCreate = ...,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchEvent:
    _ = current_user
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )

    event = record_batch_event(
        db=db,
        batch_id=batch_id,
        event_type=payload.event_type,
        data=payload.data,
        organization=payload.organization,
        timestamp=payload.timestamp,
    )
    return event


@router.get(
    "/{batch_id}/events/verify",
    response_model=BatchEventVerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Kiểm tra tính toàn vẹn chuỗi sự kiện (Hàm T-28)",
    description=(
        "Quét toàn bộ chuỗi sự kiện của lô: đối chiếu prev_hash liên kết và "
        "tính toán lại hash nội dung từng bản ghi. Phát hiện 100% nếu có sửa lén (SQL UPDATE) "
        "hoặc xoá bản ghi (SQL DELETE)."
    ),
)
def verify_batch_events_endpoint(
    batch_id: int = Path(..., ge=1, description="ID của lô nông sản"),
    db: Session = Depends(get_db),
) -> BatchEventVerifyResponse:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )

    report = verify_batch_events_integrity(db=db, batch_id=batch_id)
    return BatchEventVerifyResponse(**report.model_dump())


@router.get(
    "/{batch_id}/verify-chain",
    response_model=BatchEventVerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Bí danh kiểm tra tính toàn vẹn chuỗi sự kiện (Hàm T-28)",
    description="Route ngắn gọn cho hàm kiểm tra toàn vẹn T-28.",
)
def verify_batch_chain_endpoint(
    batch_id: int = Path(..., ge=1, description="ID của lô nông sản"),
    db: Session = Depends(get_db),
) -> BatchEventVerifyResponse:
    return verify_batch_events_endpoint(batch_id, db)


@router.post(
    "/{batch_id}/seed-events",
    response_model=list[BatchEventResponse],
    status_code=status.HTTP_200_OK,
    summary="Tạo 10 sự kiện chuẩn chuỗi lạnh cho lô (Phục vụ demo & nghiệm thu)",
    description="Dựng một chuỗi đầy đủ 10 sự kiện chuẩn nối băm mật mã SHA-256 từ thu hoạch đến bán lẻ.",
)
def seed_batch_events_endpoint(
    batch_id: int = Path(..., ge=1, description="ID của lô nông sản"),
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> list[BatchEvent]:
    _ = current_user
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )

    events = build_10_events_for_batch(db, batch_id)
    return events


@router.post(
    "/{batch_id}/simulate-tamper",
    response_model=BatchEventVerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Giả lập sửa lén dữ liệu qua SQL trực tiếp (Kiểm thử cảnh báo T-31)",
    description=(
        "Mô phỏng hành vi hacker hoặc kẻ xấu can thiệp trái phép cơ sở dữ liệu "
        "bằng SQL UPDATE để kiểm tra xem hệ thống có bật banner cảnh báo đỏ ở đầu hay không."
    ),
)
def simulate_tamper_endpoint(
    batch_id: int = Path(..., ge=1, description="ID của lô nông sản"),
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchEventVerifyResponse:
    _ = current_user
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )

    simulate_tamper_event(db, batch_id)
    report = verify_batch_events_integrity(db=db, batch_id=batch_id)
    return BatchEventVerifyResponse(**report.model_dump())


@router.post(
    "/{batch_id}/reset-events",
    response_model=BatchEventVerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Khôi phục chuỗi sự kiện nguyên vẹn 100% (Phục vụ demo)",
    description="Dựng lại chuỗi sự kiện nguyên vẹn hoàn toàn để trả về trạng thái hợp lệ.",
)
def reset_batch_events_endpoint(
    batch_id: int = Path(..., ge=1, description="ID của lô nông sản"),
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchEventVerifyResponse:
    _ = current_user
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )

    reset_batch_events(db, batch_id)
    report = verify_batch_events_integrity(db=db, batch_id=batch_id)
    return BatchEventVerifyResponse(**report.model_dump())


# ----------------------------------------------- Tách lô nông sản (T-40 / T-41 / SCRUM-57) ---
@router.post(
    "/{batch_id}/split",
    response_model=BatchSplitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tách lô nông sản có khoá dòng lô mẹ (T-40 / T-41 / SCRUM-57)",
    description=(
        "Tách lô nông sản mẹ thành danh sách các lô con.\n\n"
        "- Sử dụng **khóa dòng bi quan (`SELECT ... FOR UPDATE`)** để giao dịch thứ hai phải chờ "
        "và đọc được số dư đã trừ, chống 100% race condition và xuất khống.\n"
        "- Khối lượng dùng kiểu **số thập phân cố định (Decimal/Numeric)**, tuyệt đối không dùng float.\n"
        "- Thao tác tách vượt khối lượng còn lại bị từ chối ngay lập tức (`400 Bad Request`)."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "Dữ liệu không hợp lệ hoặc tổng khối lượng tách vượt quá khối lượng khả dụng.",
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Chưa đăng nhập.",
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "Không có quyền thực hiện (chỉ farmer hoặc admin).",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Không tìm thấy lô mẹ.",
        },
    },
)
def split_batch_endpoint(
    payload: BatchSplitRequest,
    batch_id: int = Path(..., ge=1, description="ID của lô nông sản mẹ cần tách"),
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchSplitResponse:
    """Endpoint tách lô nông sản có khóa dòng lô mẹ."""
    # Kiểm tra lô tồn tại trước
    existing_batch = db.get(Batch, batch_id)
    if existing_batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản mẹ #{batch_id}.",
        )

    initial_qty = existing_batch.quantity
    parent_code = existing_batch.batch_code

    try:
        updated_parent, created_children = split_batch(
            db=db,
            parent_batch_id=batch_id,
            child_quantities=payload.child_quantities,
            operator_user=current_user,
        )
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi hệ thống trong giao dịch tách lô: {err}",
        ) from err

    total_split_qty = sum(payload.child_quantities)
    return BatchSplitResponse(
        parent_batch_id=updated_parent.id,
        parent_batch_code=updated_parent.batch_code or parent_code,
        initial_quantity=initial_qty,
        remaining_quantity=updated_parent.quantity,
        total_split_quantity=total_split_qty,
        child_batches=[BatchResponse.model_validate(c) for c in created_children],
        message=(
            f"Tách thành công {len(created_children)} lô con từ lô mẹ #{batch_id}. "
            f"Khối lượng còn lại của lô mẹ: {updated_parent.quantity} kg."
        ),
    )


@router.post(
    "/merge",
    response_model=BatchMergeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Gộp nhiều lô nông sản (Batch Merge)",
    description=(
        "Thực hiện gộp tối thiểu 2 lô mẹ thành một lô mới với khóa dòng chống deadlock "
        "(sắp xếp ID tăng dần) và bảo toàn khối lượng bằng số học Decimal (T-44 / T-46 / SCRUM-60 / SCRUM-62)."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "Dữ liệu không hợp lệ (trùng lô mẹ, vượt tồn kho, thiếu lô mẹ...).",
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Chưa đăng nhập.",
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "Không có quyền thực hiện (chỉ farmer hoặc admin).",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Không tìm thấy lô mẹ hoặc vùng trồng.",
        },
    },
)
def merge_batches_endpoint(
    payload: BatchMergeRequest,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchMergeResponse:
    """Endpoint gộp nhiều lô nông sản có khóa dòng chống deadlock."""
    try:
        return merge_batches_transaction(
            db=db,
            payload=payload,
            current_user=current_user,
        )
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi hệ thống trong giao dịch gộp lô: {err}",
        ) from err



