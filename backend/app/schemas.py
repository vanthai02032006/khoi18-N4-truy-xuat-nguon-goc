"""Pydantic schemas - định nghĩa "hợp đồng" dữ liệu vào/ra của API.

Tách riêng schemas (Pydantic) khỏi models (SQLAlchemy) giúp:
- Không lộ cấu trúc bảng ra ngoài API.
- Validate dữ liệu đầu vào tự động và sinh tài liệu Swagger chuẩn.
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Response của endpoint ``GET /health``.

    Ví dụ::

        {"status": "running"}
    """

    model_config = ConfigDict(
        json_schema_extra={"example": {"status": "running"}},
    )

    status: str = Field(
        ...,
        description="Trạng thái hoạt động của API.",
        examples=["running"],
    )


# ------------------------------------------------------------------ Auth ---
# Sprint 4: đăng nhập + phân quyền cơ bản. Không JWT -> response đăng nhập
# chỉ có `username` + `role`, client tự gửi lại thông tin đăng nhập
# (HTTP Basic) ở các request sau.
class LoginRequest(BaseModel):
    """Dữ liệu client gửi lên khi đăng nhập (``POST /auth/login``)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"username": "admin", "password": "123456"},
        },
    )

    username: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Tên đăng nhập.",
        examples=["admin"],
    )
    password: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Mật khẩu dạng thô (backend tự băm SHA-256 để so sánh với database).",
        examples=["123456"],
    )


class LoginResponse(BaseModel):
    """Kết quả đăng nhập thành công: ``username`` + ``role``.

    **Không có token** (vì dự án không dùng JWT). Frontend dùng ``role`` để
    hiển thị đúng chức năng theo phân quyền.

    Ví dụ::

        {"username": "admin", "role": "admin"}
    """

    model_config = ConfigDict(
        json_schema_extra={"example": {"username": "admin", "role": "admin"}},
    )

    username: str = Field(
        ...,
        description="Tên đăng nhập vừa xác thực thành công.",
        examples=["admin"],
    )
    role: str = Field(
        ...,
        description="Vai trò của tài khoản: `admin` (toàn quyền) hoặc `farmer` (nông dân).",
        examples=["admin", "farmer"],
    )
    organization_id: str | None = Field(
        default=None,
        description="Tổ chức / đơn vị của tài khoản.",
        examples=["ORG_ADMIN", "ORG_MY_XUONG"],
    )


class UserResponse(BaseModel):
    """Thông tin tài khoản trả ra API (``GET /users`` - chỉ admin).

    Cố tình **không** có field ``password``: mật khẩu (đã băm) không bao giờ
    được trả về client.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": {"id": 1, "username": "admin", "role": "admin", "organization_id": "ORG_ADMIN"}},
    )

    id: int = Field(..., description="Mã định danh tài khoản.", examples=[1])
    username: str = Field(..., description="Tên đăng nhập.", examples=["admin"])
    role: str = Field(..., description="Vai trò: `admin` hoặc `farmer`.", examples=["admin"])
    organization_id: str | None = Field(
        default=None,
        description="Tổ chức / đơn vị của tài khoản.",
        examples=["ORG_ADMIN"],
    )
    email: str | None = Field(
        default=None,
        description="Địa chỉ email tài khoản.",
        examples=["admin@agritrace.vn"],
    )
    is_active: bool = Field(
        default=True,
        description="Trạng thái tài khoản (True: đang hoạt động, False: đã vô hiệu hoá).",
        examples=[True],
    )


# ------------------------------------------------------------------ Farm ---
_FARM_EXAMPLE: dict = {
    "id": 1,
    "name": "Vùng trồng xoài Cao Lãnh",
    "location": "Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp",
    "area": 2.5,
    "owner": "Hợp tác xã Xoài Mỹ Xương",
}


