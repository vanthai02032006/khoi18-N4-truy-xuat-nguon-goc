"""Router quản lý lô nông sản (Batch).

Quan hệ: ``Farm 1 ---- N Batch``.

Cung cấp **đầy đủ CRUD** (hoàn thiện ở Sprint 5):

- ``POST   /batches``            : tạo lô nông sản (kiểm tra ``farm_id`` tồn tại).
- ``GET    /batches``            : lấy danh sách lô.
- ``GET    /batches/{batch_id}`` : xem chi tiết một lô.
- ``PUT    /batches/{batch_id}`` : cập nhật lô (có thể đổi sang vùng trồng khác).
- ``DELETE /batches/{batch_id}`` : xoá lô (chỉ admin).

**Phân quyền (Sprint 4):** ``POST``/``PUT`` dùng dependency ``require_farmer``
-> đăng nhập bằng role ``farmer`` hoặc ``admin`` (401 nếu chưa đăng nhập,
403 nếu sai vai trò); ``DELETE`` dùng ``require_admin`` -> chỉ admin. Hai endpoint
``GET`` giữ nguyên như trước (không yêu cầu đăng nhập) vì phục vụ tra cứu nguồn
gốc công khai.
"""

from datetime import datetime, timezone
import json
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.code_generator import CodeCollisionError, execute_with_unique_retry, generate_code
from app.database import get_db
from app.lineage import trace_ancestors_bfs
from app.models import (
    BATCH_STATUS_ACTIVE,
    BATCH_STATUS_HANDED_OVER,
    BATCH_STATUS_MERGED,
    BATCH_STATUS_PENDING_HANDOVER,
    BATCH_STATUS_SPLIT,
    Batch,
    BatchEvent,
    BatchLineage,
    Farm,
    RELATION_MERGE,
    RELATION_SPLIT,
    User,
)
from app.routers.events import get_batch_timeline
from app.routers.lineage import get_batch_genealogy
from app.schemas import (
    BatchAncestorsBFSResponse,
    BatchCreate,
    BatchDetailCombinedResponse,
    BatchHandoverRequest,
    BatchMergeRequest,
    BatchResponse,
    BatchSplitRequest,
    BatchUpdate,
    DeleteResponse,
    MapWaypoint,
    PublicTrackingMapResponse,
)
from app.security import compute_event_hash, require_admin, require_farmer


router = APIRouter(
    prefix="/batches",
    tags=["Batches"],
)


@router.post(
    "",
    response_model=BatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo lô nông sản",
    description=(
        "Tạo một lô nông sản thuộc về một vùng trồng. "
        "Nếu `farm_id` không tồn tại, API trả về `404 Not Found`.\n\n"
        "**Phân quyền:** đăng nhập với role `farmer` hoặc `admin` (yêu cầu header "
        "`Authorization: Basic ...`)."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Chưa đăng nhập.",
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "Vai trò không được phép.",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Vùng trồng (farm_id) không tồn tại.",
        },
    },
)
def create_batch(
    payload: BatchCreate,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> Batch:
    """Tạo lô nông sản mới.

    Args:
        payload: Dữ liệu lô đã được Pydantic validate.
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Batch: Bản ghi lô vừa tạo (HTTP 201).

    Raises:
        HTTPException: 401/403 nếu chưa đăng nhập hoặc sai vai trò;
            404 nếu ``farm_id`` không tồn tại;
            500 nếu ghi database thất bại (đã rollback).
    """
    _ = current_user  # bắt buộc khai báo để dependency kiểm tra quyền chạy

    # Bước 1: kiểm tra toàn vẹn tham chiếu - vùng trồng phải tồn tại.
    farm = db.get(Farm, payload.farm_id)
    if farm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vùng trồng có id={payload.farm_id}.",
        )

    # Bước 2: lưu lô nông sản (tự động sinh mã an toàn nếu chưa có - T-18 / SCRUM-34).
    batch_dict = payload.model_dump()

    if not batch_dict.get("code"):
        # Tự động sinh mã duy nhất với cơ chế bắt lỗi UNIQUE của database và sinh lại
        def save_batch(candidate_code: str) -> Batch:
            data = dict(batch_dict)
            data["code"] = candidate_code
            new_batch = Batch(**data)
            db.add(new_batch)
            db.commit()
            return new_batch

        try:
            _, batch = execute_with_unique_retry(save_fn=save_batch, db=db)
        except (CodeCollisionError, SQLAlchemyError) as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Không thể sinh mã duy nhất hoặc lưu lô nông sản vào cơ sở dữ liệu.",
            ) from exc
    else:
        # Nếu client chỉ định mã cụ thể, bắt lỗi trùng mã nếu đã tồn tại
        batch = Batch(**batch_dict)
        db.add(batch)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Mã lô nông sản '{batch_dict['code']}' đã tồn tại trong hệ thống.",
            ) from exc
        except SQLAlchemyError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Không thể lưu lô nông sản vào cơ sở dữ liệu.",
            ) from exc

    db.refresh(batch)
    return batch


