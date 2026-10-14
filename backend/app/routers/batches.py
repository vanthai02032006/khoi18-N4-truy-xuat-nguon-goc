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
from __future__ import annotations

import json
import secrets
import string
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import cast, func, or_, select, String

from decimal import Decimal
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.batch_split import to_fixed_decimal
from app.cache import trace_cache
from app.database import get_db
from app.events import record_event
from app.models import (
    BATCH_STATUS_CONSUMED,
    EVENT_TYPE_BATCH_CONSUMED,
    EVENT_TYPE_LOCATION_CHANGED,
    Batch,
    BatchCustodyHistory,
    BatchEvent,
    BatchRelation,
    Farm,
    Location,
    RecallOrder,
    User,
)
from app.recall_orders import (
    build_order_response,
    find_open_order_for_batch,
    open_recall_order_for_batch,
)
from app.schemas import (
    BatchCreate,
    BatchDetailResponse,
    BatchIncidentRequest,
    BatchLocationTransferRequest,
    BatchMergeItem,
    BatchMergeRequest,
    BatchMergeResponse,
    BatchResponse,
    BatchSplitRequest,
    BatchSplitResponse,
    BatchUpdate,
    DeleteResponse,
    HandoverActionResponse,
    HandoverInitiateRequest,
    HandoverRejectRequest,
    MapWaypoint,
    PublicTrackingMapResponse,
    RecallOrderResponse,
)
from app.security import compute_event_hash, get_current_user, get_optional_current_user, require_admin, require_farmer, require_inspector
from app.services.authorization_service import can_user_view_batch, filter_viewable_batches, user_holds_batch, user_org_key, user_org_keys, viewable_batch_ids
from app.tenant import get_tenant_org, require_tenant_context

router = APIRouter(
    prefix="/batches",
    tags=["Batches"],
)


def generate_unique_batch_code(db: Session, prefix: str = "LOT") -> str:
    """Tự động sinh mã lô riêng biệt, duy nhất (ngay cả khi 2 người cùng bấm lưu cùng lúc).

    Định dạng: LOT-YYYYMMDD-<RANDOM4> (ví dụ: LOT-20261007-A9F3).
    Dùng secrets.choice để đảm bảo tính ngẫu nhiên và kiểm tra không trùng lặp trong DB.
    """
    date_part = datetime.now().strftime("%Y%m%d")
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(10):
        random_suffix = "".join(secrets.choice(alphabet) for _ in range(4))
        candidate = f"{prefix}-{date_part}-{random_suffix}"
        # Kiểm tra xem mã đã tồn tại chưa
        exists = db.scalar(select(Batch.id).where(Batch.batch_code == candidate))
        if not exists:
            return candidate

    # Trường hợp hi hữu mở rộng thêm độ dài
    long_suffix = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{prefix}-{date_part}-{long_suffix}"


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
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> Batch:
    """Tạo lô nông sản mới."""
    _ = current_user

    # Bước 1: kiểm tra toàn vẹn tham chiếu - vùng trồng phải tồn tại.
    farm = db.get(Farm, payload.farm_id)
    if farm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vùng trồng có id={payload.farm_id}.",
        )

    # Bước 2: kiểm tra thửa đất có thuộc quyền quản lý của tổ chức hiện tại (SCRUM-36 / T-20).
    if current_org and current_org != "HTX Nông Nghiệp Số 4" and current_user.role != "admin":
        farm_org = getattr(farm, "organization", None) or farm.owner
        if farm_org != current_org and current_org not in farm_org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Thửa đất #{payload.farm_id} thuộc về tổ chức khác ({farm.owner}), bạn không có quyền tạo lô trên thửa đất này.",
            )

    # Bước 3: sinh mã lô tự động duy nhất
    code = generate_unique_batch_code(db)

    # Bước 4: lưu lô nông sản.
    batch_data = payload.model_dump()
    holder_org = current_org if (current_org and current_org != "HTX Nông Nghiệp Số 4") else (farm.owner or "HTX Nông Nghiệp Số 4")
    if "remaining_quantity" not in batch_data or batch_data.get("remaining_quantity") is None:
        batch_data["remaining_quantity"] = payload.quantity
    if not batch_data.get("organization_id"):
        batch_data["organization_id"] = user_org_key(current_user)

    batch = Batch(**batch_data, batch_code=code, current_holder_org=holder_org)
    db.add(batch)

    try:
        db.flush()
        db.add(BatchCustodyHistory(batch_id=batch.id, organization_id=batch.organization_id))
        db.commit()
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
    summary="Lấy danh sách lô nông sản (hỗ trợ lọc sản phẩm & phân trang con trỏ - SCRUM-49/70)",
    description="Hỗ trợ tìm kiếm, lọc theo loại sản phẩm, phân trang con trỏ và phân quyền theo SCRUM-70.",
)
def list_batches(
    product: str | None = Query(None, description="Lọc chính xác hoặc tương đối theo tên sản phẩm."),
    product_name: str | None = Query(None, description="Bộ lọc theo tên loại nông sản."),
    search: str | None = Query(None, description="Tìm kiếm mã lô hoặc tên sản phẩm không phân biệt hoa thường."),
    cursor: int | None = Query(None, ge=1, description="ID con trỏ cho trang kế tiếp (Keyset pagination)."),
    limit: int = Query(20, ge=1, le=100, description="Số lượng bản ghi tối đa trả về (mặc định 20 - SCRUM-49)."),
    holding: bool = Query(False, description="Chỉ lấy lô tổ chức của tôi ĐANG giữ (S-14), bỏ lô từng giữ và lô tổ tiên."),
    current_user: User = Depends(get_current_user),
    request_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> list[Batch]:
    """Danh sách lô mới nhất trước, phân trang con trỏ (SCRUM-49 / S-14).

    Bắt buộc đăng nhập (S-05): trước đây gọi không cần đăng nhập vẫn lộ toàn bộ lô.
    Quyền xem lọc ngay trong SQL để phân trang không bị hụt trang.
    """
    stmt = select(Batch)
    allowed_ids = viewable_batch_ids(db, current_user, request_org)
    if allowed_ids is not None:
        stmt = stmt.where(Batch.id.in_(allowed_ids))
    if holding:
        keys = user_org_keys(current_user, request_org)
        stmt = stmt.where(or_(Batch.organization_id.in_(keys), Batch.current_holder_org.in_(keys)))

    # Lọc theo sản phẩm
    filter_prod = product or product_name
    if filter_prod and filter_prod.strip():
        stmt = stmt.where(Batch.product_name.ilike(f"%{filter_prod.strip()}%"))

    # Tìm kiếm theo tên sản phẩm, mã lô (batch_code), hoặc ID (không phân biệt chữ thường chữ hoa)
    if search and search.strip():
        import re
        search_term = search.strip()
        clean_id = search_term.lower().lstrip("#").replace("batch-", "").strip()
        conditions = [
            Batch.product_name.ilike(f"%{search_term}%"),
            Batch.batch_code.ilike(f"%{search_term}%"),
            cast(Batch.id, String).ilike(f"%{search_term}%"),
        ]
        # Chỉ coi là tìm theo ID khi người dùng gõ toàn số hoặc "#số"; trước đây chữ số
        # trong mã lô (vd "VT0003") bị đem so với ID nên trả về cả lô không liên quan.
        if clean_id.isdigit():
            conditions.append(Batch.id == int(clean_id))
        stmt = stmt.where(or_(*conditions))

    # Phân trang con trỏ, mới nhất trước: trang sau có id nhỏ hơn cursor (SCRUM-49).
    if cursor:
        stmt = stmt.where(Batch.id < cursor)

    stmt = stmt.order_by(Batch.id.desc()).limit(limit)
    batches_list = list(db.scalars(stmt).all())
    # Backfill batch_code nếu có bản ghi cũ chưa có mã
    for b in batches_list:
        if not b.batch_code:
            b.batch_code = f"LOT-{b.id:04d}"
    return batches_list


@router.get(
    "/{batch_id}",
    response_model=BatchDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Xem chi tiết một lô nông sản",
    description=(
        "Trả về thông tin chi tiết và quan hệ phả hệ của lô theo `id`. "
        "Kiểm tra phân quyền theo quy tắc SCRUM-70: user/org phải đang giữ, "
        "từng giữ, hoặc là tổ tiên của lô đang giữ."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Chưa đăng nhập.",
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "Không có quyền xem lô này theo quy tắc SCRUM-70.",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Không tìm thấy lô nông sản.",
        },
    },
)
def get_batch(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần xem."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Batch:
    """Lấy chi tiết một lô nông sản theo ``id`` có kiểm tra quyền SCRUM-70 (bắt buộc đăng nhập - S-05)."""
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )
    if not batch.batch_code:
        batch.batch_code = f"LOT-{batch.id:04d}"

    if not can_user_view_batch(db, current_user, batch_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản hoặc tổ chức của bạn không có quyền xem thông tin lô nông sản này.",
        )
    return batch


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
    if batch.status == BATCH_STATUS_CONSUMED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lô nông sản #{batch_id} đã tiêu thụ nên không thể sửa.",
        )

    # Kiểm tra lại toàn vẹn tham chiếu: vùng trồng (mới) phải tồn tại.
    farm = db.get(Farm, payload.farm_id)
    if farm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vùng trồng có id={payload.farm_id}.",
        )

    # Kiểm tra phân quyền tổ chức: farmer không được sửa lô của tổ chức khác
    if getattr(current_user, "role", None) != "admin":
        user_org = getattr(current_user, "organization_id", None)
        if user_org and batch.organization_id and batch.organization_id != user_org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền sửa lô nông sản thuộc tổ chức khác.",
            )

    for field, value in payload.model_dump().items():
        # Không ghi đè organization_id thành None nếu payload không chỉ định
        if field == "organization_id" and value is None:
            continue
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


