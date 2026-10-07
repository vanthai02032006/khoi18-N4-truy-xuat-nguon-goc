"""Pydantic schemas - định nghĩa "hợp đồng" dữ liệu vào/ra của API.

Tách riêng schemas (Pydantic) khỏi models (SQLAlchemy) giúp:
- Không lộ cấu trúc bảng ra ngoài API.
- Validate dữ liệu đầu vào tự động và sinh tài liệu Swagger chuẩn.
"""

from datetime import date

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

    @field_validator("area")
    @classmethod
    def validate_area_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Diện tích thửa đất phải lớn hơn 0 ha (không được âm hoặc bằng 0).")
        return v


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
    "code": "7X9KM2RP",
    "farm_id": 1,
    "product_name": "Xoài cát Chu",
    "quantity": 120.5,
    "harvest_date": "2026-01-15",
}


class BatchCreate(BaseModel):
    """Dữ liệu client gửi lên khi tạo lô nông sản mới (``POST /batches``).

    ``farm_id`` phải trỏ tới một vùng trồng **đã tồn tại** — router sẽ trả
    ``404 Not Found`` nếu không tìm thấy.
    Mã lô 8 ký tự (``code``) được hệ thống tự động sinh ngẫu nhiên an toàn nếu client để trống.
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

    code: str | None = Field(
        None,
        min_length=8,
        max_length=8,
        description="Mã định danh lô hàng 8 ký tự (tự động sinh an toàn nếu để trống - T-18 / SCRUM-34).",
        examples=["7X9KM2RP"],
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
    code: str = Field(..., description="Mã truy xuất nguồn gốc 8 ký tự duy nhất (T-18 / SCRUM-34).", examples=["7X9KM2RP"])
    farm_id: int = Field(..., description="ID vùng trồng xuất xứ.", examples=[1])
    product_name: str = Field(..., description="Tên sản phẩm của lô.")
    quantity: float = Field(..., description="Số lượng / khối lượng (kg).")
    harvest_date: date = Field(..., description="Ngày thu hoạch.")
    status: str = Field(default="ACTIVE", description="Trạng thái hiện tại của lô (ACTIVE, PENDING_HANDOVER, HANDED_OVER, SPLIT, MERGED).", examples=["ACTIVE"])



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
    tampered_index: int | None = Field(None, description="Vị trí sự kiện đầu tiên bị sai lệch nếu có.")
    events: list[BatchEventResponse]


# ----------------------------------------------------------- Inspection Audit ---
class InspectionResultResponse(BaseModel):
    """Kết quả thẩm định tính toàn vẹn của lô hàng dành cho cán bộ kiểm tra (T-28 / SCRUM-44)."""

    batch_id: int = Field(..., description="ID định danh số của lô nông sản.")
    batch_code: str = Field(..., description="Mã truy xuất 8 ký tự của lô nông sản.")
    product_name: str = Field(..., description="Tên sản phẩm của lô nông sản.")
    is_valid: bool = Field(..., description="True nếu toàn bộ chuỗi sự kiện nguyên vẹn, False nếu bị can thiệp.")
    error_type: str | None = Field(None, description="Loại lỗi (TAMPERED_PAYLOAD, BROKEN_CHAIN, NO_EVENTS).")
    tampered_index: int | None = Field(None, description="Vị trí (chỉ số 0-based) sự kiện bị sai lệch nếu phát hiện.")
    tampered_event_id: int | None = Field(None, description="ID sự kiện bị can thiệp nếu phát hiện.")
    details: str = Field(..., description="Mô tả kết quả hoặc chi tiết lỗi vi phạm.")
    inspector: str = Field(..., description="Tên tài khoản cán bộ kiểm tra.")
    timestamp: str = Field(..., description="Thời điểm thực hiện kiểm định (ISO 8601).")
    total_events: int = Field(..., description="Tổng số sự kiện trong chuỗi.")
    events: list[BatchEventResponse] = Field(default_factory=list, description="Danh sách các sự kiện trong chuỗi.")


class InspectionLogResponse(BaseModel):
    """Bản ghi nhật ký kiểm định lưu vết để đối chiếu về sau (T-28 / SCRUM-44)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    batch_code: str
    inspector: str
    timestamp: str
    is_valid: bool
    error_type: str | None = None
    tampered_index: int | None = None
    details: str


