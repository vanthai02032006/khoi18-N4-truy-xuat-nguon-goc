"""Router quản lý danh mục sản phẩm (Product Catalog Router).

QUY TẮC THIẾT KẾ (T-14 / SCRUM-30):
- Bảng `products` là danh mục sản phẩm chuẩn dùng chung toàn hệ thống.
- Endpoint KHÔNG lọc theo organization_id.
- Ràng buộc UNIQUE cho trường tên sản phẩm (`name`) để chống trùng lặp.
- Đơn vị tính (`unit`) sử dụng ENUM chuẩn.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crud.products import ProductQueryLayer
from app.database import get_db
from app.models import Product, ProductUnit, User
from app.schemas import ProductCreate, ProductResponse, ProductUpdate
from app.security import require_admin, require_farmer

router = APIRouter(
    prefix="/products",
    tags=["Products (Danh mục sản phẩm)"],
)


@router.get(
    "",
    response_model=list[ProductResponse],
    summary="Lấy danh mục sản phẩm toàn hệ thống",
    description=(
        "Trả về toàn bộ danh mục sản phẩm chuẩn dùng chung cho tất cả các tổ chức "
        "(Tenant Scope Bypassed). Không phân biệt tổ chức của người gọi."
    ),
)
def list_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    unit: ProductUnit | None = Query(None, description="Lọc theo đơn vị tính"),
    db: Session = Depends(get_db),
) -> list[Product]:
    """Danh sách sản phẩm toàn hệ thống - không lọc theo tenant."""
    products = ProductQueryLayer.list_all(db=db, skip=skip, limit=limit, unit=unit)
    return list(products)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Xem chi tiết một sản phẩm trong danh mục",
)
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
) -> Product:
    product = ProductQueryLayer.get_by_id(db, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có id={product_id} trong danh mục hệ thống.",
        )
    return product


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Thêm sản phẩm mới vào danh mục toàn hệ thống",
    description="Tên sản phẩm phải là duy nhất (UNIQUE) trên toàn hệ thống. Đơn vị tính phải thuộc ENUM chuẩn.",
)
def create_product(
    payload: ProductCreate,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> Product:
    _ = current_user

    # Kiểm tra trùng tên sản phẩm trước khi tạo
    existing = ProductQueryLayer.get_by_name(db, payload.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Sản phẩm với tên '{payload.name}' đã tồn tại trong danh mục hệ thống (ràng buộc UNIQUE).",
        )

    try:
        return ProductQueryLayer.create(db, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Trùng lặp tên sản phẩm: '{payload.name}'.",
        ) from exc


@router.patch(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Cập nhật thông tin sản phẩm trong danh mục",
    description="Cập nhật một phần thông tin sản phẩm. Tên mới (nếu có) phải là duy nhất (UNIQUE).",
)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> Product:
    _ = current_user

    product = ProductQueryLayer.get_by_id(db, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có id={product_id}.",
        )

    # Kiểm tra trùng tên nếu tên mới khác tên cũ
    if payload.name is not None and payload.name.strip() != product.name:
        existing = ProductQueryLayer.get_by_name(db, payload.name)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sản phẩm với tên '{payload.name}' đã tồn tại trong danh mục hệ thống (ràng buộc UNIQUE).",
            )

    try:
        return ProductQueryLayer.update(db, product, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Trùng lặp tên sản phẩm: '{payload.name}'.",
        ) from exc


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xoá sản phẩm khỏi danh mục toàn hệ thống (chỉ admin)",
    description="Chỉ tài khoản **admin** được phép xoá sản phẩm khỏi danh mục.",
)
def delete_product(
    product_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    _ = current_user

    product = ProductQueryLayer.get_by_id(db, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có id={product_id}.",
        )

    ProductQueryLayer.delete(db, product)