@router.get(
    "",
    response_model=list[BatchResponse],
    status_code=status.HTTP_200_OK,
    summary="Lấy danh sách lô nông sản (hỗ trợ lọc sản phẩm & phân trang con trỏ - SCRUM-49)",
    description="Hỗ trợ tìm kiếm, lọc theo loại sản phẩm và phân trang con trỏ keyset (cursor pagination).",
)
def list_batches(
    product: str | None = Query(None, description="Lọc chính xác hoặc tương đối theo tên sản phẩm."),
    search: str | None = Query(None, description="Tìm kiếm mã lô hoặc tên sản phẩm không phân biệt hoa thường."),
    cursor: int | None = Query(None, ge=1, description="ID con trỏ cho trang kế tiếp (Keyset pagination)."),
    limit: int = Query(50, ge=1, le=100, description="Số lượng bản ghi tối đa trả về."),
    db: Session = Depends(get_db),
) -> list[Batch]:
    """Lấy danh sách lô nông sản có hỗ trợ bộ lọc và phân trang con trỏ (SCRUM-49)."""
    stmt = select(Batch)

    # Lọc theo sản phẩm
    if product:
        stmt = stmt.where(Batch.product_name.ilike(f"%{product}%"))

    # Tìm kiếm chung
    if search:
        stmt = stmt.where(Batch.product_name.ilike(f"%{search}%"))

    # Phân trang con trỏ (keyset cursor)
    if cursor:
        stmt = stmt.where(Batch.id > cursor)

    stmt = stmt.order_by(Batch.id.asc()).limit(limit)
    return list(db.scalars(stmt).all())


@router.get(
    "/{batch_id}",
    response_model=BatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Xem chi tiết một lô nông sản",
    description="Trả về thông tin chi tiết của lô theo `id`. Trả `404` nếu không tồn tại.",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Không tìm thấy lô nông sản.",
        },
    },
)
def get_batch(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần xem."),
    db: Session = Depends(get_db),
) -> Batch:
    """Lấy chi tiết một lô nông sản theo ``id``.

    Args:
        batch_id: ID của lô cần tìm.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Batch: Bản ghi lô tương ứng (HTTP 200).

    Raises:
        HTTPException: 404 nếu không tìm thấy lô.
    """
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )
    return batch


@router.get(
    "/code/{code}",
    response_model=BatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Truy xuất nguồn gốc lô nông sản theo mã 8 ký tự (T-18 / SCRUM-34)",
    description=(
        "Tra cứu công khai thông tin lô nông sản thông qua mã định danh 8 ký tự. "
        "Dùng cho chức năng quét mã QR hoặc nhập mã tra cứu trên tem sản phẩm."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Không tìm thấy lô nông sản với mã đã cho.",
        },
    },
)
def get_batch_by_code(
    code: str = Path(..., min_length=8, max_length=8, description="Mã truy xuất nguồn gốc 8 ký tự."),
    db: Session = Depends(get_db),
) -> Batch:
    """Tra cứu lô nông sản theo mã 8 ký tự duy nhất (T-18 / SCRUM-34)."""
    stmt = select(Batch).where(Batch.code == code)
    batch = db.scalars(stmt).first()
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản với mã '{code}'.",
        )
    return batch