class FarmCreate(BaseModel):
    """Dữ liệu client gửi lên khi tạo vùng trồng mới (``POST /farms``).

    Chỉ chứa các field client được phép nhập - ``id`` do database sinh ra.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Vùng trồng xoài Cao Lãnh",
                "location": "Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp",
                "area": 2.5,
                "owner": "Hợp tác xã Xoài Mỹ Xương",
            }
        },
    )

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tên vùng trồng.",
        examples=["Vùng trồng xoài Cao Lãnh"],
    )
    location: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Địa điểm của vùng trồng (xã/huyện/tỉnh).",
        examples=["Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp"],
    )
    area: float = Field(
        ...,
        gt=0,
        description="Diện tích canh tác, đơn vị hecta (ha). Phải lớn hơn 0.",
        examples=[2.5],
    )
    owner: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Chủ sở hữu vùng trồng.",
        examples=["Hợp tác xã Xoài Mỹ Xương"],
    )


class FarmUpdate(FarmCreate):
    """Dữ liệu client gửi lên khi **sửa** vùng trồng (``PUT /farms/{farm_id}``).

    Kế thừa ``FarmCreate`` để dùng lại đúng bộ quy tắc validate (tên/địa điểm/
    chủ sở hữu không rỗng, diện tích > 0). ``PUT`` là cập nhật *thay thế* nên
    client gửi **đầy đủ 4 trường** như khi tạo mới; backend ghi đè giá trị cũ.

    Khai báo class riêng (dù giống hệt ``FarmCreate``) để Swagger UI hiển thị
    đúng tên schema ``FarmUpdate`` ở endpoint ``PUT``.

    Ví dụ::

        {"name": "Vùng trồng xoài Cao Lãnh", "location": "...", "area": 3.2,
         "owner": "Hợp tác xã Xoài Mỹ Xương"}
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Vùng trồng xoài Cao Lãnh",
                "location": "Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp",
                "area": 3.2,
                "owner": "Hợp tác xã Xoài Mỹ Xương",
            }
        },
    )


class FarmResponse(BaseModel):
    """Dữ liệu API trả về cho một vùng trồng (kèm ``id``).

    ``from_attributes=True`` cho phép khởi tạo trực tiếp từ ORM object
    (``Farm``) mà không cần convert thủ công::

        FarmResponse.model_validate(farm_obj)
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": _FARM_EXAMPLE},
    )

    id: int = Field(..., description="Mã định danh vùng trồng.", examples=[1])
    name: str = Field(..., description="Tên vùng trồng.")
    location: str = Field(..., description="Địa điểm của vùng trồng.")
    area: float = Field(..., description="Diện tích canh tác (ha).")
    owner: str = Field(..., description="Chủ sở hữu vùng trồng.")


# ----------------------------------------------------------------- Batch ---
_BATCH_EXAMPLE: dict = {
    "id": 1,
    "farm_id": 1,
    "product_name": "Xoài cát Chu",
    "quantity": 120.5,
    "harvest_date": "2026-01-15",
}


class BatchCreate(BaseModel):
    """Dữ liệu client gửi lên khi tạo lô nông sản mới (``POST /batches``).

    ``farm_id`` phải trỏ tới một vùng trồng **đã tồn tại** — router sẽ trả
    ``404 Not Found`` nếu không tìm thấy.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "farm_id": 1,
                "product_name": "Xoài cát Chu",
                "quantity": 120.5,
                "harvest_date": "2026-01-15",
            }
        },
    )

    farm_id: int = Field(
        ...,
        gt=0,
        description="ID vùng trồng (farms.id) - phải tồn tại.",
        examples=[1],
    )
    product_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tên sản phẩm của lô.",
        examples=["Xoài cát Chu"],
    )
    quantity: float = Field(
        ...,
        gt=0,
        description="Số lượng / khối lượng của lô, đơn vị kg. Phải lớn hơn 0.",
        examples=[120.5],
    )
    harvest_date: date = Field(
        ...,
        description="Ngày thu hoạch, định dạng yyyy-MM-dd.",
        examples=["2026-01-15"],
    )
    organization_id: str | None = Field(
        default=None,
        max_length=50,
        description="Tổ chức nắm giữ lô (tuỳ chọn, mặc định lấy theo tài khoản tạo).",
        examples=["ORG_MY_XUONG"],
    )