@router.post(
    "/{batch_id}/consume",
    response_model=BatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Đánh dấu lô đã bán hết",
    description=(
        "Đánh dấu lô đã tới tay người mua: đưa khối lượng còn lại về 0, "
        "chuyển trạng thái sang `consumed` và ghi sự kiện vào chuỗi bất biến."
    ),
)
def consume_batch(
    batch_id: int = Path(..., ge=1, description="ID lô đã bán hết."),
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> Batch:
    """Đánh dấu lô hiện tại đã bán hết và ghi nhận vào chuỗi sự kiện."""
    batch = db.scalar(
        select(Batch).where(Batch.id == batch_id).with_for_update()
    )
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )
    if batch.status == BATCH_STATUS_CONSUMED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lô nông sản #{batch_id} đã được đánh dấu tiêu thụ.",
        )

    effective_holder = batch.current_holder_org or "HTX Nông Nghiệp Số 4"
    if not user_holds_batch(current_user, batch, current_org):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Lô hàng đang do '{effective_holder}' nắm giữ, bạn không có quyền cập nhật.",
        )

    previous_quantity = batch.quantity
    if not batch.batch_code:
        batch.batch_code = f"LOT-{batch.id:04d}"
    batch.quantity = 0
    batch.status = BATCH_STATUS_CONSUMED
    record_event(
        db,
        batch_id=batch.id,
        event_type=EVENT_TYPE_BATCH_CONSUMED,
        actor=current_user.username,
        organization=current_org,
        payload={
            "action": "BATCH_CONSUMED",
            "previous_quantity": previous_quantity,
            "remaining_quantity": 0,
            "status": BATCH_STATUS_CONSUMED,
            "description": f"Lô #{batch.id} ({batch.product_name}) đã bán hết và tới tay người mua.",
        },
    )

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể ghi nhận lô đã tiêu thụ.",
        ) from exc

    db.refresh(batch)
    return batch


# =========================================================================
# BATCH LOCATION TRACKING (S-18 - ghi nhận lô đang nằm ở cửa hàng nào)
# =========================================================================


def _require_owned_location(db: Session, location_id: int, organization: str) -> Location:
    """Lấy địa điểm theo ``id`` và kiểm tra địa điểm thuộc đúng tổ chức (S-18).

    Args:
        db: Session SQLAlchemy hiện tại.
        location_id: ID địa điểm cần lấy.
        organization: Tổ chức của người gọi (header ``X-Organization-Id``).

    Returns:
        Location: Địa điểm thuộc tổ chức đang gọi.

    Raises:
        HTTPException: **404** nếu không có địa điểm với ``location_id``;
            **403** nếu địa điểm thuộc tổ chức khác.
    """
    location = db.get(Location, location_id)
    if location is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy địa điểm có id={location_id}.",
        )
    if location.organization != organization:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Địa điểm #{location_id} thuộc tổ chức '{location.organization}', "
                "bạn không có quyền sử dụng."
            ),
        )
    return location


