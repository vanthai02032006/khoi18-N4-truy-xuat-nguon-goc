from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Batch, BatchEvent, Organization, User
from app.schemas import BatchEventCreate, BatchEventResponse, BatchTimelineResponse
from app.security import compute_event_hash, get_current_user, require_farmer
from app.tenant import scope_query_by_tenant

router = APIRouter(prefix="/batches", tags=["Chuỗi sự kiện & Truy xuất (Batch Events)"])


@router.post(
    "/{batch_id}/events",
    response_model=BatchEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ghi thêm sự kiện vào chuỗi bản ghi lô hàng (Append-only)",
)
def record_batch_event(
    batch_id: int,
    data: BatchEventCreate,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchEvent:
    """Ghi thêm một sự kiện mới vào lô hàng.

    Tự động lấy hash của sự kiện trước đó làm previous_hash và sinh mã hash SHA-256
    mới bảo vệ tính toàn vẹn.
    """
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản với ID #{batch_id}.",
        )

    # Lấy sự kiện cuối cùng để lấy previous_hash
    stmt = (
        select(BatchEvent)
        .where(BatchEvent.batch_id == batch_id)
        .order_by(BatchEvent.id.desc())
        .limit(1)
    )
    last_event = db.scalars(stmt).first()
    prev_hash = last_event.hash if last_event else "0" * 64

    now_iso = datetime.now(timezone.utc).isoformat()
    actor_name = current_user.username
    event_hash = compute_event_hash(
        event_type=data.event_type,
        payload=data.payload,
        actor=actor_name,
        organization=data.organization,
        timestamp=now_iso,
        previous_hash=prev_hash,
    )

    new_event = BatchEvent(
        batch_id=batch_id,
        event_type=data.event_type,
        payload=data.payload,
        actor=actor_name,
        organization=data.organization,
        timestamp=now_iso,
        hash=event_hash,
        previous_hash=prev_hash,
    )
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    return new_event


