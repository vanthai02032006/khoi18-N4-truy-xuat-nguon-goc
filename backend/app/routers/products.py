"""Router quản lý **danh mục sản phẩm dùng chung** cho mọi tổ chức.

Bảng ``products`` là **danh mục chuẩn toàn hệ thống**: không có cột
``organization_id`` và các endpoint dưới đây **không lọc theo tổ chức** của
người gọi - mọi tổ chức dùng chung một danh mục.

- ``GET  /products``            : xem danh mục (farmer **và** admin đều xem được).
- ``POST /products``            : thêm sản phẩm mới - **chỉ admin**.
- ``PUT  /products/{product_id}``: sửa (thay thế) sản phẩm - **chỉ admin**.

**Phân quyền ghi (quan trọng):** quyền thêm/sửa được kiểm soát **ở máy chủ**
bằng dependency ``require_admin`` - ``farmer`` gọi ``POST``/``PUT`` sẽ nhận
``403 Forbidden`` (chưa đăng nhập: ``401``). Việc ẩn nút thêm/sửa trên giao diện
chỉ là tiện ích, không phải lớp bảo vệ.

**Chống trùng lặp:** tên sản phẩm là duy nhất trên toàn hệ thống (UNIQUE ở
database + kiểm tra trước khi ghi) nên thêm/sửa trùng tên trả ``409 Conflict``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, status

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Product, User
from app.schemas import ProductCreate, ProductResponse, ProductUpdate
from app.security import require_admin, require_farmer

router = APIRouter(
    prefix="/products",
    tags=["Products"],
)


def _ensure_name_available(
    db: Session,
    name: str,
    *,
    exclude_id: int | None = None,
) -> None:
    """Chặn trùng tên sản phẩm trong danh mục dùng chung.

    Args:
        db: Session SQLAlchemy hiện tại.
        name: Tên sản phẩm muốn kiểm tra (đã được Pydantic chuẩn hoá).
        exclude_id: ID bản ghi được phép trùng tên (chính nó) - dùng khi sửa
            sản phẩm mà không đổi tên.

    Raises:
        HTTPException: **409** nếu đã có sản phẩm khác mang đúng tên này.
    """
    existing = db.scalar(select(Product).where(Product.name == name))
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Sản phẩm '{name}' đã tồn tại trong danh mục dùng chung.",
        )


@router.get(
    "",
    response_model=list[ProductResponse],
    status_code=status.HTTP_200_OK,
    summary="Lấy danh mục sản phẩm dùng chung",
    description=(
        "Trả về toàn bộ sản phẩm trong **danh mục dùng chung toàn hệ thống**, "
        "sắp xếp theo tên (A→Z). Danh mục không phân biệt tổ chức: mọi tổ chức "
        "nhìn thấy cùng một danh sách.\n\n"
        "**Phân quyền:** đăng nhập với role `farmer` hoặc `admin` - `farmer` chỉ "
        "xem danh mục để chọn sản phẩm, không có quyền ghi."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Vai trò không được phép."},
    },
)
def list_products(
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> list[Product]:
    """Lấy danh mục sản phẩm dùng chung (không lọc theo tổ chức).

    Args:
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        list[Product]: Danh sách sản phẩm, sắp xếp theo ``name`` (rỗng nếu chưa có).

    Raises:
        HTTPException: 401 nếu chưa đăng nhập; 403 nếu sai vai trò.
    """
    _ = current_user
    return list(db.scalars(select(Product).order_by(Product.name)).all())


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Thêm sản phẩm vào danh mục dùng chung (chỉ admin)",
    description=(
        "Thêm một sản phẩm mới vào danh mục dùng chung và trả về bản ghi vừa tạo "
        "(kèm `id`). Tên sản phẩm phải **duy nhất** trên toàn hệ thống.\n\n"
        "**Phân quyền:** chỉ `role = admin` được thêm (dùng `require_admin`). "
        "Farmer gọi sẽ nhận `403 Forbidden`."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Đã đăng nhập nhưng không phải admin."},
        status.HTTP_409_CONFLICT: {"description": "Tên sản phẩm đã tồn tại trong danh mục."},
    },
)
def create_product(
    payload: ProductCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Product:
    """Thêm sản phẩm mới vào danh mục dùng chung (chỉ admin).

    Args:
        payload: Dữ liệu sản phẩm đã được Pydantic validate.
        current_user: Tài khoản admin đã được ``require_admin`` kiểm tra quyền.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Product: Bản ghi sản phẩm vừa tạo (HTTP 201).

    Raises:
        HTTPException: 401 nếu chưa đăng nhập; 403 nếu không phải admin;
            409 nếu tên sản phẩm đã tồn tại;
            500 nếu ghi database thất bại (đã rollback).
    """
    _ = current_user

    _ensure_name_available(db, payload.name)

    product = Product(**payload.model_dump())
    db.add(product)

    try:
        db.commit()
    except IntegrityError as exc:
        # Tranh chấp hiếm gặp: tên vừa bị request khác thêm trước khi commit.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Sản phẩm '{payload.name}' đã tồn tại trong danh mục dùng chung.",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể lưu sản phẩm vào cơ sở dữ liệu.",
        ) from exc

    db.refresh(product)
    return product


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK,
    summary="Cập nhật sản phẩm trong danh mục dùng chung (chỉ admin)",
    description=(
        "Cập nhật (thay thế) thông tin sản phẩm theo `id`. Client gửi đầy đủ các "
        "trường như khi thêm mới; tên mới cũng phải duy nhất. Trả `404` nếu sản "
        "phẩm không tồn tại.\n\n"
        "**Phân quyền:** chỉ `role = admin` được sửa (dùng `require_admin`). "
        "Farmer gọi sẽ nhận `403 Forbidden`."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Đã đăng nhập nhưng không phải admin."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy sản phẩm."},
        status.HTTP_409_CONFLICT: {"description": "Tên sản phẩm đã tồn tại trong danh mục."},
    },
)
def update_product(
    payload: ProductUpdate,
    product_id: int = Path(..., ge=1, description="ID sản phẩm cần sửa."),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Product:
    """Cập nhật sản phẩm theo ``id`` (chỉ admin).

    Args:
        payload: Dữ liệu mới đã được Pydantic validate.
        product_id: ID sản phẩm cần sửa.
        current_user: Tài khoản admin đã được ``require_admin`` kiểm tra quyền.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Product: Bản ghi sản phẩm sau khi cập nhật (HTTP 200).

    Raises:
        HTTPException: 401 nếu chưa đăng nhập; 403 nếu không phải admin;
            404 nếu không tìm thấy sản phẩm; 409 nếu tên mới trùng sản phẩm khác;
            500 nếu ghi database thất bại (đã rollback).
    """
    _ = current_user

    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có id={product_id} trong danh mục dùng chung.",
        )

    _ensure_name_available(db, payload.name, exclude_id=product_id)

    for field, value in payload.model_dump().items():
        setattr(product, field, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Sản phẩm '{payload.name}' đã tồn tại trong danh mục dùng chung.",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể cập nhật sản phẩm trong cơ sở dữ liệu.",
        ) from exc

    db.refresh(product)
    return product