@router.post(
    "/{batch_id}/location",
    response_model=BatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Chuyển lô về một địa điểm (S-18)",
    description=(
        "Ghi nhận lô đang nằm ở địa điểm nào: cập nhật `current_location_id` của "
        "lô và **ghi thêm sự kiện `LOCATION_CHANGED`** vào chuỗi băm bất biến để "
        "lịch sử di chuyển của lô không thể sửa lén.\n\n"
        "**Ràng buộc:** địa điểm phải thuộc đúng tổ chức đang giữ lô `403`; lô đã "
        "tiêu thụ không di chuyển được `400`."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Lô đã tiêu thụ (không di chuyển được)."},
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Lô/địa điểm thuộc tổ chức khác."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy lô hoặc địa điểm."},
    },
)
def move_batch_to_location(
    payload: BatchLocationTransferRequest,
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần chuyển."),
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> Batch:
    """Chuyển lô về địa điểm và ghi sự kiện ``LOCATION_CHANGED`` vào chuỗi.

    Args:
        payload: ``location_id`` địa điểm nhận lô.
        batch_id: ID lô cần chuyển.
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        current_org: Tổ chức của người gọi - phải là bên đang giữ lô.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Batch: Lô sau khi cập nhật địa điểm hiện tại (HTTP 200).

    Raises:
        HTTPException: 404 nếu không tìm thấy lô/địa điểm; 400 nếu lô đã tiêu thụ;
            403 nếu lô hoặc địa điểm thuộc tổ chức khác; 500 nếu ghi database thất bại.
    """
    batch = db.scalar(select(Batch).where(Batch.id == batch_id).with_for_update())
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )
    if batch.status == BATCH_STATUS_CONSUMED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lô nông sản #{batch_id} đã tiêu thụ nên không thể chuyển địa điểm.",
        )

    effective_holder = batch.current_holder_org or "HTX Nông Nghiệp Số 4"
    if not user_holds_batch(current_user, batch, current_org):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Lô hàng đang do '{effective_holder}' nắm giữ, bạn không có quyền di chuyển.",
        )

    location = _require_owned_location(db, payload.location_id, current_org)

    if not batch.batch_code:
        batch.batch_code = f"LOT-{batch.id:04d}"

    previous_location_id = batch.current_location_id
    previous_location_name = batch.current_location_name
    batch.current_location_id = location.id
    record_event(
        db,
        batch_id=batch.id,
        event_type=EVENT_TYPE_LOCATION_CHANGED,
        actor=current_user.username,
        organization=current_org,
        payload={
            "action": "LOCATION_CHANGED",
            "batch_code": batch.batch_code or f"LOT-{batch.id:04d}",
            "from_location_id": previous_location_id,
            "from_location_name": previous_location_name,
            "to_location_id": location.id,
            "to_location_name": location.name,
            "to_location_address": location.address,
            "holding_organization": effective_holder,
            "description": (
                f"Lô #{batch.id} được chuyển tới địa điểm '{location.name}' "
                f"({location.address})."
            ),
        },
    )

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể ghi nhận địa điểm hiện tại của lô.",
        ) from exc

    db.refresh(batch)
    return batch


# =========================================================================
# ĐÁNH DẤU SỰ CỐ & MỞ LỆNH THU HỒI (S-19)
# =========================================================================


@router.post(
    "/{batch_id}/incident",
    response_model=RecallOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Đánh dấu lô có sự cố và mở lệnh thu hồi (Cán bộ kiểm tra / Admin)",
    description=(
        "Cán bộ kiểm tra đánh dấu một lô có sự cố kèm lý do: hệ thống **mở ngay "
        "một lệnh thu hồi gắn với lô** (có mã lệnh riêng) và ghi sự kiện "
        "`INCIDENT_FLAGGED` (kèm mã lệnh) vào **chuỗi băm bất biến** của lô.\n\n"
        "**Ràng buộc:** mỗi lô chỉ có tối đa **một** lệnh đang mở - nếu lô đã có "
        "lệnh `IN_PROGRESS`, API trả `409` kèm mã lệnh đang mở để tránh mở trùng.\n\n"
        "**Phân quyền:** chỉ `inspector` hoặc `admin` (vai trò khác nhận `403`)."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {
            "description": "Đã đăng nhập nhưng không phải cán bộ kiểm tra / admin.",
        },
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy lô nông sản."},
        status.HTTP_409_CONFLICT: {"description": "Lô đã có lệnh thu hồi đang mở."},
    },
)
def flag_batch_incident(
    payload: BatchIncidentRequest,
    batch_id: int = Path(..., ge=1, description="ID lô bị đánh dấu sự cố."),
    current_user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
) -> RecallOrderResponse:
    """Đánh dấu lô có sự cố, mở lệnh thu hồi và ghi sự kiện vào chuỗi băm của lô.

    Args:
        payload: Lý do sự cố (bắt buộc) và tuỳ chọn tiêu đề/mô tả/bên liên quan.
        batch_id: ID lô bị đánh dấu.
        current_user: Tài khoản đã đăng nhập (inspector hoặc admin).
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        RecallOrderResponse: Lệnh thu hồi vừa mở kèm danh sách bên liên quan (HTTP 201).

    Raises:
        HTTPException: 401/403 nếu chưa đăng nhập / sai vai trò; 404 nếu không
            tìm thấy lô; 409 nếu lô đã có lệnh đang mở; 500 nếu ghi database thất bại.
    """
    batch = db.scalar(select(Batch).where(Batch.id == batch_id).with_for_update())
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )

    open_order = find_open_order_for_batch(db, batch_id)
    if open_order is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"Lô #{batch_id} đã có lệnh thu hồi đang mở ({open_order.order_code}). "
                    "Hãy hoàn tất lệnh đó trước khi mở lệnh mới."
                ),
                "open_order_id": open_order.id,
                "open_order_code": open_order.order_code,
                "open_order_status": open_order.status,
            },
        )

    try:
        order = open_recall_order_for_batch(
            db,
            batch=batch,
            reason=payload.reason,
            actor=current_user.username,
            title=payload.title,
            description=payload.description,
            target_organizations=payload.target_organizations,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể lưu lệnh thu hồi vào cơ sở dữ liệu.",
        ) from exc

    order = db.scalar(
        select(RecallOrder)
        .options(selectinload(RecallOrder.targets))
        .where(RecallOrder.id == order.id)
    )
    return build_order_response(order)


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

    event_count = db.scalar(
        select(func.count())
        .select_from(BatchEvent)
        .where(BatchEvent.batch_id == batch_id)
    )
    if event_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Lô nông sản #{batch_id} đã có {event_count} sự kiện trong nhật ký "
                "chuỗi băm nên không thể xoá (bảng chỉ thêm). "
                "Muốn đính chính, hãy ghi thêm một sự kiện mới thay vì xoá lô."
            ),
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


