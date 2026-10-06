"""Khai báo ORM models (bảng dữ liệu) bằng SQLAlchemy 2.0.

Sprint 1: khung dự án + endpoint ``GET /health`` (chưa có bảng nghiệp vụ).
Sprint 2: module **Farm** (quản lý vùng trồng - bảng ``farms``)
và module **Batch** (quản lý lô nông sản - bảng ``batches``).
Sprint 4: module **Auth** (đăng nhập + phân quyền - bảng ``users``).
Sprint 6: module **Product** (danh mục sản phẩm dùng chung - bảng ``products``).

Quan hệ giữa các bảng::

    Farm 1 ---- N Batch   (một vùng trồng có nhiều lô nông sản)
    User                  (bảng độc lập, dùng cho đăng nhập/phân quyền)
    Product               (danh mục chuẩn dùng chung mọi tổ chức, không có
                           organization_id và không lọc theo tổ chức)

File này là điểm duy nhất (single source of truth) khai báo bảng dữ liệu.
Mọi model đều kế thừa ``Base`` và bảng sẽ được ``init_db()`` trong
``app/database.py`` tự động tạo khi ứng dụng khởi động (xem ``lifespan``
ở ``app/main.py``) - không cần chạy script SQL thủ công.
"""

from datetime import date
from enum import Enum

