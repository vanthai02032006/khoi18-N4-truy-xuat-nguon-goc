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

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Batch, BatchCustodyHistory, Farm, User
from app.schemas import (
    BatchCreate,
    BatchDetailResponse,
    BatchMergeRequest,
    BatchMergeResponse,
    BatchResponse,
    BatchUpdate,
    DeleteResponse,
)
from app.security import get_current_user, require_admin, require_farmer
from app.services.authorization_service import can_user_view_batch, filter_viewable_batches
from app.services.batch_service import merge_batches_transaction

router = APIRouter(
    prefix="/batches",
    tags=["Batches"],
)


@router.post(
    "/merge",
    response_model=BatchMergeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Giao dịch gộp lô nông sản (SCRUM-60)",
    description=(
        "Thực hiện giao dịch gộp nhiều lô mẹ thành một lô mới trong Database Transaction.\n\n"
        "- Validate tồn kho, khoá các lô mẹ theo thứ tự tăng dần chống deadlock.\n"
        "- Trừ tồn kho từng lô mẹ và sinh lô con mới với tổng khối lượng.\n"
        "- Lưu vết quan hệ phả hệ parent-child.\n"
        "- Rollback toàn bộ nếu có bất kỳ bước nào thất bại."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Dữ liệu không hợp lệ hoặc vượt tồn kho."},
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Vai trò không được phép."},
        status.HTTP_404_NOT_FOUND: {"description": "Lô mẹ hoặc vùng trồng không tồn tại."},
    },
)
def merge_batches_endpoint(
    payload: BatchMergeRequest,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchMergeResponse:
    """Gộp nhiều lô mẹ thành một lô mới."""
    return merge_batches_transaction(db, payload, current_user)


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
    """Tạo lô nông sản mới."""
    _ = current_user

    # Bước 1: kiểm tra toàn vẹn tham chiếu - vùng trồng phải tồn tại.
    farm = db.get(Farm, payload.farm_id)
    if farm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vùng trồng có id={payload.farm_id}.",
        )

    # Bước 2: lưu lô nông sản.
    batch_data = payload.model_dump()
    if "remaining_quantity" not in batch_data or batch_data.get("remaining_quantity") is None:
        batch_data["remaining_quantity"] = payload.quantity
    if not batch_data.get("organization_id"):
        batch_data["organization_id"] = getattr(current_user, "organization_id", None) or "ORG_MY_XUONG"

    batch = Batch(**batch_data)
    db.add(batch)

    try:
        db.flush()
        db.add(BatchCustodyHistory(batch_id=batch.id, organization_id=batch.organization_id))
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
    description=(
        "Trả về danh sách lô nông sản mà người dùng có quyền xem theo quy tắc SCRUM-70. "
        "Hỗ trợ tìm kiếm theo mã lô (hoặc từ khoá) và lọc theo loại sản phẩm nông sản."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
    },
)
def list_batches(
    search: str | None = Query(default=None, description="Tìm theo mã lô hoặc tên sản phẩm"),
    product_name: str | None = Query(default=None, description="Bộ lọc theo tên loại nông sản"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Batch]:
    """Lấy danh sách lô nông sản có phân quyền và tìm kiếm/lọc."""
    query = select(Batch).order_by(Batch.id)

    if product_name and product_name.strip():
        query = query.where(Batch.product_name == product_name.strip())

    all_batches = list(db.scalars(query).all())

    if search and search.strip():
        term = search.strip().lower()
        all_batches = [
            b for b in all_batches
            if str(b.id) == term or term in b.product_name.lower()
        ]

    return filter_viewable_batches(db, current_user, all_batches)


@router.get(
    "/{batch_id}",
    response_model=BatchDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Xem chi tiết một lô nông sản",
    description=(
        "Trả về thông tin chi tiết và quan hệ phả hệ của lô theo `id`. "
        "Kiểm tra phân quyền theo quy tắc SCRUM-70: user/org phải đang giữ, "
        "từng giữ, hoặc là tổ tiên của lô đang giữ."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Chưa đăng nhập.",
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "Không có quyền xem lô này theo quy tắc SCRUM-70.",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Không tìm thấy lô nông sản.",
        },
    },
)
def get_batch(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần xem."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Batch:
    """Lấy chi tiết một lô nông sản theo ``id`` có kiểm tra quyền SCRUM-70."""
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )

    if not can_user_view_batch(db, current_user, batch_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản hoặc tổ chức của bạn không có quyền xem thông tin lô nông sản này.",
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