class BatchUpdate(BatchCreate):
    """Dữ liệu client gửi lên khi **sửa** lô nông sản (``PUT /batches/{batch_id}``).

    Kế thừa ``BatchCreate`` (dùng lại validate: ``farm_id`` > 0, ``quantity`` > 0,
    ``harvest_date`` đúng định dạng ISO). Client gửi **đầy đủ các trường**; router
    trả ``404`` nếu lô hoặc ``farm_id`` mới không tồn tại.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "farm_id": 1,
                "product_name": "Xoài cát Chu",
                "quantity": 150,
                "harvest_date": "2026-01-16",
            }
        },
    )


class BatchResponse(BaseModel):
    """Dữ liệu API trả về cho một lô nông sản (kèm ``id``).

    ``harvest_date`` được serialize thành chuỗi ``yyyy-MM-dd``.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": _BATCH_EXAMPLE},
    )

    id: int = Field(..., description="Mã định danh lô nông sản.", examples=[1])
    farm_id: int = Field(..., description="ID vùng trồng xuất xứ.", examples=[1])
    product_name: str = Field(..., description="Tên sản phẩm của lô.")
    quantity: float = Field(..., description="Số lượng / khối lượng ban đầu (kg).")
    remaining_quantity: float = Field(
        default=0.0,
        description="Số lượng tồn còn lại sau khi xuất/gộp (kg).",
    )
    harvest_date: date = Field(..., description="Ngày thu hoạch.")
    organization_id: str | None = Field(
        default=None,
        description="Tổ chức hiện đang nắm giữ lô nông sản.",
    )


# -------------------------------------------------------- Batch Merge & Genealogy ---
class ParentBatchItem(BaseModel):
    """Một lô mẹ được trích số lượng để gộp vào lô con (SCRUM-60)."""

    parent_batch_id: int = Field(
        ...,
        gt=0,
        description="ID lô mẹ cần lấy số lượng.",
        examples=[1],
    )
    used_quantity: float = Field(
        ...,
        gt=0,
        description="Số lượng lấy từ lô mẹ (kg). Phải lớn hơn 0 và không vượt quá tồn.",
        examples=[50.0],
    )


class BatchMergeRequest(BaseModel):
    """Dữ liệu yêu cầu thực hiện giao dịch gộp lô (SCRUM-60)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "parents": [
                    {"parent_batch_id": 1, "used_quantity": 50.0},
                    {"parent_batch_id": 2, "used_quantity": 80.0},
                ],
                "product_name": "Lô Xoài Cát Chu Chọn Lọc (Gộp Đợt 1)",
                "farm_id": 1,
                "harvest_date": "2026-09-30",
            }
        }
    )

    parents: list[ParentBatchItem] = Field(
        ...,
        min_length=2,
        description="Danh sách các lô mẹ (tối thiểu 2 lô mẹ để gộp).",
    )
    product_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tên sản phẩm cho lô mới hình thành.",
        examples=["Lô Xoài Cát Chu Chọn Lọc (Gộp Đợt 1)"],
    )
    farm_id: int | None = Field(
        default=None,
        gt=0,
        description="ID vùng trồng cho lô mới (nếu không cung cấp sẽ kế thừa từ lô mẹ đầu tiên).",
    )
    harvest_date: date = Field(
        ...,
        description="Ngày gộp / tạo lô mới, định dạng yyyy-MM-dd.",
        examples=["2026-09-30"],
    )
    organization_id: str | None = Field(
        default=None,
        max_length=50,
        description="Tổ chức nắm giữ lô mới (mặc định lấy theo tài khoản đăng nhập).",
    )


class BatchRelationResponse(BaseModel):
    """Thông tin quan hệ cha-con phả hệ giữa các lô."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Mã quan hệ.")
    parent_batch_id: int = Field(..., description="ID lô mẹ.")
    child_batch_id: int = Field(..., description="ID lô con.")
    used_quantity: float = Field(..., description="Khối lượng đã lấy từ lô mẹ (kg).")


class ParentRemainingResponse(BaseModel):
    """Khối lượng còn lại của từng lô mẹ sau khi hoàn tất giao dịch gộp."""

    parent_batch_id: int = Field(..., description="ID lô mẹ.")
    remaining_quantity: float = Field(..., description="Khối lượng còn lại sau khi trừ (kg).")


class BatchMergeResponse(BaseModel):
    """Kết quả trả về sau khi giao dịch gộp lô thành công trong database transaction."""

    new_batch: BatchResponse = Field(..., description="Thông tin lô con mới được tạo.")
    relations: list[BatchRelationResponse] = Field(
        ...,
        description="Danh sách các quan hệ phả hệ parent-child đã lưu vết.",
    )
    parent_remainings: list[ParentRemainingResponse] = Field(
        ...,
        description="Khối lượng tồn còn lại của từng lô mẹ.",
    )


