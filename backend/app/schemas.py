"""Pydantic schemas - định nghĩa "hợp đồng" dữ liệu vào/ra của API.

Tách riêng schemas (Pydantic) khỏi models (SQLAlchemy) giúp:
- Không lộ cấu trúc bảng ra ngoài API.
- Validate dữ liệu đầu vào tự động và sinh tài liệu Swagger chuẩn.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

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
    "quantity": "120.5000",
    "harvest_date": "2026-01-15",
    "parent_id": None,
    "batch_code": "LOT-01-20260115-01",
    "is_restricted": False,
    "owner": "farmer",
}


class BatchCreate(BaseModel):
    """Dữ liệu client gửi lên khi tạo lô nông sản mới (``POST /batches``).

    ``farm_id`` phải trỏ tới một vùng trồng **đã tồn tại** — router sẽ trả
    ``404 Not Found`` nếu không tìm thấy.
    Khối lượng dùng kiểu số thập phân cố định (Decimal), tuyệt đối không dùng float (T-41 / SCRUM-57).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "farm_id": 1,
                "product_name": "Xoài cát Chu",
                "quantity": "120.5000",
                "harvest_date": "2026-01-15",
                "parent_id": None,
                "batch_code": "LOT-01-20260115-01",
                "is_restricted": False,
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
    quantity: Decimal = Field(
        ...,
        gt=Decimal("0"),
        description="Số lượng / khối lượng của lô (kg) dùng số thập phân cố định Decimal. Tuyệt đối không dùng float.",
        examples=[Decimal("120.5000")],
    )
    harvest_date: date = Field(
        ...,
        description="Ngày thu hoạch, định dạng yyyy-MM-dd.",
        examples=["2026-01-15"],
    )
    parent_id: int | None = Field(
        default=None,
        gt=0,
        description="ID của lô mẹ nếu là lô con (phả hệ T-39 / T-41). Để None nếu là lô gốc.",
    )
    batch_code: str | None = Field(
        default=None,
        max_length=100,
        description="Mã định danh lô theo chuẩn T-19. Nếu để trống hệ thống sẽ tự sinh.",
    )
    is_restricted: bool = Field(
        default=False,
        description="Cờ bảo mật hạn chế xem theo T-54 (True: chỉ admin và chủ lô được xem).",
    )


class BatchUpdate(BaseModel):
    """Dữ liệu client gửi lên khi **sửa** lô nông sản (``PUT /batches/{batch_id}``)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "farm_id": 1,
                "product_name": "Xoài cát Chu",
                "quantity": "150.0000",
                "harvest_date": "2026-01-16",
            }
        },
    )

    farm_id: int = Field(..., gt=0, description="ID vùng trồng xuất xứ.")
    product_name: str = Field(..., min_length=1, max_length=255, description="Tên sản phẩm của lô.")
    quantity: Decimal = Field(..., gt=Decimal("0"), description="Khối lượng của lô (kg) dạng Decimal.")
    harvest_date: date = Field(..., description="Ngày thu hoạch.")
    is_restricted: bool | None = Field(default=None, description="Cập nhật quyền hạn chế xem.")


class BatchResponse(BaseModel):
    """Dữ liệu API trả về cho một lô nông sản (kèm ``id``).

    Khối lượng trả về dạng số thập phân cố định Decimal.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": _BATCH_EXAMPLE},
    )

    id: int = Field(..., description="Mã định danh lô nông sản.", examples=[1])
    farm_id: int = Field(..., description="ID vùng trồng xuất xứ.", examples=[1])
    product_name: str = Field(..., description="Tên sản phẩm của lô.")
    quantity: Decimal = Field(..., description="Số lượng / khối lượng (kg) dạng Decimal.")
    harvest_date: date = Field(..., description="Ngày thu hoạch.")
    parent_id: int | None = Field(default=None, description="ID của lô mẹ (nếu có).")
    batch_code: str | None = Field(default=None, description="Mã định danh lô.")
    is_restricted: bool = Field(default=False, description="Cờ hạn chế xem.")
    owner: str | None = Field(default=None, description="Tài khoản chủ sở hữu.")


# ----------------------------------------------- Tách lô nông sản (T-40 / T-41 / SCRUM-57) ---
class BatchSplitRequest(BaseModel):
    """Dữ liệu client gửi lên khi tách lô nông sản (``POST /batches/{batch_id}/split``).

    Khối lượng mỗi lô con bắt buộc dùng Decimal (số thập phân cố định), tuyệt đối không dùng float.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "child_quantities": ["250.0000", "350.0000"],
                "note": "Tách đóng thùng xuất khẩu đợt 1",
            }
        },
    )

    child_quantities: list[Decimal] = Field(
        ...,
        min_length=1,
        description="Danh sách khối lượng (kg) của các lô con cần tách. Kiểu Decimal, không dùng float.",
        examples=[[Decimal("250.0000"), Decimal("350.0000")]],
    )
    note: str | None = Field(
        default=None,
        max_length=255,
        description="Ghi chú nghiệp vụ phân tách lô.",
    )


class BatchSplitResponse(BaseModel):
    """Kết quả trả về khi tách lô thành công."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "parent_batch_id": 1,
                "parent_batch_code": "LOT-01-20260115-01",
                "initial_quantity": "800.0000",
                "remaining_quantity": "200.0000",
                "total_split_quantity": "600.0000",
                "child_batches": [],
                "message": "Tách thành công 2 lô con. Lô mẹ còn lại 200.0000 kg.",
            }
        },
    )

    parent_batch_id: int = Field(..., description="ID của lô mẹ.")
    parent_batch_code: str | None = Field(default=None, description="Mã của lô mẹ.")
    initial_quantity: Decimal = Field(..., description="Khối lượng ban đầu của lô mẹ.")
    remaining_quantity: Decimal = Field(..., description="Khối lượng còn lại của lô mẹ sau khi trừ.")
    total_split_quantity: Decimal = Field(..., description="Tổng khối lượng đã phân tách cho các lô con.")
    child_batches: list[BatchResponse] = Field(..., description="Danh sách các lô con vừa được tạo.")
    message: str = Field(..., description="Thông báo kết quả giao dịch.")


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


