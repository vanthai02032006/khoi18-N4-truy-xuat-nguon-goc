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
from app.events import EVENT_TYPE_HARVEST, record_event
from app.models import Batch, Event, Farm, User
from app.schemas import (
    BatchCreate,
    BatchResponse,
    BatchUpdate,
    DeleteResponse,
    EventResponse,
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
    summary="Tạo lô nông sản và ghi sự kiện thu hoạch",
    description=(
        "Tạo một lô nông sản thuộc về một vùng trồng và tự động ghi nhận sự kiện thu hoạch "
        "(HARVEST) đầu tiên trong cùng 1 transaction duy nhất với mã băm SHA-256 (T-20 & T-24).\n\n"
        "Nếu `farm_id` không tồn tại, API trả về `404 Not Found`.\n"
        "Nếu có bất kỳ lỗi nào xảy ra trong quá trình tạo lô hoặc ghi sự kiện, transaction sẽ "
        "được rollback sạch (cả lô lẫn sự kiện đều không được lưu).\n\n"
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
    """Tạo lô nông sản mới và ghi sự kiện thu hoạch trong cùng 1 transaction duy nhất.

    Args:
        payload: Dữ liệu lô đã được Pydantic validate.
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Batch: Bản ghi lô vừa tạo (HTTP 201).

    Raises:
        HTTPException: 401/403 nếu chưa đăng nhập hoặc sai vai trò;
            404 nếu ``farm_id`` không tồn tại;
            500 nếu ghi database thất bại (đã rollback sạch cả lô và sự kiện).
    """
    _ = current_user  # bắt buộc khai báo để dependency kiểm tra quyền chạy

    # Bước 1: kiểm tra toàn vẹn tham chiếu - vùng trồng phải tồn tại.
    farm = db.get(Farm, payload.farm_id)
    if farm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vùng trồng có id={payload.farm_id}.",
        )

    # Bước 2: thực hiện tạo lô và ghi sự kiện thu hoạch trong cùng 1 transaction duy nhất (T-20 + T-24)
    try:
        # Thêm lô nông sản vào session
        batch = Batch(**payload.model_dump())
        db.add(batch)
        # db.flush() đẩy dữ liệu xuống database để sinh batch.id nhưng CHƯA commit
        db.flush()

        # Ghi nhận sự kiện thu hoạch (HARVEST) bằng hàm ghi sự kiện (T-24 / SCRUM-40).
        # Hàm record_event nhận transaction `db` từ bên gọi, không tự mở transaction riêng.
        harvest_details = {
            "farm_id": farm.id,
            "farm_name": farm.name,
            "farm_location": farm.location,
            "product_name": batch.product_name,
            "quantity": batch.quantity,
            "harvest_date": str(batch.harvest_date),
        }
        record_event(
            db=db,
            batch_id=batch.id,
            event_type=EVENT_TYPE_HARVEST,
            actor=current_user.username,
            description=(
                f"Thu hoạch {batch.quantity} kg {batch.product_name} "
                f"tại vùng trồng #{farm.id} ({farm.name})."
            ),
            data=harvest_details,
        )

        # Commit duy nhất: cả Batch và Event cùng được lưu thành công vào cơ sở dữ liệu
        db.commit()
    except SQLAlchemyError as exc:
        # Rollback sạch cả lô lẫn sự kiện nếu có lỗi SQLAlchemy
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể lưu lô nông sản và sự kiện thu hoạch vào cơ sở dữ liệu.",
        ) from exc
    except Exception as exc:
        # Rollback sạch đối với mọi ngoại lệ khác
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Đã xảy ra lỗi trong quá trình tạo lô và ghi sự kiện thu hoạch.",
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


@router.get(
    "/{batch_id}/events",
    response_model=list[EventResponse],
    status_code=status.HTTP_200_OK,
    summary="Xem danh sách sự kiện truy xuất của lô nông sản",
    description="Trả về chuỗi sự kiện truy xuất nguồn gốc của lô theo thứ tự thời gian (`id` tăng dần).",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Không tìm thấy lô nông sản.",
        },
    },
)
def list_batch_events(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần xem sự kiện."),
    db: Session = Depends(get_db),
) -> list[Event]:
    """Lấy danh sách các sự kiện truy xuất nguồn gốc của lô theo ``batch_id``.

    Args:
        batch_id: ID lô nông sản.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        list[Event]: Danh sách các sự kiện của lô (kèm hash SHA-256 và previous_hash).

    Raises:
        HTTPException: 404 nếu không tìm thấy lô nông sản.
    """
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )

    return list(
        db.scalars(
            select(Event).where(Event.batch_id == batch_id).order_by(Event.id)
        ).all()
    )

