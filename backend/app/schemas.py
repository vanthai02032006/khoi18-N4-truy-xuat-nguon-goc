"""Pydantic schemas - định nghĩa "hợp đồng" dữ liệu vào/ra của API.

Tách riêng schemas (Pydantic) khỏi models (SQLAlchemy) giúp:
- Không lộ cấu trúc bảng ra ngoài API.
- Validate dữ liệu đầu vào tự động và sinh tài liệu Swagger chuẩn.
"""

from datetime import date, datetime
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
    current_owner: str | None = Field(
        default=None,
        description=(
            "Tổ chức/đơn vị **đang giữ quyền quản lý lô**. Đổi sang bên nhận khi "
            "bàn giao được xác nhận; giữ nguyên khi bàn giao bị từ chối."
        ),
        examples=["Hợp tác xã Xoài Mỹ Xương"],
    )


# -------------------------------------------------------------- Handover ---
_HANDOVER_EXAMPLE: dict = {
    "id": 1,
    "batch_id": 1,
    "sender_id": 1,
    "sender_name": "Hợp tác xã Xoài Mỹ Xương",
    "receiver_id": 2,
    "receiver_name": "Công ty Thu mua Xuất khẩu Mekong",
    "status": "pending",
    "notes": "Bàn giao lô xoài sang kho đóng gói xuất khẩu.",
    "created_at": "2026-10-07T00:00:00",
    "updated_at": None,
    "current_batch_owner": "Hợp tác xã Xoài Mỹ Xương",
}


class HandoverCreate(BaseModel):
    """Dữ liệu client gửi lên khi **tạo** phiếu bàn giao (``POST /handovers``).

    Phiếu mới luôn ở trạng thái ``pending``; lô **vẫn thuộc bên giao** cho tới khi
    bên nhận xác nhận.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "batch_id": 1,
                "receiver_id": 2,
                "receiver_name": "Công ty Thu mua Xuất khẩu Mekong",
                "notes": "Bàn giao lô xoài sang kho đóng gói xuất khẩu.",
            }
        },
    )

    batch_id: int = Field(
        ...,
        gt=0,
        description="ID lô nông sản cần bàn giao (phải tồn tại).",
        examples=[1],
    )
    receiver_id: int | None = Field(
        default=None,
        gt=0,
        description=(
            "ID tài khoản **bên nhận** - căn cứ để kiểm tra quyền gọi xác nhận/từ "
            "chối. Nếu bỏ trống, phiếu vẫn tạo được nhưng **không ai** gọi được "
            "xác nhận/từ chối (bên nhận chưa có tài khoản hệ thống)."
        ),
        examples=[2],
    )
    receiver_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tên hiển thị của bên nhận; khi xác nhận sẽ thành chủ sở hữu lô.",
        examples=["Công ty Thu mua Xuất khẩu Mekong"],
    )
    notes: str | None = Field(
        default=None,
        max_length=500,
        description="Ghi chú kèm theo khi tạo phiếu (không bắt buộc).",
        examples=["Bàn giao lô xoài sang kho đóng gói xuất khẩu."],
    )


class HandoverAccept(BaseModel):
    """Dữ liệu client gửi lên khi **xác nhận** bàn giao (``POST /handovers/{id}/accept``)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"notes": "Đã kiểm tra chất lượng và nhận đủ số lượng."}
        },
    )

    notes: str | None = Field(
        default=None,
        max_length=500,
        description="Ghi chú khi tiếp nhận (không bắt buộc).",
        examples=["Đã kiểm tra chất lượng và nhận đủ số lượng."],
    )


class HandoverReject(BaseModel):
    """Dữ liệu client gửi lên khi **từ chối** bàn giao (``POST /handovers/{id}/reject``).

    **Lý do từ chối là bắt buộc**: phiếu từ chối phải lưu được vết giải thích vì
    sao không tiếp nhận, nếu không hồ sơ truy xuất nguồn gốc sẽ thiếu căn cứ.
    Chuỗi rỗng hoặc chỉ gồm khoảng trắng bị coi là thiếu lý do → ``422``.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"reason": "Lô bị dập trong quá trình vận chuyển, không đạt chuẩn."}
        },
    )

    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Lý do từ chối (bắt buộc, không được rỗng).",
        examples=["Lô bị dập trong quá trình vận chuyển, không đạt chuẩn."],
    )

    @field_validator("reason")
    @classmethod
    def _normalize_reason(cls, value: str) -> str:
        """Bỏ khoảng trắng thừa và chặn lý do rỗng/toàn khoảng trắng."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Lý do từ chối không được để trống.")
        return cleaned


class HandoverResponse(BaseModel):
    """Thông tin một phiếu bàn giao.

    ``current_batch_owner`` phản ánh **trạng thái hiện tại** của lô sau thao tác:
    khi phiếu còn ``pending`` hoặc đã ``rejected`` thì vẫn là bên giao; khi đã
    ``accepted`` thì là bên nhận.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": _HANDOVER_EXAMPLE},
    )

    id: int = Field(..., description="Mã phiếu bàn giao.", examples=[1])
    batch_id: int = Field(..., description="ID lô nông sản được bàn giao.", examples=[1])
    sender_id: int | None = Field(default=None, description="ID tài khoản bên giao.")
    sender_name: str = Field(..., description="Tên bên giao.")
    receiver_id: int | None = Field(
        default=None,
        description="ID tài khoản bên nhận (người duy nhất được gọi xác nhận/từ chối).",
    )
    receiver_name: str = Field(..., description="Tên bên nhận.")
    status: str = Field(
        ...,
        description="Trạng thái: `pending` (chờ xử lý), `accepted` (đã nhận), `rejected` (từ chối).",
        examples=["pending"],
    )
    notes: str | None = Field(
        default=None,
        description="Ghi chú của phiếu; khi từ chối chứa **lý do từ chối**.",
    )
    created_at: datetime = Field(..., description="Thời điểm tạo phiếu.")
    updated_at: datetime | None = Field(
        default=None,
        description="Thời điểm xác nhận hoặc từ chối (null nếu còn chờ xử lý).",
    )
    current_batch_owner: str | None = Field(
        default=None,
        description="Tổ chức/đơn vị hiện đang giữ quyền quản lý lô.",
    )


class HandoverActionResponse(HandoverResponse):
    """Kết quả của thao tác xác nhận/từ chối, kèm **danh sách sự kiện đã ghi**.

    ``recorded_events`` giúp kiểm chứng ngay trên API rằng nghiệp vụ đã ghi đủ
    nhật ký trong cùng transaction: xác nhận → 2 sự kiện
    (`HANDOVER_ACCEPTED`, `OWNER_CHANGED`); từ chối → 1 sự kiện
    (`HANDOVER_REJECTED`).
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                **_HANDOVER_EXAMPLE,
                "status": "accepted",
                "current_batch_owner": "Công ty Thu mua Xuất khẩu Mekong",
                "recorded_events": ["HANDOVER_ACCEPTED", "OWNER_CHANGED"],
            }
        },
    )

    recorded_events: list[str] = Field(
        default_factory=list,
        description="Các loại sự kiện đã ghi vào nhật ký lô trong cùng transaction.",
        examples=[["HANDOVER_ACCEPTED", "OWNER_CHANGED"]],
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