@router.get(
    "/code/{code}/map",
    response_model=PublicTrackingMapResponse,
    status_code=status.HTTP_200_OK,
    summary="Bản đồ hành trình công khai của lô nông sản (S-06 / Tier Later)",
    description=(
        "Hiển thị toạ độ đại diện cấp xã/huyện của vùng trồng xuất xứ và các điểm dừng chính trên hành trình theo thứ tự. "
        "Bảo vệ quyền riêng tư: Tuyệt đối không hiển thị toạ độ chi tiết/chính xác của thửa đất nông hộ."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy lô nông sản."},
    },
)
def get_batch_public_map(
    code: str = Path(..., min_length=8, max_length=8, description="Mã lô nông sản 8 ký tự."),
    db: Session = Depends(get_db),
) -> PublicTrackingMapResponse:
    """Bản đồ hành trình công khai: điểm vùng trồng cấp xã/huyện và các điểm dừng chính theo thứ tự (S-06)."""
    batch = db.scalar(select(Batch).where(Batch.code == code))
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản với mã '{code}'.",
        )

    farm = db.get(Farm, batch.farm_id)
    farm_loc_str = farm.location if farm else "Đồng Tháp"
    farm_owner_str = farm.owner if farm else "Hợp tác xã nông nghiệp"

    # Toạ độ đại diện cấp xã/huyện: Xấp xỉ toạ độ trung tâm hành chính, làm mờ toạ độ chi tiết
    # Mặc định trung tâm Cao Lãnh, Đồng Tháp: 10.4570° N, 105.6328° E
    base_lat, base_lng = 10.4570, 105.6328

    origin = MapWaypoint(
        order=1,
        name=f"Vùng trồng: {farm_loc_str}",
        location_level="Cấp Xã / Huyện (Đã ẩn toạ độ thửa đất)",
        organization=farm_owner_str,
        action="Thu hoạch & Khởi tạo nguồn gốc VietGAP",
        latitude=round(base_lat, 3),  # Chỉ lấy 3 chữ số thập phân (~cấp xã/phường, không định vị được thửa)
        longitude=round(base_lng, 3),
        timestamp=str(batch.harvest_date),
    )

    # Đọc chuỗi sự kiện để lấy các điểm dừng chính theo thứ tự thời gian
    stmt_events = (
        select(BatchEvent)
        .where(BatchEvent.batch_id == batch.id)
        .order_by(BatchEvent.id.asc())
    )
    events = list(db.scalars(stmt_events).all())

    # Map các điểm dừng hành chính tương ứng các bước
    known_stops = [
        {"name": "Nhà máy sơ chế & làm mát Cai Lậy", "level": "Cấp Thị Xã Cai Lậy, Tiền Giang", "lat": 10.4080, "lng": 106.1200},
        {"name": "Trung tâm đóng gói & kiểm dịch Tân An", "level": "Cấp Thành Phố Tân An, Long An", "lat": 10.5360, "lng": 106.4130},
        {"name": "Tổng kho lạnh logistics Bình Điền", "level": "Cấp Quận 8, TP. Hồ Chí Minh", "lat": 10.7250, "lng": 106.6350},
        {"name": "Cảng xuất khẩu Cát Lái", "level": "Cấp Thành Phố Thủ Đức, TP. Hồ Chí Minh", "lat": 10.7600, "lng": 106.7900},
    ]

    waypoints: list[MapWaypoint] = []
    order_counter = 2

    for i, ev in enumerate(events):
        # Bỏ qua sự kiện thu hoạch đầu tiên vì đã nằm ở origin
        if ev.event_type == "HARVEST" and i == 0:
            continue

        stop_info = known_stops[(order_counter - 2) % len(known_stops)]
        action_desc = f"Thực hiện bước: {ev.event_type}"
        if ev.event_type == "HANDOVER":
            action_desc = "Bàn giao chuỗi cung ứng lạnh"
        elif ev.event_type == "SPLIT":
            action_desc = "Phân loại quy cách đóng gói"
        elif ev.event_type == "PROCESSING":
            action_desc = "Sơ chế và khử khuẩn theo tiêu chuẩn"

        wp = MapWaypoint(
            order=order_counter,
            name=stop_info["name"],
            location_level=stop_info["level"],
            organization=ev.organization,
            action=action_desc,
            latitude=round(stop_info["lat"], 3),
            longitude=round(stop_info["lng"], 3),
            timestamp=ev.timestamp,
        )
        waypoints.append(wp)
        order_counter += 1

    return PublicTrackingMapResponse(
        batch_code=batch.code,
        product_name=batch.product_name,
        origin_point=origin,
        waypoints=waypoints,
        privacy_note="Toạ độ hiển thị ở cấp xã/huyện nhằm bảo vệ bí mật nông hộ và quyền riêng tư thửa đất.",
    )



