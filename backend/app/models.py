"""Khai báo ORM models (bảng dữ liệu) bằng SQLAlchemy 2.0.

Sprint 1: khung dự án + endpoint ``GET /health`` (chưa có bảng nghiệp vụ).
Sprint 2: module **Farm** (quản lý vùng trồng - bảng ``farms``)
và module **Batch** (quản lý lô nông sản - bảng ``batches``).
Sprint 4: module **Auth** (đăng nhập + phân quyền - bảng ``users``).
Bàn giao: module **Handover** (bảng ``handovers`` - luồng bàn giao quyền giữ lô)
và bảng nhật ký ``batch_events`` (sự kiện vòng đời lô phục vụ truy xuất nguồn gốc).

Quan hệ giữa các bảng::

    Farm 1 ---- N Batch   (một vùng trồng có nhiều lô nông sản)
    Batch 1 --- N Handover (lịch sử bàn giao quyền giữ lô)
    Batch 1 --- N BatchEvent (nhật ký sự kiện vòng đời lô)
    User                  (bảng độc lập, dùng cho đăng nhập/phân quyền)

File này là điểm duy nhất (single source of truth) khai báo bảng dữ liệu.
Mọi model đều kế thừa ``Base`` và bảng sẽ được ``init_db()`` trong
``app/database.py`` tự động tạo khi ứng dụng khởi động (xem ``lifespan``
ở ``app/main.py``) - không cần chạy script SQL thủ công.
"""

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    """Giờ UTC dạng naive để lưu vào cột ``TIMESTAMP WITHOUT TIME ZONE``.

    Dùng ``datetime.now(timezone.utc)`` rồi bỏ ``tzinfo`` thay vì
    ``datetime.utcnow()`` - cùng giá trị nhưng không kích hoạt cảnh báo
    ``DeprecationWarning`` (``utcnow`` sẽ bị loại bỏ ở phiên bản Python sau).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
        current_owner: **Tổ chức/đơn vị đang giữ quyền quản lý lô**. Ban đầu lấy
            theo chủ vùng trồng; khi bên nhận **xác nhận** bàn giao thì trường này
            được đổi sang bên nhận (xem ``app/routers/handovers.py``). Trong lúc
            bàn giao còn *chờ xử lý* hoặc bị **từ chối**, giá trị này giữ nguyên.
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
    # NULL = chưa ghi nhận chủ sở hữu (lô cũ trước khi có nghiệp vụ bàn giao).
    current_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)

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
            f"product_name={self.product_name!r} owner={self.current_owner!r}>"
        )


# ------------------------------------------------- Trạng thái bàn giao ---
# Khai báo thành hằng số để router/schemas/test không phải gõ chuỗi rải rác và
# chỉ cần đổi một chỗ nếu sau này thêm trạng thái mới (ví dụ "cancelled").
HANDOVER_STATUS_PENDING: str = "pending"
HANDOVER_STATUS_ACCEPTED: str = "accepted"
HANDOVER_STATUS_REJECTED: str = "rejected"
HANDOVER_STATUSES: tuple[str, ...] = (
    HANDOVER_STATUS_PENDING,
    HANDOVER_STATUS_ACCEPTED,
    HANDOVER_STATUS_REJECTED,
)

# --------------------------------------------------- Loại sự kiện vòng đời ---
EVENT_TYPE_BATCH_CREATED: str = "BATCH_CREATED"
EVENT_TYPE_HANDOVER_PENDING: str = "HANDOVER_PENDING"
EVENT_TYPE_HANDOVER_ACCEPTED: str = "HANDOVER_ACCEPTED"
EVENT_TYPE_HANDOVER_REJECTED: str = "HANDOVER_REJECTED"
EVENT_TYPE_OWNER_CHANGED: str = "OWNER_CHANGED"
EVENT_TYPES: tuple[str, ...] = (
    EVENT_TYPE_BATCH_CREATED,
    EVENT_TYPE_HANDOVER_PENDING,
    EVENT_TYPE_HANDOVER_ACCEPTED,
    EVENT_TYPE_HANDOVER_REJECTED,
    EVENT_TYPE_OWNER_CHANGED,
)


class Handover(Base):
    """Phiếu bàn giao quyền giữ lô nông sản giữa bên giao và bên nhận - bảng ``handovers``.

    Luồng nghiệp vụ:

    1. Bên giao tạo phiếu → trạng thái ``pending``. **Lô vẫn thuộc bên giao**
       (``Batch.current_owner`` giữ nguyên).
    2. Bên nhận **xác nhận** (``accept``) → trạng thái ``accepted``, quyền giữ lô
       chuyển sang bên nhận.
    3. Bên nhận **từ chối** (``reject``) → trạng thái ``rejected``, quyền giữ lô
       **giữ nguyên** ở bên giao, lý do từ chối được lưu lại.

    Chỉ **bên nhận** được gọi 2 thao tác xác nhận/từ chối; kiểm tra ở máy chủ
    bằng dependency ``require_handover_receiver`` (xem ``app/routers/handovers.py``).

    Attributes:
        id: Khoá chính, tự tăng.
        batch_id: Khoá ngoại trỏ tới ``batches.id`` (lô được bàn giao).
        sender_id: ID tài khoản bên giao (``users.id``), có thể ``None``.
        sender_name: Tên hiển thị của bên giao (tổ chức/đơn vị).
        receiver_id: ID tài khoản **bên nhận** - căn cứ để kiểm tra quyền gọi
            xác nhận/từ chối. ``None`` nghĩa là bên nhận chưa có tài khoản hệ
            thống, khi đó không ai gọi được 2 thao tác này.
        receiver_name: Tên hiển thị của bên nhận; khi xác nhận, giá trị này được
            ghi vào ``Batch.current_owner``.
        status: ``pending`` / ``accepted`` / ``rejected``.
        notes: Ghi chú khi tạo phiếu, hoặc **lý do từ chối** do bên nhận nhập.
        created_at: Thời điểm tạo phiếu.
        updated_at: Thời điểm xác nhận hoặc từ chối.
    """

    __tablename__ = "handovers"
    # Ràng buộc CSDL: mỗi lô chỉ có **tối đa 1 phiếu bàn giao đang chờ** xử lý.
    # Đây là chỉ mục duy nhất một phần (partial unique index) - chỉ áp dụng cho
    # các dòng có `status = 'pending'`, nên một lô vẫn giữ được đầy đủ lịch sử
    # các phiếu đã accepted/rejected.
    __table_args__ = (
        Index(
            "uq_handovers_one_pending_per_batch",
            "batch_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id"),
        nullable=False,
        index=True,
    )
    sender_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False)
    receiver_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    receiver_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=HANDOVER_STATUS_PENDING,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utcnow,
        nullable=False,
    )
    # `onupdate=_utcnow`: tự động ghi nhận thời điểm xác nhận/từ chối mỗi khi
    # phiếu được cập nhật, nên router không phải tự set thủ công.
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        onupdate=_utcnow,
    )

    # Quan hệ N-1 tới lô; không dùng cascade để tránh vô tình xoá mất lịch sử
    # bàn giao khi xoá lô (nhật ký truy xuất nguồn gốc phải giữ được).
    batch: Mapped["Batch"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        return (
            f"<Handover id={self.id} batch_id={self.batch_id} "
            f"status={self.status!r} receiver={self.receiver_name!r}>"
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
    "EVENT_TYPE_BATCH_CREATED",
    "EVENT_TYPE_HANDOVER_ACCEPTED",
    "EVENT_TYPE_HANDOVER_PENDING",
    "EVENT_TYPE_HANDOVER_REJECTED",
    "EVENT_TYPE_OWNER_CHANGED",
    "EVENT_TYPES",
    "Farm",
    "HANDOVER_STATUS_ACCEPTED",
    "HANDOVER_STATUS_PENDING",
    "HANDOVER_STATUS_REJECTED",
    "HANDOVER_STATUSES",
    "Handover",
    "ROLE_ADMIN",
    "ROLE_FARMER",
    "ROLES",
    "User",
]
