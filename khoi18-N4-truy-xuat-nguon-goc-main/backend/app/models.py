"""Khai báo ORM models (bảng dữ liệu) bằng SQLAlchemy 2.0.

Sprint 1: khung dự án + endpoint ``GET /health`` (chưa có bảng nghiệp vụ).
Sprint 2: module **Farm** (quản lý vùng trồng - bảng ``farms``)
và module **Batch** (quản lý lô nông sản - bảng ``batches``).
Sprint 4: module **Auth** (đăng nhập + phân quyền - bảng ``users``).

Quan hệ giữa các bảng::

    Farm 1 ---- N Batch   (một vùng trồng có nhiều lô nông sản)
    User                  (bảng độc lập, dùng cho đăng nhập/phân quyền)

File này là điểm duy nhất (single source of truth) khai báo bảng dữ liệu.
Mọi model đều kế thừa ``Base`` và bảng sẽ được ``init_db()`` trong
``app/database.py`` tự động tạo khi ứng dụng khởi động (xem ``lifespan``
ở ``app/main.py``) - không cần chạy script SQL thủ công.
"""

from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String
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

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        return (
            f"<Batch id={self.id} farm_id={self.farm_id} "
            f"product_name={self.product_name!r}>"
        )


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


# ------------------------------------------------------------- Product (T-14 / SCRUM-30) ---
import enum
from datetime import datetime
from sqlalchemy import DateTime, Enum as SQLEnum


class ProductUnit(str, enum.Enum):
    """Các đơn vị tính chuẩn mực của sản phẩm nông sản."""
    KG = "kg"
    G = "g"
    TON = "ton"
    LITER = "liter"
    BOX = "box"
    BOTTLE = "bottle"
    PIECE = "piece"
    BUNDLE = "bundle"


class Product(Base):
    """Danh mục sản phẩm chuẩn dùng chung toàn hệ thống - bảng ``products``.

    LƯU Ý THIẾT KẾ KIẾN TRÚC & PHÂN QUYỀN (GLOBAL CATALOG / NO TENANT SCOPE):
    - Bảng này là DANH MỤC DÙNG CHUNG TOÀN HỆ THỐNG (System-wide Global Catalog).
    - Bảng này TUYỆT ĐỐI KHÔNG có cột `organization_id` (không gắn tenant ID).
    - Tầng truy vấn (Query Layer / CRUD) KHÔNG áp dụng bất kỳ bộ lọc theo tổ chức
      (Organization/Tenant Scope) nào đối với bảng này. Mọi tổ chức/đối tác đều
      được phép truy cập và tham chiếu danh mục sản phẩm này.
    - Cột `name` có ràng buộc UNIQUE để chống trùng lặp tên sản phẩm trong toàn hệ thống.
    - Cột `unit` sử dụng kiểu ENUM chứa các giá trị đơn vị tính chuẩn (kg, g, ton, liter, box,...).

    Attributes:
        id: Khoá chính, tự tăng.
        name: Tên sản phẩm chuẩn (DUY NHẤT toàn hệ thống).
        unit: Đơn vị tính chuẩn theo ProductUnit ENUM.
        description: Mô tả chi tiết hoặc tiêu chuẩn canh tác (VietGAP, GlobalGAP...).
        created_at: Thời điểm tạo bản ghi.
        updated_at: Thời điểm cập nhật cuối cùng.
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )
    unit: Mapped[ProductUnit] = mapped_column(
        SQLEnum(ProductUnit, name="product_unit_enum", native_enum=False),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Product id={self.id} name={self.name!r} unit={self.unit.value}>"


# -------------------------------------------------------- BatchEvent (T-25 / SCRUM-41) ---
class BatchEvent(Base):
    """Sự kiện hành trình truy xuất nguồn gốc & chuỗi lạnh - bảng ``batch_events``.

    LƯU Ý BẢO MẬT & PHÂN QUYỀN (APPEND-ONLY AUDIT LOG):
    - Bảng này là sổ nhật ký hành trình bất biến (Immutable Audit Log).
    - Tài khoản App Runtime CHỈ ĐƯỢC PHÉP THỰC HIỆN LỆNH SELECT VÀ INSERT.
    - Quyền UPDATE và DELETE BỊ THU HỒI HOÀN TOÀN ở cấp độ CSDL (PostgreSQL Grants & Triggers).
    - Bất kỳ câu lệnh UPDATE hoặc DELETE nào phát ra từ tài khoản ứng dụng sẽ bị
      CSDL TỪ CHỐI NGAY LẬP TỨC.

    Attributes:
        id: Khoá chính, tự tăng.
        batch_id: Khoá ngoại trỏ đến lô hàng ``batches.id``.
        event_type: Loại sự kiện (HARVEST, COLD_STORAGE, HANDOVER, MERGE, INSPECTION...).
        details: Chi tiết sự kiện / ghi chú vận hành.
        temperature: Nhiệt độ chuỗi lạnh đo được tại thời điểm ghi nhận (°C).
        created_by: Tài khoản thực hiện ghi nhận sự kiện.
        timestamp: Thời điểm sự kiện diễn ra.
        prev_hash: Giá trị băm SHA-256 của bản ghi sự kiện liền trước (Hash chaining chống sửa lén).
        hash_code: Giá trị băm SHA-256 của chính sự kiện này.
    """

    __tablename__ = "batch_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hash_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BatchEvent id={self.id} batch_id={self.batch_id} type={self.event_type!r}>"


# ----------------------------------------------------------- Handover (T-35 / SCRUM-51) ---
class Handover(Base):
    """Phiếu bàn giao lô nông sản giữa các tổ chức đối tác - bảng ``handovers``.

    Attributes:
        id: Khoá chính, tự tăng.
        batch_id: Lô hàng được bàn giao.
        sender_org_id: ID tổ chức bàn giao (tổ chức của người dùng hiện tại).
        recipient_org_id: ID tổ chức đối tác nhận bàn giao.
        recipient_org_name: Tên công khai của tổ chức đối tác nhận.
        notes: Ghi chú cho đợt bàn giao.
        status: Trạng thái bàn giao (PENDING, COMPLETED, REJECTED).
        created_at: Thời điểm tạo phiếu bàn giao.
    """

    __tablename__ = "handovers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id"),
        nullable=False,
        index=True,
    )
    sender_org_id: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_org_id: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_org_name: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False)
    is_overdue: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Handover id={self.id} batch_id={self.batch_id} to={self.recipient_org_name!r} overdue={self.is_overdue}>"


__all__ = [
    "Base",
    "Batch",
    "BatchEvent",
    "Farm",
    "Handover",
    "Product",
    "ProductUnit",
    "ROLE_ADMIN",
    "ROLE_FARMER",
    "ROLES",
    "User",
]