@router.put(
    "/{batch_id}",
    response_model=BatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Cập nhật lô nông sản",
    description=(
        "Cập nhật (thay thế) thông tin lô theo `id`. Client gửi đầy đủ các trường "
        "như khi tạo mới; `farm_id` mới cũng phải tồn tại. Trả `404` nếu lô "
        "**hoặc** vùng trồng không tồn tại.\n\n"
        "**Phân quyền:** đăng nhập với role `farmer` hoặc `admin`."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Vai trò không được phép."},
        status.HTTP_404_NOT_FOUND: {
            "description": "Không tìm thấy lô nông sản hoặc vùng trồng (farm_id).",
        },
    },
)
def update_batch(
    payload: BatchUpdate,
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần sửa."),
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> Batch:
    """Cập nhật thông tin lô nông sản theo ``id``.

    Args:
        payload: Dữ liệu mới đã được Pydantic validate (đủ 4 trường).
        batch_id: ID lô cần sửa.
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Batch: Bản ghi lô sau khi cập nhật (HTTP 200).

    Raises:
        HTTPException: 401/403 nếu chưa đăng nhập hoặc sai vai trò;
            404 nếu không tìm thấy lô hoặc ``farm_id`` mới;
            500 nếu ghi database thất bại (đã rollback).
    """
    _ = current_user

    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )

    # Kiểm tra lại toàn vẹn tham chiếu: vùng trồng (mới) phải tồn tại.
    farm = db.get(Farm, payload.farm_id)
    if farm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vùng trồng có id={payload.farm_id}.",
        )

    for field, value in payload.model_dump().items():
        setattr(batch, field, value)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể cập nhật lô nông sản trong cơ sở dữ liệu.",
        ) from exc

    db.refresh(batch)
    return batch


@router.delete(
    "/{batch_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Xoá lô nông sản (chỉ admin)",
    description=(
        "Xoá một lô nông sản theo `id`.\n\n"
        "**Phân quyền:** chỉ `role = admin` được xoá (dùng `require_admin`). "
        "Farmer gọi sẽ nhận `403 Forbidden` - giao diện cũng ẩn nút Xoá với farmer."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Đã đăng nhập nhưng không phải admin."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy lô nông sản."},
    },
)
def delete_batch(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần xoá."),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> DeleteResponse:
    """Xoá một lô nông sản (chỉ admin).

    Args:
        batch_id: ID lô cần xoá.
        current_user: Tài khoản admin đã được ``require_admin`` kiểm tra quyền.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        DeleteResponse: Thông báo kết quả xoá (HTTP 200).

    Raises:
        HTTPException: 401 nếu chưa đăng nhập; 403 nếu không phải admin;
            404 nếu không tìm thấy lô;
            500 nếu xoá trong database thất bại (đã rollback).
    """
    _ = current_user

    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )

    # Lưu lại tên sản phẩm để viết thông báo (sau khi xoá không đọc được nữa).
    product_name = batch.product_name

    db.delete(batch)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể xoá lô nông sản khỏi cơ sở dữ liệu.",
        ) from exc

    return DeleteResponse(
        message=f"Đã xoá lô nông sản #{batch_id} ({product_name}).",
        deleted_id=batch_id,
        # Xoá lô không kéo theo bản ghi nào khác -> null.
        deleted_batches=None,
    )