from sqlalchemy import CheckConstraint, Date, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Farm(Base):
    """Vùng trồng nông sản - mắt xích đầu tiên của chuỗi cung ứng.

    Attributes:
        id: Khoá chính, tự tăng.
        name: Tên vùng trồng (ví dụ: "Vùng trồng xoài Cao Lãnh").
        location: Địa điểm của vùng trồng (xã/huyện/tỉnh).
        area: Diện tích canh tác, đơn vị hecta (ha).
        owner: Chủ sở hữu vùng trồng (hộ nông dân / hợp tác xã / doanh nghiệp).
        batches: Các lô nông sản thu hoạch từ vùng trồng này (quan hệ 1-N).
    """

    __tablename__ = "farms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    area: Mapped[float] = mapped_column(Float, nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)

    # Quan hệ 1-N: một vùng trồng có nhiều lô nông sản.
    # `cascade="all, delete-orphan"`: khi Farm bị xoá thì các Batch của nó cũng
    # bị xoá theo -> không để lại dữ liệu mồ côi. Hành vi này được dùng bởi
    # `DELETE /farms/{farm_id}` (xem `app/routers/farms.py`).
    batches: Mapped[list["Batch"]] = relationship(
        back_populates="farm",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        return f"<Farm id={self.id} name={self.name!r} area={self.area}ha>"


class Batch(Base):
    """Lô nông sản thu hoạch từ một vùng trồng.

    Quan hệ: ``Farm 1 ---- N Batch``.

    Attributes:
        id: Khoá chính, tự tăng.
        farm_id: Khoá ngoại trỏ tới ``farms.id`` (vùng trồng xuất xứ).
        product_name: Tên sản phẩm của lô (ví dụ: "Xoài cát Chu").
        quantity: Số lượng / khối lượng của lô, đơn vị kg.
        harvest_date: Ngày thu hoạch.
        farm: Đối tượng ``Farm`` tương ứng (chiều N-1 của quan hệ).
    """

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # `index=True` để tra cứu "các lô của một vùng trồng" nhanh hơn.
    farm_id: Mapped[int] = mapped_column(
        ForeignKey("farms.id"),
        nullable=False,
        index=True,
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    harvest_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Quan hệ N-1: nhiều lô có thể thuộc về một vùng trồng.
    farm: Mapped["Farm"] = relationship(back_populates="batches")

    # Quan hệ 1-N: một lô có chuỗi sự kiện lịch sử (SCRUM-39).
    events: Mapped[list["BatchEvent"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        return (
            f"<Batch id={self.id} farm_id={self.farm_id} "
            f"product_name={self.product_name!r}>"
        )


class BatchEvent(Base):
    """Bảng sự kiện gắn với lô hàng (batch_events) — Cơ chế chuỗi bản ghi không sửa được.

    Đáp ứng SCRUM-39 (T-23) & K-01:
    - Chỉ cho phép ghi thêm (Append-only).
    - Mỗi sự kiện lưu: loại sự kiện, nội dung JSON (payload), người thực hiện, tổ chức,
      thời điểm, hash của chính nó và previous_hash tạo thành chuỗi liên kết mật mã.
    """

    __tablename__ = "batch_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[str] = mapped_column(String(1000), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    organization: Mapped[str] = mapped_column(String(100), nullable=False, default="HTX Nông Nghiệp Số 4")
    timestamp: Mapped[str] = mapped_column(String(50), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="0" * 64)

    batch: Mapped["Batch"] = relationship(back_populates="events")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BatchEvent id={self.id} batch_id={self.batch_id} type={self.event_type!r} hash={self.hash[:8]}>"


# --------------------------------------------------------------- Product ---
class ProductUnit(str, Enum):
    """Đơn vị tính chuẩn của danh mục sản phẩm.

    Kế thừa ``str`` để giá trị dùng được trực tiếp như chuỗi (``"kg"``,
    ``"ton"``...) khi validate ở Pydantic và khi ghi xuống database, đồng thời
    Swagger UI hiển thị đúng danh sách lựa chọn.
    """

    KG = "kg"
    G = "g"
    TON = "ton"
    LITER = "liter"
    BOX = "box"
    BOTTLE = "bottle"
    PIECE = "piece"
    BUNDLE = "bundle"


#: Các đơn vị tính hợp lệ - dùng để sinh ràng buộc CHECK ở bảng ``products``.
PRODUCT_UNITS: tuple[str, ...] = tuple(unit.value for unit in ProductUnit)

#: Đơn vị tính mặc định của sản phẩm mới khi client không gửi lên.
DEFAULT_PRODUCT_UNIT: str = ProductUnit.KG.value


class Product(Base):
    """Sản phẩm trong **danh mục dùng chung** cho mọi tổ chức - bảng ``products``.

    Khác với ``Farm``/``Batch`` (dữ liệu thuộc từng tổ chức), danh mục sản phẩm
    là dữ liệu **chuẩn dùng chung toàn hệ thống**: bảng không có cột
    ``organization_id`` và ``GET /products`` không lọc theo tổ chức của người gọi.

    Quyền ghi (thêm/sửa) chỉ dành cho role ``admin``; ``farmer`` chỉ đọc danh
    mục để chọn - xem ``app/routers/products.py``.

    Attributes:
        id: Khoá chính, tự tăng.
        name: Tên sản phẩm, **duy nhất** trên toàn hệ thống (UNIQUE + index).
        unit: Đơn vị tính chuẩn, thuộc ``ProductUnit`` (mặc định ``"kg"``).
        description: Mô tả ngắn, không bắt buộc.
    """

    __tablename__ = "products"
    # Ràng buộc CHECK ở tầng database: chỉ nhận các đơn vị tính chuẩn. Pydantic
    # đã chặn ở tầng API, ràng buộc này bảo vệ dữ liệu khi ghi bằng đường khác.
    __table_args__ = (
        CheckConstraint(
            "unit IN (" + ", ".join(f"'{unit}'" for unit in PRODUCT_UNITS) + ")",
            name="chk_products_unit",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )
    unit: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DEFAULT_PRODUCT_UNIT,
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        return f"<Product id={self.id} name={self.name!r} unit={self.unit}>"


# ------------------------------------------------------------- Vai trò ---
# Khai báo thành hằng số để không phải gõ chuỗi "admin"/"farmer" rải rác
# trong code (tránh lỗi gõ sai, chỉ cần đổi giá trị ở một chỗ nếu sau này
# muốn thêm vai trò mới như "inspector" hay "retailer").
ROLE_ADMIN: str = "admin"
ROLE_FARMER: str = "farmer"
ROLES: tuple[str, ...] = (ROLE_ADMIN, ROLE_FARMER)


class User(Base):
    """Tài khoản đăng nhập của hệ thống - bảng ``users``.

    Sprint 4 dùng bảng này cho chức năng **đăng nhập + phân quyền cơ bản**.
    Hệ thống **không dùng JWT**, không token/session phía server: client gửi
    kèm `username`/`password` (HTTP Basic) ở mỗi request, backend tra bảng này
    để biết người gọi là ai (xem ``app/security.py``).

    Attributes:
        id: Khoá chính, tự tăng.
        username: Tên đăng nhập, **duy nhất** (có index để tra cứu nhanh).
        password: Mật khẩu **đã băm** (SHA-256 hex = 64 ký tự) - không bao giờ
            lưu mật khẩu dạng thô, và API cũng không bao giờ trả cột này ra.
        role: Vai trò của tài khoản: ``"admin"`` (quản trị - toàn quyền) hoặc
            ``"farmer"`` (nông dân - quản lý nông sản).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
    )
    # 64 ký tự là độ dài chuỗi hex của SHA-256 (xem `hash_password`).
    password: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=ROLE_FARMER)

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        # Không in `password` để tránh lộ mật khẩu đã băm ra log.
        return f"<User id={self.id} username={self.username!r} role={self.role}>"


__all__ = [
    "Base",
    "Batch",
    "BatchEvent",
    "DEFAULT_PRODUCT_UNIT",
    "Farm",
    "PRODUCT_UNITS",
    "Product",
    "ProductUnit",
    "ROLE_ADMIN",
    "ROLE_FARMER",
    "ROLES",
    "User",
]