@router.post(
    "/{batch_id}/split",
    response_model=BatchSplitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tách nhập nhiều dòng lô con (SCRUM-56 / T-40)",
    description=(
        "Tách một lô nông sản mẹ thành nhiều lô con theo danh sách khối lượng gửi lên. "
        "Nếu tổng khối lượng nhập vượt quá số dư còn lại của lô mẹ, từ chối với lỗi 400 Bad Request. "
        "Tự động trừ số lượng của lô mẹ và ghi nhận sự kiện SPLIT vào chuỗi bất biến.\n\n"
        "**Chia lô cho nhiều cửa hàng (S-18):** mỗi dòng lô con có thể kèm `location_id` "
        "để gắn lô con vào một địa điểm thuộc tổ chức đang giữ lô mẹ; bỏ trống thì lô "
        "con giữ địa điểm hiện tại của lô mẹ."
    ),
)
def split_batch(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản mẹ cần tách."),
    payload: BatchSplitRequest = ...,
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> BatchSplitResponse:
    """Tách một lô nông sản mẹ thành danh sách các lô con (T-40)."""
    # 1. Đọc lô mẹ với khóa dòng bi quan
    stmt = select(Batch).where(Batch.id == batch_id).with_for_update()
    batch = db.scalar(stmt)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản mẹ có id={batch_id}.",
        )
    if batch.status == BATCH_STATUS_CONSUMED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lô nông sản mẹ #{batch_id} đã tiêu thụ nên không thể tách.",
        )

    # 2. Kiểm tra quyền nắm giữ: lô phải do tổ chức của người gọi nắm giữ (AC 4: 403 Forbidden)
    effective_holder = batch.current_holder_org or "HTX Nông Nghiệp Số 4"
    if not user_holds_batch(current_user, batch, current_org):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Lô nông sản #{batch_id} do '{effective_holder}' nắm giữ, bạn không có quyền tách lô này.",
        )

    # 3. Tính tổng khối lượng các dòng lô con yêu cầu bằng Decimal cố định
    from app.batch_split import to_fixed_decimal
    parent_qty = to_fixed_decimal(batch.quantity)
    child_quantities = [to_fixed_decimal(item.quantity) for item in payload.items]
    total_requested = sum(child_quantities)

    # 4. Kiểm tra nếu tổng vượt quá số dư của lô mẹ -> 400 Bad Request
    if total_requested > parent_qty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Tổng khối lượng các lô con ({total_requested} kg) vượt quá "
                f"khối lượng còn lại của lô mẹ ({parent_qty} kg)."
            ),
        )

    # Kiểm tra địa điểm nhận hàng của từng dòng lô con (S-30 / SCRUM-98): mỗi lô con
    # chỉ được gắn vào địa điểm thuộc chính tổ chức đang gửi request.
    requested_location_ids = {
        item.location_id for item in payload.items if item.location_id is not None
    }
    locations_by_id: dict[int, Location] = {
        location_id: _require_owned_location(db, location_id, current_org)
        for location_id in requested_location_ids
    }

    # 5. Thực thi trong giao dịch nguyên tử, rollback hoàn toàn nếu có lỗi
    try:
        # Trừ khối lượng của lô mẹ
        remaining_qty = parent_qty - total_requested
        batch.quantity = float(remaining_qty)

        # Tạo các lô con với mã riêng và ghi rõ parent_id
        child_batches: list[Batch] = []
        for idx, (item, qty_dec) in enumerate(zip(payload.items, child_quantities), start=1):
            if payload.simulate_error_at_index is not None and idx == payload.simulate_error_at_index:
                raise RuntimeError(
                    f"Giả lập lỗi tại lô con thứ {idx} để kiểm tra cơ chế rollback sạch sẽ."
                )

            child_code = generate_unique_batch_code(db)
            child = Batch(
                farm_id=batch.farm_id,
                product_name=batch.product_name,
                quantity=float(qty_dec),
                harvest_date=batch.harvest_date,
                batch_code=child_code,
                parent_id=batch.id,  # Ghi rõ lô mẹ (T-39 / AC 1)
                current_holder_org=batch.current_holder_org,
                current_owner=batch.current_owner,
                is_restricted=batch.is_restricted,
                organization_id=batch.organization_id,
                current_location_id=(
                    item.location_id if item.location_id is not None else batch.current_location_id
                ),
            )
            db.add(child)
            child_batches.append(child)

        # Flush tạm để gán ID tự tăng cho các lô con
        db.flush()

        # Ghi quan hệ cha-con vào batch_relations (T-39): truy ngược/truy xuôi (S-21, S-26)
        # và quy tắc quyền xem tổ tiên (S-23) đều đọc bảng này.
        for child, qty_dec in zip(child_batches, child_quantities):
            db.add(BatchRelation(
                parent_batch_id=batch.id,
                child_batch_id=child.id,
                used_quantity=qty_dec,
            ))
            if child.organization_id:
                db.add(BatchCustodyHistory(batch_id=child.id, organization_id=child.organization_id))

        # Ghi nhận sự kiện SPLIT vào chuỗi sự kiện bất biến của lô mẹ (SCRUM-39 / T-23 / T-40)
        last_event_stmt = (
            select(BatchEvent)
            .where(BatchEvent.batch_id == batch_id)
            .order_by(BatchEvent.id.desc())
            .limit(1)
        )
        last_event = db.scalars(last_event_stmt).first()
        prev_hash = last_event.hash if last_event else "0" * 64

        now_iso = datetime.now(timezone.utc).isoformat()
        org_name = current_org or get_tenant_org()
        actor_name = current_user.username

        child_details = [
            {
                "id": c.id,
                "batch_code": c.batch_code or f"LOT-{c.id:04d}",
                "quantity": c.quantity,
                "location_id": c.current_location_id,
                "location_name": (
                    locations_by_id[c.current_location_id].name
                    if c.current_location_id in locations_by_id
                    else c.current_location_name
                ),
            }
            for c in child_batches
        ]
        child_codes = [c["batch_code"] for c in child_details]

        event_payload_dict = {
            "action": "SPLIT",
            "parent_batch_id": batch_id,
            "parent_batch_code": batch.batch_code or f"LOT-{batch.id:04d}",
            "total_split_quantity": float(total_requested),
            "remaining_quantity": batch.quantity,
            "child_batch_codes": child_codes,
            "children": child_details,
        }
        event_payload_str = json.dumps(event_payload_dict, ensure_ascii=False)

        h = compute_event_hash(
            event_type="SPLIT",
            payload=event_payload_str,
            actor=actor_name,
            organization=org_name,
            timestamp=now_iso,
            previous_hash=prev_hash,
        )
        split_event = BatchEvent(
            batch_id=batch_id,
            event_type="SPLIT",
            payload=event_payload_str,
            actor=actor_name,
            organization=org_name,
            timestamp=now_iso,
            hash=h,
            previous_hash=prev_hash,
        )
        db.add(split_event)

        # Ghi nhận sự kiện khai sinh (BIRTH / SPLIT_CHILD) trên dòng thời gian của TỪNG lô con nêu mã lô mẹ
        parent_code = batch.batch_code or f"LOT-{batch.id:04d}"
        for child in child_batches:
            birth_payload_dict = {
                "action": "BIRTH",
                "birth_type": "SPLIT_CHILD",
                "batch_id": child.id,
                "batch_code": child.batch_code or f"LOT-{child.id:04d}",
                "parent_batch_id": batch.id,
                "parent_batch_code": parent_code,
                "initial_quantity": child.quantity,
                "location_id": child.current_location_id,
                "location_name": (
                    locations_by_id[child.current_location_id].name
                    if child.current_location_id in locations_by_id
                    else child.current_location_name
                ),
            }
            birth_payload_str = json.dumps(birth_payload_dict, ensure_ascii=False)
            birth_prev_hash = "0" * 64
            birth_hash = compute_event_hash(
                event_type="BIRTH",
                payload=birth_payload_str,
                actor=actor_name,
                organization=org_name,
                timestamp=now_iso,
                previous_hash=birth_prev_hash,
            )
            child_birth_event = BatchEvent(
                batch_id=child.id,
                event_type="BIRTH",
                payload=birth_payload_str,
                actor=actor_name,
                organization=org_name,
                timestamp=now_iso,
                hash=birth_hash,
                previous_hash=birth_prev_hash,
            )
            db.add(child_birth_event)

        db.commit()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi khi lưu giao dịch tách lô: {exc}",
        ) from exc

    for c in child_batches:
        db.refresh(c)
    db.refresh(batch)

    return BatchSplitResponse(
        parent_batch_id=batch.id,
        parent_remaining_quantity=batch.quantity,
        child_batches=[BatchResponse.model_validate(c) for c in child_batches],
        message=f"Tách thành công {len(child_batches)} lô con từ lô mẹ #{batch.id}.",
    )