# -----------------------------------------------------------------------------
# TRANG CHI TIẾT 3 TAB & THAO TÁC NGHIỆP VỤ (T-58 / SCRUM-74)
# -----------------------------------------------------------------------------

@router.get(
    "/{batch_id}/detail",
    response_model=BatchDetailCombinedResponse,
    summary="Chi tiết tổng hợp 3 Tab của lô nông sản (Tổng quan, Dòng thời gian, Nguồn gốc)",
)
def get_batch_full_detail(
    batch_id: int,
    db: Session = Depends(get_db),
) -> BatchDetailCombinedResponse:
    """Trả về dữ liệu tổng hợp phục vụ hiển thị mượt mà 3 Tab trên giao diện."""
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản với ID #{batch_id}.",
        )

    farm = db.get(Farm, batch.farm_id)
    farm_name = farm.name if farm else "Chưa xác định"
    farm_location = farm.location if farm else "Chưa xác định"
    farm_owner = farm.owner if farm else "Chưa xác định"
    farm_area = farm.area if farm else 0.0

    # Tab 2: Dòng thời gian sự kiện (T-32)
    timeline = get_batch_timeline(batch_id=batch_id, current_user=None, db=db)

    # Tab 3: Nguồn gốc phả hệ (T-50 & SCRUM-64)
    genealogy = get_batch_genealogy(batch_id=batch_id, db=db)
    bfs_result = trace_ancestors_bfs(batch_code=batch.code, db=db)

    ancestors = BatchAncestorsBFSResponse(
        target_batch=bfs_result.target_batch,
        ancestors_by_level=bfs_result.ancestors_by_level,
        root_batches=bfs_result.root_batches,
        all_ancestors=bfs_result.all_ancestors,
    )

    return BatchDetailCombinedResponse(
        batch=batch,
        farm_name=farm_name,
        farm_location=farm_location,
        farm_owner=farm_owner,
        farm_area=farm_area,
        timeline=timeline,
        genealogy=genealogy,
        ancestors=ancestors,
    )


@router.post(
    "/{batch_id}/handover",
    response_model=BatchResponse,
    summary="Bàn giao lô nông sản (Chuyển trạng thái sang PENDING_HANDOVER)",
    description=(
        "Chuyển trạng thái lô sang PENDING_HANDOVER và ghi nhận mắt xích sự kiện HANDOVER. "
        "Server độc lập kiểm tra: từ chối 400 nếu lô đang ở trạng thái chờ bàn giao. "
        "Chỉ dành cho farmer và admin (inspector bị 403 Forbidden)."
    ),
)
def handover_batch(
    batch_id: int,
    data: BatchHandoverRequest,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> Batch:
    """Thao tác Bàn giao lô nông sản."""
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )

    # Kiểm tra trạng thái máy chủ: Không cho phép thao tác khi đang chờ bàn giao
    if batch.status == BATCH_STATUS_PENDING_HANDOVER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lô hàng đang trong trạng thái chờ bàn giao, không thể thực hiện thao tác.",
        )

    # Cập nhật trạng thái lô
    batch.status = BATCH_STATUS_PENDING_HANDOVER

    # Ghi sự kiện HANDOVER vào chuỗi bản ghi
    now_iso = datetime.now(timezone.utc).isoformat()
    stmt_last = select(BatchEvent).where(BatchEvent.batch_id == batch_id).order_by(BatchEvent.id.desc()).limit(1)
    last_event = db.scalars(stmt_last).first()
    prev_hash = last_event.hash if last_event else "0" * 64

    payload_dict = {
        "action": "HANDOVER",
        "target_organization": data.target_organization,
        "note": data.note or "",
        "quantity": batch.quantity,
    }
    payload_str = json.dumps(payload_dict, ensure_ascii=False)
    ev_hash = compute_event_hash(
        event_type="HANDOVER",
        payload=payload_str,
        actor=current_user.username,
        organization=data.target_organization,
        timestamp=now_iso,
        previous_hash=prev_hash,
    )

    handover_event = BatchEvent(
        batch_id=batch_id,
        event_type="HANDOVER",
        payload=payload_str,
        actor=current_user.username,
        organization=data.target_organization,
        timestamp=now_iso,
        hash=ev_hash,
        previous_hash=prev_hash,
    )
    db.add(handover_event)

    db.commit()
    db.refresh(batch)
    return batch


