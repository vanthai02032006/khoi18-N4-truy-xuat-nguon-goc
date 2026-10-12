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

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Batch, BatchEvent, BatchRelation, Farm, Organization, Product, User
from app.schemas import (
    BatchCreate,
    BatchDirectSummaryResponse,
    BatchMergeRequest,
    BatchMergeResponse,
    BatchResponse,
    BatchSplitRequest,
    BatchSplitResponse,
    BatchUpdate,
    DeleteResponse,
    DescendantBatchItem,
    DirectParentChildItem,
    HandoverActionResponse,
    HandoverInitiateRequest,
    HandoverRejectRequest,
    LineageGraphEdge,
    LineageGraphNode,
    LineageGraphResponse,
    MapWaypoint,
    OriginFarmItem,
    PublicTrackingMapResponse,
    TraceBackResponse,
    TraceForwardResponse,
)
from app.security import compute_event_hash, get_current_user, require_admin, require_farmer
from app.tenant import get_tenant_org, require_tenant_context
from app.lineage import (
    LineageCycleError,
    extract_lineage_relations_from_events,
    find_ancestors_bfs,
    find_descendants_bfs,
)

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
    _ = current_user  # bắt buộc khai báo để dependency kiểm tra quyền chạy

    # Bước 1: kiểm tra toàn vẹn tham chiếu - vùng trồng phải tồn tại.
    farm = db.get(Farm, payload.farm_id)
    if farm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vùng trồng có id={payload.farm_id}.",
        )

    # Bước 2: kiểm tra thửa đất có thuộc quyền quản lý của tổ chức hiện tại (SCRUM-36 / T-20).
    # Áp dụng đối với các trường hợp tổ chức cụ thể (trừ admin hệ thống hoặc tổ chức mặc định toàn quyền).
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
    batch = Batch(**batch_data, batch_code=code, current_holder_org=holder_org)
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

    # Tìm kiếm theo tên sản phẩm, mã lô (batch_code), hoặc ID (không phân biệt chữ thường chữ hoa)
    if search:
        import re
        search_term = search.strip()
        conditions = [
            Batch.product_name.ilike(f"%{search_term}%"),
            Batch.batch_code.ilike(f"%{search_term}%"),
            cast(Batch.id, String).ilike(f"%{search_term}%"),
        ]
        digits = re.findall(r"\d+", search_term)
        if digits:
            try:
                num_id = int(digits[-1])
                conditions.append(Batch.id == num_id)
                conditions.append(Batch.batch_code.ilike(f"LOT-%{num_id:04d}%"))
            except ValueError:
                pass
        stmt = stmt.where(or_(*conditions))

    # Phân trang con trỏ (keyset cursor)
    if cursor:
        stmt = stmt.where(Batch.id > cursor)

    stmt = stmt.order_by(Batch.id.asc()).limit(limit)
    batches_list = list(db.scalars(stmt).all())
    # Backfill batch_code nếu có bản ghi cũ chưa có mã
    for b in batches_list:
        if not b.batch_code:
            b.batch_code = f"LOT-{b.id:04d}"
    return batches_list


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
    """Lấy chi tiết một lô nông sản theo ``id``."""
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản có id={batch_id}.",
        )
    if not batch.batch_code:
        batch.batch_code = f"LOT-{batch.id:04d}"
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
        "Tự động trừ số lượng của lô mẹ và ghi nhận sự kiện SPLIT vào chuỗi bất biến."
    ),
)
def split_batch(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản mẹ cần tách."),
    payload: BatchSplitRequest = ...,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchSplitResponse:
    """Tách một lô nông sản mẹ thành danh sách các lô con (T-40)."""
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản mẹ có id={batch_id}.",
        )

    # 1. Tính tổng khối lượng các dòng lô con yêu cầu
    total_requested = round(sum(item.quantity for item in payload.items), 4)

    # 2. Kiểm tra nếu tổng vượt quá số dư của lô mẹ -> 400 Bad Request
    if total_requested > batch.quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Tổng khối lượng các lô con ({total_requested} kg) vượt quá "
                f"khối lượng còn lại của lô mẹ ({batch.quantity} kg)."
            ),
        )

    # 3. Trừ khối lượng của lô mẹ
    batch.quantity = round(batch.quantity - total_requested, 4)

    # 4. Tạo các lô con
    child_batches: list[Batch] = []
    for item in payload.items:
        child_code = generate_unique_batch_code(db)
        child = Batch(
            farm_id=batch.farm_id,
            product_name=batch.product_name,
            quantity=item.quantity,
            harvest_date=batch.harvest_date,
            batch_code=child_code,
            current_holder_org=batch.current_holder_org,
        )
        db.add(child)
        child_batches.append(child)

    # Commit tạm để lấy ID tự tăng của các lô con
    try:
        db.flush()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể tạo các lô con trong cơ sở dữ liệu.",
        ) from exc

    # 5. Ghi nhận sự kiện SPLIT vào chuỗi sự kiện bất biến của lô mẹ (SCRUM-39 / T-23 / T-40)
    # Lấy previous_hash của lô mẹ
    last_event_stmt = (
        select(BatchEvent)
        .where(BatchEvent.batch_id == batch_id)
        .order_by(BatchEvent.id.desc())
        .limit(1)
    )
    last_event = db.scalars(last_event_stmt).first()
    prev_hash = last_event.hash if last_event else "0" * 64

    now_iso = datetime.now(timezone.utc).isoformat()
    org_name = get_tenant_org()
    actor_name = current_user.username

    child_details = [
        {
            "id": c.id,
            "batch_code": c.batch_code or f"LOT-{c.id:04d}",
            "quantity": c.quantity,
        }
        for c in child_batches
    ]
    child_codes = [c["batch_code"] for c in child_details]

    event_payload_dict = {
        "action": "SPLIT",
        "parent_batch_id": batch_id,
        "parent_batch_code": batch.batch_code or f"LOT-{batch.id:04d}",
        "total_split_quantity": total_requested,
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

    # 6. Ghi nhận sự kiện khai sinh (BIRTH / SPLIT_CHILD) trên dòng thời gian của TỪNG lô con nêu mã lô mẹ
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

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi khi lưu giao dịch tách lô.",
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
    """Gộp nhiều lô thành một lô mới."""
    if len(payload.items) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cần ít nhất 2 lô để thực hiện thao tác gộp.",
        )

    # 1. Thu thập và kiểm tra tính hợp lệ của từng lô mẹ
    parent_batches: list[Batch] = []
    parent_contributions: list[dict] = []
    total_merged_qty = 0.0

    for item in payload.items:
        p_batch = db.get(Batch, item.batch_id)
        if not p_batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy lô mẹ có ID #{item.batch_id}.",
            )

        # Kiểm tra khối lượng đóng góp
        if item.quantity <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Khối lượng gộp từ lô #{item.batch_id} phải lớn hơn 0.",
            )

        if item.quantity > p_batch.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Khối lượng yêu cầu lấy từ lô #{item.batch_id} ({item.quantity} kg) "
                    f"vượt quá khối lượng còn lại của lô ({p_batch.quantity} kg)."
                ),
            )

        # Trừ khối lượng của lô mẹ
        p_batch.quantity = round(p_batch.quantity - item.quantity, 4)
        parent_batches.append(p_batch)
        total_merged_qty = round(total_merged_qty + item.quantity, 4)

        p_code = p_batch.batch_code or f"LOT-{p_batch.id:04d}"
        parent_contributions.append({
            "id": p_batch.id,
            "batch_code": p_code,
            "quantity": item.quantity,
            "remaining_quantity": p_batch.quantity,
        })

    # 2. Tạo lô gộp mới
    primary_parent = parent_batches[0]
    new_product_name = payload.product_name or primary_parent.product_name
    new_batch_code = generate_unique_batch_code(db, prefix="LOT-MERGE")

    merged_batch = Batch(
        farm_id=primary_parent.farm_id,
        product_name=new_product_name,
        quantity=total_merged_qty,
        harvest_date=primary_parent.harvest_date,
        batch_code=new_batch_code,
        current_holder_org=primary_parent.current_holder_org,
    )
    db.add(merged_batch)

    try:
        db.flush()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể tạo lô gộp mới trong cơ sở dữ liệu.",
        ) from exc

    now_iso = datetime.now(timezone.utc).isoformat()
    org_name = get_tenant_org()
    actor_name = current_user.username

    # 3. Ghi nhận sự kiện MERGE trên lô gộp mới (nêu đầy đủ các lô mẹ và khối lượng lấy từ mỗi lô)
    parent_codes_list = [pc["batch_code"] for pc in parent_contributions]
    merged_event_payload = {
        "action": "MERGE",
        "batch_id": merged_batch.id,
        "batch_code": new_batch_code,
        "total_merged_quantity": total_merged_qty,
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

    # 4. Ghi nhận sự kiện MERGE_PARENT vào dòng thời gian của TỪNG lô mẹ
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

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi khi lưu giao dịch gộp lô.",
        ) from exc

    db.refresh(merged_batch)
    for pb in parent_batches:
        db.refresh(pb)

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

    # Kiểm tra quyền: người gọi phải thuộc tổ chức đang nắm giữ lô hàng (trừ admin)
    effective_holder = batch.current_holder_org or "HTX Nông Nghiệp Số 4"
    if current_user.role != "admin" and current_org != effective_holder:
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


