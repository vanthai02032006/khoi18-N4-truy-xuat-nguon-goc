"""Pydantic schemas - định nghĩa "hợp đồng" dữ liệu vào/ra của API.

Tách riêng schemas (Pydantic) khỏi models (SQLAlchemy) giúp:
- Không lộ cấu trúc bảng ra ngoài API.
- Validate dữ liệu đầu vào tự động và sinh tài liệu Swagger chuẩn.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class UserResponse(BaseModel):
    """Thông tin tài khoản trả ra API (``GET /users`` - chỉ admin).

    Cố tình **không** có field ``password``: mật khẩu (đã băm) không bao giờ
    được trả về client.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": {"id": 1, "username": "admin", "role": "admin"}},
    )

    id: int = Field(..., description="Mã định danh tài khoản.", examples=[1])
    username: str = Field(..., description="Tên đăng nhập.", examples=["admin"])
    role: str = Field(..., description="Vai trò: `admin` hoặc `farmer`.", examples=["admin"])


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
        description="Ngày thu hoạch, định dạng yyyy-MM-dd. Không được ở tương lai.",
        examples=["2026-01-15"],
    )

    @field_validator("harvest_date")
    @classmethod
    def validate_harvest_date(cls, v: date) -> date:
        """Từ chối ngày thu hoạch trong tương lai (SCRUM-36 / T-20)."""
        if v > date.today():
            raise ValueError("Ngày thu hoạch không được ở trong tương lai.")
        return v


class BatchUpdate(BatchCreate):
    """Dữ liệu client gửi lên khi **sửa** lô nông sản (``PUT /batches/{batch_id}``).

    Kế thừa ``BatchCreate`` (dùng lại validate: ``farm_id`` > 0, ``quantity`` > 0,
    ``harvest_date`` đúng định dạng ISO). Client gửi **đầy đủ 4 trường**; router
    trả ``404`` nếu lô hoặc ``farm_id`` mới không tồn tại.

    Lưu ý: đổi ``farm_id`` = chuyển lô sang vùng trồng khác (vẫn phải tồn tại).

    Ví dụ::

        {"farm_id": 1, "product_name": "Xoài cát Chu", "quantity": 150,
         "harvest_date": "2026-01-16"}
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
    batch_code: str = Field(..., description="Mã lô sinh tự động (Unique Batch Code).", examples=["LOT-20261007-0001"])
    farm_id: int = Field(..., description="ID vùng trồng xuất xứ.", examples=[1])
    product_name: str = Field(..., description="Tên sản phẩm của lô.")
    quantity: float = Field(..., description="Số lượng / khối lượng (kg).")
    harvest_date: date = Field(..., description="Ngày thu hoạch.")
    current_holder_org: str = Field(
        default="HTX Nông Nghiệp Số 4",
        description="Tổ chức hiện đang nắm giữ lô hàng thực tế.",
    )
    pending_receiver_org: str | None = Field(
        default=None,
        description="Tổ chức đang được bàn giao (chờ xác nhận hoặc từ chối).",
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
    deleted_batches: Optional[int] = Field(
        default=None,
        description=(
            "Số lô nông sản bị xoá kèm - chỉ có giá trị khi gọi "
            "`DELETE /farms/{farm_id}` (xoá vùng trồng sẽ xoá theo mọi lô thuộc "
            "vùng đó). `null` khi xoá một lô nông sản."
        ),
        examples=[2],
    )


# ----------------------------------------------------------- Batch Events ---
class BatchEventCreate(BaseModel):
    """Dữ liệu ghi thêm sự kiện vào lô hàng (SCRUM-39)."""

    event_type: str = Field(..., description="Loại sự kiện (HARVEST, PROCESSING, HANDOVER, SPLIT, MERGE).", examples=["HARVEST"])
    payload: str = Field(..., description="Dữ liệu chi tiết sự kiện dạng JSON canonical.", examples=['{"weight": 500, "note": "Thu hoach buoi sang"}'])
    organization: str = Field(default="HTX Nông Nghiệp Số 4", description="Tên tổ chức ghi nhận.")


class BatchEventResponse(BaseModel):
    """Thông tin một sự kiện trong chuỗi bản ghi."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    event_type: str
    payload: str
    actor: str
    organization: str
    organization_name: str | None = None
    timestamp: str
    hash: str
    previous_hash: str


class BatchTimelineResponse(BaseModel):
    """Dòng thời gian sự kiện của một lô hàng kèm trạng thái tính toàn vẹn và phân trang."""

    batch_id: int
    is_valid: bool = Field(..., description="True nếu toàn bộ chuỗi mã băm toàn vẹn, False nếu bị can thiệp sửa lén.")
    tampered_index: Optional[int] = Field(None, description="Vị trí sự kiện đầu tiên bị sai lệch nếu có.")
    total: int = Field(default=0, description="Tổng số sự kiện của lô hàng.")
    limit: Optional[int] = Field(default=None, description="Số lượng sự kiện tối đa trên mỗi trang.")
    offset: Optional[int] = Field(default=None, description="Vị trí bắt đầu lấy sự kiện.")
    events: list[BatchEventResponse]