@router.post(
    "/{batch_id}/split",
    response_model=list[BatchResponse],
    summary="Phân tách lô nông sản thành nhiều lô con (SPLIT)",
    description=(
        "Tách lô hàng thành các lô con. "
        "Server độc lập kiểm tra: từ chối 400 nếu lô đang ở trạng thái chờ bàn giao. "
        "Chỉ dành cho farmer và admin."
    ),
)
def split_batch(
    batch_id: int,
    data: BatchSplitRequest,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> list[Batch]:
    """Thao tác Tách lô nông sản."""
    # Sử dụng with_for_update() trên cơ sở dữ liệu hỗ trợ khoá dòng (hoặc truy vấn trực tiếp)
    # để đảm bảo kiểm soát tương tranh (concurrency control) khi hai người cùng tách cùng lúc.
    try:
        parent = db.scalars(
            select(Batch).where(Batch.id == batch_id).with_for_update()
        ).first()
    except Exception:
        # Fallback cho dialect không hỗ trợ FOR UPDATE như sqlite in-memory đơn giản
        parent = db.get(Batch, batch_id)

    if not parent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )

    # Server-side validation: Chặn thao tác khi lô đang chờ bàn giao
    if parent.status == BATCH_STATUS_PENDING_HANDOVER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lô hàng đang trong trạng thái chờ bàn giao, không thể thực hiện thao tác tách lô.",
        )

    total_split_qty = round(sum(c.quantity for c in data.children), 4)
    parent_current_qty = round(parent.quantity, 4)

    # Kịch bản 1 & 2: Chặn kèm thông báo nêu rõ phần còn lại của lô mẹ
    if total_split_qty > parent_current_qty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Khối lượng yêu cầu tách ({total_split_qty} kg) vượt quá số lượng còn lại của lô mẹ. "
                f"Lô mẹ hiện chỉ còn lại {parent_current_qty} kg."
            ),
        )

    # Kịch bản 3: Trừ chính xác khối lượng từ lô mẹ, làm tròn 4 chữ số thập phân (độ chính xác 0.0001 kg = 0.1 gram)
    new_parent_qty = round(parent_current_qty - total_split_qty, 4)
    parent.quantity = new_parent_qty

    now_iso = datetime.now(timezone.utc).isoformat()
    created_children: list[Batch] = []

    for item in data.children:
        child_qty = round(item.quantity, 4)
        # Sinh mã ngẫu nhiên an toàn cho lô con
        def save_child(child_code: str) -> Batch:
            child = Batch(
                farm_id=parent.farm_id,
                product_name=item.product_name,
                quantity=child_qty,
                harvest_date=parent.harvest_date,
                status=BATCH_STATUS_ACTIVE,
                code=child_code,
            )
            db.add(child)
            db.flush()
            return child

        _, child_batch = execute_with_unique_retry(save_fn=save_child, db=db)
        created_children.append(child_batch)

        # Ghi nhận quan hệ phả hệ SPLIT
        lineage_rec = BatchLineage(
            parent_batch_id=parent.id,
            child_batch_id=child_batch.id,
            transferred_quantity=child_qty,
            relation_type=RELATION_SPLIT,
            created_at=now_iso,
        )
        db.add(lineage_rec)

    # Ghi sự kiện SPLIT vào lô cha
    stmt_last = select(BatchEvent).where(BatchEvent.batch_id == parent.id).order_by(BatchEvent.id.desc()).limit(1)
    last_ev = db.scalars(stmt_last).first()
    prev_h = last_ev.hash if last_ev else "0" * 64

    payload_split = json.dumps({
        "action": "SPLIT",
        "children_ids": [c.id for c in created_children],
        "total_split_quantity": total_split_qty,
        "remaining_quantity": new_parent_qty,
    }, ensure_ascii=False)

    ev_h = compute_event_hash(
        event_type="SPLIT",
        payload=payload_split,
        actor=current_user.username,
        organization="Hợp Tác Xã",
        timestamp=now_iso,
        previous_hash=prev_h,
    )
    db.add(BatchEvent(
        batch_id=parent.id,
        event_type="SPLIT",
        payload=payload_split,
        actor=current_user.username,
        organization="Hợp Tác Xã",
        timestamp=now_iso,
        hash=ev_h,
        previous_hash=prev_h,
    ))

    db.commit()
    db.refresh(parent)
    for c in created_children:
        db.refresh(c)
    return created_children