# =====================================================================
# SPRINT 2 / SPRINT 3: TỔNG HỢP CHI TIẾT & TRUY VẾT PHẢ HỆ
# (SCRUM-74, SCRUM-105, SCRUM-108, SCRUM-109)
# =====================================================================

def _get_all_lineage_relations(db: Session) -> list[dict]:
    """Tổng hợp tất cả các quan hệ cha - con trong hệ thống (từ BatchRelation, Batch.parent_id, và events)."""
    relations: list[dict] = []
    seen = set()

    batch_cache: dict[int, str] = {}

    def get_code(b_id: int) -> str:
        if b_id not in batch_cache:
            b = db.get(Batch, b_id)
            batch_cache[b_id] = (b.batch_code if b and b.batch_code else f"LOT-{b_id:04d}")
        return batch_cache[b_id]

    # 1. Từ bảng BatchRelation (quan hệ gộp nhiều-nhiều)
    br_stmt = select(
        BatchRelation.parent_batch_id,
        BatchRelation.child_batch_id,
        BatchRelation.used_quantity,
    )
    br_rows = db.execute(br_stmt).all()
    for p_id, c_id, qty in br_rows:
        p_code = get_code(p_id)
        c_code = get_code(c_id)
        key = (p_code, c_code)
        if key not in seen:
            seen.add(key)
            relations.append({
                "parent": p_code,
                "child": c_code,
                "type": "MERGE",
                "quantity": float(qty) if qty is not None else None,
            })

    # 2. Từ Batch.parent_id (quan hệ tách 1-cha -> nhiều-con)
    split_batches = db.execute(
        select(Batch.id, Batch.batch_code, Batch.parent_id, Batch.quantity)
        .where(Batch.parent_id.is_not(None))
    ).all()
    for b_id, b_code, p_id, qty in split_batches:
        p_code = get_code(p_id)
        c_code = b_code or f"LOT-{b_id:04d}"
        key = (p_code, c_code)
        if key not in seen:
            seen.add(key)
            relations.append({
                "parent": p_code,
                "child": c_code,
                "type": "SPLIT",
                "quantity": float(qty) if qty is not None else None,
            })

    # 3. Từ BatchEvent (các sự kiện SPLIT và MERGE trong lịch sử nếu chưa có ở 2 bảng trên)
    all_events = list(db.scalars(select(BatchEvent).order_by(BatchEvent.id.asc())).all())
    event_relations = extract_lineage_relations_from_events(all_events)
    for er in event_relations:
        key = (er["parent"], er["child"])
        if key not in seen:
            seen.add(key)
            relations.append(er)

    return relations