class BatchDetailResponse(BatchResponse):
    """Chi tiết đầy đủ của một lô nông sản, kèm cây phả hệ cha và con (SCRUM-50 / SCRUM-70)."""

    parent_relations: list[BatchRelationResponse] = Field(
        default_factory=list,
        description="Danh sách các lô mẹ mà lô này kế thừa từ đó.",
    )
    child_relations: list[BatchRelationResponse] = Field(
        default_factory=list,
        description="Danh sách các lô con được tách hoặc gộp từ lô này.",
    )



# ----------------------------------------------------------------- Chung ---
class DeleteResponse(BaseModel):
    """Kết quả một lần xoá thành công (``DELETE /farms/{id}``, ``DELETE /batches/{id}``).

    Cố tình trả **200 OK kèm nội dung** (thay vì ``204 No Content``) để giao diện
    hiển thị được thông báo "đã xoá cái gì" cho người dùng.

    Ví dụ::

        {"message": "Đã xoá vùng trồng #2 và 2 lô nông sản thuộc vùng đó.",
         "deleted_id": 2, "deleted_batches": 2}
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Đã xoá vùng trồng #2 và 2 lô nông sản thuộc vùng đó.",
                "deleted_id": 2,
                "deleted_batches": 2,
            }
        },
    )

    message: str = Field(
        ...,
        description="Thông báo kết quả xoá (hiển thị trực tiếp trên giao diện).",
        examples=["Đã xoá vùng trồng #2 và 2 lô nông sản thuộc vùng đó."],
    )
    deleted_id: int = Field(
        ...,
        description="ID của bản ghi vừa bị xoá.",
        examples=[2],
    )
    deleted_batches: int | None = Field(
        default=None,
        description=(
            "Số lô nông sản bị xoá kèm - chỉ có giá trị khi gọi "
            "`DELETE /farms/{farm_id}` (xoá vùng trồng sẽ xoá theo mọi lô thuộc "
            "vùng đó). `null` khi xoá một lô nông sản."
        ),
        examples=[2],
    )


# =========================================================================
# SCRUM-138: SAO LƯU DỮ LIỆU & DIỄN TẬP KHÔI PHỤC
# =========================================================================
class BackupLogResponse(BaseModel):
    """Thông tin chi tiết một bản sao lưu CSDL."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    status: str
    file_path: str
    file_size_bytes: int
    sha256_checksum: str
    details: str | None = None
    rehearsal_verified: bool = False


class BackupRunResponse(BaseModel):
    """Kết quả kích hoạt tiến trình sao lưu CSDL."""

    message: str
    backup: BackupLogResponse
    retained_backups_count: int


class BackupRehearsalRequest(BaseModel):
    """Yêu cầu diễn tập khôi phục CSDL trên database tạm thời."""

    backup_id: int | None = Field(default=None, description="ID của bản backup cần thử khôi phục.")
    backup_file_path: str | None = Field(default=None, description="Hoặc đường dẫn trực tiếp file backup.")


class BackupRehearsalResponse(BaseModel):
    """Kết quả diễn tập khôi phục trên CSDL tạm (dry-run)."""

    status: str  # "SUCCESS" hoặc "FAILED"
    tested_file: str
    integrity_check_output: str
    tables_checked: dict[str, int]
    details: str


# =========================================================================
# SCRUM-136: JOB S-12 KIỂM TRA TÍNH TOÀN VẸN CHUỖI DỮ LIỆU
# =========================================================================
class IntegrityViolationItem(BaseModel):
    """Chi tiết một vi phạm tính toàn vẹn phát hiện được."""

    category: str  # "HASH_CHAIN_MISMATCH", "FOREIGN_KEY_VIOLATION", "QUANTITY_ANOMALY", "CYCLE_DETECTED"
    table: str
    record_id: int | str
    message: str