@router.get(
    "/{batch_id}/events",
    response_model=BatchTimelineResponse,
    summary="Lấy dòng thời gian sự kiện kèm tên tổ chức trong 1 lượt (Audit Chain)",
)
def get_batch_timeline(
    batch_id: int,
    limit: Optional[int] = Query(None, ge=1, description="Số lượng sự kiện tối đa trên mỗi trang (Hỗ trợ phân trang khi > 500 sự kiện)"),
    offset: int = Query(0, ge=0, description="Vị trí bắt đầu lấy sự kiện"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BatchTimelineResponse:
    """Truy xuất toàn bộ chuỗi sự kiện của lô hàng kèm tên tổ chức trong 1 lượt truy vấn (JOIN).

    Không dùng truy vấn con (subquery) hay N+1 query.
    Tận dụng composite index (batch_id, timestamp, id) ở T-23.
    Lọc theo quyền xem qua tenant scope ở T-12.
    Tự động áp dụng phân trang (mặc định 500) khi lô hàng có trên 500 sự kiện.
    """
    # 1. Câu truy vấn duy nhất nối bảng batch_events và organizations
    # Ghép theo BatchEvent.org_id == Organization.id hoặc Organization.name / code
    join_condition = or_(
        BatchEvent.org_id == Organization.id,
        BatchEvent.organization == Organization.code,
        BatchEvent.organization == Organization.name,
    )

    stmt = (
        select(BatchEvent, Organization.name.label("org_full_name"))
        .outerjoin(Organization, join_condition)
        .where(BatchEvent.batch_id == batch_id)
    )

    # 2. Lọc theo quyền xem qua hàm phân quyền tổ chức ở T-12 (Data isolation)
    # Riêng tài khoản admin và cán bộ kiểm tra (inspector) có thẩm quyền giám sát toàn bộ chuỗi
    if current_user.role not in ("admin", "inspector"):
        stmt = scope_query_by_tenant(stmt, BatchEvent)

    # 3. Dùng chỉ mục ở T-23: sắp xếp theo đúng thứ tự thời gian (timestamp.asc(), id.asc())
    stmt = stmt.order_by(BatchEvent.timestamp.asc(), BatchEvent.id.asc())

    # 4. Phân trang: nếu truyền limit rõ ràng, áp dụng limit/offset
    if limit is not None:
        stmt = stmt.offset(offset).limit(limit)

    # Thực thi đúng 1 câu lệnh SQL duy nhất
    rows = db.execute(stmt).all()

    # Chuyển đổi kết quả sang BatchEventResponse kèm organization_name
    events_response: list[BatchEventResponse] = []
    is_valid = True
    tampered_index: Optional[int] = None
    expected_prev_hash = "0" * 64

    for idx, row in enumerate(rows):
        ev: BatchEvent = row[0]
        org_name: Optional[str] = row[1] if row[1] else ev.organization

        # Quét tính toàn vẹn của chuỗi hash (SCRUM-44)
        if is_valid:
            # Nếu đang ở trang đầu (idx == 0 và offset == 0), previous_hash kỳ vọng là 0*64
            # Với trang tiếp theo (offset > 0), liên kết hash được kiểm tra giữa các phần tử liền kề
            if idx == 0 and offset == 0:
                if ev.previous_hash != expected_prev_hash:
                    is_valid = False
                    tampered_index = idx
            elif idx > 0:
                if ev.previous_hash != expected_prev_hash:
                    is_valid = False
                    tampered_index = offset + idx

            if is_valid:
                recomputed = compute_event_hash(
                    event_type=ev.event_type,
                    payload=ev.payload,
                    actor=ev.actor,
                    organization=ev.organization,
                    timestamp=ev.timestamp,
                    previous_hash=ev.previous_hash,
                )
                if recomputed != ev.hash:
                    is_valid = False
                    tampered_index = offset + idx

            expected_prev_hash = ev.hash

        events_response.append(
            BatchEventResponse(
                id=ev.id,
                batch_id=ev.batch_id,
                event_type=ev.event_type,
                payload=ev.payload,
                actor=ev.actor,
                organization=ev.organization,
                organization_name=org_name,
                timestamp=ev.timestamp,
                hash=ev.hash,
                previous_hash=ev.previous_hash,
            )
        )

    # Hỗ trợ phân trang mặc định khi lô có trên 500 sự kiện và chưa truyền limit
    total_returned = len(events_response)
    effective_limit = limit
    if limit is None and total_returned > 500:
        effective_limit = 500
        events_response = events_response[:500]

    return BatchTimelineResponse(
        batch_id=batch_id,
        is_valid=is_valid,
        tampered_index=tampered_index,
        total=total_returned,
        limit=effective_limit,
        offset=offset,
        events=events_response,
    )


@router.get(
    "/{batch_id}/export-pdf",
    summary="Xuất hồ sơ truy xuất nguồn gốc ra tệp PDF phục vụ cán bộ kiểm tra",
)
def export_traceability_dossier_pdf(
    batch_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Xuất hồ sơ truy xuất đầy đủ ra tệp PDF đính kèm biên bản kiểm tra:

    Bao gồm:
    - Thông tin lô nông sản, vùng trồng, khối lượng, ngày thu hoạch.
    - Dòng thời gian sự kiện (Audit chain).
    - Phả hệ tổ tiên và hậu duệ (Lineage tree).
    - Vi phạm chuỗi lạnh (nếu có).
    - Lệnh kiểm tra / thu hồi liên quan (nếu có).
    - Kết quả kiểm tra toàn vẹn chuỗi hash kèm thời điểm xuất và mã hash cuối chuỗi.
    """
    from fastapi.responses import Response
    from app.lineage import extract_lineage_relations_from_events, find_ancestors_bfs, find_descendants_bfs
    from app.models import ColdChainViolation, RecallOrder
    from app.pdf_exporter import build_traceability_dossier_pdf

    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản với ID #{batch_id}.",
        )

    # 1. Dòng thời gian sự kiện & kiểm tra toàn vẹn
    timeline_res = get_batch_timeline(
        batch_id=batch_id,
        limit=None,
        offset=0,
        current_user=current_user,
        db=db,
    )

    events_list = [ev.model_dump() for ev in timeline_res.events]
    final_hash = events_list[-1]["hash"] if events_list else ("0" * 64)

    # 2. Phả hệ tổ tiên và hậu duệ
    all_events = list(db.scalars(select(BatchEvent).order_by(BatchEvent.id.asc())).all())
    relations = extract_lineage_relations_from_events(all_events)

    b_code = batch.batch_code or f"BATCH-{batch.id}"
    try:
        ancestors = find_ancestors_bfs(b_code, relations)
    except Exception:
        ancestors = []

    try:
        descendants = find_descendants_bfs(b_code, relations)
    except Exception:
        descendants = []

    # 3. Vi phạm chuỗi lạnh liên quan (theo tên sản phẩm hoặc mã lô)
    viol_stmt = select(ColdChainViolation).order_by(ColdChainViolation.id.desc())
    all_viols = list(db.scalars(viol_stmt).all())
    batch_viols = []
    for v in all_viols:
        if (batch.batch_code and batch.batch_code in v.shipment_code) or (batch.product_name in v.product_types_json):
            batch_viols.append({
                "shipment_code": v.shipment_code,
                "recorded_temperature": v.recorded_temperature,
                "duration_minutes": v.duration_minutes,
                "applied_temp_min": v.applied_temp_min,
                "applied_temp_max": v.applied_temp_max,
                "violation_reason": v.violation_reason,
            })

    # 4. Lệnh thu hồi liên quan
    orders_stmt = select(RecallOrder).where(RecallOrder.batch_id == batch.id).order_by(RecallOrder.id.desc())
    orders_records = list(db.scalars(orders_stmt).all())
    recall_orders = []
    for o in orders_records:
        total_t = len(o.targets)
        conf_t = sum(1 for t in o.targets if t.status == "CONFIRMED")
        recall_orders.append({
            "order_code": o.order_code,
            "title": o.title,
            "reason": o.reason,
            "progress_ratio": f"{conf_t}/{total_t}" if total_t > 0 else "0/0",
            "status": o.status,
        })

    # 5. Thông tin lô nông sản
    farm_obj = batch.farm
    batch_dict = {
        "id": batch.id,
        "batch_code": batch.batch_code,
        "product_name": batch.product_name,
        "quantity": batch.quantity,
        "harvest_date": str(batch.harvest_date),
        "current_holder_org": batch.current_holder_org,
        "farm": {
            "name": farm_obj.name if farm_obj else "—",
            "location": farm_obj.location if farm_obj else "—",
            "owner": farm_obj.owner if farm_obj else "—",
        },
    }

    pdf_bytes = build_traceability_dossier_pdf(
        batch_data=batch_dict,
        events_data=events_list,
        ancestors=ancestors,
        descendants=descendants,
        violations=batch_viols,
        recall_orders=recall_orders,
        integrity_status=timeline_res.is_valid,
        tampered_index=timeline_res.tampered_index,
        final_hash=final_hash,
        inspector_name=current_user.username,
    )

    filename = f"Ho-so-truy-xuat-{b_code}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Integrity-Status": "VALID" if timeline_res.is_valid else "TAMPERED",
            "X-Final-Hash": final_hash,
        },
    )