@router.get(
    "/{batch_id}/direct-summary",
    response_model=BatchDirectSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Truy vấn tổng hợp chi tiết lô kèm lô mẹ và lô con trực tiếp (SCRUM-74)",
    description="Gom thông tin lô, thửa, sản phẩm, tổ chức đang giữ và quan hệ trực tiếp 1 tầng (T-39). Kiểm quyền qua T-54. Tối đa 3 câu lệnh SQL, không tải toàn bộ dòng thời gian.",
)
def get_batch_direct_summary(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BatchDirectSummaryResponse:
    """Hàm tổng hợp thông tin nhanh, chỉ đếm số lượng sự kiện thay vì tải toàn bộ.

    Tối đa 3 câu lệnh SQL:
    - Câu 1: Lấy Batch JOIN Farm (lô + thửa + tổ chức).
    - Câu 2: Lấy các lô mẹ và lô con trực tiếp 1 tầng (T-39).
    - Câu 3: Đếm số lượng sự kiện (COUNT(*)).
    """
    from sqlalchemy import literal, union_all

    # -----------------------------------------------------------------
    # Câu SQL 1: Lấy thông tin Lô kèm Vùng trồng (Farm)
    # -----------------------------------------------------------------
    stmt = (
        select(
            Batch.id,
            Batch.batch_code,
            Batch.product_name,
            Batch.quantity,
            Batch.harvest_date,
            Batch.is_restricted,
            Batch.current_holder_org,
            Batch.current_owner,
            Batch.parent_id,
            Farm.id.label("farm_id"),
            Farm.name.label("farm_name"),
            Farm.location.label("farm_location"),
            Farm.area.label("farm_area"),
            Farm.owner.label("farm_owner"),
        )
        .join(Farm, Batch.farm_id == Farm.id)
        .where(Batch.id == batch_id)
    )
    b_row = db.execute(stmt).first()
    if not b_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )

    # Kiểm tra quyền qua T-54 (Cờ bảo mật is_restricted)
    if b_row.is_restricted and current_user.role not in ("admin", "inspector"):
        current_org = get_tenant_org()
        user_is_owner = current_user.username == (b_row.current_owner or "")
        org_matches = current_org == b_row.current_holder_org
        if not (user_is_owner or org_matches):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Lô nông sản này đang bị hạn chế bảo mật (T-54). Bạn không có quyền truy cập.",
            )

    # -----------------------------------------------------------------
    # Câu SQL 2: Lấy quan hệ mẹ và con trực tiếp (T-39 / 1 tầng, đúng 1 câu UNION ALL)
    # -----------------------------------------------------------------
    # 1. Lô mẹ từ gộp (BatchRelation)
    parents_from_merge = select(
        Batch.id,
        Batch.batch_code,
        Batch.product_name,
        Batch.quantity,
        BatchRelation.used_quantity.label("used_quantity"),
        literal("PARENT_MERGE").label("rel_role"),
    ).join(BatchRelation, Batch.id == BatchRelation.parent_batch_id).where(BatchRelation.child_batch_id == batch_id)

    # 2. Lô mẹ từ tách (Batch.parent_id)
    parents_from_split = select(
        Batch.id,
        Batch.batch_code,
        Batch.product_name,
        Batch.quantity,
        literal(None).label("used_quantity"),
        literal("PARENT_SPLIT").label("rel_role"),
    ).where(Batch.id == b_row.parent_id if b_row.parent_id else False)

    # 3. Lô con từ gộp (BatchRelation)
    children_from_merge = select(
        Batch.id,
        Batch.batch_code,
        Batch.product_name,
        Batch.quantity,
        BatchRelation.used_quantity.label("used_quantity"),
        literal("CHILD_MERGE").label("rel_role"),
    ).join(BatchRelation, Batch.id == BatchRelation.child_batch_id).where(BatchRelation.parent_batch_id == batch_id)

    # 4. Lô con từ tách (Batch.parent_id == batch_id)
    children_from_split = select(
        Batch.id,
        Batch.batch_code,
        Batch.product_name,
        Batch.quantity,
        literal(None).label("used_quantity"),
        literal("CHILD_SPLIT").label("rel_role"),
    ).where(Batch.parent_id == batch_id)

    union_stmt = union_all(
        parents_from_merge,
        parents_from_split,
        children_from_merge,
        children_from_split,
    )
    rel_rows = db.execute(union_stmt).all()

    direct_parents: list[DirectParentChildItem] = []
    direct_children: list[DirectParentChildItem] = []
    seen_parent_ids = set()
    seen_child_ids = set()

    for r in rel_rows:
        rb_id, rb_code, rb_pname, rb_qty, used_q, rel_role = r[0], r[1], r[2], r[3], r[4], r[5]
        code_str = rb_code or f"LOT-{rb_id:04d}"
        used_float = float(used_q) if used_q is not None else None

        if "PARENT" in rel_role and rb_id not in seen_parent_ids:
            seen_parent_ids.add(rb_id)
            direct_parents.append(
                DirectParentChildItem(
                    batch_id=rb_id,
                    batch_code=code_str,
                    product_name=rb_pname,
                    quantity=rb_qty,
                    used_quantity=used_float,
                    relation_type="MERGE" if "MERGE" in rel_role else "SPLIT",
                )
            )
        elif "CHILD" in rel_role and rb_id not in seen_child_ids:
            seen_child_ids.add(rb_id)
            direct_children.append(
                DirectParentChildItem(
                    batch_id=rb_id,
                    batch_code=code_str,
                    product_name=rb_pname,
                    quantity=rb_qty,
                    used_quantity=used_float,
                    relation_type="MERGE" if "MERGE" in rel_role else "SPLIT",
                )
            )

    # -----------------------------------------------------------------
    # Câu SQL 3: Chỉ đếm số lượng sự kiện, không tải toàn bộ dòng thời gian
    # -----------------------------------------------------------------
    events_count = db.scalar(
        select(func.count(BatchEvent.id)).where(BatchEvent.batch_id == batch_id)
    ) or 0

    batch_code = b_row.batch_code or f"LOT-{b_row.id:04d}"

    return BatchDirectSummaryResponse(
        id=b_row.id,
        batch_code=batch_code,
        product_name=b_row.product_name,
        quantity=b_row.quantity,
        harvest_date=str(b_row.harvest_date),
        is_restricted=b_row.is_restricted,
        current_holder_org=b_row.current_holder_org,
        current_owner=b_row.current_owner,
        farm={
            "id": b_row.farm_id,
            "name": b_row.farm_name,
            "location": b_row.farm_location,
            "area": b_row.farm_area,
            "owner": b_row.farm_owner,
            "plot_name": f"Thửa {b_row.farm_id} - {b_row.farm_name}",
        },
        product={
            "name": b_row.product_name,
            "unit": "kg",
            "description": f"Sản phẩm {b_row.product_name} thu hoạch tiêu chuẩn",
        },
        holder_org={
            "code": b_row.current_holder_org,
            "name": b_row.current_holder_org,
            "owner": b_row.current_owner or b_row.current_holder_org,
        },
        direct_parents=direct_parents,
        direct_children=direct_children,
        events_count=events_count,
    )