# ----------------------------------------------------------- Batch Lineage (T-37 / SCRUM-53) ---
class BatchLineageCreate(BaseModel):
    """Dữ liệu khai báo quan hệ phân tách (SPLIT) hoặc sáp nhập (MERGE) giữa các lô hàng."""

    parent_batch_id: int = Field(..., gt=0, description="ID lô cha xuất xứ.")
    child_batch_id: int = Field(..., gt=0, description="ID lô con tiếp nhận.")
    transferred_quantity: float = Field(..., gt=0, description="Khối lượng chuyển từ cha sang con (kg).")
    relation_type: str = Field(..., pattern="^(SPLIT|MERGE)$", description="Loại quan hệ: 'SPLIT' (tách) hoặc 'MERGE' (gộp).", examples=["SPLIT", "MERGE"])


class BatchLineageResponse(BaseModel):
    """Thông tin một bản ghi quan hệ phả hệ lô hàng."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_batch_id: int
    child_batch_id: int
    transferred_quantity: float
    relation_type: str
    created_at: str


class BatchGenealogyNode(BaseModel):
    """Nút thông tin lô hàng trong cây phả hệ."""

    batch_id: int
    batch_code: str
    product_name: str
    transferred_quantity: float
    relation_type: str
    created_at: str


class BatchGenealogyResponse(BaseModel):
    """Toàn bộ phả hệ truy ngược (Parents) và truy xuôi (Children) của lô hàng."""

    target_batch_id: int
    target_batch_code: str
    target_product_name: str
    parents: list[BatchGenealogyNode] = Field(default_factory=list, description="Danh sách các lô cha đóng góp tạo nên lô này (Truy ngược - Backward trace).")
    children: list[BatchGenealogyNode] = Field(default_factory=list, description="Danh sách các lô con được sinh ra từ lô này (Truy xuôi - Forward trace).")


class BatchAncestorsBFSResponse(BaseModel):
    """Kết quả truy ngược phả hệ theo tầng bằng BFS (T-48 / SCRUM-64)."""

    target_batch: str
    ancestors_by_level: list[list[str]] = Field(..., description="Danh sách tổ tiên phân theo từng tầng (tầng 1: cha trực tiếp, tầng 2: ông bà...).")
    root_batches: list[str] = Field(..., description="Danh sách các lô gốc xuất xứ (không có cha).")
    all_ancestors: list[str] = Field(default_factory=list, description="Danh sách phẳng toàn bộ tổ tiên theo thứ tự BFS.")


# --------------------------------------------------- Thao tác Lô & 3 Tab (T-58 / SCRUM-74) ---
class BatchHandoverRequest(BaseModel):
    """Yêu cầu bàn giao lô nông sản."""

    target_organization: str = Field(..., description="Tổ chức / đơn vị tiếp nhận bàn giao.", examples=["Công Ty Chế Biến Xuất Khẩu Đồng Tháp"])
    note: str | None = Field(None, description="Ghi chú thêm về lô bàn giao.", examples=["Bàn giao đợt 1 xe lạnh 12 tấn"])


class BatchSplitChildItem(BaseModel):
    """Thông tin một lô con trong thao tác tách lô."""

    product_name: str = Field(..., description="Tên nông sản của lô con.")
    quantity: float = Field(..., gt=0, description="Khối lượng chuyển sang lô con (kg).")


class BatchSplitRequest(BaseModel):
    """Yêu cầu phân tách lô nông sản thành nhiều lô con (SPLIT)."""

    children: list[BatchSplitChildItem] = Field(..., min_length=2, description="Danh sách các lô con tách ra (tối thiểu 2 lô con).")


class BatchMergeRequest(BaseModel):
    """Yêu cầu sáp nhập nhiều lô nông sản thành một lô mới (MERGE)."""

    parent_batch_ids: list[int] = Field(..., min_length=2, description="Danh sách ID các lô cha tham gia gộp.")
    product_name: str = Field(..., description="Tên sản phẩm của lô gộp mới.")
    transferred_quantities: list[float] | None = Field(None, description="Khối lượng chuyển từ từng lô cha tương ứng (nếu để trống sẽ lấy toàn bộ).")


class BatchDetailCombinedResponse(BaseModel):
    """Dữ liệu tổng hợp phục vụ trang chi tiết 3 Tab (T-58 / SCRUM-74)."""

    batch: BatchResponse = Field(..., description="Dữ liệu lô hàng (Tab 1: Tổng quan).")
    farm_name: str = Field(..., description="Tên vùng trồng / thửa đất.")
    farm_location: str = Field(..., description="Vị trí thửa đất.")
    farm_owner: str = Field(..., description="Chủ sở hữu thửa đất.")
    farm_area: float = Field(..., description="Diện tích thửa đất (ha).")
    timeline: BatchTimelineResponse = Field(..., description="Dòng thời gian sự kiện (Tab 2: Dòng thời gian - T-32).")
    genealogy: BatchGenealogyResponse = Field(..., description="Phả hệ cha & con (Tab 3: Nguồn gốc - T-50).")
    ancestors: BatchAncestorsBFSResponse = Field(..., description="Cây tổ tiên BFS theo tầng và danh sách lô gốc.")


# ----------------------------------------------------------- Lệnh thu hồi (Recall) ---
class RecallItem(BaseModel):
    """Một lô hậu duệ cần thu hồi theo chuỗi phân tách / sáp nhập."""

    batch_code: str
    batch_id: int | None = None
    product_name: str | None = None
    quantity: float | None = None
    organization: str | None = None
    level: int
    relation_type: str
    is_merged_multiple_sources: bool = False
    other_sources: list[str] = Field(default_factory=list)


class RecallOrderResponse(BaseModel):
    """Kết quả phát lệnh thu hồi sản phẩm từ lô gốc."""

    root_batch_code: str
    total_affected_batches: int
    total_affected_organizations: int
    affected_organizations: list[str]
    items: list[RecallItem]


# ----------------------------------------------------------- Bản đồ hành trình công khai (S-06) ---
class MapWaypoint(BaseModel):
    """Điểm dừng trên hành trình công khai (Cấp xã/huyện, bảo vệ quyền riêng tư thửa đất)."""

    order: int = Field(..., description="Thứ tự điểm dừng trên hành trình (1, 2, 3...).")
    name: str = Field(..., description="Tên điểm dừng / cơ sở / chặng trung chuyển.")
    location_level: str = Field(..., description="Cấp hành chính hiển thị (Xã / Phường hoặc Quận / Huyện).")
    organization: str = Field(..., description="Tên tổ chức / đơn vị thực hiện chặng này.")
    action: str = Field(..., description="Hành động tại điểm dừng (Xuất phát, Sơ chế, Vận chuyển, Phân phối...).")
    latitude: float = Field(..., description="Toạ độ vĩ độ xấp xỉ cấp xã/huyện.")
    longitude: float = Field(..., description="Toạ độ kinh độ xấp xỉ cấp xã/huyện.")
    timestamp: str | None = Field(None, description="Thời điểm ghi nhận.")


class PublicTrackingMapResponse(BaseModel):
    """Dữ liệu hiển thị bản đồ hành trình công khai cho người tiêu dùng (S-06)."""

    batch_code: str
    product_name: str
    origin_point: MapWaypoint = Field(..., description="Điểm vùng trồng xuất xứ (toạ độ đại diện cấp xã/huyện).")
    waypoints: list[MapWaypoint] = Field(default_factory=list, description="Các điểm dừng chính theo thứ tự thời gian.")
    privacy_note: str = Field(
        default="Toạ độ hiển thị ở cấp xã/huyện nhằm bảo vệ bí mật nông hộ và quyền riêng tư thửa đất.",
        description="Ghi chú về tính ẩn danh và bảo vệ vị trí chính xác của thửa đất.",
    )




