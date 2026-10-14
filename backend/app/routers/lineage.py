"""Router phả hệ lô hàng: chi tiết lô, truy ngược, truy xuôi, sơ đồ (Sprint 3-4).

Chuyển từ phần việc của Phan Quang (nhánh ``feature/phanquang-tasks``) và sửa:

- Bắt buộc đăng nhập và kiểm quyền xem lô đích (S-05 / S-23).
- Gặp chu trình ở CẢ truy ngược lẫn truy xuôi đều báo lỗi 409, không trả danh sách
  rỗng (bản cũ nuốt lỗi khi truy xuôi -> thu hồi sót lô, rủi ro R-03).
- Địa điểm lấy từ địa điểm thật của lô (S-30), trạng thái đã tiêu thụ lấy từ
  ``batches.status`` (S-29) thay vì đoán từ sự kiện.
- Nạp đồ thị và thông tin lô theo lô lớn, không truy vấn từng lô.

Endpoint:

- ``GET /batches/{id}/direct-summary`` : chi tiết + lô mẹ/con trực tiếp (S-25 / SCRUM-74, SCRUM-97)
- ``GET /batches/{id}/trace-back``     : truy ngược tới lô thu hoạch gốc (S-21 / SCRUM-105)
- ``GET /batches/{id}/trace-forward``  : truy xuôi mọi hậu duệ + nơi giữ (S-26, S-27 / SCRUM-107, SCRUM-108)
- ``GET /batches/{id}/lineage-graph``  : dữ liệu sơ đồ trái-giữa-phải (S-28 / SCRUM-109)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BATCH_STATUS_CONSUMED, Batch, BatchEvent, User
from app.schemas import (
    BatchDirectSummaryResponse,
    DescendantBatchItem,
    DirectParentChildItem,
    LineageGraphEdge,
    LineageGraphNode,
    LineageGraphResponse,
    OriginFarmItem,
    TraceBackResponse,
    TraceForwardResponse,
)
from app.security import get_current_user
from app.services.authorization_service import viewable_batch_ids
from app.services.lineage_service import (
    LineageCycleError,
    LineageGraph,
    batch_code,
    load_batches,
)

router = APIRouter(prefix="/batches", tags=["Phả hệ lô (Sprint 3-4)"])


# ---------------------------------------------------------------- tiện ích
def _require_viewable(db: Session, user: User, batch_id: int) -> tuple[Batch, set[int] | None]:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Không tìm thấy lô nông sản #{batch_id}.")
    allowed = viewable_batch_ids(db, user)
    if allowed is not None and batch_id not in allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Tài khoản hoặc tổ chức của bạn không có quyền xem lô nông sản này.",
        )
    return batch, allowed


def _cycle_error(exc: LineageCycleError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"{exc} Đã dừng truy vết để không lặp vô hạn - cần sửa dữ liệu phả hệ.",
    )


def _is_consumed(b: Batch) -> bool:
    return b.status == BATCH_STATUS_CONSUMED


def _remaining(b: Batch) -> float:
    return 0.0 if _is_consumed(b) else round(float(b.quantity or 0.0), 4)


def _location_label(b: Batch) -> str:
    if b.current_location is not None:
        addr = f" - {b.current_location.address}" if b.current_location.address else ""
        return f"{b.current_location.name}{addr}"
    return "Chưa gắn địa điểm"


def _origin_item(b: Batch) -> OriginFarmItem:
    farm = b.farm
    return OriginFarmItem(
        batch_id=b.id,
        batch_code=batch_code(b),
        product_name=b.product_name,
        harvest_date=str(b.harvest_date),
        initial_quantity=b.quantity,
        farm_id=farm.id if farm else 0,
        farm_name=farm.name if farm else "—",
        farm_location=farm.location if farm else "—",
        farm_area=farm.area if farm else 0.0,
        farm_owner=farm.owner if farm else "—",
        plot_name=f"Thửa {farm.id} - {farm.name}" if farm else "Vùng trồng gốc",
    )


# ------------------------------------------------- S-25: chi tiết lô 1 tầng
@router.get(
    "/{batch_id}/direct-summary",
    response_model=BatchDirectSummaryResponse,
    summary="Chi tiết lô kèm lô mẹ và lô con trực tiếp (S-25 / SCRUM-97, SCRUM-74)",
)
def get_batch_direct_summary(
    batch_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BatchDirectSummaryResponse:
    batch, _ = _require_viewable(db, current_user, batch_id)
    graph = LineageGraph.load(db)
    parent_ids = set(graph.parents_of.get(batch_id, []))
    child_ids = set(graph.children_of.get(batch_id, []))
    info = load_batches(db, parent_ids | child_ids | {batch_id})
    qty_by_edge = {(e.parent_id, e.child_id): (e.quantity, e.kind) for e in graph.edges}

    def item(other_id: int, edge: tuple[int, int]) -> DirectParentChildItem:
        b = info[other_id]
        qty, kind = qty_by_edge.get(edge, (None, "SPLIT"))
        return DirectParentChildItem(
            batch_id=b.id,
            batch_code=batch_code(b),
            product_name=b.product_name,
            quantity=b.quantity,
            used_quantity=qty,
            relation_type=kind,
        )

    b = info[batch_id]
    farm = b.farm
    events_count = db.scalar(select(func.count(BatchEvent.id)).where(BatchEvent.batch_id == batch_id)) or 0
    return BatchDirectSummaryResponse(
        id=b.id,
        batch_code=batch_code(b),
        product_name=b.product_name,
        quantity=b.quantity,
        harvest_date=str(b.harvest_date),
        is_restricted=bool(b.is_restricted),
        current_holder_org=b.current_holder_org,
        current_owner=b.current_owner,
        farm={
            "id": farm.id if farm else None,
            "name": farm.name if farm else None,
            "location": farm.location if farm else None,
            "area": farm.area if farm else None,
            "owner": farm.owner if farm else None,
            "plot_name": f"Thửa {farm.id} - {farm.name}" if farm else None,
        },
        product={"name": b.product_name, "status": b.status},
        holder_org={
            "code": b.organization_id,
            "name": b.current_holder_org,
            "location": _location_label(b),
        },
        direct_parents=[item(pid, (pid, batch_id)) for pid in sorted(parent_ids) if pid in info],
        direct_children=[item(cid, (batch_id, cid)) for cid in sorted(child_ids) if cid in info],
        events_count=events_count,
    )


# --------------------------------------------------- S-21: truy ngược nguồn gốc
@router.get(
    "/{batch_id}/trace-back",
    response_model=TraceBackResponse,
    summary="Truy ngược tới các lô thu hoạch gốc kèm vùng trồng (S-21 / SCRUM-105)",
    responses={409: {"description": "Dữ liệu phả hệ có chu trình."}},
)
def get_batch_trace_back(
    batch_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TraceBackResponse:
    batch, _ = _require_viewable(db, current_user, batch_id)
    graph = LineageGraph.load(db)
    try:
        anc = graph.ancestors(batch_id)
    except LineageCycleError as exc:
        raise _cycle_error(exc) from exc

    if not anc:
        info = load_batches(db, {batch_id})
        return TraceBackResponse(
            target_batch_id=batch.id,
            target_batch_code=batch_code(batch),
            target_product_name=batch.product_name,
            target_quantity=batch.quantity,
            is_standalone=True,
            depth=0,
            all_ancestor_codes=[],
            origin_farms=[_origin_item(info[batch_id])],
        )

    info = load_batches(db, set(anc))
    ordered = sorted(anc, key=lambda i: (anc[i], i))
    roots = sorted(graph.roots_among(set(anc)))
    return TraceBackResponse(
        target_batch_id=batch.id,
        target_batch_code=batch_code(batch),
        target_product_name=batch.product_name,
        target_quantity=batch.quantity,
        is_standalone=False,
        depth=max(anc.values()),
        all_ancestor_codes=[batch_code(info[i]) for i in ordered if i in info],
        origin_farms=[_origin_item(info[i]) for i in roots if i in info],
    )


# ------------------------------------------ S-26, S-27: truy xuôi + nơi đang giữ
@router.get(
    "/{batch_id}/trace-forward",
    response_model=TraceForwardResponse,
    summary="Truy xuôi mọi hậu duệ kèm tổ chức, địa điểm và khối lượng còn (S-26, S-27 / SCRUM-107, SCRUM-108)",
    responses={409: {"description": "Dữ liệu phả hệ có chu trình."}},
)
def get_batch_trace_forward(
    batch_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TraceForwardResponse:
    """Thà liệt kê thừa còn hơn sót: mọi hậu duệ đều được đếm.

    Admin và cán bộ kiểm tra thấy chi tiết toàn bộ. Tài khoản khác chỉ thấy chi tiết
    lô mình được xem; phần còn lại được đếm ở ``hidden_count`` để không bị hiểu là "không có".
    """
    batch, allowed = _require_viewable(db, current_user, batch_id)
    graph = LineageGraph.load(db)
    try:
        desc = graph.descendants(batch_id)
    except LineageCycleError as exc:
        raise _cycle_error(exc) from exc

    visible = set(desc) if allowed is None else {i for i in desc if i in allowed}
    info = load_batches(db, visible)
    items: list[DescendantBatchItem] = []
    for i in sorted(visible, key=lambda x: (desc[x], x)):
        b = info.get(i)
        if b is None:
            continue
        consumed = _is_consumed(b)
        items.append(
            DescendantBatchItem(
                batch_id=b.id,
                batch_code=batch_code(b),
                product_name=b.product_name,
                current_holder_org=b.current_holder_org,
                location=_location_label(b),
                location_id=b.current_location_id,
                remaining_quantity=_remaining(b),
                status="CONSUMED" if consumed else "IN_STOCK",
                status_label="Đã tiêu thụ" if consumed else "Còn hàng",
                depth=desc[i],
                is_merged_from_multiple_sources=len(graph.parents_of.get(i, [])) > 1,
            )
        )
    return TraceForwardResponse(
        target_batch_id=batch.id,
        target_batch_code=batch_code(batch),
        target_product_name=batch.product_name,
        total_descendants=len(desc),
        descendants=items,
        total_remaining_quantity=round(sum(x.remaining_quantity for x in items), 4),
        consumed_count=sum(1 for x in items if x.status == "CONSUMED"),
        hidden_count=len(desc) - len(items),
    )


# ------------------------------------------------------------ S-28: sơ đồ
@router.get(
    "/{batch_id}/lineage-graph",
    response_model=LineageGraphResponse,
    summary="Dữ liệu sơ đồ phả hệ: tổ tiên trái, lô hiện tại giữa, hậu duệ phải (S-28 / SCRUM-109)",
    responses={409: {"description": "Dữ liệu phả hệ có chu trình."}},
)
def get_batch_lineage_graph(
    batch_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LineageGraphResponse:
    batch, allowed = _require_viewable(db, current_user, batch_id)
    graph = LineageGraph.load(db)
    try:
        anc = graph.ancestors(batch_id)
        desc = graph.descendants(batch_id)
    except LineageCycleError as exc:
        raise _cycle_error(exc) from exc

    involved = {batch_id} | set(anc) | set(desc)
    if allowed is not None:
        # Tổ tiên luôn xem được (S-23); hậu duệ chỉ hiện nếu được xem.
        involved = {batch_id} | set(anc) | {i for i in desc if i in allowed}
    info = load_batches(db, involved)

    level = {batch_id: 0, **{i: -d for i, d in anc.items()}, **{i: d for i, d in desc.items()}}
    nodes: list[LineageGraphNode] = []
    for i in sorted(involved, key=lambda x: (level.get(x, 0), x)):
        b = info.get(i)
        if b is None:
            continue
        lvl = level.get(i, 0)
        nodes.append(
            LineageGraphNode(
                id=batch_code(b),
                batch_id=b.id,
                label=f"{batch_code(b)}\n{b.product_name} · {_remaining(b):g} kg",
                group="current" if lvl == 0 else ("ancestor" if lvl < 0 else "descendant"),
                level=lvl,
                product_name=b.product_name,
                quantity=b.quantity,
                holder_org=b.current_holder_org,
                status="Đã tiêu thụ" if _is_consumed(b) else "Còn hàng",
                farm_name=b.farm.name if b.farm else None,
            )
        )
    edges = [
        LineageGraphEdge(
            from_node=batch_code(info[e.parent_id]),
            to_node=batch_code(info[e.child_id]),
            label=e.kind,
            quantity=e.quantity,
        )
        for e in graph.edges_within(set(info))
    ]
    return LineageGraphResponse(
        target_batch_code=batch_code(batch),
        target_batch_id=batch.id,
        nodes=nodes,
        edges=edges,
    )