# ----------------------------------------------- Sự kiện chuỗi cung ứng (T-28 / T-31) ---
class BatchEventCreate(BaseModel):
    """Yêu cầu tạo sự kiện mới cho chuỗi cung ứng của lô nông sản."""

    event_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Mã loại sự kiện (HARVEST, QUALITY_INSPECTION, PACKAGING, COLD_STORAGE_IN...).",
        examples=["HARVEST"],
    )
    organization: str = Field(
        default="Hợp tác xã Nông nghiệp Cao Lãnh",
        max_length=255,
        description="Tên tổ chức / đơn vị thực hiện sự kiện.",
        examples=["Hợp tác xã Nông nghiệp Cao Lãnh"],
    )
    data: str = Field(
        ...,
        max_length=1000,
        description="Dữ liệu chi tiết sự kiện (JSON hoặc văn bản ghi nhận).",
        examples=['{"temp_c": 4.0, "facility": "Kho lạnh Mỹ Xương #2"}'],
    )
    timestamp: str | None = Field(
        default=None,
        description="Thời điểm ghi nhận ISO-8601 (để trống sẽ lấy thời gian hiện tại).",
    )


class BatchEventResponse(BaseModel):
    """Thông tin sự kiện với mã băm mật mã SHA-256 chuỗi (T-28 / T-31)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    sequence: int
    event_type: str
    organization: str
    data: str
    timestamp: str
    prev_hash: str
    hash: str


class BatchEventVerifyResponse(BaseModel):
    """Kết quả kiểm tra tính toàn vẹn chuỗi sự kiện của lô nông sản (Hàm T-28)."""

    batch_id: int
    is_valid: bool
    status: str
    total_events: int
    verified_count: int
    tamper_type: str | None = None
    tampered_event_id: int | None = None
    tampered_sequence: int | None = None
    detail: str
    recorded_hash: str | None = None
    expected_hash: str | None = None
    recorded_prev_hash: str | None = None
    expected_prev_hash: str | None = None


# ----------------------------------------------- Gộp lô nông sản (T-44 / T-46 / SCRUM-60 / SCRUM-62) ---
class ParentBatchItem(BaseModel):
    """Thông tin một lô mẹ và khối lượng lấy ra để gộp."""

    parent_batch_id: int = Field(..., gt=0, description="ID lô nông sản mẹ cần trích xuất.")
    used_quantity: Decimal = Field(
        ...,
        gt=Decimal("0"),
        description="Khối lượng trích xuất từ lô mẹ (kg) dạng Decimal cố định. Không dùng float.",
        examples=[Decimal("250.0000")],
    )


class BatchMergeRequest(BaseModel):
    """Yêu cầu gộp nhiều lô nông sản thành một lô mới."""

    parents: list[ParentBatchItem] = Field(
        ...,
        min_length=2,
        description="Danh sách các lô mẹ cần gộp (tối thiểu 2 lô, không trùng ID).",
    )
    product_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tên sản phẩm của lô gộp mới hình thành.",
        examples=["Xoài Phân Loại Đóng Gói (Gộp Đợt 1)"],
    )
    farm_id: int | None = Field(
        default=None,
        gt=0,
        description="ID vùng trồng xuất xứ (để trống sẽ lấy theo lô mẹ đầu tiên).",
    )
    harvest_date: date = Field(
        ...,
        description="Ngày thu hoạch hoặc ngày tạo lô gộp (YYYY-MM-DD).",
        examples=["2026-10-05"],
    )
    batch_code: str | None = Field(
        default=None,
        max_length=100,
        description="Mã định danh lô gộp (nếu để trống hệ thống sẽ tự sinh).",
    )
    is_restricted: bool = Field(
        default=False,
        description="Cờ bảo mật hạn chế xem.",
    )
    note: str | None = Field(
        default=None,
        max_length=255,
        description="Ghi chú nghiệp vụ gộp lô.",
    )


class BatchRelationResponse(BaseModel):
    """Thông tin quan hệ phả hệ cha - con giữa các lô gộp."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_batch_id: int
    child_batch_id: int
    used_quantity: Decimal


class ParentRemainingResponse(BaseModel):
    """Khối lượng tồn còn lại của lô mẹ sau khi trích xuất gộp."""

    model_config = ConfigDict(from_attributes=True)

    parent_batch_id: int
    remaining_quantity: Decimal


class BatchMergeResponse(BaseModel):
    """Kết quả trả về khi thực hiện gộp lô thành công."""

    model_config = ConfigDict(from_attributes=True)

    new_batch: BatchResponse = Field(..., description="Lô mới vừa được tạo từ giao dịch gộp.")
    relations: list[BatchRelationResponse] = Field(..., description="Các liên kết phả hệ cha - con đã tạo.")
    parent_remainings: list[ParentRemainingResponse] = Field(..., description="Số dư còn lại của từng lô mẹ.")
    message: str = Field(..., description="Thông báo kết quả giao dịch.")