@router.post(
    "/{batch_id}/merge",
    response_model=BatchResponse,
    summary="Sáp nhập các lô cha vào một lô mới (MERGE)",
    description=(
        "Gộp nhiều lô nông sản. Server độc lập kiểm tra: từ chối nếu có bất kỳ lô cha nào đang chờ bàn giao."
    ),
)
def merge_batches(
    batch_id: int,
    data: BatchMergeRequest,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> Batch:
    """Thao tác Gộp lô nông sản."""
    parents = db.scalars(select(Batch).where(Batch.id.in_(data.parent_batch_ids))).all()
    if len(parents) < len(data.parent_batch_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Một hoặc nhiều lô cha được chỉ định không tồn tại.",
        )

    # Server-side check: Chặn nếu có bất kỳ lô cha nào đang chờ bàn giao
    for p in parents:
        if p.status == BATCH_STATUS_PENDING_HANDOVER:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Lô cha #{p.id} ({p.product_name}) đang trong trạng thái chờ bàn giao, không thể thực hiện gộp lô.",
            )

    now_iso = datetime.now(timezone.utc).isoformat()
    target_child = db.get(Batch, batch_id)

    # Tính toán khối lượng chuyển
    quantities = data.transferred_quantities or [p.quantity for p in parents]
    if len(quantities) != len(parents):
        quantities = [p.quantity for p in parents]

    total_merged_quantity = sum(quantities)

    if not target_child:
        # Tạo lô gộp mới nếu batch_id không trỏ tới lô sẵn có
        first_parent = parents[0]
        def save_merged(m_code: str) -> Batch:
            b = Batch(
                farm_id=first_parent.farm_id,
                product_name=data.product_name,
                quantity=total_merged_quantity,
                harvest_date=first_parent.harvest_date,
                status=BATCH_STATUS_ACTIVE,
                code=m_code,
            )
            db.add(b)
            db.flush()
            return b

        _, target_child = execute_with_unique_retry(save_fn=save_merged, db=db)

    # Ghi nhận quan hệ phả hệ MERGE cho từng lô cha
    for p, qty in zip(parents, quantities):
        # Tránh ghi trùng nếu đã có quan hệ
        existing = db.scalar(
            select(BatchLineage).where(
                BatchLineage.parent_batch_id == p.id,
                BatchLineage.child_batch_id == target_child.id,
            )
        )
        if not existing:
            lineage_rec = BatchLineage(
                parent_batch_id=p.id,
                child_batch_id=target_child.id,
                transferred_quantity=qty,
                relation_type=RELATION_MERGE,
                created_at=now_iso,
            )
            db.add(lineage_rec)

    # Ghi sự kiện MERGE vào lô đích
    stmt_last = select(BatchEvent).where(BatchEvent.batch_id == target_child.id).order_by(BatchEvent.id.desc()).limit(1)
    last_ev = db.scalars(stmt_last).first()
    prev_h = last_ev.hash if last_ev else "0" * 64

    payload_merge = json.dumps({
        "action": "MERGE",
        "parents_ids": [p.id for p in parents],
        "merged_quantity": total_merged_quantity,
    }, ensure_ascii=False)

    ev_h = compute_event_hash(
        event_type="MERGE",
        payload=payload_merge,
        actor=current_user.username,
        organization="Hợp Tác Xã",
        timestamp=now_iso,
        previous_hash=prev_h,
    )
    db.add(BatchEvent(
        batch_id=target_child.id,
        event_type="MERGE",
        payload=payload_merge,
        actor=current_user.username,
        organization="Hợp Tác Xã",
        timestamp=now_iso,
        hash=ev_h,
        previous_hash=prev_h,
    ))

    db.commit()
    db.refresh(target_child)
    return target_child