@router.get(
    "/{batch_id}/trace-back",
    response_model=TraceBackResponse,
    status_code=status.HTTP_200_OK,
    summary="Truy ngược nguồn gốc vùng trồng của lô nông sản (SCRUM-105)",
    description="Truy ngược xuất xứ đến các lô thu hoạch gốc kèm vùng trồng và thửa. Khớp đáp án mẫu qua nhiều tầng tách/gộp, tự dừng khi có chu trình.",
)
def get_batch_trace_back(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản."),
    db: Session = Depends(get_db),
) -> TraceBackResponse:
    """Truy ngược nguồn gốc vùng trồng (SCRUM-105):

    - Giả sử lô là kết quả gộp của hai lô đến từ hai vùng trồng khác nhau -> thấy cả hai lô thu hoạch gốc kèm tên vùng trồng và thửa.
    - Giả sử lô đi qua ba tầng tách và gộp -> kết quả khớp đáp án đếm tay.
    - Giả sử lô chưa từng tách hay gộp -> trả về chính nó.
    - Giả sử dữ liệu lỗi tạo ra chu trình cha-con -> dừng và báo lỗi thay vì lặp vô hạn.
    """
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )
    b_code = batch.batch_code or f"LOT-{batch.id:04d}"

    relations = _get_all_lineage_relations(db)

    # Chạy BFS truy ngược tổ tiên
    try:
        ancestor_codes = find_ancestors_bfs(b_code, relations)
    except LineageCycleError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phát hiện chu trình phả hệ cha-con: Dữ liệu vòng lặp không hợp lệ ({str(e)}). Đã dừng truy ngược để chống lặp vô hạn.",
        )

    # Ca: Lô chưa từng tách hay gộp -> trả về chính nó
    if not ancestor_codes:
        farm = batch.farm
        origin_farm = OriginFarmItem(
            batch_id=batch.id,
            batch_code=b_code,
            product_name=batch.product_name,
            harvest_date=str(batch.harvest_date),
            initial_quantity=batch.quantity,
            farm_id=farm.id if farm else 0,
            farm_name=farm.name if farm else "—",
            farm_location=farm.location if farm else "—",
            farm_area=farm.area if farm else 0.0,
            farm_owner=farm.owner if farm else "—",
            plot_name=f"Thửa {farm.id if farm else 0} - {farm.name if farm else 'Vùng trồng gốc'}",
        )
        return TraceBackResponse(
            target_batch_id=batch.id,
            target_batch_code=b_code,
            target_product_name=batch.product_name,
            target_quantity=batch.quantity,
            is_standalone=True,
            depth=0,
            all_ancestor_codes=[],
            origin_farms=[origin_farm],
        )

    # Tìm các lô gốc thu hoạch (root harvest batches):
    # Những lô trong ancestor_codes mà không có cha nào trong relations
    parent_map: dict[str, list[str]] = {}
    for r in relations:
        parent_map.setdefault(r["child"], []).append(r["parent"])

    root_codes = []
    for code in ancestor_codes:
        if not parent_map.get(code):
            root_codes.append(code)

    if not root_codes:
        root_codes = ancestor_codes[-1:]

    origin_farms: list[OriginFarmItem] = []
    for r_code in root_codes:
        r_batch = db.scalar(select(Batch).where(or_(Batch.batch_code == r_code, cast(Batch.id, String) == r_code)))
        if r_batch:
            farm = r_batch.farm
            origin_farms.append(
                OriginFarmItem(
                    batch_id=r_batch.id,
                    batch_code=r_code,
                    product_name=r_batch.product_name,
                    harvest_date=str(r_batch.harvest_date),
                    initial_quantity=r_batch.quantity,
                    farm_id=farm.id if farm else 0,
                    farm_name=farm.name if farm else "—",
                    farm_location=farm.location if farm else "—",
                    farm_area=farm.area if farm else 0.0,
                    farm_owner=farm.owner if farm else "—",
                    plot_name=f"Thửa {farm.id if farm else 0} - {farm.name if farm else 'Vùng trồng gốc'}",
                )
            )

    return TraceBackResponse(
        target_batch_id=batch.id,
        target_batch_code=b_code,
        target_product_name=batch.product_name,
        target_quantity=batch.quantity,
        is_standalone=False,
        depth=len(ancestor_codes),
        all_ancestor_codes=ancestor_codes,
        origin_farms=origin_farms,
    )


