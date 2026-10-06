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
    """Dữ liệu client gửi lên khi **sửa** vùng trồng (``PUT /farms/{farm_id}``)."""

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
    """Dữ liệu API trả về cho một vùng trồng (kèm ``id``)."""

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
    "current_owner": "Hợp tác xã Xoài Mỹ Xương",
}


class BatchCreate(BaseModel):
    """Dữ liệu client gửi lên khi tạo lô nông sản mới (``POST /batches``)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "farm_id": 1,
                "product_name": "Xoài cát Chu",
                "quantity": 120.5,
                "harvest_date": "2026-01-15",
                "current_owner": "Hợp tác xã Xoài Mỹ Xương",
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
    current_owner: str | None = Field(
        default=None,
        max_length=255,
        description="Bên nắm quyền sở hữu/quản lý hiện tại (nếu để trống, tự lấy theo chủ vùng trồng hoặc người tạo).",
        examples=["Hợp tác xã Xoài Mỹ Xương"],
    )


class BatchUpdate(BatchCreate):
    """Dữ liệu client gửi lên khi **sửa** lô nông sản (``PUT /batches/{batch_id}``)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "farm_id": 1,
                "product_name": "Xoài cát Chu",
                "quantity": 150,
                "harvest_date": "2026-01-16",
                "current_owner": "Hợp tác xã Xoài Mỹ Xương",
            }
        },
    )


class BatchResponse(BaseModel):
    """Dữ liệu API trả về cho một lô nông sản (kèm ``id`` và ``current_owner``)."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": _BATCH_EXAMPLE},
    )

    id: int = Field(..., description="Mã định danh lô nông sản.", examples=[1])
    farm_id: int = Field(..., description="ID vùng trồng xuất xứ.", examples=[1])
    product_name: str = Field(..., description="Tên sản phẩm của lô.")
    quantity: float = Field(..., description="Số lượng / khối lượng (kg).")
    harvest_date: date = Field(..., description="Ngày thu hoạch.")
    current_owner: str | None = Field(
        default=None,
        description="Bên/đơn vị đang nắm quyền quản lý/sở hữu lô hiện tại.",
        examples=["Hợp tác xã Xoài Mỹ Xương"],
    )


# -------------------------------------------------------------- Handover ---
_HANDOVER_EXAMPLE: dict = {
    "id": 1,
    "batch_id": 1,
    "sender_id": 2,
    "sender_name": "farmer (HTX Xoài Mỹ Xương)",
    "receiver_id": None,
    "receiver_name": "Công ty Thu mua Xuất khẩu Mekong",
    "status": "pending",
    "notes": "Bàn giao chuyển giao lô hàng tại kho sơ chế",
    "created_at": "2026-10-06T15:30:00",
    "updated_at": None,
    "current_batch_owner": "farmer (HTX Xoài Mỹ Xương)",
}


class HandoverCreate(BaseModel):
    """Dữ liệu gửi lên khi tạo yêu cầu bàn giao lô nông sản (``POST /handovers``)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "batch_id": 1,
                "receiver_name": "Công ty Thu mua Xuất khẩu Mekong",
                "receiver_id": None,
                "sender_name": None,
                "notes": "Bàn giao lô xoài sang kho đóng gói xuất khẩu.",
            }
        },
    )

    batch_id: int = Field(
        ...,
        gt=0,
        description="ID của lô nông sản cần bàn giao (phải tồn tại).",
        examples=[1],
    )
    receiver_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tên bên nhận / đơn vị tiếp nhận quyền quản lý.",
        examples=["Công ty Thu mua Xuất khẩu Mekong"],
    )
    receiver_id: int | None = Field(
        default=None,
        gt=0,
        description="ID tài khoản bên nhận (nếu có tài khoản hệ thống).",
        examples=[3],
    )
    sender_name: str | None = Field(
        default=None,
        max_length=255,
        description="Tên bên giao (mặc định lấy theo tài khoản đang đăng nhập hoặc chủ lô hiện tại).",
        examples=["HTX Xoài Mỹ Xương"],
    )
    notes: str | None = Field(
        default=None,
        max_length=500,
        description="Ghi chú bàn giao kèm theo.",
        examples=["Bàn giao tại kho lạnh phân phối."],
    )