# ------------------------------------------------------------- Batch Split (T-40) ---
class BatchSplitItem(BaseModel):
    """Thông tin một dòng lô con cần tách."""

    quantity: float = Field(..., gt=0, description="Khối lượng của lô con (kg). Phải lớn hơn 0.")
    note: str | None = Field(default=None, max_length=255, description="Ghi chú phân loại / đóng thùng.")


class BatchSplitRequest(BaseModel):
    """Dữ liệu yêu cầu tách nhập nhiều dòng lô con (SCRUM-56 / T-40)."""

    items: list[BatchSplitItem] = Field(..., min_length=1, description="Danh sách các dòng khối lượng lô con.")


class BatchSplitResponse(BaseModel):
    """Kết quả sau khi tách lô: danh sách các mã lô con và số dư còn lại của lô mẹ."""

    parent_batch_id: int
    parent_remaining_quantity: float
    child_batches: list[BatchResponse]
    message: str


# ------------------------------------------------------------- Batch Merge ---
class BatchMergeItem(BaseModel):
    """Dòng đóng góp từ một lô mẹ vào mẻ gộp."""

    batch_id: int = Field(..., gt=0, description="ID hoặc mã định danh của lô mẹ cần gộp.")
    quantity: float = Field(..., gt=0, description="Khối lượng lấy từ lô mẹ này (kg). Phải lớn hơn 0.")


class BatchMergeRequest(BaseModel):
    """Yêu cầu gộp nhiều lô nông sản thành một lô mới."""

    items: list[BatchMergeItem] = Field(..., min_length=2, description="Danh sách các lô thành phần cần gộp (tối thiểu 2 lô).")
    product_name: str | None = Field(default=None, max_length=255, description="Tên sản phẩm của lô mới (nếu không truyền sẽ lấy theo lô mẹ đầu tiên).")
    note: str | None = Field(default=None, max_length=500, description="Ghi chú mẻ gộp.")


class BatchMergeResponse(BaseModel):
    """Kết quả sau khi gộp lô thành công."""

    merged_batch: BatchResponse
    parent_batches: list[BatchResponse]
    message: str


# ------------------------------------------------------------- Batch Handover ---
class HandoverInitiateRequest(BaseModel):
    """Dữ liệu khởi tạo bàn giao lô hàng sang một tổ chức khác."""

    target_organization: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tên tổ chức bên nhận bàn giao (ví dụ: 'Hợp tác xã Sơ chế Mỹ Xương').",
    )
    note: str | None = Field(
        default=None,
        max_length=500,
        description="Ghi chú đợt bàn giao (ví dụ: 'Giao 500kg sơ chế').",
    )

    @field_validator("target_organization")
    @classmethod
    def validate_target_org(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Tên tổ chức nhận không được để trống.")
        return trimmed


class HandoverRejectRequest(BaseModel):
    """Dữ liệu khi từ chối nhận bàn giao - bắt buộc kèm lý do rõ ràng."""

    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Lý do từ chối nhận lô hàng (bắt buộc, không được để trống).",
    )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Lý do từ chối không được để trống.")
        return trimmed


class HandoverActionResponse(BaseModel):
    """Kết quả sau khi thực hiện thao tác bàn giao (khởi tạo, xác nhận, từ chối)."""

    batch_id: int
    current_holder_org: str
    pending_receiver_org: str | None
    status: str
    message: str
    event_id: int
    event_hash: str


# ----------------------------------------------------------- Recall / Inspection Orders ---
class RecallOrderTargetCreate(BaseModel):
    """Tổ chức liên quan cần thực thi lệnh."""

    org_name: str = Field(..., min_length=1, max_length=255, description="Tên tổ chức liên quan.")


class RecallOrderCreate(BaseModel):
    """Dữ liệu ban hành lệnh thu hồi / kiểm tra."""

    title: str = Field(..., min_length=1, max_length=255, description="Tiêu đề lệnh (ví dụ: 'Thu hồi khẩn cấp lô xoài nhiễm khuẩn').")
    reason: str = Field(..., min_length=1, max_length=500, description="Lý do ban hành lệnh.")
    description: str | None = Field(default=None, max_length=1000, description="Mô tả chi tiết và hướng dẫn xử lý.")
    batch_id: int | None = Field(default=None, description="ID lô nông sản liên quan (nếu có).")
    target_organizations: list[str] = Field(..., min_length=1, description="Danh sách các tổ chức liên quan phải xác nhận.")


class RecallOrderConfirm(BaseModel):
    """Dữ liệu xác nhận thực thi lệnh từ một tổ chức."""

    org_name: str | None = Field(default=None, description="Tên tổ chức xác nhận (nếu không truyền qua header X-Organization-Id).")
    note: str | None = Field(default=None, max_length=500, description="Ghi chú xác nhận hoặc kết quả xử lý.")


