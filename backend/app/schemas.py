"""Pydantic schemas - định nghĩa "hợp đồng" dữ liệu vào/ra của API.

Tách riêng schemas (Pydantic) khỏi models (SQLAlchemy) giúp:
- Không lộ cấu trúc bảng ra ngoài API.
- Validate dữ liệu đầu vào tự động và sinh tài liệu Swagger chuẩn.
"""

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import ProductUnit


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


# --------------------------------------------------------------- Product ---
# Danh mục sản phẩm dùng chung cho mọi tổ chức (không có organization_id):
# chỉ `admin` được thêm/sửa, các vai trò khác chỉ đọc để chọn sản phẩm.
_PRODUCT_EXAMPLE: dict = {
    "id": 1,
    "name": "Xoài Cát Chu",
    "unit": "kg",
    "description": "Xoài cát chu loại 1 thu hoạch tại Cao Lãnh, Đồng Tháp.",
}


class ProductCreate(BaseModel):
    """Dữ liệu client gửi lên khi **thêm** sản phẩm (``POST /products``) - chỉ admin.

    Vì danh mục dùng chung cho mọi tổ chức nên ``name`` phải **duy nhất** trên
    toàn hệ thống; gửi trùng tên, backend trả ``409 Conflict``.
    """

    model_config = ConfigDict(
        # Trả `unit` về chuỗi ("kg", "ton"...) thay vì Enum để `model_dump()`
        # map thẳng vào ORM model `Product` giống các schema Farm/Batch.
        use_enum_values=True,
        json_schema_extra={
            "example": {
                "name": "Xoài Cát Chu",
                "unit": "kg",
                "description": "Xoài cát chu loại 1 thu hoạch tại Cao Lãnh, Đồng Tháp.",
            }
        },
    )

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tên sản phẩm - duy nhất trên toàn hệ thống.",
        examples=["Xoài Cát Chu"],
    )
    unit: ProductUnit = Field(
        ...,
        description=(
            "Đơn vị tính chuẩn: `kg`, `g`, `ton`, `liter`, `box`, `bottle`, "
            "`piece`, `bundle`."
        ),
        examples=["kg"],
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="Mô tả ngắn về sản phẩm (không bắt buộc).",
        examples=["Xoài cát chu loại 1 thu hoạch tại Cao Lãnh, Đồng Tháp."],
    )

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        """Chuẩn hoá tên sản phẩm trước khi kiểm tra trùng lặp/ghi database.

        Bỏ khoảng trắng thừa ở hai đầu và chặn tên chỉ gồm khoảng trắng - nếu
        không, danh mục dùng chung sẽ chứa những tên vô nghĩa gây khó tra cứu.
        """
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Tên sản phẩm không được để trống.")
        return cleaned

    @field_validator("description")
    @classmethod
    def _normalize_description(cls, value: str | None) -> str | None:
        """Chuẩn hoá mô tả: chuỗi rỗng/toàn khoảng trắng được lưu thành ``None``."""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ProductUpdate(ProductCreate):
    """Dữ liệu client gửi lên khi **sửa** sản phẩm (``PUT /products/{product_id}``) - chỉ admin.

    Kế thừa ``ProductCreate`` để dùng lại đúng bộ quy tắc validate. ``PUT`` là
    cập nhật *thay thế* nên client gửi **đầy đủ** các trường như khi thêm mới;
    backend ghi đè giá trị cũ. Đổi tên sang tên đã có ở sản phẩm khác → ``409``.
    """

    model_config = ConfigDict(
        use_enum_values=True,
        json_schema_extra={
            "example": {
                "name": "Xoài Cát Chu",
                "unit": "kg",
                "description": "Cập nhật: xoài cát chu loại 1, đóng thùng 10kg.",
            }
        },
    )


class ProductResponse(BaseModel):
    """Dữ liệu API trả về cho một sản phẩm trong danh mục (kèm ``id``).

    ``from_attributes=True`` cho phép khởi tạo trực tiếp từ ORM object ``Product``.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": _PRODUCT_EXAMPLE},
    )

    id: int = Field(..., description="Mã định danh sản phẩm.", examples=[1])
    name: str = Field(..., description="Tên sản phẩm (duy nhất toàn hệ thống).")
    unit: ProductUnit = Field(
        ...,
        description="Đơn vị tính chuẩn của sản phẩm.",
        examples=["kg"],
    )
    description: str | None = Field(
        default=None,
        description="Mô tả ngắn về sản phẩm (có thể là `null`).",
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
    timestamp: str
    hash: str
    previous_hash: str


class BatchTimelineResponse(BaseModel):
    """Dòng thời gian sự kiện của một lô hàng kèm trạng thái tính toàn vẹn."""

    batch_id: int
    is_valid: bool = Field(..., description="True nếu toàn bộ chuỗi mã băm toàn vẹn, False nếu bị can thiệp sửa lén.")
    tampered_index: Optional[int] = Field(None, description="Vị trí sự kiện đầu tiên bị sai lệch nếu có.")
    events: list[BatchEventResponse]
