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

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
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
    """Lô nông sản thu hoạch từ một vùng trồng hoặc tạo từ giao dịch gộp (SCRUM-60).

    Quan hệ: ``Farm 1 ---- N Batch``.

    Attributes:
        id: Khoá chính, tự tăng.
        farm_id: Khoá ngoại trỏ tới ``farms.id`` (vùng trồng xuất xứ).
        product_name: Tên sản phẩm của lô (ví dụ: "Xoài cát Chu").
        quantity: Số lượng / khối lượng ban đầu của lô, đơn vị kg.
        remaining_quantity: Khối lượng tồn còn lại sau khi xuất hoặc gộp lô (SCRUM-60).
        harvest_date: Ngày thu hoạch hoặc ngày gộp lô.
        organization_id: Mã tổ chức / đơn vị hiện đang nắm giữ lô (SCRUM-70).
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
    remaining_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    harvest_date: Mapped[date] = mapped_column(Date, nullable=False)
    organization_id: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None, index=True)

    # Quan hệ N-1: nhiều lô có thể thuộc về một vùng trồng.
    farm: Mapped["Farm"] = relationship(back_populates="batches")

    # Quan hệ phả hệ (Genealogy) - SCRUM-60
    parent_relations: Mapped[list["BatchRelation"]] = relationship(
        "BatchRelation",
        foreign_keys="[BatchRelation.child_batch_id]",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    child_relations: Mapped[list["BatchRelation"]] = relationship(
        "BatchRelation",
        foreign_keys="[BatchRelation.parent_batch_id]",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        return (
            f"<Batch id={self.id} farm_id={self.farm_id} "
            f"product_name={self.product_name!r} remaining={self.remaining_quantity}>"
        )


class BatchRelation(Base):
    """Bảng quan hệ cha - con (Genealogy / Phả hệ lô hàng) - SCRUM-60.

    Lưu vết các lô mẹ (parent) gộp thành lô con (child) và khối lượng lấy từ mỗi lô mẹ.
    """

    __tablename__ = "batch_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    child_batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    used_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("parent_batch_id", "child_batch_id", name="uq_parent_child_batch"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<BatchRelation parent_id={self.parent_batch_id} -> child_id={self.child_batch_id} "
            f"used={self.used_quantity}kg>"
        )


class BatchCustodyHistory(Base):
    """Lịch sử nắm giữ lô của tổ chức (Custody / Ownership History) - SCRUM-70.

    Dùng để kiểm tra quyền: tổ chức hiện tại đang giữ hoặc từng giữ lô nông sản.
    """

    __tablename__ = "batch_custody_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BatchCustodyHistory batch_id={self.batch_id} org={self.organization_id}>"


# ------------------------------------------------------------- Vai trò ---
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
        organization_id: Tổ chức mà tài khoản này trực thuộc (SCRUM-70).
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
    organization_id: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None, index=True)

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        return f"<User id={self.id} username={self.username!r} role={self.role} org={self.organization_id}>"


__all__ = [
    "Base",
    "Batch",
    "BatchCustodyHistory",
    "BatchRelation",
    "Farm",
    "ROLE_ADMIN",
    "ROLE_FARMER",
    "ROLES",
    "User",
]

