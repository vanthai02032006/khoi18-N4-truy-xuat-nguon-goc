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
from typing import Any

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

    # Phả hệ lô nông sản (T-49 / SCRUM-65):
    # `parent_id` trỏ tới lô cha (lô mà lô này được phân tách/sơ chế/chế biến từ đó).
    # Nếu `parent_id is None`, đây là LÔ GỐC (Root batch) thu hoạch trực tiếp từ `farm_id`.
    parent_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id"),
        nullable=True,
        index=True,
        default=None,
    )

    # Phân quyền xem theo T-54:
    # `is_restricted`: nếu True thì chỉ admin hoặc chủ sở hữu (owner) được xem;
    # các tài khoản không có quyền xem sẽ bị trả về mã lỗi 403 Forbidden.
    is_restricted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    # Mã định danh lô theo T-19:
    batch_code: Mapped[str] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        default=None,
    )

    owner: Mapped[str] = mapped_column(
        String(255),
        nullable=True,
        default=None,
    )

    # Quan hệ N-1: nhiều lô có thể thuộc về một vùng trồng.
    farm: Mapped["Farm"] = relationship(back_populates="batches")

    # Quan hệ tự tham chiếu (self-referential) cho phả hệ:
    parent: Mapped[Any] = relationship(
        "Batch",
        remote_side="Batch.id",
        foreign_keys=[parent_id],
        backref="children",
    )

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        return (
            f"<Batch id={self.id} farm_id={self.farm_id} parent_id={self.parent_id} "
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


class BatchEvent(Base):
    """Sự kiện trong chuỗi cung ứng của lô nông sản (T-29 / SCRUM-45).

    Cơ chế Cryptographic Hash Chain (chống sửa lén / tamper-evident log)
    mà không cần blockchain phức tạp:
    - Mỗi sự kiện lưu `prev_hash` trỏ tới hash của sự kiện liền trước.
    - Hash của sự kiện hiện tại được tính bằng SHA-256 trên nội dung bản ghi + prev_hash.
    - Bất kỳ hành vi sửa lén nội dung qua SQL hoặc xoá bản ghi đều làm đứt gãy chuỗi
      và bị phát hiện 100% trong quá trình kiểm tra tính toàn vẹn.

    Attributes:
        id: Khoá chính, tự tăng.
        batch_id: Khoá ngoại trỏ tới lô nông sản (`batches.id`).
        sequence: Thứ tự sự kiện trong lô (1, 2, 3...).
        event_type: Mã loại sự kiện (HARVEST, PACKAGING, COLD_STORAGE...).
        data: Dữ liệu chi tiết sự kiện (dạng chuỗi / JSON).
        timestamp: Thời gian ghi nhận sự kiện (ISO-8601).
        prev_hash: Mã băm SHA-256 của sự kiện liền trước (hoặc chuỗi 64 ký tự '0' nếu là genesis).
        hash: Mã băm SHA-256 của sự kiện này (xác thực toàn vẹn).
    """

    __tablename__ = "batch_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    data: Mapped[str] = mapped_column(String(1000), nullable=False)
    timestamp: Mapped[str] = mapped_column(String(50), nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Quan hệ ngược về Batch
    batch: Mapped["Batch"] = relationship("Batch", backref="events")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<BatchEvent id={self.id} batch_id={self.batch_id} seq={self.sequence} "
            f"type={self.event_type!r} hash={self.hash[:8]}...>"
        )


__all__ = [
    "Base",
    "Batch",
    "BatchEvent",
    "Farm",
    "ROLE_ADMIN",
    "ROLE_FARMER",
    "ROLES",
    "User",
]