@router.get(
    "/{batch_id}/trace-forward",
    response_model=TraceForwardResponse,
    status_code=status.HTTP_200_OK,
    summary="Truy xuôi phả hệ nói rõ từng lô đang ở đâu và còn bao nhiêu (SCRUM-108)",
    description="Cán bộ kiểm tra xem danh sách các lô hậu duệ, tổ chức đang giữ, địa điểm và khối lượng còn lại. Hậu duệ đã bán hết hiện trạng thái Đã tiêu thụ và khối lượng 0.",
)
def get_batch_trace_forward(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản."),
    db: Session = Depends(get_db),
) -> TraceForwardResponse:
    """Truy xuôi nguồn gốc hậu duệ (SCRUM-108):

    - Mỗi lô kèm tổ chức đang giữ, địa điểm và khối lượng còn lại.
    - Hậu duệ đã bán hết hiện trạng thái 'Đã tiêu thụ' và khối lượng còn lại 0.
    """
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )
    b_code = batch.batch_code or f"LOT-{batch.id:04d}"

    relations = _get_all_lineage_relations(db)

    # Chạy BFS tìm hậu duệ
    try:
        descendant_codes = find_descendants_bfs(b_code, relations)
    except Exception:
        descendant_codes = []

    # Xây dựng bản đồ độ sâu tầng hậu duệ
    child_map: dict[str, list[str]] = {}
    for r in relations:
        child_map.setdefault(r["parent"], []).append(r["child"])

    depth_map: dict[str, int] = {}
    from collections import deque
    q = deque([(b_code, 0)])
    while q:
        curr, d = q.popleft()
        for ch in child_map.get(curr, []):
            if ch not in depth_map:
                depth_map[ch] = d + 1
                q.append((ch, d + 1))

    descendant_items: list[DescendantBatchItem] = []
    for d_code in descendant_codes:
        d_batch = db.scalar(select(Batch).where(or_(Batch.batch_code == d_code, cast(Batch.id, String) == d_code)))
        if not d_batch:
            continue

        is_consumed = d_batch.quantity <= 0
        latest_event = db.scalar(
            select(BatchEvent)
            .where(BatchEvent.batch_id == d_batch.id)
            .order_by(BatchEvent.id.desc())
        )
        if latest_event and latest_event.event_type in ("CONSUMED", "SOLD", "EXHAUSTED", "RETAIL_SOLD"):
            is_consumed = True

        remaining_qty = 0.0 if is_consumed else round(float(d_batch.quantity), 2)
        status_code = "CONSUMED" if is_consumed else "IN_STOCK"
        status_label = "Đã tiêu thụ" if is_consumed else "Còn hàng (Lưu kho)"

        location_str = "Kho trung chuyển"
        if latest_event:
            try:
                ev_p = json.loads(latest_event.payload) if isinstance(latest_event.payload, str) else latest_event.payload
                location_str = ev_p.get("location") or ev_p.get("store") or ev_p.get("destination") or location_str
            except Exception:
                pass
        if location_str == "Kho trung chuyển" and d_batch.farm:
            location_str = f"Kho tại {d_batch.farm.location}"

        descendant_items.append(
            DescendantBatchItem(
                batch_id=d_batch.id,
                batch_code=d_code,
                product_name=d_batch.product_name,
                current_holder_org=d_batch.current_holder_org,
                location=location_str,
                remaining_quantity=remaining_qty,
                status=status_code,
                status_label=status_label,
                depth=depth_map.get(d_code, 1),
            )
        )

    return TraceForwardResponse(
        target_batch_id=batch.id,
        target_batch_code=b_code,
        target_product_name=batch.product_name,
        total_descendants=len(descendant_items),
        descendants=descendant_items,
    )


