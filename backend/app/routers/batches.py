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

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.batch_split import split_batch
from app.cache import trace_cache
from app.database import get_db
from app.event_chain import (
    IntegrityVerificationReport,
    build_10_events_for_batch,
    record_batch_event,
    verify_batch_events_integrity,
)
from app.models import Batch, BatchEvent, Farm, User
from app.schemas import (
    BatchCreate,
    BatchEventCreate,
    BatchEventResponse,
    BatchEventVerifyResponse,
    BatchLineageTier,
    BatchResponse,
    BatchSplitRequest,
    BatchSplitResponse,
    BatchTraceResponse,
    BatchUpdate,
    DeleteResponse,
    OriginFarmInfo,
    RootBatchInfo,
)
from app.security import (
    check_batch_view_permission,
    get_current_user_optional,
    require_admin,
    require_farmer,
)

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

    # Bước 1.1: kiểm tra lô cha nếu có khai báo parent_id (T-49)
    if payload.parent_id is not None:
        parent_batch = db.get(Batch, payload.parent_id)
        if parent_batch is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy lô nông sản cha có id={payload.parent_id}.",
            )

    # Bước 2: tự động sinh mã lô theo chuẩn T-19 nếu chưa có
    from app.batch_split import generate_batch_code

    batch_data = payload.model_dump()
    if not batch_data.get("batch_code"):
        count_same_day = (
            db.query(Batch)
            .filter(
                Batch.farm_id == payload.farm_id,
                Batch.harvest_date == payload.harvest_date,
            )
            .count()
        )
        batch_data["batch_code"] = generate_batch_code(
            farm_id=payload.farm_id,
            harvest_date=payload.harvest_date,
            parent_id=payload.parent_id,
            sequence=count_same_day + 1,
        )

    # Bước 3: lưu lô nông sản.
    batch = Batch(**batch_data)
    db.add(batch)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể lưu lô nông sản vào cơ sở dữ liệu.",
        ) from exc

    db.refresh(batch)
    trace_cache.invalidate()
    return batch


@router.get(
    "",
    response_model=list[BatchResponse],
    status_code=status.HTTP_200_OK,
    summary="Lấy danh sách lô nông sản",
    description="Trả về toàn bộ lô nông sản, sắp xếp theo `id` tăng dần.",
)
def list_batches(db: Session = Depends(get_db)) -> list[Batch]:
    """Lấy danh sách lô nông sản.

    Args:
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        list[Batch]: Danh sách lô (rỗng nếu chưa có dữ liệu).
    """
    return list(db.scalars(select(Batch).order_by(Batch.id)).all())


@router.get(
    "/{batch_id}",
    response_model=BatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Xem chi tiết một lô nông sản",
    description=(
        "Trả về thông tin chi tiết của lô theo `id`.\n\n"
        "**Phân quyền (T-54):** nếu lô ở chế độ bảo mật và người gọi không có quyền xem, "
        "API trả về mã lỗi `403 Forbidden`."
    ),
    responses={
        status.HTTP_403_FORBIDDEN: {
            "description": "Không có quyền xem lô nông sản này (T-54).",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Không tìm thấy lô nông sản.",
        },
    },
)
def get_batch(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần xem."),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> Batch:
    """Lấy chi tiết một lô nông sản theo ``id``.

    Args:
        batch_id: ID của lô cần tìm.
        current_user: Người dùng hiện tại (nếu có đăng nhập).
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Batch: Bản ghi lô tương ứng (HTTP 200).

    Raises:
        HTTPException: 403 nếu không có quyền xem theo T-54;
            404 nếu không tìm thấy lô.
    """
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )

    # Kiểm tra quyền xem theo T-54
    check_batch_view_permission(batch, current_user)
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
    """Cập nhật thông tin lô nông sản theo ``id``."""
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

    # Kiểm tra lô cha nếu có khai báo parent_id
    if payload.parent_id is not None:
        if payload.parent_id == batch_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Lô nông sản không thể làm cha của chính nó trong phả hệ.",
            )
        parent_batch = db.get(Batch, payload.parent_id)
        if parent_batch is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy lô nông sản cha có id={payload.parent_id}.",
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
    trace_cache.invalidate(batch_id)
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
    """Xoá một lô nông sản (chỉ admin)."""
    _ = current_user

    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )

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

    trace_cache.invalidate(batch_id)
    return DeleteResponse(
        message=f"Đã xoá lô nông sản #{batch_id} ({product_name}).",
        deleted_id=batch_id,
        deleted_batches=None,
    )


