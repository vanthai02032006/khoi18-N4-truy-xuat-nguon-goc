"""Pydantic schemas - định nghĩa "hợp đồng" dữ liệu vào/ra của API.

Tách riêng schemas (Pydantic) khỏi models (SQLAlchemy) giúp:
- Không lộ cấu trúc bảng ra ngoài API.
- Validate dữ liệu đầu vào tự động và sinh tài liệu Swagger chuẩn.
"""

from datetime import date, datetime

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
    farm_id: int = Field(..., description="ID vùng trồng xuất xứ.", examples=[1])
    product_name: str = Field(..., description="Tên sản phẩm của lô.")
    quantity: float = Field(..., description="Số lượng / khối lượng (kg).")
    harvest_date: date = Field(..., description="Ngày thu hoạch.")


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


# --------------------------------------------------------------- Product Schemas ---
from app.models import ProductUnit


class ProductCreate(BaseModel):
    """Schema tạo mới sản phẩm danh mục (T-14 / SCRUM-30).

    Lưu ý: Bảng danh mục dùng chung toàn hệ thống, KHÔNG có trường organization_id.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Xoài Cát Chu",
                "unit": "kg",
                "description": "Xoài Cát Chu đặc sản Cao Lãnh đạt chuẩn VietGAP",
            }
        }
    )

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tên sản phẩm (duy nhất toàn hệ thống).",
        examples=["Xoài Cát Chu"],
    )
    unit: ProductUnit = Field(
        ...,
        description="Đơn vị tính chuẩn theo enum (kg, g, ton, liter, box, bottle, piece, bundle).",
        examples=["kg"],
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="Mô tả chi tiết sản phẩm / tiêu chuẩn canh tác.",
        examples=["Xoài Cát Chu chuẩn VietGAP"],
    )


class ProductUpdate(BaseModel):
    """Schema cập nhật sản phẩm."""

    name: str | None = Field(None, min_length=1, max_length=255)
    unit: ProductUnit | None = None
    description: str | None = None


class ProductResponse(BaseModel):
    """Schema thông tin sản phẩm trả về API."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "name": "Xoài Cát Chu",
                "unit": "kg",
                "description": "Xoài Cát Chu chuẩn VietGAP",
            }
        },
    )

    id: int = Field(..., description="Mã định danh sản phẩm.")
    name: str = Field(..., description="Tên sản phẩm chuẩn.")
    unit: ProductUnit = Field(..., description="Đơn vị tính.")
    description: str | None = Field(default=None, description="Mô tả sản phẩm.")


# ------------------------------------------------------------ BatchEvent Schemas ---
class BatchEventCreate(BaseModel):
    """Schema tạo sự kiện chuỗi lạnh / hành trình lô hàng (Append-Only)."""

    batch_id: int = Field(..., description="Mã lô nông sản.")
    event_type: str = Field(..., max_length=50, description="Loại sự kiện (HARVEST, COLD_STORAGE, HANDOVER, MERGE).")
    details: str | None = Field(None, max_length=1000, description="Chi tiết sự kiện.")
    temperature: float | None = Field(None, description="Nhiệt độ đo được (°C).")


class BatchEventResponse(BaseModel):
    """Schema trả về thông tin sự kiện chuỗi lạnh."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    event_type: str
    details: str | None
    temperature: float | None
    created_by: str
    prev_hash: str | None
    hash_code: str | None


# -------------------------------------------------------------- Handover Schemas ---
class HandoverCreate(BaseModel):
    """Schema tạo phiếu bàn giao nông sản cho tổ chức nhận (T-35 / SCRUM-51)."""

    batch_id: int = Field(..., gt=0, description="ID lô hàng cần bàn giao.")
    recipient_org_id: int = Field(..., gt=0, description="ID tổ chức nhận bàn giao (bắt buộc khác tổ chức gửi).")
    recipient_org_name: str = Field(..., min_length=1, max_length=255, description="Tên công khai của tổ chức nhận.")
    notes: str | None = Field(None, max_length=500, description="Ghi chú đợt bàn giao.")


class HandoverResponse(BaseModel):
    """Schema thông tin phiếu bàn giao."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    sender_org_id: int
    recipient_org_id: int
    recipient_org_name: str
    notes: str | None
    status: str
    is_overdue: bool
    created_at: datetime


# ------------------------------------------------------------ BatchMerge Schemas ---
class MergeSourceBatchItem(BaseModel):
    """Một lô tham gia quá trình gộp và sản lượng trích xuất."""

    batch_id: int = Field(..., gt=0, description="ID lô nguồn.")
    extracted_quantity: float = Field(..., gt=0, description="Khối lượng trích xuất từ lô này (kg).")


class BatchMergeRequest(BaseModel):
    """Yêu cầu gộp nhiều lô nông sản (T-45 / SCRUM-61)."""

    farm_id: int = Field(..., gt=0, description="ID vùng trồng đích lưu lô gộp mới.")
    source_batches: list[MergeSourceBatchItem] = Field(..., min_length=2, description="Danh sách các lô cần gộp (tối thiểu 2 lô).")
    notes: str | None = Field(None, max_length=500, description="Ghi chú đợt gộp lô.")


class BatchMergeResponse(BaseModel):
    """Kết quả gộp lô thành công."""

    message: str
    new_batch_id: int
    new_batch_name: str
    total_quantity: float
    source_batch_ids: list[int]
