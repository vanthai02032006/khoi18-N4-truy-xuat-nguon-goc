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
from app.models import Batch, Farm, User
from app.schemas import (
    BatchCreate,
    BatchMergeRequest,
    BatchMergeResponse,
    BatchResponse,
    BatchUpdate,
    DeleteResponse,
)
from app.security import require_admin, require_farmer

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
    batch = Batch(**payload.model_dump())
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


# ----------------------------------------------------------- Batch Merge (T-21 / T-44) ---
@router.post(
    "/merge",
    response_model=BatchMergeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Gộp các lô nông sản",
    description=(
        "Gộp nhiều lô nông sản thành một lô mới theo mẫu T-21 / T-44 (SCRUM-60).\n\n"
        "**Quy tắc kiểm tra đầu vào (Validation Layer):**\n"
        "1. Các lô phải có cùng mã sản phẩm (`product_name` / `product_id`).\n"
        "2. Các lô phải cùng do tổ chức / chủ sở hữu hiện tại nắm giữ.\n"
        "3. Khối lượng lấy phải lớn hơn 0 và không vượt quá khối lượng còn lại của từng lô.\n\n"
        "Nếu vi phạm, trả về lỗi `400 Bad Request` nêu rõ mã lô vi phạm."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "Dữ liệu đầu vào vi phạm quy tắc gộp (nêu rõ mã lô vi phạm).",
        },
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Vai trò không được phép."},
    },
)
def merge_batches_endpoint(
    payload: BatchMergeRequest,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchMergeResponse:
    """Xử lý yêu cầu gộp lô nông sản có kiểm tra ràng buộc đầu vào."""
    from datetime import date
    from app.schemas import BatchMergeResponse
    from app.validators.batch_merge import (
        BatchMergeInputItem,
        BatchRecord,
        validate_batch_merge_input,
    )

    # 1. Truy vấn các lô nguồn từ cơ sở dữ liệu
    source_ids = [item.batch_id for item in payload.source_batches]
    batches_in_db = db.scalars(select(Batch).where(Batch.id.in_(source_ids))).all()
    batches_map_db = {b.id: b for b in batches_in_db}

    # 2. Xây dựng bản đồ BatchRecord để truyền vào validator
    # Lưu ý: holder_org_id lấy theo farm.owner (hoặc farm_id) của tổ chức
    batches_map: dict[str | int, BatchRecord] = {}
    for b in batches_in_db:
        owner = b.farm.owner if b.farm else str(b.farm_id)
        batches_map[b.id] = BatchRecord(
            batch_id=b.id,
            product_id=b.product_name,
            holder_org_id=owner,
            remaining_quantity=b.quantity,
            product_name=b.product_name,
        )

    # Xác định tổ chức của user hiện tại (hoặc lấy từ lô nguồn hợp lệ đầu tiên nếu admin)
    first_batch = batches_in_db[0] if batches_in_db else None
    current_org_id = first_batch.farm.owner if (first_batch and first_batch.farm) else current_user.username

    # 3. Thực hiện kiểm tra đầu vào qua lớp validator chuẩn
    items_to_validate = [
        BatchMergeInputItem(batch_id=item.batch_id, take_quantity=item.take_quantity)
        for item in payload.source_batches
    ]
    validation_result = validate_batch_merge_input(
        items=items_to_validate,
        batches_map=batches_map,
        current_org_id=current_org_id,
        raise_exception=False,
    )

    if not validation_result.is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Dữ liệu đầu vào không hợp lệ khi thực hiện gộp lô.",
                "violating_batch_ids": validation_result.violating_batch_ids,
                "errors": [
                    {
                        "code": err.code.value,
                        "violating_batch_id": err.violating_batch_id,
                        "message": err.message,
                        "details": err.details,
                    }
                    for err in validation_result.errors
                ],
            },
        )

    # 4. Khi hợp lệ: Trừ số lượng lô nguồn và tạo lô mới
    total_new_quantity = sum(item.take_quantity for item in payload.source_batches)
    first_b = batches_map_db[payload.source_batches[0].batch_id]
    
    for item in payload.source_batches:
        b = batches_map_db[item.batch_id]
        b.quantity -= item.take_quantity

    new_batch = Batch(
        farm_id=first_b.farm_id,
        product_name=payload.new_batch_name or first_b.product_name,
        quantity=total_new_quantity,
        harvest_date=date.today(),
    )
    db.add(new_batch)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể lưu lô gộp mới vào cơ sở dữ liệu.",
        ) from exc

    db.refresh(new_batch)

    return BatchMergeResponse(
        message=f"Gộp {len(payload.source_batches)} lô nông sản thành công.",
        new_batch_id=new_batch.id,
        total_quantity=total_new_quantity,
        source_batch_ids=source_ids,
    )