# =========================================================================
# ENDPOINTS TRUY VẾT NGUỒN GỐC & PHẢ HỆ (T-49 / SCRUM-65 & T-54)
# =========================================================================
@router.get(
    "/{batch_id}/trace",
    response_model=BatchTraceResponse,
    status_code=status.HTTP_200_OK,
    summary="Truy vết nguồn gốc và phả hệ lô nông sản (T-49 & T-54)",
    description=(
        "**Endpoint nghiệp vụ T-49 & T-54:**\n\n"
        "- Trả về kết quả truy vết nguồn gốc của lô nông sản (T-49) kèm "
        "thông tin vùng trồng của lô gốc.\n"
        "- Danh sách phả hệ được hiển thị theo từng tầng (Tầng 1: Lô gốc -> "
        "Tầng N: Lô hiện tại).\n"
        "- **Phân quyền theo T-54:** Nếu lô không có quyền xem, trả về mã lỗi `403 Forbidden`.\n"
        "- **Lưu ý kỹ thuật:** Kết quả truy vết được cache theo lô trong 60 giây "
        "vì lịch sử phả hệ không thay đổi."
    ),
    responses={
        status.HTTP_403_FORBIDDEN: {
            "description": "Lô không có quyền xem bị trả về mã lỗi 403 Forbidden (T-54).",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Không tìm thấy lô nông sản.",
        },
    },
)
def trace_batch(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản cần truy vết phả hệ."),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> BatchTraceResponse:
    """Truy vết nguồn gốc và phả hệ lô nông sản (T-49 & T-54).

    Args:
        batch_id: ID lô nông sản cần truy vết.
        current_user: Tài khoản đang gọi API (nếu có xác thực).
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        BatchTraceResponse: Kết quả truy vết gồm lô gốc, vùng trồng gốc và danh sách theo tầng.

    Raises:
        HTTPException: 403 nếu lô không có quyền xem theo T-54;
            404 nếu không tìm thấy lô hoặc vùng trồng.
    """
    # 1. Kiểm tra sự tồn tại của lô
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )

    # 2. Kiểm tra quyền xem theo T-54 (BẮT BUỘC thực thi trước khi lấy cache)
    check_batch_view_permission(batch, current_user)

    # 3. Kiểm tra cache trong 60 giây
    cached_entry = trace_cache.get(batch_id)
    if cached_entry is not None:
        cached_data, remaining_seconds = cached_entry
        # Tạo bản sao cập nhật trạng thái cached và số giây còn lại
        return cached_data.model_copy(
            update={
                "cached": True,
                "cache_remaining_seconds": remaining_seconds,
            }
        )

    # 4. Duyệt chuỗi phả hệ ngược dòng từ lô hiện tại lên lô gốc
    chain: list[Batch] = [batch]
    visited: set[int] = {batch.id}
    curr = batch

    while curr.parent_id is not None:
        parent = db.get(Batch, curr.parent_id)
        if parent is None or parent.id in visited:
            break
        # Kiểm tra quyền xem các lô tổ tiên theo T-54
        check_batch_view_permission(parent, current_user)
        chain.append(parent)
        visited.add(parent.id)
        curr = parent

    root_batch_obj = chain[-1]
    root_farm_obj = root_batch_obj.farm or db.get(Farm, root_batch_obj.farm_id)
    if root_farm_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vùng trồng của lô gốc #{root_batch_obj.id}.",
        )

    # 5. Xây dựng danh sách theo tầng (Level 1: Lô gốc -> Level N: Lô hiện tại)
    chain_from_root = list(reversed(chain))
    total_tiers = len(chain_from_root)
    lineage_tiers: list[BatchLineageTier] = []

    for idx, b in enumerate(chain_from_root):
        level = idx + 1
        if idx == 0:
            tier_name = "Tầng 1 (Lô gốc)"
        elif idx == total_tiers - 1:
            tier_name = f"Tầng {level} (Lô hiện tại)"
        else:
            tier_name = f"Tầng {level} (Lô trung gian)"

        farm_obj = b.farm or db.get(Farm, b.farm_id)
        farm_name = farm_obj.name if farm_obj else None

        lineage_tiers.append(
            BatchLineageTier(
                level=level,
                tier_name=tier_name,
                batch_id=b.id,
                product_name=b.product_name,
                quantity=b.quantity,
                harvest_date=b.harvest_date,
                farm_id=b.farm_id,
                farm_name=farm_name,
                parent_id=b.parent_id,
                is_root=(idx == 0),
                is_current=(b.id == batch.id),
            )
        )

    current_farm = batch.farm or db.get(Farm, batch.farm_id)
    response_data = BatchTraceResponse(
        batch_id=batch.id,
        product_name=batch.product_name,
        quantity=batch.quantity,
        harvest_date=batch.harvest_date,
        farm_id=batch.farm_id,
        farm_name=current_farm.name if current_farm else None,
        root_batch=RootBatchInfo(
            id=root_batch_obj.id,
            farm_id=root_batch_obj.farm_id,
            product_name=root_batch_obj.product_name,
            quantity=root_batch_obj.quantity,
            harvest_date=root_batch_obj.harvest_date,
        ),
        origin_farm=OriginFarmInfo(
            id=root_farm_obj.id,
            name=root_farm_obj.name,
            location=root_farm_obj.location,
            area=root_farm_obj.area,
            owner=root_farm_obj.owner,
        ),
        lineage=lineage_tiers,
        cached=False,
        cache_ttl_seconds=trace_cache.ttl_seconds,
        cache_remaining_seconds=trace_cache.ttl_seconds,
    )

    # 6. Lưu kết quả vào cache 60s
    trace_cache.set(batch_id, response_data)

    return response_data