@router.get(
    "/{batch_id}/lineage-graph",
    response_model=LineageGraphResponse,
    status_code=status.HTTP_200_OK,
    summary="Dữ liệu sơ đồ phả hệ (Trái - Giữa - Phải) cho lô nông sản (SCRUM-109)",
    description="Cung cấp node và edge với lô hiện tại ở giữa (level=0), tổ tiên bên trái (level < 0), hậu duệ bên phải (level > 0).",
)
def get_batch_lineage_graph(
    batch_id: int = Path(..., ge=1, description="ID lô nông sản."),
    db: Session = Depends(get_db),
) -> LineageGraphResponse:
    """Sơ đồ phả hệ trực quan (SCRUM-109):

    - Lô hiện tại ở giữa (level = 0, group = 'current').
    - Tổ tiên bên trái (level < 0, group = 'ancestor').
    - Hậu duệ bên phải (level > 0, group = 'descendant').
    """
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản #{batch_id}.",
        )
    b_code = batch.batch_code or f"LOT-{batch.id:04d}"

    relations = _get_all_lineage_relations(db)

    # 1. Tìm tổ tiên
    try:
        ancestors = find_ancestors_bfs(b_code, relations)
    except Exception:
        ancestors = []

    # 2. Tìm hậu duệ
    try:
        descendants = find_descendants_bfs(b_code, relations)
    except Exception:
        descendants = []

    all_involved_codes = set(ancestors) | {b_code} | set(descendants)

    level_map = {b_code: 0}

    parent_map: dict[str, list[str]] = {}
    child_map: dict[str, list[str]] = {}
    for r in relations:
        p, c = r["parent"], r["child"]
        parent_map.setdefault(c, []).append(p)
        child_map.setdefault(p, []).append(c)

    from collections import deque
    q_anc = deque([(b_code, 0)])
    visited_anc = {b_code}
    while q_anc:
        curr, lvl = q_anc.popleft()
        for p in parent_map.get(curr, []):
            if p not in visited_anc:
                visited_anc.add(p)
                level_map[p] = lvl - 1
                q_anc.append((p, lvl - 1))

    q_desc = deque([(b_code, 0)])
    visited_desc = {b_code}
    while q_desc:
        curr, lvl = q_desc.popleft()
        for c in child_map.get(curr, []):
            if c not in visited_desc:
                visited_desc.add(c)
                level_map[c] = lvl + 1
                q_desc.append((c, lvl + 1))

    nodes: list[LineageGraphNode] = []
    for code in all_involved_codes:
        b = db.scalar(select(Batch).where(or_(Batch.batch_code == code, cast(Batch.id, String) == code)))
        lvl = level_map.get(code, 0)
        group = "current" if lvl == 0 else ("ancestor" if lvl < 0 else "descendant")

        status_str = "Đã tiêu thụ" if (b and b.quantity <= 0) else "Còn hàng"
        nodes.append(
            LineageGraphNode(
                id=code,
                batch_id=b.id if b else 0,
                label=f"{code}\n({b.product_name if b else 'Lô'} - {b.quantity if b else 0}kg)",
                group=group,
                level=lvl,
                product_name=b.product_name if b else "Nông sản",
                quantity=b.quantity if b else 0.0,
                holder_org=b.current_holder_org if b else "HTX Nông Nghiệp",
                status=status_str,
                farm_name=b.farm.name if (b and b.farm) else None,
            )
        )

    edges: list[LineageGraphEdge] = []
    seen_edges = set()
    for r in relations:
        p, c = r["parent"], r["child"]
        if p in all_involved_codes and c in all_involved_codes:
            edge_key = (p, c)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append(
                    LineageGraphEdge(
                        from_node=p,
                        to_node=c,
                        label=r.get("type", "DIRECT"),
                        quantity=r.get("quantity"),
                    )
                )

    return LineageGraphResponse(
        target_batch_code=b_code,
        target_batch_id=batch.id,
        nodes=nodes,
        edges=edges,
    )


