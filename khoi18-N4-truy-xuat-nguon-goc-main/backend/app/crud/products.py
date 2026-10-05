"""Query Layer / CRUD cho module Product (T-12 / T-14 / SCRUM-30).

QUY TẮC THIẾT KẾ CỐT LÕI (TENANT SCOPE BYPASSED):
==================================================
Theo yêu cầu kỹ thuật tại T-14 (SCRUM-30) và lớp truy vấn T-12:
1. Bảng `products` là danh mục sản phẩm chuẩn dùng chung TOÀN HỆ THỐNG.
2. Bảng KHÔNG có cột `organization_id`.
3. Tầng truy vấn (Query Layer) TUYỆT ĐỐI KHÔNG áp dụng bộ lọc theo tổ chức
   (Organization/Tenant Scope Filter). Mọi tổ chức đều được phép truy vấn,
   tìm kiếm và tham chiếu tới các sản phẩm trong danh mục này.
4. Tên sản phẩm được kiểm tra tính duy nhất (UNIQUE) trước khi ghi để chống trùng lặp.
"""

from collections.abc import Sequence
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, ProductUnit
from app.schemas import ProductCreate, ProductUpdate


class ProductQueryLayer:
    """Lớp truy vấn chuyên biệt cho bảng Products - KHÔNG ÁP DỤNG TENANT SCOPE."""

    @staticmethod
    def get_by_id(db: Session, product_id: int) -> Product | None:
        """Lấy thông tin sản phẩm theo ID.
        
        Lưu ý: Không lọc theo tenant_id / organization_id vì bảng dùng chung.
        """
        return db.get(Product, product_id)

    @staticmethod
    def get_by_name(db: Session, name: str) -> Product | None:
        """Tìm sản phẩm theo tên chính xác (để kiểm tra ràng buộc UNIQUE).
        
        Phạm vi: Toàn hệ thống (Global Scope).
        """
        stmt = select(Product).where(Product.name == name.strip())
        return db.scalar(stmt)

    @staticmethod
    def list_all(
        db: Session,
        skip: int = 0,
        limit: int = 100,
        unit: ProductUnit | None = None,
    ) -> Sequence[Product]:
        """Lấy danh sách sản phẩm trong danh mục toàn hệ thống.

        LƯU Ý QUAN TRỌNG:
        - Bảng này KHÔNG áp dụng bộ lọc theo tổ chức (Tenant/Organization Scope Bypassed).
        - Toàn bộ các tổ chức trong chuỗi cung ứng đều xem chung 1 danh mục sản phẩm thống nhất.
        """
        stmt = select(Product).order_by(Product.id.asc()).offset(skip).limit(limit)
        if unit is not None:
            stmt = stmt.where(Product.unit == unit)
        return db.scalars(stmt).all()

    @staticmethod
    def create(db: Session, payload: ProductCreate) -> Product:
        """Tạo sản phẩm mới vào danh mục toàn hệ thống.
        
        Không gắn organization_id. Đảm bảo ràng buộc UNIQUE tên sản phẩm.
        """
        product = Product(
            name=payload.name.strip(),
            unit=payload.unit,
            description=payload.description,
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        return product

    @staticmethod
    def update(db: Session, product: Product, payload: ProductUpdate) -> Product:
        """Cập nhật thông tin sản phẩm."""
        update_data = payload.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if key == "name" and value is not None:
                value = value.strip()
            setattr(product, key, value)
        db.commit()
        db.refresh(product)
        return product

    @staticmethod
    def delete(db: Session, product: Product) -> None:
        """Xoá sản phẩm khỏi danh mục."""
        db.delete(product)
        db.commit()
