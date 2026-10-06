"""Router quản lý vùng trồng (Farm) - module đầu tiên của Sprint 2.

Cung cấp **đầy đủ CRUD** (hoàn thiện ở Sprint 5):

- ``POST   /farms``           : tạo vùng trồng mới.
- ``GET    /farms``           : lấy danh sách vùng trồng.
- ``PUT    /farms/{farm_id}`` : cập nhật (thay thế) thông tin vùng trồng.
- ``DELETE /farms/{farm_id}`` : xoá vùng trồng - **xoá kèm** mọi lô nông sản của nó.

**Phân quyền (Sprint 4):** ``POST``/``GET``/``PUT`` dùng dependency
``require_farmer`` -> yêu cầu đăng nhập bằng HTTP Basic, cho phép role ``farmer``
và ``admin``. Riêng ``DELETE`` dùng ``require_admin`` -> **chỉ admin** được xoá
(trên giao diện, nút Xoá cũng bị ẩn với farmer). Chưa đăng nhập → **401**,
sai vai trò → **403**.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Farm, User
from app.schemas import DeleteResponse, FarmCreate, FarmResponse, FarmUpdate
from app.security import require_admin, require_farmer

router = APIRouter(
    prefix="/farms",
    tags=["Farms"],
)


@router.post(
    "",
    response_model=FarmResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo vùng trồng mới",
    description=(
        "Lưu một vùng trồng mới vào database và trả về bản ghi vừa tạo (kèm `id`).\n\n"
        "**Phân quyền:** đăng nhập với role `farmer` hoặc `admin` (yêu cầu header "
        "`Authorization: Basic ...`)."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Vai trò không được phép."},
    },
)
def create_farm(
    payload: FarmCreate,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> Farm:
    """Tạo vùng trồng mới.

    Args:
        payload: Dữ liệu vùng trồng đã được Pydantic validate.
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        db: Session SQLAlchemy được cấp và tự đóng bởi dependency ``get_db``.

    Returns:
        Farm: Bản ghi vùng trồng vừa tạo (HTTP 201).

    Raises:
        HTTPException: 401/403 nếu chưa đăng nhập hoặc sai vai trò;
            500 nếu ghi database thất bại (đã rollback).
    """
    _ = current_user  # bắt buộc khai báo để dependency kiểm tra quyền chạy

    # `model_dump()` chuyển Pydantic model -> dict để map thẳng vào ORM model.
    farm = Farm(**payload.model_dump())
    db.add(farm)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        # Rollback để session không ở trạng thái lỗi cho các request sau.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể lưu vùng trồng vào cơ sở dữ liệu.",
        ) from exc

    # Đọc lại bản ghi để lấy `id` do database sinh ra.
    db.refresh(farm)
    return farm


@router.get(
    "",
    response_model=list[FarmResponse],
    status_code=status.HTTP_200_OK,
    summary="Lấy danh sách vùng trồng",
    description=(
        "Trả về toàn bộ vùng trồng, sắp xếp theo `id` tăng dần.\n\n"
        "**Phân quyền:** đăng nhập với role `farmer` hoặc `admin` - cả hai vai trò "
        "đều được phép xem."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Vai trò không được phép."},
    },
)
def list_farms(
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> list[Farm]:
    """Lấy danh sách vùng trồng.

    Args:
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        list[Farm]: Danh sách vùng trồng (rỗng nếu chưa có dữ liệu).

    Raises:
        HTTPException: 401/403 nếu chưa đăng nhập hoặc sai vai trò.
    """
    _ = current_user
    # SQLAlchemy 2.0 style: `select()` + `db.scalars()` -> trả về ORM objects.
    return list(db.scalars(select(Farm).order_by(Farm.id)).all())


@router.put(
    "/{farm_id}",
    response_model=FarmResponse,
    status_code=status.HTTP_200_OK,
    summary="Cập nhật vùng trồng",
    description=(
        "Cập nhật (thay thế) thông tin vùng trồng theo `id`. Client gửi đầy đủ "
        "các trường như khi tạo mới. Trả `404` nếu vùng trồng không tồn tại.\n\n"
        "**Phân quyền:** đăng nhập với role `farmer` hoặc `admin` - cả hai vai "
        "trò đều được sửa dữ liệu nông sản."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Vai trò không được phép."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy vùng trồng."},
    },
)
def update_farm(
    payload: FarmUpdate,
    farm_id: int = Path(..., ge=1, description="ID vùng trồng cần sửa."),
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> Farm:
    """Cập nhật thông tin vùng trồng theo ``id``.

    Args:
        payload: Dữ liệu mới đã được Pydantic validate (đủ 4 trường).
        farm_id: ID vùng trồng cần sửa.
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Farm: Bản ghi vùng trồng sau khi cập nhật (HTTP 200).

    Raises:
        HTTPException: 401/403 nếu chưa đăng nhập hoặc sai vai trò;
            404 nếu không tìm thấy vùng trồng;
            500 nếu ghi database thất bại (đã rollback).
    """
    _ = current_user

    farm = db.get(Farm, farm_id)
    if farm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vùng trồng có id={farm_id}.",
        )

    # Ghi đè từng trường (PUT = cập nhật thay thế) - không tạo bản ghi mới.
    for field, value in payload.model_dump().items():
        setattr(farm, field, value)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể cập nhật vùng trồng trong cơ sở dữ liệu.",
        ) from exc

    db.refresh(farm)
    return farm


@router.delete(
    "/{farm_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Xoá vùng trồng (chỉ admin)",
    description=(
        "Xoá vùng trồng theo `id` và **xoá kèm toàn bộ lô nông sản** thuộc vùng "
        "đó (nhờ `cascade=\"all, delete-orphan\"` ở quan hệ Farm 1-N). Số lô bị "
        "xoá kèm được trả về ở field `deleted_batches` để giao diện thông báo.\n\n"
        "**Phân quyền:** chỉ `role = admin` được xoá (dùng `require_admin`). "
        "Farmer gọi sẽ nhận `403 Forbidden` - giao diện cũng ẩn nút Xoá với farmer."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Đã đăng nhập nhưng không phải admin."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy vùng trồng."},
    },
)
def delete_farm(
    farm_id: int = Path(..., ge=1, description="ID vùng trồng cần xoá."),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> DeleteResponse:
    """Xoá một vùng trồng (chỉ admin) cùng các lô nông sản của nó.

    Args:
        farm_id: ID vùng trồng cần xoá.
        current_user: Tài khoản admin đã được ``require_admin`` kiểm tra quyền.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        DeleteResponse: Thông báo + số lô nông sản bị xoá kèm (HTTP 200).

    Raises:
        HTTPException: 401 nếu chưa đăng nhập; 403 nếu không phải admin;
            404 nếu không tìm thấy vùng trồng;
            500 nếu xoá trong database thất bại (đã rollback).
    """
    _ = current_user

    farm = db.get(Farm, farm_id)
    if farm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vùng trồng có id={farm_id}.",
        )

    # Đếm số lô TRƯỚC khi xoá: sau `db.delete()` không nên truy vấn lại quan hệ
    # này (bản ghi đang chờ bị xoá ở transaction hiện tại).
    deleted_batches = len(farm.batches)

    db.delete(farm)  # cascade -> các Batch con bị xoá theo
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể xoá vùng trồng khỏi cơ sở dữ liệu.",
        ) from exc

    return DeleteResponse(
        message=(
            f"Đã xoá vùng trồng #{farm_id} và {deleted_batches} lô nông sản "
            "thuộc vùng đó."
        ),
        deleted_id=farm_id,
        deleted_batches=deleted_batches,
    )

