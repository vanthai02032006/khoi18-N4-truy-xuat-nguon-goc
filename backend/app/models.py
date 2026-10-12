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

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
    batches: Mapped[list["Batch"]] = relationship(
        back_populates="farm",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        return f"<Farm id={self.id} name={self.name!r} area={self.area}ha>"


class Batch(Base):
    """Lô nông sản thu hoạch từ một vùng trồng hoặc tạo từ giao dịch gộp (SCRUM-60).

    Quan hệ: ``Farm 1 ---- N Batch``.
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
    """Bảng quan hệ cha - con (Genealogy / Phả hệ lô hàng) - SCRUM-60."""

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
    """Lịch sử nắm giữ lô của tổ chức (Custody / Ownership History) - SCRUM-70."""

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
    """Tài khoản đăng nhập của hệ thống - bảng ``users``."""

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
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover - chỉ dùng khi debug/log
        return f"<User id={self.id} username={self.username!r} role={self.role} org={self.organization_id} active={self.is_active}>"


# -------------------------------------------------- SCRUM-138: Backup Log ---
class BackupLog(Base):
    """Nhật ký sao lưu cơ sở dữ liệu - SCRUM-138."""

    __tablename__ = "backup_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # "SUCCESS", "FAILED"
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256_checksum: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    rehearsal_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BackupLog id={self.id} status={self.status} path={self.file_path}>"


# --------------------------------------- SCRUM-136: Integrity Check Log ---
class IntegrityCheckLog(Base):
    """Nhật ký kiểm tra tính toàn vẹn chuỗi dữ liệu (Job S-12) - SCRUM-136."""

    __tablename__ = "integrity_check_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # "PASSED", "FAILED"
    scanned_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON chi tiết lỗi
    triggered_by: Mapped[str] = mapped_column(String(50), nullable=False, default="NIGHTLY_JOB_S12")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<IntegrityCheckLog id={self.id} status={self.status} errors={self.error_count}>"


# ------------------------------ SCRUM-126: Cold Chain Temperature Data ---
class TemperatureThresholdConfig(Base):
    """Cấu hình ngưỡng nhiệt độ bảo quản theo từng loại nông sản - SCRUM-126."""

    __tablename__ = "temperature_threshold_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    min_temp: Mapped[float] = mapped_column(Float, nullable=False)
    max_temp: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TemperatureThresholdConfig product={self.product_name!r} range=[{self.min_temp}, {self.max_temp}]C>"


class TemperatureReading(Base):
    """Số đo nhiệt độ thô từ cảm biến chuỗi lạnh (Raw Telemetry) - SCRUM-126."""

    __tablename__ = "temperature_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    sensor_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    is_violation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    is_aggregated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    violation_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TemperatureReading id={self.id} sensor={self.sensor_id} temp={self.temperature}C violation={self.is_violation}>"


class HourlyTemperatureSummary(Base):
    """Dữ liệu nhiệt độ tổng hợp theo từng giờ (MIN, MAX, AVG) - SCRUM-126."""

    __tablename__ = "hourly_temperature_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    sensor_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    hour_bucket: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    min_temp: Mapped[float] = mapped_column(Float, nullable=False)
    max_temp: Mapped[float] = mapped_column(Float, nullable=False)
    avg_temp: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    violation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("batch_id", "sensor_id", "hour_bucket", name="uq_batch_sensor_hour"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<HourlyTemperatureSummary sensor={self.sensor_id} bucket={self.hour_bucket} avg={self.avg_temp}C>"


# ----------------------------- SCRUM-91: Organization Invitations & Audits ---
class OrganizationInvitation(Base):
    """Lời mời tham gia tổ chức qua email - SCRUM-91."""

    __tablename__ = "organization_invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    organization_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=ROLE_FARMER)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    is_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    invited_by_username: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OrganizationInvitation email={self.email!r} org={self.organization_id} used={self.is_used}>"


class OrganizationAuditLog(Base):
    """Nhật ký kiểm toán hoạt động thành viên & phân quyền - SCRUM-91."""

    __tablename__ = "organization_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    operator_username: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_email_or_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OrganizationAuditLog org={self.organization_id} action={self.action} by={self.operator_username}>"


__all__ = [
    "BackupLog",
    "Base",
    "Batch",
    "BatchCustodyHistory",
    "BatchRelation",
    "Farm",
    "HourlyTemperatureSummary",
    "IntegrityCheckLog",
    "OrganizationAuditLog",
    "OrganizationInvitation",
    "ROLE_ADMIN",
    "ROLE_FARMER",
    "ROLES",
    "TemperatureReading",
    "TemperatureThresholdConfig",
    "User",
]


