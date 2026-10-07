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

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import Date, Float, ForeignKey, Index, Integer, String
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
    # Mã lô riêng biệt tự động sinh (Unique Batch Code)
    batch_code: Mapped[str] = mapped_column(
        String(50),
        nullable=True,
        unique=True,
        index=True,
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    harvest_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Quyền giữ lô thực tế và tổ chức nhận đang chờ xác nhận bàn giao (Handover Workflow)
    current_holder_org: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="HTX Nông Nghiệp Số 4",
        index=True,
    )
    pending_receiver_org: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default=None,
        index=True,
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


class Organization(Base):
    """Tổ chức trong chuỗi cung ứng nông sản (hợp tác xã, nhà vận chuyển, kho bãi, chế biến, bán lẻ).

    Bảng ``organizations`` phục vụ kết nối dữ liệu (JOIN) sự kiện theo tổ chức.
    """

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=True, default="")

    events: Mapped[list["BatchEvent"]] = relationship(
        back_populates="org_rel",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Organization id={self.id} code={self.code!r} name={self.name!r}>"


class BatchEvent(Base):
    """Bảng sự kiện gắn với lô hàng (batch_events) — Cơ chế chuỗi bản ghi không sửa được.

    Đáp ứng SCRUM-39 (T-23) & K-01:
    - Chỉ cho phép ghi thêm (Append-only).
    - Mỗi sự kiện lưu: loại sự kiện, nội dung JSON (payload), người thực hiện, tổ chức,
      thời điểm, hash của chính nó và previous_hash tạo thành chuỗi liên kết mật mã.
    - Có chỉ mục (Index) hỗ trợ truy vấn nhanh theo lô hàng và thời gian (T-23).
    """

    __tablename__ = "batch_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id"),
        nullable=False,
        index=True,
    )
    org_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[str] = mapped_column(String(1000), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    organization: Mapped[str] = mapped_column(String(100), nullable=False, default="HTX Nông Nghiệp Số 4", index=True)
    timestamp: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="0" * 64)

    batch: Mapped["Batch"] = relationship(back_populates="events")
    org_rel: Mapped[Optional["Organization"]] = relationship(back_populates="events")

    # Chỉ mục phức hợp (Composite index) theo (batch_id, timestamp) và (batch_id, id) phục vụ T-23
    __table_args__ = (
        Index("ix_batch_events_batch_time", "batch_id", "timestamp"),
        Index("ix_batch_events_batch_id_id", "batch_id", "id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BatchEvent id={self.id} batch_id={self.batch_id} type={self.event_type!r} hash={self.hash[:8]}>"


# ------------------------------------------------------------- Vai trò ---
# Khai báo thành hằng số để không phải gõ chuỗi "admin"/"farmer"/"inspector" rải rác
# trong code.
ROLE_ADMIN: str = "admin"
ROLE_FARMER: str = "farmer"
ROLE_INSPECTOR: str = "inspector"
ROLES: tuple[str, ...] = (ROLE_ADMIN, ROLE_FARMER, ROLE_INSPECTOR)


class RecallOrder(Base):
    """Lệnh thu hồi / xử lý khẩn cấp do Cán bộ kiểm tra phát hành (Recall / Inspection Order).

    Giúp cán bộ kiểm tra theo dõi tiến độ phản hồi/xác nhận từ tất cả các tổ chức liên quan,
    biết chính xác bao nhiêu bên đã xác nhận (ví dụ 3/5), những bên nào chưa xong,
    và tự động hoàn tất lệnh khi bên cuối cùng xác nhận.
    """

    __tablename__ = "recall_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=True, default="")
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("batches.id"), nullable=True, index=True)
    issuer_username: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    completed_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="IN_PROGRESS", index=True)  # IN_PROGRESS, COMPLETED, CANCELLED

    targets: Mapped[list["RecallOrderTarget"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RecallOrder id={self.id} code={self.order_code!r} status={self.status!r}>"


class RecallOrderTarget(Base):
    """Danh sách các tổ chức liên quan chịu trách nhiệm thực thi và xác nhận lệnh."""

    __tablename__ = "recall_order_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("recall_orders.id"), nullable=False, index=True)
    org_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING")  # PENDING, CONFIRMED
    confirmed_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    order: Mapped["RecallOrder"] = relationship(back_populates="targets")

    __table_args__ = (
        Index("ix_recall_target_order_org", "order_id", "org_name", unique=True),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RecallOrderTarget id={self.id} org={self.org_name!r} status={self.status!r}>"


class ColdChainThreshold(Base):
    """Cấu hình ngưỡng nhiệt độ và độ trễ cảnh báo chuỗi lạnh cho từng loại sản phẩm.

    Đáp ứng yêu cầu:
    - Quản trị hệ thống đặt ngưỡng trên (temp_max), ngưỡng dưới (temp_min), độ trễ (delay_minutes).
    - Ví dụ:
      * Rau lá (Rau muống, Rau cải): temp_min=2.0°C, temp_max=8.0°C, delay=15 phút.
      * Thịt đông lạnh: temp_min=-22.0°C, temp_max=-18.0°C, delay=5 phút.
    - Chuyến chở nhiều sản phẩm áp ngưỡng chặt nhất:
      * temp_min_effective = max(temp_min của các sản phẩm)
      * temp_max_effective = min(temp_max của các sản phẩm)
      * delay_effective = min(delay_minutes của các sản phẩm)
    """

    __tablename__ = "cold_chain_thresholds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_type: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    temp_min: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)
    temp_max: Mapped[float] = mapped_column(Float, nullable=False, default=8.0)
    delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    description: Mapped[str] = mapped_column(String(255), nullable=True, default="")
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(50), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ColdChainThreshold type={self.product_type!r} range=[{self.temp_min}, {self.temp_max}] delay={self.delay_minutes}m>"


class ColdChainViolation(Base):
    """Vi phạm chuỗi lạnh đã được ghi nhận trong quá trình vận chuyển / lưu kho.

    Nguyên tắc: Đổi cấu hình ngưỡng không tính lại vi phạm đã ghi (Snapshot bất biến).
    Lưu lại giá trị ngưỡng và độ trễ tại thời điểm vi phạm xảy ra.
    """

    __tablename__ = "cold_chain_violations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    product_types_json: Mapped[str] = mapped_column(String(500), nullable=False)  # JSON list of product types
    recorded_temperature: Mapped[float] = mapped_column(Float, nullable=False)
    duration_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    # Ngưỡng áp dụng tại thời điểm vi phạm (snapshot)
    applied_temp_min: Mapped[float] = mapped_column(Float, nullable=False)
    applied_temp_max: Mapped[float] = mapped_column(Float, nullable=False)
    applied_delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    violation_reason: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    location: Mapped[str] = mapped_column(String(255), nullable=True, default="Xe lạnh chuyên dụng")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ColdChainViolation shipment={self.shipment_code!r} temp={self.recorded_temperature} reason={self.violation_reason!r}>"


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
        role: Vai trò của tài khoản: ``"admin"`` (quản trị - toàn quyền),
            ``"farmer"`` (nông dân - quản lý nông sản), hoặc ``"inspector"`` (cán bộ kiểm tra).
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
    "ColdChainThreshold",
    "ColdChainViolation",
    "Farm",
    "Organization",
    "RecallOrder",
    "RecallOrderTarget",
    "ROLE_ADMIN",
    "ROLE_FARMER",
    "ROLE_INSPECTOR",
    "ROLES",
    "User",
]