class IntegrityCheckLogResponse(BaseModel):
    """Thông tin lần kiểm tra toàn vẹn chuỗi dữ liệu (Job S-12)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    status: str  # "PASSED", "FAILED"
    scanned_records: int
    error_count: int
    details: str
    triggered_by: str


class IntegrityCheckRunResponse(BaseModel):
    """Kết quả thực thi kiểm tra toàn vẹn dữ liệu."""

    message: str
    result: IntegrityCheckLogResponse
    violations: list[IntegrityViolationItem] = []


# =========================================================================
# SCRUM-126: DỮ LIỆU NHIỆT ĐỘ CHUỖI LẠNH & TỔNG HỢP THEO GIỜ
# =========================================================================
class TemperatureReadingCreate(BaseModel):
    """Gửi một số đo nhiệt độ mới từ cảm biến chuỗi lạnh."""

    batch_id: int | None = Field(default=None, description="Mã lô nông sản được giám sát (nếu có).")
    sensor_id: str = Field(..., min_length=1, max_length=50, description="Mã định danh cảm biến nhiệt độ.")
    temperature: float = Field(..., description="Nhiệt độ đo được (°C).")
    recorded_at: datetime | None = Field(default=None, description="Thời điểm đo. Nếu để trống lấy thời điểm hiện tại.")


class TemperatureReadingResponse(BaseModel):
    """Thông tin số đo nhiệt độ thô từ cảm biến."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int | None
    sensor_id: str
    temperature: float
    recorded_at: datetime
    is_violation: bool
    is_aggregated: bool
    violation_reason: str | None = None


class HourlyTemperatureSummaryResponse(BaseModel):
    """Dữ liệu nhiệt độ tổng hợp theo từng giờ (MIN, MAX, AVG)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int | None
    sensor_id: str
    hour_bucket: datetime
    min_temp: float
    max_temp: float
    avg_temp: float
    sample_count: int
    violation_count: int
    created_at: datetime


class TemperatureThresholdResponse(BaseModel):
    """Ngưỡng nhiệt độ chuẩn bảo quản nông sản."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    product_name: str
    min_temp: float
    max_temp: float
    description: str | None


class ColdChainAggregateRunResponse(BaseModel):
    """Kết quả tiến trình tổng hợp số đo nhiệt độ cũ hơn 30 ngày."""

    message: str
    cutoff_time: datetime
    aggregated_buckets: int
    purged_raw_readings: int
    retained_violation_readings: int


# =========================================================================
# SCRUM-91: MỜI THÀNH VIÊN VÀ PHÂN QUYỀN TỔ CHỨC
# =========================================================================
class InvitationCreateRequest(BaseModel):
    """Yêu cầu mời nhân viên mới vào tổ chức."""

    email: str = Field(..., min_length=3, max_length=255, description="Email người nhận lời mời.")
    role: str = Field(default="farmer", description="Vai trò được chỉ định: `farmer` hoặc `admin`.")
    organization_id: str | None = Field(
        default=None,
        description="Mã tổ chức. Nếu không truyền, hệ thống tự lấy organization của người mời.",
    )
    expires_in_hours: int = Field(default=48, ge=1, le=168, description="Thời hạn lời mời tính bằng giờ (mặc định 48h).")


class InvitationResponse(BaseModel):
    """Thông tin lời mời thành viên."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    organization_id: str
    role: str
    expires_at: datetime
    is_used: bool
    is_revoked: bool
    invited_by_username: str
    created_at: datetime
    invite_token: str | None = Field(
        default=None,
        description="Token lời mời (chỉ trả về một lần duy nhất khi tạo mới hoặc trong môi trường test/demo).",
    )
    invite_link: str | None = Field(
        default=None,
        description="Đường link thiết lập mật khẩu chứa token lời mời.",
    )


class InvitationAcceptRequest(BaseModel):
    """Thiết lập tài khoản và mật khẩu từ đường link lời mời."""

    token: str = Field(..., min_length=10, description="Token bí mật trong lời mời email.")
    username: str = Field(..., min_length=3, max_length=50, description="Tên đăng nhập mong muốn.")
    password: str = Field(..., min_length=6, max_length=128, description="Mật khẩu đăng nhập mới.")


class MemberDeactivateRequest(BaseModel):
    """Vô hiệu hóa hoặc kích hoạt lại tài khoản thành viên."""

    user_id: int = Field(..., description="ID tài khoản cần thay đổi trạng thái.")
    active: bool = Field(..., description="True để kích hoạt lại, False để vô hiệu hóa.")
    reason: str | None = Field(default=None, description="Lý do thay đổi trạng thái tài khoản.")


class OrganizationAuditLogResponse(BaseModel):
    """Nhật ký hoạt động quản lý thành viên & phân quyền."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: str
    operator_username: str
    action: str
    target_email_or_user: str | None
    details: str | None
    created_at: datetime