@router.get(
    "/{batch_id}/lineage",
    response_model=BatchTraceResponse,
    include_in_schema=False,
)
def get_batch_lineage(
    batch_id: int = Path(..., ge=1),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> BatchTraceResponse:
    """Route bí danh (alias) trỏ về trace_batch."""
    return trace_batch(batch_id, current_user, db)


@router.get(
    "/{batch_id}/origin",
    response_model=BatchTraceResponse,
    include_in_schema=False,
)
def get_batch_origin(
    batch_id: int = Path(..., ge=1),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> BatchTraceResponse:
    """Route bí danh (alias) trỏ về trace_batch."""
    return trace_batch(batch_id, current_user, db)


# ----------------------------------------------------------------- Tách Lô (S-17 / T-39) ---
@router.post(
    "/{batch_id}/split",
    response_model=BatchSplitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tách lô nông sản (S-17 / T-39 / SCRUM-55)",
    description=(
        "Hàm tách nhận lô mẹ và danh sách khối lượng lô con:\n"
        "- Mở transaction bảo đảm tính toàn vẹn (atomic transaction).\n"
        "- Trừ khối lượng còn lại của lô mẹ.\n"
        "- Tạo từng lô con bằng hàm sinh mã ở T-19.\n"
        "- Ghi nhận quan hệ phả hệ ở T-39 (`parent_id = parent_batch.id`).\n"
        "- **Ràng buộc kỹ thuật:** Lô con kế thừa loại sản phẩm và nguồn gốc của lô mẹ, "
        "không cho phép nhập sai lệch.\n"
        "- Nếu có lỗi phát sinh (kể cả ở lô con thứ hai), toàn bộ giao dịch được rollback sạch sẽ.\n\n"
        "**Phân quyền:** yêu cầu tài khoản `farmer` hoặc `admin`."
    ),
)
def split_batch_endpoint(
    batch_id: int = Path(..., ge=1, description="ID của lô nông sản mẹ cần tách"),
    payload: BatchSplitRequest = ...,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchSplitResponse:
    # 1. Kiểm tra lô mẹ tồn tại
    parent_batch = db.get(Batch, batch_id)
    if parent_batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản mẹ #{batch_id}.",
        )

    # 2. Kiểm tra quyền xem/tách theo chính sách T-54
    check_batch_view_permission(parent_batch, current_user)

    # 3. Thực thi nghiệp vụ tách lô trong transaction
    try:
        updated_parent, created_children = split_batch(
            db=db,
            parent_batch_id=batch_id,
            child_quantities=payload.child_quantities,
            operator_user=current_user,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Giao dịch tách lô thất bại và đã được rollback sạch sẽ: {str(exc)}",
        )

    total_split = round(sum(payload.child_quantities), 4)
    return BatchSplitResponse(
        message=f"Đã tách thành công {len(created_children)} lô con từ lô mẹ #{batch_id}.",
        parent_batch=BatchResponse.model_validate(updated_parent),
        child_batches=[BatchResponse.model_validate(c) for c in created_children],
        total_split_quantity=total_split,
        remaining_quantity=updated_parent.quantity,
    )


# ==============================================================================
# SỰ KIỆN CHUỖI CUNG ỨNG & CHỐNG SỬA LÉN (T-29 / SCRUM-45)
# ==============================================================================


@router.post(
    "/{batch_id}/events",
    response_model=BatchEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ghi nhận sự kiện chuỗi cung ứng (T-29)",
    description=(
        "Ghi nhận một sự kiện vào chuỗi sự kiện của lô nông sản. "
        "Hệ thống tự động liên kết `prev_hash` và băm SHA-256 theo chuẩn T-29."
    ),
)
def add_batch_event_endpoint(
    batch_id: int = Path(..., ge=1, description="ID của lô nông sản"),
    payload: BatchEventCreate = ...,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchEventResponse:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )
    check_batch_view_permission(batch, current_user)

    event = record_batch_event(
        db=db,
        batch_id=batch_id,
        event_type=payload.event_type,
        data=payload.data,
        timestamp=payload.timestamp,
    )
    return BatchEventResponse.model_validate(event)


@router.get(
    "/{batch_id}/events",
    response_model=list[BatchEventResponse],
    status_code=status.HTTP_200_OK,
    summary="Lấy danh sách sự kiện của lô (T-29)",
    description="Trả về danh sách sự kiện theo thứ tự tăng dần kèm mã băm SHA-256 chuỗi.",
)
def list_batch_events_endpoint(
    batch_id: int = Path(..., ge=1, description="ID của lô nông sản"),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> list[BatchEventResponse]:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )
    check_batch_view_permission(batch, current_user)

    events = (
        db.query(BatchEvent)
        .filter(BatchEvent.batch_id == batch_id)
        .order_by(BatchEvent.sequence.asc(), BatchEvent.id.asc())
        .all()
    )
    return [BatchEventResponse.model_validate(e) for e in events]


@router.get(
    "/{batch_id}/events/verify",
    response_model=BatchEventVerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Kiểm tra tính toàn vẹn & chống sửa lén chuỗi sự kiện (T-29)",
    description=(
        "Quét toàn bộ chuỗi sự kiện của lô: đối chiếu prev_hash liên kết và "
        "tính toán lại hash nội dung. Phát hiện 100% nếu có sửa lén (SQL UPDATE) "
        "hoặc xoá bản ghi (SQL DELETE)."
    ),
)
def verify_batch_events_endpoint(
    batch_id: int = Path(..., ge=1, description="ID của lô nông sản"),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> BatchEventVerifyResponse:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )
    check_batch_view_permission(batch, current_user)

    report = verify_batch_events_integrity(db=db, batch_id=batch_id)
    return BatchEventVerifyResponse(**report.model_dump())