# =========================================================================
# BATCH MERGE WORKFLOW (Gộp nhiều lô thành một lô mới)
# =========================================================================


@router.post(
    "/merge",
    response_model=BatchMergeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Gộp nhiều lô thành một lô mới",
    description=(
        "Gộp khối lượng từ 2 hoặc nhiều lô mẹ thành một lô mới. "
        "Ghi nhận sự kiện MERGE_PARENT trên từng lô mẹ và sự kiện MERGE trên lô con mới."
    ),
)
def merge_batches(
    payload: BatchMergeRequest,
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> BatchMergeResponse:
    """Gộp nhiều lô thành một lô mới (S-19 / SCRUM-60, SCRUM-61, SCRUM-62)."""
    # Tương thích payload kiểu SCRUM-60 (`parents`) lẫn kiểu giao diện (`items`).
    if not payload.items and payload.parents:
        payload.items = [
            BatchMergeItem(batch_id=p.parent_batch_id, quantity=float(p.used_quantity))
            for p in payload.parents
        ]
    if len(payload.items) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cần ít nhất 2 lô để thực hiện thao tác gộp.",
        )

    # Kiểm tra trùng lặp lô mẹ trong cùng yêu cầu
    raw_batch_ids = [item.batch_id for item in payload.items]
    if len(raw_batch_ids) != len(set(raw_batch_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Danh sách lô mẹ bị trùng lặp trong cùng yêu cầu gộp.",
        )

    # 1. Khóa dòng chống deadlock theo thứ tự ID tăng dần (Global Lock Ordering)
    sorted_items = sorted(payload.items, key=lambda x: x.batch_id)
    parents_map: dict[int, Batch] = {}
    for item in sorted_items:
        stmt = select(Batch).where(Batch.id == item.batch_id).with_for_update()
        p_batch = db.scalar(stmt)
        if not p_batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy lô mẹ có ID #{item.batch_id}.",
            )
        parents_map[item.batch_id] = p_batch

    # 2. Kiểm tra thẩm quyền tổ chức (Kịch bản 4: 403 Forbidden nếu không do tổ chức giữ)
    for item in payload.items:
        p_batch = parents_map[item.batch_id]
        effective_holder = p_batch.current_holder_org or "HTX Nông Nghiệp Số 4"
        if not user_holds_batch(current_user, p_batch, current_org):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Lô nông sản #{p_batch.id} do '{effective_holder}' nắm giữ, bạn không có quyền gộp lô này.",
            )

    # S-29: lô đã tiêu thụ là trạng thái cuối, không được gộp tiếp.
    for p_batch in parents_map.values():
        if p_batch.status == BATCH_STATUS_CONSUMED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Lô nông sản #{p_batch.id} đã tiêu thụ nên không thể gộp.",
            )

    # 3. Kiểm tra tính đồng nhất của sản phẩm (Kịch bản 3: 400 Bad Request nếu khác loại sản phẩm)
    primary_parent = parents_map[payload.items[0].batch_id]
    base_product = primary_parent.product_name.strip()
    for item in payload.items:
        p_batch = parents_map[item.batch_id]
        if p_batch.product_name.strip().lower() != base_product.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Không thể gộp các lô khác loại sản phẩm: phát hiện '{p_batch.product_name}' "
                    f"và '{base_product}'. Chỉ được gộp các lô cùng loại sản phẩm."
                ),
            )

    # 4. Kiểm tra khối lượng và trừ kho bằng Decimal bảo toàn tuyệt đối (Kịch bản 1 & 2)
    zero_decimal = Decimal("0.0000")
    parent_batches: list[Batch] = []
    parent_contributions: list[dict] = []
    total_merged_dec = zero_decimal

    for item in payload.items:
        p_batch = parents_map[item.batch_id]
        needed_dec = to_fixed_decimal(item.quantity)
        current_dec = to_fixed_decimal(p_batch.quantity)

        if needed_dec <= zero_decimal:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Khối lượng gộp từ lô #{p_batch.id} phải lớn hơn 0.",
            )

        if needed_dec > current_dec:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Khối lượng yêu cầu lấy từ lô #{p_batch.id} ({needed_dec} kg) "
                    f"vượt quá khối lượng còn lại của lô ({current_dec} kg)."
                ),
            )

        # Trừ khối lượng của lô mẹ
        rem_dec = current_dec - needed_dec
        p_batch.quantity = float(rem_dec)
        parent_batches.append(p_batch)
        total_merged_dec += needed_dec

        p_code = p_batch.batch_code or f"LOT-{p_batch.id:04d}"
        parent_contributions.append({
            "id": p_batch.id,
            "batch_code": p_code,
            "quantity": float(needed_dec),
            "remaining_quantity": float(rem_dec),
        })

    # 5. Tạo lô gộp mới
    new_product_name = payload.product_name or primary_parent.product_name
    new_batch_code = generate_unique_batch_code(db, prefix="LOT-MERGE")

    merged_batch = Batch(
        farm_id=primary_parent.farm_id,
        product_name=new_product_name,
        quantity=float(total_merged_dec),
        harvest_date=primary_parent.harvest_date,
        batch_code=new_batch_code,
        current_holder_org=primary_parent.current_holder_org,
        current_owner=primary_parent.current_owner,
        is_restricted=any(pb.is_restricted for pb in parent_batches),
    )
    db.add(merged_batch)

    try:
        db.flush()

        # 6. Ghi nhận quan hệ phả hệ cha - con nhiều-nhiều qua BatchRelation
        for item in payload.items:
            p_batch = parents_map[item.batch_id]
            used_dec = to_fixed_decimal(item.quantity)
            rel = BatchRelation(
                parent_batch_id=p_batch.id,
                child_batch_id=merged_batch.id,
                used_quantity=used_dec,
            )
            db.add(rel)

        now_iso = datetime.now(timezone.utc).isoformat()
        org_name = current_org or get_tenant_org()
        actor_name = current_user.username

        # 7. Ghi nhận sự kiện MERGE trên lô gộp mới (nêu đầy đủ các lô mẹ và khối lượng lấy từ mỗi lô)
        parent_codes_list = [pc["batch_code"] for pc in parent_contributions]
        merged_event_payload = {
            "action": "MERGE",
            "batch_id": merged_batch.id,
            "batch_code": new_batch_code,
            "total_merged_quantity": float(total_merged_dec),
            "parent_batches": parent_contributions,
            "parent_batch_codes": parent_codes_list,
            "note": payload.note,
        }
        merged_payload_str = json.dumps(merged_event_payload, ensure_ascii=False)
        merged_prev_hash = "0" * 64
        merged_h = compute_event_hash(
            event_type="MERGE",
            payload=merged_payload_str,
            actor=actor_name,
            organization=org_name,
            timestamp=now_iso,
            previous_hash=merged_prev_hash,
        )
        merged_event = BatchEvent(
            batch_id=merged_batch.id,
            event_type="MERGE",
            payload=merged_payload_str,
            actor=actor_name,
            organization=org_name,
            timestamp=now_iso,
            hash=merged_h,
            previous_hash=merged_prev_hash,
        )
        db.add(merged_event)

        # 8. Ghi nhận sự kiện MERGE_PARENT vào dòng thời gian của TỪNG lô mẹ
        for contrib, p_batch in zip(parent_contributions, parent_batches):
            last_ev_stmt = (
                select(BatchEvent)
                .where(BatchEvent.batch_id == p_batch.id)
                .order_by(BatchEvent.id.desc())
                .limit(1)
            )
            last_ev = db.scalars(last_ev_stmt).first()
            p_prev_hash = last_ev.hash if last_ev else "0" * 64

            p_ev_payload = {
                "action": "MERGED_INTO",
                "parent_batch_id": p_batch.id,
                "parent_batch_code": contrib["batch_code"],
                "contributed_quantity": contrib["quantity"],
                "remaining_quantity": p_batch.quantity,
                "target_merged_batch_id": merged_batch.id,
                "target_merged_batch_code": new_batch_code,
            }
            p_ev_payload_str = json.dumps(p_ev_payload, ensure_ascii=False)
            p_ev_h = compute_event_hash(
                event_type="MERGE_PARENT",
                payload=p_ev_payload_str,
                actor=actor_name,
                organization=org_name,
                timestamp=now_iso,
                previous_hash=p_prev_hash,
            )
            p_event = BatchEvent(
                batch_id=p_batch.id,
                event_type="MERGE_PARENT",
                payload=p_ev_payload_str,
                actor=actor_name,
                organization=org_name,
                timestamp=now_iso,
                hash=p_ev_h,
                previous_hash=p_prev_hash,
            )
            db.add(p_event)

        db.commit()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi khi lưu giao dịch gộp lô: {exc}",
        ) from exc

    db.refresh(merged_batch)
    for pb in parent_batches:
        db.refresh(pb)
        trace_cache.invalidate(pb.id)
    trace_cache.invalidate(merged_batch.id)

    return BatchMergeResponse(
        merged_batch=BatchResponse.model_validate(merged_batch),
        parent_batches=[BatchResponse.model_validate(pb) for pb in parent_batches],
        message=f"Đã gộp thành công {len(parent_batches)} lô mẹ vào lô mới #{merged_batch.id} ({new_batch_code}).",
    )