class HandoverAction(BaseModel):
    """Dữ liệu gửi lên khi xử lý yêu cầu bàn giao (tiếp nhận hoặc từ chối)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "notes": "Đã kiểm tra chất lượng và tiếp nhận đủ số lượng.",
            }
        },
    )

    notes: str | None = Field(
        default=None,
        max_length=500,
        description="Ghi chú phản hồi khi tiếp nhận hoặc từ chối bàn giao.",
        examples=["Đã kiểm tra đạt chuẩn độ tươi."],
    )


class HandoverResponse(BaseModel):
    """Thông tin trả về của một yêu cầu bàn giao lô nông sản."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": _HANDOVER_EXAMPLE},
    )

    id: int = Field(..., description="Mã định danh yêu cầu bàn giao.", examples=[1])
    batch_id: int = Field(..., description="Mã lô nông sản bàn giao.", examples=[1])
    sender_id: int | None = Field(default=None, description="ID tài khoản bên giao.")
    sender_name: str = Field(..., description="Tên bên giao.")
    receiver_id: int | None = Field(default=None, description="ID tài khoản bên nhận.")
    receiver_name: str = Field(..., description="Tên bên nhận.")
    status: str = Field(
        ...,
        description="Trạng thái bàn giao: `pending` (chờ xử lý), `accepted` (đã nhận), `rejected` (từ chối).",
        examples=["pending"],
    )
    notes: str | None = Field(default=None, description="Ghi chú bàn giao.")
    created_at: datetime = Field(..., description="Thời điểm khởi tạo yêu cầu bàn giao.")
    updated_at: datetime | None = Field(default=None, description="Thời điểm tiếp nhận hoặc từ chối.")
    current_batch_owner: str | None = Field(
        default=None,
        description="Bên hiện đang nắm giữ quyền quản lý lô hàng (khi pending vẫn là bên giao).",
        examples=["HTX Xoài Mỹ Xương"],
    )


# ----------------------------------------------------------------- Event ---
_EVENT_EXAMPLE: dict = {
    "id": 1,
    "batch_id": 1,
    "event_type": "HANDOVER_PENDING",
    "actor_id": 2,
    "actor_name": "farmer",
    "description": "Khởi tạo yêu cầu bàn giao từ [farmer] sang [Công ty Mekong]",
    "metadata_info": '{"batch_id":1,"status":"pending"}',
    "created_at": "2026-10-06T15:30:00",
}


class EventResponse(BaseModel):
    """Dữ liệu trả về cho một sự kiện vòng đời lô nông sản (Task T-25)."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": _EVENT_EXAMPLE},
    )

    id: int = Field(..., description="Mã sự kiện.", examples=[1])
    batch_id: int = Field(..., description="ID lô nông sản liên quan.", examples=[1])
    event_type: str = Field(
        ...,
        description="Loại sự kiện (HANDOVER_PENDING, HANDOVER_ACCEPTED, HANDOVER_REJECTED, BATCH_CREATED,...).",
        examples=["HANDOVER_PENDING"],
    )
    actor_id: int | None = Field(default=None, description="ID tài khoản người thực hiện.")
    actor_name: str | None = Field(default=None, description="Tên người/đơn vị thực hiện sự kiện.")
    description: str | None = Field(default=None, description="Mô tả chi tiết sự kiện.")
    metadata_info: str | None = Field(default=None, description="Dữ liệu JSON/text bổ sung kèm sự kiện.")
    created_at: datetime = Field(..., description="Thời điểm diễn ra sự kiện.")


# ----------------------------------------------------------------- Chung ---
class DeleteResponse(BaseModel):
    """Kết quả một lần xoá thành công (``DELETE /farms/{id}``, ``DELETE /batches/{id}``)."""

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
            "`DELETE /farms/{farm_id}`. `null` khi xoá một lô nông sản."
        ),
        examples=[2],
    )


__all__ = [
    "BatchCreate",
    "BatchResponse",
    "BatchUpdate",
    "DeleteResponse",
    "EventResponse",
    "FarmCreate",
    "FarmResponse",
    "FarmUpdate",
    "HandoverAction",
    "HandoverCreate",
    "HandoverResponse",
    "HealthResponse",
    "LoginRequest",
    "LoginResponse",
    "UserResponse",
]
