"""Pydantic schemas - định nghĩa "hợp đồng" dữ liệu vào/ra của API.

Tách riêng schemas (Pydantic) khỏi models (SQLAlchemy) giúp:
- Không lộ cấu trúc bảng ra ngoài API.
- Validate dữ liệu đầu vào tự động và sinh tài liệu Swagger chuẩn.
"""

from datetime import date

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
    parent_id: int | None = Field(
        default=None,
        description="ID lô cha trong phả hệ (null nếu là lô gốc thu hoạch ban đầu).",
        examples=[None],
    )
    is_restricted: bool = Field(
        default=False,
        description="Chế độ bảo mật riêng tư (chỉ admin hoặc chủ sở hữu xem được - T-54).",
        examples=[False],
    )


class BatchUpdate(BatchCreate):
    """Dữ liệu client gửi lên khi **sửa** lô nông sản (``PUT /batches/{batch_id}``).

    Kế thừa ``BatchCreate`` (dùng lại validate: ``farm_id`` > 0, ``quantity`` > 0,
    ``harvest_date`` đúng định dạng ISO). Client gửi **đầy đủ các trường**; router
    trả ``404`` nếu lô hoặc ``farm_id`` mới không tồn tại.

    Lưu ý: đổi ``farm_id`` = chuyển lô sang vùng trồng khác (vẫn phải tồn tại).

    Ví dụ::

        {"farm_id": 1, "product_name": "Xoài cát Chu", "quantity": 150,
         "harvest_date": "2026-01-16", "parent_id": null, "is_restricted": false}
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "farm_id": 1,
                "product_name": "Xoài cát Chu",
                "quantity": 150,
                "harvest_date": "2026-01-16",
                "parent_id": None,
                "is_restricted": False,
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
    parent_id: int | None = Field(default=None, description="ID lô cha trong phả hệ.")
    batch_code: str | None = Field(default=None, description="Mã định danh lô sinh theo T-19.")
    is_restricted: bool = Field(default=False, description="Cờ phân quyền bảo mật T-54.")


# ------------------------------------------------------------- Tách Lô (S-17 / T-39) ---
class BatchSplitRequest(BaseModel):
    """Yêu cầu tách lô nông sản mẹ thành các lô con (S-17).

    Lưu ý kỹ thuật: Lô con kế thừa loại sản phẩm và nguồn gốc của lô mẹ,
    không cho phép nhập sai lệch.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "child_quantities": [300.0, 400.0],
                "note": "Tách 2 lô con phục vụ đóng gói siêu thị",
            }
        }
    )

    child_quantities: list[float] = Field(
        ...,
        min_length=1,
        description="Danh sách khối lượng (kg) các lô con cần tách (mỗi phần tử > 0).",
        examples=[[300.0, 400.0]],
    )
    note: str | None = Field(
        default=None,
        description="Ghi chú mục đích tách lô (tùy chọn).",
    )


class BatchSplitResponse(BaseModel):
    """Kết quả trả về sau khi tách lô nông sản (S-17)."""

    message: str = Field(..., description="Thông báo kết quả giao dịch.")
    parent_batch: BatchResponse = Field(..., description="Thông tin lô mẹ sau khi bị trừ khối lượng.")
    child_batches: list[BatchResponse] = Field(..., description="Danh sách các lô con mới được tạo thành công.")
    total_split_quantity: float = Field(..., description="Tổng khối lượng đã tách (kg).")
    remaining_quantity: float = Field(..., description="Khối lượng còn lại của lô mẹ (kg).")


# -------------------------------------------------------- Phả hệ & Truy vết (T-49) ---
class OriginFarmInfo(BaseModel):
    """Thông tin vùng trồng của lô gốc (T-49)."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Mã định danh vùng trồng gốc.")
    name: str = Field(..., description="Tên vùng trồng gốc.")
    location: str = Field(..., description="Địa điểm / vị trí địa lý vùng trồng.")
    area: float = Field(..., description="Diện tích canh tác (ha).")
    owner: str = Field(..., description="Chủ sở hữu / Hộ nông dân canh tác.")


class RootBatchInfo(BaseModel):
    """Thông tin lô gốc trong cây phả hệ (T-49)."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Mã định danh lô gốc.")
    farm_id: int = Field(..., description="Mã vùng trồng của lô gốc.")
    product_name: str = Field(..., description="Tên nông sản / cây trồng của lô gốc.")
    quantity: float = Field(..., description="Sản lượng thu hoạch ban đầu (kg).")
    harvest_date: date = Field(..., description="Ngày thu hoạch ban đầu.")