# =========================================================================
# HANDOVER WORKFLOW (Quy trình bàn giao & nhận lô - SCRUM-xx)
# =========================================================================


@router.post(
    "/{batch_id}/handover/initiate",
    response_model=HandoverActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Khởi tạo bàn giao lô hàng sang tổ chức khác",
    description=(
        "Bên đang nắm giữ lô hàng tạo yêu cầu bàn giao sang một bên nhận. "
        "Yêu cầu phải có xác nhận của bên nhận để quyền nắm giữ được chuyển giao."
    ),
)
def initiate_handover(
    batch_id: int = Path(..., gt=0, description="Mã định danh lô hàng cần bàn giao"),
    payload: HandoverInitiateRequest = ...,
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> HandoverActionResponse:
    """Khởi tạo đợt bàn giao lô hàng."""
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô hàng có id={batch_id}.",
        )
    if batch.status == BATCH_STATUS_CONSUMED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lô hàng #{batch_id} đã tiêu thụ nên không thể bàn giao.",
        )

    # Kiểm tra quyền: người gọi phải thuộc tổ chức đang nắm giữ lô hàng (trừ admin)
    effective_holder = batch.current_holder_org or "HTX Nông Nghiệp Số 4"
    if not user_holds_batch(current_user, batch, current_org):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Lô hàng đang do '{effective_holder}' nắm giữ, bạn không có quyền bàn giao lô hàng này.",
        )

    # Không thể tự bàn giao cho chính mình
    if payload.target_organization == effective_holder:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tổ chức nhận bàn giao không được trùng với tổ chức hiện đang nắm giữ lô hàng.",
        )

    # Cập nhật trạng thái chờ nhận
    batch.pending_receiver_org = payload.target_organization

    # Ghi nhận sự kiện HANDOVER_INITIATED vào chuỗi sự kiện bất biến
    last_event_stmt = (
        select(BatchEvent)
        .where(BatchEvent.batch_id == batch_id)
        .order_by(BatchEvent.id.desc())
        .limit(1)
    )
    last_event = db.scalars(last_event_stmt).first()
    prev_hash = last_event.hash if last_event else "0" * 64

    now_iso = datetime.now(timezone.utc).isoformat()
    event_payload_dict = {
        "action": "HANDOVER_INITIATED",
        "batch_id": batch_id,
        "from_organization": effective_holder,
        "to_organization": payload.target_organization,
        "note": payload.note,
    }
    event_payload_str = json.dumps(event_payload_dict, ensure_ascii=False)

    h = compute_event_hash(
        event_type="HANDOVER_INITIATED",
        payload=event_payload_str,
        actor=current_user.username,
        organization=current_org,
        timestamp=now_iso,
        previous_hash=prev_hash,
    )
    handover_event = BatchEvent(
        batch_id=batch_id,
        event_type="HANDOVER_INITIATED",
        payload=event_payload_str,
        actor=current_user.username,
        organization=current_org,
        timestamp=now_iso,
        hash=h,
        previous_hash=prev_hash,
    )
    db.add(handover_event)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi khi lưu thông tin khởi tạo bàn giao.",
        ) from exc

    db.refresh(batch)
    db.refresh(handover_event)

    return HandoverActionResponse(
        batch_id=batch.id,
        current_holder_org=batch.current_holder_org,
        pending_receiver_org=batch.pending_receiver_org,
        status="PENDING_RECEIVER_ACCEPTANCE",
        message=f"Đã tạo yêu cầu bàn giao lô #{batch.id} cho '{batch.pending_receiver_org}'. Đang chờ bên nhận xác nhận.",
        event_id=handover_event.id,
        event_hash=handover_event.hash,
    )