class RecallTargetResponse(BaseModel):
    """Trạng thái thực thi của một tổ chức trong lệnh."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    org_name: str
    status: str
    confirmed_at: str | None = None
    confirmed_by: str | None = None
    note: str | None = None


class RecallOrderResponse(BaseModel):
    """Thông tin chi tiết một lệnh thu hồi kèm tiến độ."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_code: str
    title: str
    description: str | None = None
    reason: str
    batch_id: int | None = None
    issuer_username: str
    created_at: str
    completed_at: str | None = None
    status: str
    targets: list[RecallTargetResponse]

    # Các trường tổng hợp tiến độ phục vụ cán bộ kiểm tra
    total_targets: int
    confirmed_count: int
    pending_count: int
    progress_ratio: str  # Ví dụ "3/5"
    is_completed: bool
    pending_organizations: list[str]  # Tên các bên chưa xong


class RecallOrderConfirmResponse(BaseModel):
    """Kết quả sau khi một tổ chức xác nhận lệnh."""

    order_id: int
    order_code: str
    org_name: str
    target_status: str
    order_status: str
    progress_ratio: str
    is_order_completed: bool
    message: str
    event_id: int | None = None
    event_hash: str | None = None


# ------------------------------------------------------------- Cold Chain ---
class ColdChainThresholdCreate(BaseModel):
    """Cấu hình ngưỡng nhiệt độ và độ trễ cho một loại sản phẩm mới."""

    product_type: str = Field(..., min_length=1, max_length=100, description="Tên loại sản phẩm (VD: 'Rau lá', 'Thịt đông lạnh')")
    temp_min: float = Field(..., description="Ngưỡng nhiệt độ dưới (°C)")
    temp_max: float = Field(..., description="Ngưỡng nhiệt độ trên (°C)")
    delay_minutes: int = Field(..., ge=0, description="Độ trễ cho phép vượt ngưỡng trước khi cảnh báo (phút)")
    description: str | None = Field(default="", max_length=255)

    @field_validator("temp_max")
    @classmethod
    def validate_temp_range(cls, v: float, info) -> float:
        min_v = info.data.get("temp_min")
        if min_v is not None and v <= min_v:
            raise ValueError("Ngưỡng trên (temp_max) phải lớn hơn ngưỡng dưới (temp_min)")
        return v


class ColdChainThresholdUpdate(BaseModel):
    """Cập nhật ngưỡng nhiệt độ hoặc độ trễ."""

    temp_min: float | None = None
    temp_max: float | None = None
    delay_minutes: int | None = Field(default=None, ge=0)
    description: str | None = None


class ColdChainThresholdResponse(BaseModel):
    """Thông tin cấu hình ngưỡng nhiệt độ của loại sản phẩm."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    product_type: str
    temp_min: float
    temp_max: float
    delay_minutes: int
    description: str | None = None
    created_at: str
    updated_at: str


class MultiProductEffectiveThresholdRequest(BaseModel):
    """Yêu cầu tính toán ngưỡng hiệu dụng cho chuyến xe chở nhiều sản phẩm."""

    product_types: list[str] = Field(..., min_length=1, description="Danh sách các loại sản phẩm cùng chở")


class MultiProductEffectiveThresholdResponse(BaseModel):
    """Ngưỡng áp dụng chặt nhất cho chuyến hàng chở nhiều sản phẩm."""

    product_types: list[str]
    effective_temp_min: float
    effective_temp_max: float
    effective_delay_minutes: int
    strictest_rule_summary: str
    is_compatible: bool
    compatibility_warning: str | None = None


class TelemetryCheckRequest(BaseModel):
    """Kiểm tra một bản ghi telemetry nhiệt độ của chuyến xe so với ngưỡng."""

    shipment_code: str = Field(..., min_length=1, max_length=50)
    product_types: list[str] = Field(..., min_length=1)
    recorded_temperature: float
    duration_minutes: float = Field(..., ge=0)
    location: str | None = "Xe lạnh chuyên dụng"


class TelemetryCheckResponse(BaseModel):
    """Kết quả đánh giá vi phạm chuỗi lạnh."""

    shipment_code: str
    product_types: list[str]
    recorded_temperature: float
    duration_minutes: float
    applied_temp_min: float
    applied_temp_max: float
    applied_delay_minutes: int
    is_violated: bool
    violation_reason: str | None = None
    violation_id: int | None = None


class ColdChainViolationResponse(BaseModel):
    """Chi tiết một bản ghi vi phạm chuỗi lạnh đã lưu (bất biến, không bị tính lại khi đổi cấu hình)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_code: str
    product_types_json: str
    product_types: list[str] = []
    recorded_temperature: float
    duration_minutes: float
    applied_temp_min: float
    applied_temp_max: float
    applied_delay_minutes: int
    violation_reason: str
    timestamp: str
    location: str | None = None


