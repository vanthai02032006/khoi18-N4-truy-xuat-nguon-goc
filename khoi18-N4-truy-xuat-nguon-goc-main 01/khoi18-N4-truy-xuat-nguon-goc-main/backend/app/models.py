"""Khai báo ORM models (bảng dữ liệu) bằng SQLAlchemy 2.0.

Sprint 1: khung dự án + endpoint ``GET /health``.
Sprint 2: module **Farm** (quản lý vùng trồng - bảng ``farms``)
và module **Batch** (quản lý lô nông sản - bảng ``batches``).
Sprint 4: module **Auth** (đăng nhập + phân quyền - bảng ``users``).
Sprint 6/T-33: module **Handover** (bàn giao lô hàng - bảng ``handovers``)
kèm hệ thống **Event** (ghi sự kiện vòng đời lô hàng - bảng ``events`` theo T-25).

Quan hệ giữa các bảng::

    Farm 1 ---- N Batch   (một vùng trồng có nhiều lô nông sản)
    Batch 1 ---- N Handover (một lô nông sản có nhiều lượt bàn giao qua các khâu)
    Batch 1 ---- N Event    (một lô nông sản có nhật ký sự kiện vòng đời/truy xuất)
    User                  (bảng tài khoản, dùng cho đăng nhập/phân quyền)

Ràng buộc toàn vẹn đặc biệt:
    Mỗi lô nông sản (Batch) chỉ có tối đa 1 bàn giao đang ở trạng thái chờ xử lý (status = 'pending').
    Được bảo đảm bởi Unique Index một phần (Partial Unique Index) trên CSDL:
    `uq_batch_pending_handover` (batch_id WHERE status = 'pending').
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String, func, text
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
    # bị xoá theo -> không để lại dữ liệu mồ côi.
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
        current_owner: Bên/chủ thể đang nắm quyền quản lý/sở hữu lô hiện tại.
        farm: Đối tượng ``Farm`` tương ứng (chiều N-1 của quan hệ).
        handovers: Lịch sử các lần bàn giao của lô hàng.
        events: Nhật ký sự kiện vòng đời phục vụ truy xuất nguồn gốc (T-25).
    """

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    farm_id: Mapped[int] = mapped_column(
        ForeignKey("farms.id"),
        nullable=False,
        index=True,
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    harvest_date: Mapped[date] = mapped_column(Date, nullable=False)
    current_owner: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)

    # Quan hệ N-1: nhiều lô có thể thuộc về một vùng trồng.
    farm: Mapped["Farm"] = relationship(back_populates="batches")

    # Quan hệ 1-N: một lô nông sản có nhiều lượt bàn giao và sự kiện truy xuất.
    handovers: Mapped[list["Handover"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        return (
            f"<Batch id={self.id} farm_id={self.farm_id} "
            f"product_name={self.product_name!r} owner={self.current_owner!r}>"
        )


# ------------------------------------------------------------- Vai trò ---
ROLE_ADMIN: str = "admin"
ROLE_FARMER: str = "farmer"
ROLES: tuple[str, ...] = (ROLE_ADMIN, ROLE_FARMER)


class User(Base):
    """Tài khoản đăng nhập của hệ thống - bảng ``users``.

    Attributes:
        id: Khoá chính, tự tăng.
        username: Tên đăng nhập, **duy nhất** (có index để tra cứu nhanh).
        password: Mật khẩu **đã băm** (SHA-256 hex = 64 ký tự).
        role: Vai trò của tài khoản: ``"admin"`` hoặc ``"farmer"``.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
    )
    password: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=ROLE_FARMER)

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        return f"<User id={self.id} username={self.username!r} role={self.role}>"


# ------------------------------------------------ Trạng thái & Sự kiện Bàn giao ---
HANDOVER_STATUS_PENDING: str = "pending"
HANDOVER_STATUS_ACCEPTED: str = "accepted"
HANDOVER_STATUS_REJECTED: str = "rejected"
HANDOVER_STATUSES: tuple[str, ...] = (
    HANDOVER_STATUS_PENDING,
    HANDOVER_STATUS_ACCEPTED,
    HANDOVER_STATUS_REJECTED,
)

# Các loại sự kiện vòng đời lô hàng (Task T-25)
EVENT_TYPE_HANDOVER_PENDING: str = "HANDOVER_PENDING"
EVENT_TYPE_HANDOVER_ACCEPTED: str = "HANDOVER_ACCEPTED"
EVENT_TYPE_HANDOVER_REJECTED: str = "HANDOVER_REJECTED"
EVENT_TYPE_BATCH_CREATED: str = "BATCH_CREATED"
EVENT_TYPES: tuple[str, ...] = (
    EVENT_TYPE_HANDOVER_PENDING,
    EVENT_TYPE_HANDOVER_ACCEPTED,
    EVENT_TYPE_HANDOVER_REJECTED,
    EVENT_TYPE_BATCH_CREATED,
)


class Handover(Base):
    """Bàn giao lô nông sản giữa các bên trong chuỗi cung ứng - bảng ``handovers``.

    Theo dõi trạng thái: chờ xử lý (pending) / đã nhận (accepted) / từ chối (rejected).
    Ràng buộc trên CSDL: mỗi lô chỉ có tối đa 1 bàn giao đang ở trạng thái chờ xử lý (status = 'pending').

    Attributes:
        id: Khoá chính, tự tăng.
        batch_id: ID lô nông sản bàn giao (khoá ngoại tới batches.id).
        sender_id: ID tài khoản bên giao (nếu có).
        sender_name: Tên/thông tin bên giao nông sản.
        receiver_id: ID tài khoản bên nhận (nếu có).
        receiver_name: Tên/thông tin bên tiếp nhận nông sản.
        status: Trạng thái bàn giao ('pending', 'accepted', 'rejected').
        notes: Ghi chú bàn giao kèm theo.
        created_at: Thời điểm tạo yêu cầu bàn giao.
        updated_at: Thời điểm xử lý (tiếp nhận/từ chối) bàn giao.
    """

    __tablename__ = "handovers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False)
    receiver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    receiver_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=HANDOVER_STATUS_PENDING,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, onupdate=func.now())

    # Ràng buộc Unique trên CSDL: mỗi lô chỉ có tối đa 1 bàn giao đang chờ xử lý
    __table_args__ = (
        Index(
            "uq_batch_pending_handover",
            "batch_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    batch: Mapped["Batch"] = relationship(back_populates="handovers")
    sender: Mapped[User | None] = relationship(foreign_keys=[sender_id])
    receiver: Mapped[User | None] = relationship(foreign_keys=[receiver_id])

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Handover id={self.id} batch_id={self.batch_id} "
            f"sender={self.sender_name!r} receiver={self.receiver_name!r} status={self.status}>"
        )


class Event(Base):
    """Nhật ký sự kiện vòng đời lô nông sản (Traceability Event Log - Task T-25) - bảng ``events``.

    Attributes:
        id: Khoá chính, tự tăng.
        batch_id: ID lô nông sản liên quan (khoá ngoại tới batches.id).
        event_type: Loại sự kiện (HANDOVER_PENDING, HANDOVER_ACCEPTED, HANDOVER_REJECTED, BATCH_CREATED,...).
        actor_id: ID tài khoản thực hiện sự kiện.
        actor_name: Tên người/chủ thể kích hoạt sự kiện.
        description: Mô tả chi tiết hành động / diễn biến sự kiện.
        metadata_info: Chuỗi thông tin dữ liệu phụ trợ (JSON/Text).
        created_at: Thời điểm xảy ra sự kiện.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_info: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())

    batch: Mapped["Batch"] = relationship(back_populates="events")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Event id={self.id} batch_id={self.batch_id} "
            f"type={self.event_type!r} actor={self.actor_name!r}>"
        )


__all__ = [
    "Base",
    "Batch",
    "Event",
    "EVENT_TYPE_BATCH_CREATED",
    "EVENT_TYPE_HANDOVER_ACCEPTED",
    "EVENT_TYPE_HANDOVER_PENDING",
    "EVENT_TYPE_HANDOVER_REJECTED",
    "EVENT_TYPES",
    "Farm",
    "Handover",
    "HANDOVER_STATUS_ACCEPTED",
    "HANDOVER_STATUS_PENDING",
    "HANDOVER_STATUS_REJECTED",
    "HANDOVER_STATUSES",
    "ROLE_ADMIN",
    "ROLE_FARMER",
    "ROLES",
    "User",
]