@router.post(
    "/{batch_id}/handover/accept",
    response_model=HandoverActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Xác nhận nhận bàn giao lô hàng",
    description=(
        "Bên nhận (tổ chức được chỉ định bàn giao) xác nhận tiếp nhận lô hàng. "
        "Quyền giữ lô sẽ chuyển sang bên nhận và một sự kiện xác nhận được ghi tiếp vào chuỗi hash-chain."
    ),
)
def accept_handover(
    batch_id: int = Path(..., gt=0, description="Mã định danh lô hàng cần xác nhận"),
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> HandoverActionResponse:
    """Xác nhận nhận bàn giao lô hàng."""
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô hàng có id={batch_id}.",
        )
    if batch.status == BATCH_STATUS_CONSUMED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lô hàng #{batch_id} đã tiêu thụ nên không thể xác nhận bàn giao.",
        )

    # Kiểm tra xem lô có đợt bàn giao nào đang chờ hay không
    if not batch.pending_receiver_org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lô hàng #{batch_id} hiện không có đợt bàn giao nào đang chờ tiếp nhận.",
        )

    # Kiểm tra phân quyền: người gọi / tổ chức của người gọi có đúng là bên nhận không
    # Giả sử một người khác của tổ chức khác gọi API xác nhận bàn giao không dành cho họ -> 403
    if current_org != batch.pending_receiver_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Tổ chức của bạn ('{current_org}') không phải bên nhận được chỉ định "
                f"('{batch.pending_receiver_org}') của đợt bàn giao này."
            ),
        )

    previous_holder = batch.current_holder_org
    new_holder = current_org

    # Chuyển quyền giữ lô sang tổ chức nhận và xóa trạng thái chờ
    batch.current_holder_org = new_holder
    batch.pending_receiver_org = None

    # Ghi nhận sự kiện HANDOVER_ACCEPTED vào chuỗi sự kiện bất biến
    last_event_stmt = (
        select(BatchEvent)
        .where(BatchEvent.batch_id == batch_id)
        .order_by(BatchEvent.id.desc())
        .limit(1)
    )
    last_event = db.scalars(last_event_stmt).first()
    prev_hash = last_event.hash if last_event else "0" * 64

    now_iso = datetime.now(timezone.utc).isoformat()
    event_payload_dict = {
        "action": "HANDOVER_ACCEPTED",
        "batch_id": batch_id,
        "from_organization": previous_holder,
        "to_organization": new_holder,
        "status": "COMPLETED",
    }
    event_payload_str = json.dumps(event_payload_dict, ensure_ascii=False)

    h = compute_event_hash(
        event_type="HANDOVER_ACCEPTED",
        payload=event_payload_str,
        actor=current_user.username,
        organization=new_holder,
        timestamp=now_iso,
        previous_hash=prev_hash,
    )
    accept_event = BatchEvent(
        batch_id=batch_id,
        event_type="HANDOVER_ACCEPTED",
        payload=event_payload_str,
        actor=current_user.username,
        organization=new_holder,
        timestamp=now_iso,
        hash=h,
        previous_hash=prev_hash,
    )
    db.add(accept_event)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi khi lưu giao dịch xác nhận bàn giao.",
        ) from exc

    db.refresh(batch)
    db.refresh(accept_event)

    return HandoverActionResponse(
        batch_id=batch.id,
        current_holder_org=batch.current_holder_org,
        pending_receiver_org=batch.pending_receiver_org,
        status="ACCEPTED",
        message=f"Xác nhận nhận lô #{batch.id} thành công. Quyền giữ lô đã chuyển sang '{new_holder}'.",
        event_id=accept_event.id,
        event_hash=accept_event.hash,
    )