class BatchLineageTier(BaseModel):
    """Một tầng trong danh sách phả hệ truy vết nguồn gốc (T-49)."""

    model_config = ConfigDict(from_attributes=True)

    level: int = Field(..., description="Thứ tự tầng (1 = Tầng gốc).")
    tier_name: str = Field(..., description="Tên hiển thị tầng (ví dụ: 'Tầng 1 (Lô gốc)').")
    batch_id: int = Field(..., description="Mã lô nông sản.")
    product_name: str = Field(..., description="Tên cây trồng / sản phẩm nông sản.")
    quantity: float = Field(..., description="Khối lượng sản phẩm tại tầng này (kg).")
    harvest_date: date = Field(..., description="Ngày ghi nhận / thu hoạch.")
    farm_id: int = Field(..., description="Mã vùng trồng liên kết.")
    farm_name: str | None = Field(default=None, description="Tên vùng trồng liên kết.")
    parent_id: int | None = Field(default=None, description="Mã lô cha.")
    is_root: bool = Field(default=False, description="Có phải là lô gốc không.")
    is_current: bool = Field(default=False, description="Có phải là lô đang được tra cứu không.")


class BatchTraceResponse(BaseModel):
    """Kết quả truy vết phả hệ nguồn gốc lô nông sản (T-49 kèm T-54).

    Bao gồm thông tin lô gốc, vùng trồng của lô gốc và danh sách theo tầng.
    Kết quả được cache 60 giây theo mã lô.
    """

    model_config = ConfigDict(from_attributes=True)

    batch_id: int = Field(..., description="Mã lô đang truy vết.")
    product_name: str = Field(..., description="Tên sản phẩm lô đang truy vết.")
    quantity: float = Field(..., description="Sản lượng của lô (kg).")
    harvest_date: date = Field(..., description="Ngày thu hoạch của lô.")
    farm_id: int = Field(..., description="Mã vùng trồng của lô.")
    farm_name: str | None = Field(default=None, description="Tên vùng trồng của lô.")
    root_batch: RootBatchInfo = Field(..., description="Thông tin chi tiết lô gốc.")
    origin_farm: OriginFarmInfo = Field(..., description="Thông tin vùng trồng của lô gốc.")
    lineage: list[BatchLineageTier] = Field(..., description="Danh sách phả hệ theo từng tầng.")
    cached: bool = Field(default=False, description="Được phục vụ từ bộ nhớ đệm (Cache 60s) hay không.")
    cache_ttl_seconds: int = Field(default=60, description="Thời gian tồn tại của cache (giây).")
    cache_remaining_seconds: int = Field(default=60, description="Thời gian cache còn lại (giây).")


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


# ----------------------------------------------- Sự kiện chuỗi cung ứng (T-29) ---
class BatchEventCreate(BaseModel):
    """Yêu cầu tạo sự kiện mới cho lô nông sản."""

    event_type: str = Field(
        ...,
        description="Mã loại sự kiện (ví dụ: HARVEST, PACKAGING, COLD_STORAGE_IN...)",
        examples=["HARVEST"],
    )
    data: str = Field(
        ...,
        description="Dữ liệu chi tiết sự kiện (JSON hoặc văn bản)",
        examples=['{"temp_c": 4.0, "facility": "Kho Mỹ Xương"}'],
    )
    timestamp: str | None = Field(
        default=None,
        description="Thời điểm ghi nhận ISO-8601 (để trống sẽ lấy thời gian hiện tại)",
    )


class BatchEventResponse(BaseModel):
    """Thông tin sự kiện với mã băm mật mã SHA-256 chuỗi (T-29)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    sequence: int
    event_type: str
    data: str
    timestamp: str
    prev_hash: str
    hash: str


class BatchEventVerifyResponse(BaseModel):
    """Kết quả kiểm tra tính toàn vẹn chuỗi sự kiện của lô nông sản (T-29)."""

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

