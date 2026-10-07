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

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.code_generator import generate_code
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


BATCH_STATUS_ACTIVE: str = "ACTIVE"
BATCH_STATUS_PENDING_HANDOVER: str = "PENDING_HANDOVER"
BATCH_STATUS_HANDED_OVER: str = "HANDED_OVER"
BATCH_STATUS_SPLIT: str = "SPLIT"
BATCH_STATUS_MERGED: str = "MERGED"
BATCH_STATUSES: tuple[str, ...] = (
    BATCH_STATUS_ACTIVE,
    BATCH_STATUS_PENDING_HANDOVER,
    BATCH_STATUS_HANDED_OVER,
    BATCH_STATUS_SPLIT,
    BATCH_STATUS_MERGED,
)


class Batch(Base):
    """Lô nông sản thu hoạch từ một vùng trồng.

    Quan hệ: ``Farm 1 ---- N Batch``.

    Attributes:
        id: Khoá chính, tự tăng.
        farm_id: Khoá ngoại trỏ tới ``farms.id`` (vùng trồng xuất xứ).
        product_name: Tên sản phẩm của lô (ví dụ: "Xoài cát Chu").
        quantity: Số lượng / khối lượng của lô, đơn vị kg.
        harvest_date: Ngày thu hoạch.
        status: Trạng thái hiện tại của lô (ACTIVE, PENDING_HANDOVER, HANDED_OVER, SPLIT, MERGED).
        farm: Đối tượng ``Farm`` tương ứng (chiều N-1 của quan hệ).
    """

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Mã định danh lô hàng 8 ký tự duy nhất (T-18 / SCRUM-34)
    code: Mapped[str] = mapped_column(
        String(8),
        unique=True,
        index=True,
        nullable=False,
        default=generate_code,
    )
    # `index=True` để tra cứu "các lô của một vùng trồng" nhanh hơn.
    farm_id: Mapped[int] = mapped_column(
        ForeignKey("farms.id"),
        nullable=False,
        index=True,
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    harvest_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=BATCH_STATUS_ACTIVE,
    )


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


# ------------------------------------------------------------- Vai trò ---
# Khai báo thành hằng số để không phải gõ chuỗi "admin"/"farmer" rải rác
# trong code (tránh lỗi gõ sai, chỉ cần đổi giá trị ở một chỗ nếu sau này
# muốn thêm vai trò mới như "inspector" hay "retailer").
ROLE_ADMIN: str = "admin"
ROLE_FARMER: str = "farmer"
ROLE_INSPECTOR: str = "inspector"
ROLES: tuple[str, ...] = (ROLE_ADMIN, ROLE_FARMER, ROLE_INSPECTOR)


class InspectionLog(Base):
    """Bảng lưu lịch sử kiểm định/thẩm định tính toàn vẹn của lô hàng (T-28 / SCRUM-44).

    Ghi log kèm thời điểm, cán bộ thực hiện, trạng thái và vị trí/loại lỗi
    để phục vụ đối chiếu, thanh tra về sau.
    """

    __tablename__ = "inspection_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id"),
        nullable=False,
        index=True,
    )
    batch_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    inspector: Mapped[str] = mapped_column(String(100), nullable=False)
    timestamp: Mapped[str] = mapped_column(String(50), nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tampered_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str] = mapped_column(String(500), nullable=False)

    batch: Mapped["Batch"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<InspectionLog id={self.id} batch_code={self.batch_code!r} "
            f"valid={self.is_valid} by={self.inspector!r}>"
        )


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
        role: Vai trò của tài khoản: ``"admin"`` (quản trị), ``"farmer"`` (nông dân),
            hoặc ``"inspector"`` (cán bộ kiểm tra).
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


# ------------------------------------------------------------- Phả hệ lô hàng ---
RELATION_SPLIT: str = "SPLIT"
RELATION_MERGE: str = "MERGE"
RELATION_TYPES: tuple[str, ...] = (RELATION_SPLIT, RELATION_MERGE)


class BatchLineage(Base):
    """Bảng quan hệ phả hệ lô hàng (batch_lineage) — Đáp ứng T-37 (SCRUM-53).

    Lưu vết quan hệ phân tách (SPLIT) và sáp nhập (MERGE) giữa các lô hàng:
    - parent_batch_id: Lô cha xuất xứ.
    - child_batch_id: Lô con tiếp nhận.
    - transferred_quantity: Khối lượng chuyển từ cha sang con (kg).
    - relation_type: Loại quan hệ ("SPLIT" hoặc "MERGE").
    - created_at: Thời điểm phát sinh quan hệ (ISO 8601).

    Ràng buộc & Chỉ mục (DoD / AC):
    - Ràng buộc UNIQUE trên cặp [lô cha, lô con] để tránh ghi trùng quan hệ.
    - Tạo chỉ mục trên cả cột cha (parent_batch_id) lẫn con (child_batch_id) để tối ưu truy ngược & truy xuôi.
    - Một lô con của phép gộp có nhiều dòng cha.
    """

    __tablename__ = "batch_lineage"
    __table_args__ = (
        UniqueConstraint("parent_batch_id", "child_batch_id", name="uq_batch_lineage_parent_child"),
        Index("ix_batch_lineage_parent_batch_id", "parent_batch_id"),
        Index("ix_batch_lineage_child_batch_id", "child_batch_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    child_batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    transferred_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    relation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)

    # Relationships
    parent_batch: Mapped["Batch"] = relationship(
        "Batch",
        foreign_keys=[parent_batch_id],
        backref="child_lineages",
    )
    child_batch: Mapped["Batch"] = relationship(
        "Batch",
        foreign_keys=[child_batch_id],
        backref="parent_lineages",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<BatchLineage id={self.id} parent={self.parent_batch_id} -> "
            f"child={self.child_batch_id} qty={self.transferred_quantity}kg type={self.relation_type}>"
        )


__all__ = [
    "BATCH_STATUS_ACTIVE",
    "BATCH_STATUS_HANDED_OVER",
    "BATCH_STATUS_MERGED",
    "BATCH_STATUS_PENDING_HANDOVER",
    "BATCH_STATUS_SPLIT",
    "BATCH_STATUSES",
    "Base",
    "Batch",
    "BatchEvent",
    "BatchLineage",
    "Farm",
    "InspectionLog",
    "RELATION_MERGE",
    "RELATION_SPLIT",
    "RELATION_TYPES",
    "ROLE_ADMIN",
    "ROLE_FARMER",
    "ROLE_INSPECTOR",
    "ROLES",
    "User",
]