@router.post(
    "/{batch_id}/handover/reject",
    response_model=HandoverActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Từ chối nhận bàn giao lô hàng",
    description=(
        "Bên nhận từ chối tiếp nhận lô hàng kèm lý do cụ thể. "
        "Lô hàng vẫn thuộc quyền nắm giữ của bên giao và lý do từ chối được ghi tiếp vào chuỗi sự kiện."
    ),
)
def reject_handover(
    batch_id: int = Path(..., gt=0, description="Mã định danh lô hàng cần từ chối"),
    payload: HandoverRejectRequest = ...,
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> HandoverActionResponse:
    """Từ chối nhận bàn giao lô hàng."""
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô hàng có id={batch_id}.",
        )

    # Kiểm tra xem lô có đợt bàn giao nào đang chờ hay không
    if not batch.pending_receiver_org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lô hàng #{batch_id} hiện không có đợt bàn giao nào đang chờ để từ chối.",
        )

    # Kiểm tra phân quyền: người gọi / tổ chức của người gọi có đúng là bên nhận không
    if current_org != batch.pending_receiver_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Tổ chức của bạn ('{current_org}') không phải bên nhận được chỉ định "
                f"('{batch.pending_receiver_org}') của đợt bàn giao này."
            ),
        )

    # Lý do bắt buộc không để trống (Pydantic validator đã kiểm tra, bổ sung kiểm tra kép an toàn)
    reason_clean = (payload.reason or "").strip()
    if not reason_clean:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Lý do từ chối không được để trống.",
        )

    from_holder = batch.current_holder_org
    intended_receiver = batch.pending_receiver_org

    # Hủy đợt bàn giao: quyền giữ lô VẪN Ở BÊN GIAO, xóa pending_receiver_org
    batch.pending_receiver_org = None

    # Ghi nhận sự kiện HANDOVER_REJECTED vào chuỗi sự kiện bất biến
    last_event_stmt = (
        select(BatchEvent)
        .where(BatchEvent.batch_id == batch_id)
        .order_by(BatchEvent.id.desc())
        .limit(1)
    )
    last_event = db.scalars(last_event_stmt).first()
    prev_hash = last_event.hash if last_event else "0" * 64

    now_iso = datetime.now(timezone.utc).isoformat()
    event_payload_dict = {
        "action": "HANDOVER_REJECTED",
        "batch_id": batch_id,
        "from_organization": from_holder,
        "rejected_by_organization": intended_receiver,
        "reason": reason_clean,
        "status": "REJECTED",
    }
    event_payload_str = json.dumps(event_payload_dict, ensure_ascii=False)

    h = compute_event_hash(
        event_type="HANDOVER_REJECTED",
        payload=event_payload_str,
        actor=current_user.username,
        organization=intended_receiver,
        timestamp=now_iso,
        previous_hash=prev_hash,
    )
    reject_event = BatchEvent(
        batch_id=batch_id,
        event_type="HANDOVER_REJECTED",
        payload=event_payload_str,
        actor=current_user.username,
        organization=intended_receiver,
        timestamp=now_iso,
        hash=h,
        previous_hash=prev_hash,
    )
    db.add(reject_event)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi khi lưu giao dịch từ chối bàn giao.",
        ) from exc

    db.refresh(batch)
    db.refresh(reject_event)

    return HandoverActionResponse(
        batch_id=batch.id,
        current_holder_org=batch.current_holder_org,
        pending_receiver_org=batch.pending_receiver_org,
        status="REJECTED",
        message=f"Đã từ chối nhận lô #{batch.id}. Lô hàng vẫn ở chỗ bên giao ('{batch.current_holder_org}'). Lý do đã được ghi vào chuỗi sự kiện.",
        event_id=reject_event.id,
        event_hash=reject_event.hash,
    )


# ----------------------------------------------------------- Public Tracking Map (S-06) ---
def _build_public_tracking_map_response(batch: Batch, db: Session) -> PublicTrackingMapResponse:
    farm = db.get(Farm, batch.farm_id)
    farm_loc_str = farm.location if farm else "Đồng Tháp"
    farm_owner_str = farm.owner if farm else "Hợp tác xã nông nghiệp"

    # Toạ độ đại diện cấp xã/huyện (làm mờ toạ độ chi tiết để bảo vệ nông hộ)
    base_lat, base_lng = 10.4570, 105.6328

    code_str = batch.batch_code or f"LOT-{batch.id:04d}"

    origin = MapWaypoint(
        order=1,
        name=f"Vùng trồng: {farm_loc_str}",
        location_level="Cấp Xã / Huyện (Đã ẩn toạ độ thửa đất)",
        organization=farm_owner_str,
        action="Thu hoạch & Khởi tạo nguồn gốc VietGAP",
        latitude=round(base_lat, 3),
        longitude=round(base_lng, 3),
        timestamp=str(batch.harvest_date),
    )

    # Đọc chuỗi sự kiện để lấy các điểm dừng chính
    stmt_events = (
        select(BatchEvent)
        .where(BatchEvent.batch_id == batch.id)
        .order_by(BatchEvent.id.asc())
    )
    events = list(db.scalars(stmt_events).all())

    known_stops = [
        {"name": "Nhà máy sơ chế & làm mát Cai Lậy", "level": "Cấp Thị Xã Cai Lậy, Tiền Giang", "lat": 10.4080, "lng": 106.1200},
        {"name": "Trung tâm đóng gói & kiểm dịch Tân An", "level": "Cấp Thành Phố Tân An, Long An", "lat": 10.5360, "lng": 106.4130},
        {"name": "Tổng kho lạnh logistics Bình Điền", "level": "Cấp Quận 8, TP. Hồ Chí Minh", "lat": 10.7250, "lng": 106.6350},
        {"name": "Cảng xuất khẩu Cát Lái", "level": "Cấp Thành Phố Thủ Đức, TP. Hồ Chí Minh", "lat": 10.7600, "lng": 106.7900},
    ]

    waypoints: list[MapWaypoint] = []
    order_counter = 2

    for i, ev in enumerate(events):
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
        batch_code=code_str,
        product_name=batch.product_name,
        origin_point=origin,
        waypoints=waypoints,
        privacy_note="Toạ độ hiển thị ở cấp xã/huyện nhằm bảo vệ bí mật nông hộ và quyền riêng tư thửa đất.",
    )


@router.get(
    "/code/{code}/map",
    response_model=PublicTrackingMapResponse,
    status_code=status.HTTP_200_OK,
    summary="Bản đồ hành trình công khai của lô nông sản theo mã (S-06)",
    description="Hiển thị toạ độ đại diện cấp xã/huyện của vùng trồng xuất xứ và các điểm dừng chính.",
)
def get_batch_public_map_by_code(
    code: str = Path(..., description="Mã lô nông sản."),
    db: Session = Depends(get_db),
) -> PublicTrackingMapResponse:
    batch = db.scalar(select(Batch).where(or_(Batch.batch_code == code, cast(Batch.id, String) == code)))
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản với mã '{code}'.",
        )
    return _build_public_tracking_map_response(batch, db)


@router.get(
    "/{batch_id}/map",
    response_model=PublicTrackingMapResponse,
    status_code=status.HTTP_200_OK,
    summary="Bản đồ hành trình công khai của lô nông sản theo ID (S-06)",
    description="Hiển thị toạ độ đại diện cấp xã/huyện của vùng trồng xuất xứ và các điểm dừng chính.",
)
def get_batch_public_map_by_id(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản."),
    db: Session = Depends(get_db),
) -> PublicTrackingMapResponse:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )
    return _build_public_tracking_map_response(batch, db)
