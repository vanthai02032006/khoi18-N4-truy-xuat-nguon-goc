"""Nghiệp vụ dùng chung cho **lệnh thu hồi / kiểm tra chất lượng** (Recall Orders).

Module gom các phần bị lặp giữa ``app/routers/orders.py`` (ban hành & theo dõi tiến
độ lệnh) và ``app/routers/batches.py`` (S-19: cán bộ kiểm tra đánh dấu lô có sự cố
và mở lệnh thu hồi ngay trên chuỗi của lô):

- :func:`build_order_response` - dựng model response kèm tiến độ xác nhận.
- :func:`generate_unique_order_code` - sinh mã lệnh riêng, không trùng.
- :func:`find_open_order_for_batch` - tìm lệnh **đang mở** của một lô.
- :func:`open_recall_order_for_batch` - tạo lệnh + các bên liên quan + sự kiện
  ``INCIDENT_FLAGGED`` trong **cùng một transaction** (hàm không tự commit).

Giống ``app/events.py``, các hàm ở đây chỉ ``db.add()``/``db.flush()`` rồi trả về để
router gom toàn bộ thay đổi của một thao tác vào một transaction và commit một lần.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.events import record_event
from app.models import (
    BATCH_STATUS_CONSUMED,
    EVENT_TYPE_INCIDENT_FLAGGED,
    EVENT_TYPE_RECALL_ISSUED,
    Batch,
    RecallOrder,
    RecallOrderBatch,
    RecallOrderTarget,
)
from app.schemas import (
    RecallBatchResponse,
    RecallLocationGroupResponse,
    RecallOrderResponse,
    RecallRecoveryPlanResponse,
    RecallTargetResponse,
)

#: Trạng thái lệnh đang thực thi (chưa hoàn tất) - lệnh "đang mở".
RECALL_STATUS_IN_PROGRESS: str = "IN_PROGRESS"

#: Tổ chức ghi nhận sự kiện do cán bộ kiểm tra phát hành (khớp ``orders.py``).
INSPECTOR_ORGANIZATION: str = "Cơ quan kiểm tra chất lượng"

#: Nhãn cho các lô chưa gắn địa điểm (không biết đang ở cửa hàng nào).
UNKNOWN_LOCATION_NAME: str = "Chưa gắn địa điểm"


def build_order_response(order: RecallOrder) -> RecallOrderResponse:
    """Tính toán tiến độ xác nhận từ các bên liên quan và xây dựng model response."""
    targets = order.targets or []
    total = len(targets)
    confirmed = sum(1 for t in targets if t.status == "CONFIRMED")
    pending = total - confirmed
    pending_orgs = [t.org_name for t in targets if t.status != "CONFIRMED"]
    linked_batch_ids = [link.batch_id for link in (order.recall_batches or [])]
    if not linked_batch_ids and order.batch_id:
        linked_batch_ids = [order.batch_id]

    return RecallOrderResponse(
        id=order.id,
        order_code=order.order_code,
        title=order.title,
        description=order.description,
        reason=order.reason,
        batch_id=order.batch_id,
        issuer_username=order.issuer_username,
        created_at=order.created_at,
        completed_at=order.completed_at,
        status=order.status,
        targets=[RecallTargetResponse.model_validate(t) for t in targets],
        total_targets=total,
        confirmed_count=confirmed,
        pending_count=pending,
        progress_ratio=f"{confirmed}/{total}",
        is_completed=order.status == "COMPLETED",
        pending_organizations=pending_orgs,
        batch_ids=linked_batch_ids,
        batch_count=len(linked_batch_ids),
    )


def generate_unique_order_code(db: Session, prefix: str = "ORD-REC") -> str:
    """Sinh mã lệnh riêng, duy nhất (kiểm tra trùng trong DB trước khi trả về).

    Định dạng ``ORD-REC-0001``; nếu mã đã tồn tại (do DB cũ hoặc chèn tay) thì
    tăng dần cho tới khi tìm được mã trống.
    """
    last_id = db.scalar(select(RecallOrder.id).order_by(RecallOrder.id.desc()).limit(1)) or 0
    seq = last_id + 1
    while True:
        candidate = f"{prefix}-{seq:04d}"
        exists = db.scalar(select(RecallOrder.id).where(RecallOrder.order_code == candidate))
        if not exists:
            return candidate
        seq += 1


def find_open_order_for_batch(db: Session, batch_id: int) -> Optional[RecallOrder]:
    """Trả về lệnh **đang mở** (IN_PROGRESS) của một lô, hoặc ``None`` nếu không có.

    Dùng để chặn mở trùng: mỗi lô chỉ được có tối đa một lệnh thu hồi đang xử lý.
    """
    return db.scalar(
        select(RecallOrder)
        .options(selectinload(RecallOrder.targets))
        .where(
            RecallOrder.batch_id == batch_id,
            RecallOrder.status == RECALL_STATUS_IN_PROGRESS,
        )
        .order_by(RecallOrder.id.asc())
        .limit(1)
    )


def open_recall_order_for_batch(
    db: Session,
    *,
    batch: Batch,
    reason: str,
    actor: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    target_organizations: Optional[Sequence[str]] = None,
) -> RecallOrder:
    """Đánh dấu lô có sự cố và mở một lệnh thu hồi gắn với lô (chưa commit).

    Tạo :class:`RecallOrder` với mã riêng, khởi tạo các :class:`RecallOrderTarget`
    ở trạng thái ``PENDING`` và ghi **một** sự kiện ``INCIDENT_FLAGGED`` (kèm
    ``order_code``) vào chuỗi băm bất biến của lô.

    Args:
        db: Session SQLAlchemy đang mở transaction của request.
        batch: Lô bị đánh dấu sự cố (đã tồn tại trong DB).
        reason: Lý do đánh dấu sự cố.
        actor: Tài khoản thực hiện (``users.username``).
        title: Tiêu đề lệnh; bỏ trống sẽ tự sinh theo mã lô.
        description: Mô tả chi tiết / hướng dẫn xử lý.
        target_organizations: Các bên phải xác nhận; bỏ trống thì mặc định là
            tổ chức đang giữ lô (``batch.current_holder_org``).

    Returns:
        RecallOrder: Lệnh vừa tạo (đã có ``id`` và ``order_code``, chưa commit).

    Raises:
        ValueError: Nếu không xác định được bên liên quan nào để thực thi lệnh.
    """
    batch_code = batch.batch_code or f"LOT-{batch.id:04d}"
    unique_orgs = list(
        dict.fromkeys(org.strip() for org in (target_organizations or []) if org and org.strip())
    )
    if not unique_orgs:
        holder = (batch.current_holder_org or "").strip()
        unique_orgs = [holder] if holder else []
    if not unique_orgs:
        raise ValueError(
            "Cần ít nhất một tổ chức liên quan để thực thi lệnh thu hồi."
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    order = RecallOrder(
        order_code=generate_unique_order_code(db),
        title=(title or "").strip() or f"Thu hồi khẩn cấp lô {batch_code}",
        description=description or "",
        reason=reason,
        batch_id=batch.id,
        issuer_username=actor,
        created_at=now_iso,
        status=RECALL_STATUS_IN_PROGRESS,
    )
    db.add(order)
    db.flush()

    for org_name in unique_orgs:
        db.add(RecallOrderTarget(order_id=order.id, org_name=org_name, status="PENDING"))

    record_event(
        db,
        batch_id=batch.id,
        event_type=EVENT_TYPE_INCIDENT_FLAGGED,
        actor=actor,
        organization=INSPECTOR_ORGANIZATION,
        payload={
            "action": "INCIDENT_FLAGGED",
            "batch_code": batch_code,
            "order_id": order.id,
            "order_code": order.order_code,
            "reason": reason,
            "title": order.title,
            "target_organizations": unique_orgs,
        },
    )
    return order


def resolve_recall_batches(
    db: Session,
    *,
    batch_ids: Optional[Sequence[int]] = None,
    product_name: Optional[str] = None,
) -> list[Batch]:
    """Xác định các lô thuộc **phạm vi** một lệnh thu hồi (S-20).

    Cán bộ kiểm tra có thể chỉ định từng lô (`batch_ids` - ví dụ 20 lô đang nằm ở
    nhiều cửa hàng) hoặc chỉ nêu **tên sản phẩm** (`product_name`) để hệ thống tự
    gom mọi lô cùng sản phẩm, thay vì phải liệt kê thủ công cả kho.

    Args:
        db: Session SQLAlchemy đang mở transaction của request.
        batch_ids: Danh sách ID lô cần đưa vào lệnh.
        product_name: Tên sản phẩm - gom mọi lô cùng tên sản phẩm.

    Returns:
        list[Batch]: Các lô thuộc phạm vi, sắp xếp theo ``id`` (rỗng nếu không
        chỉ định phạm vi nào).

    Raises:
        ValueError: Nếu có ID lô không tồn tại, hoặc tên sản phẩm không khớp lô nào.
    """
    normalized_ids = list(dict.fromkeys(int(batch_id) for batch_id in (batch_ids or [])))
    if normalized_ids:
        batches = list(
            db.scalars(
                select(Batch).where(Batch.id.in_(normalized_ids)).order_by(Batch.id)
            ).all()
        )
        found_ids = {batch.id for batch in batches}
        missing = [batch_id for batch_id in normalized_ids if batch_id not in found_ids]
        if missing:
            missing_text = ", ".join(f"#{batch_id}" for batch_id in missing)
            raise ValueError(f"Không tìm thấy lô nông sản: {missing_text}.")
        return batches

    name = (product_name or "").strip()
    if name:
        batches = list(
            db.scalars(
                select(Batch).where(Batch.product_name == name).order_by(Batch.id)
            ).all()
        )
        if not batches:
            raise ValueError(f"Không có lô nào của sản phẩm '{name}' để đưa vào lệnh thu hồi.")
        return batches

    return []


def attach_batches_to_order(
    db: Session,
    order: RecallOrder,
    batches: Sequence[Batch],
) -> list[RecallOrderBatch]:
    """Gắn danh sách lô vào phạm vi lệnh thu hồi và đặt lô đầu tiên làm lô chính (chưa commit).

    Lệnh mở từ sự cố một lô (S-19) chỉ có một lô nên ``order.batch_id`` giữ nguyên
    hành vi cũ; lệnh nhiều lô (S-20) lấy lô đầu tiên làm lô chính để tương thích
    với các API cũ và ghi rõ toàn bộ phạm vi trong bảng ``recall_order_batches``.
    """
    existing_ids = {link.batch_id for link in (order.recall_batches or [])}
    links: list[RecallOrderBatch] = []
    for batch in batches:
        if batch.id in existing_ids:
            continue
        link = RecallOrderBatch(order_id=order.id, batch_id=batch.id)
        db.add(link)
        links.append(link)
        existing_ids.add(batch.id)

    if order.batch_id is None and batches:
        order.batch_id = batches[0].id

    db.flush()
    return links


def load_order_batches(db: Session, order: RecallOrder) -> list[Batch]:
    """Tải các lô thuộc phạm vi lệnh thu hồi, sắp xếp theo ``id``.

    Đọc bảng liên kết ``recall_order_batches``; lệnh cũ (chỉ có ``batch_id``, S-19)
    vẫn trả đúng lô duy nhất của nó nên không cần migrate dữ liệu lệnh đã có.
    """
    batch_ids = [link.batch_id for link in (order.recall_batches or [])]
    if not batch_ids and order.batch_id:
        batch_ids = [order.batch_id]
    if not batch_ids:
        return []

    return list(
        db.scalars(
            select(Batch).where(Batch.id.in_(batch_ids)).order_by(Batch.id)
        ).all()
    )


def record_recall_issued(
    db: Session,
    *,
    order: RecallOrder,
    batches: Sequence[Batch],
    actor: str,
    target_organizations: Sequence[str],
) -> None:
    """Ghi sự kiện ``RECALL_ISSUED`` vào chuỗi băm của **từng lô** thuộc lệnh (chưa commit).

    Nhờ vậy hồ sơ thu hồi neo vào đúng lô hàng ở đúng cửa hàng: tra lịch sử của
    bất kỳ lô nào trong lệnh đều thấy nó bị thu hồi theo lệnh nào, lý do gì.
    """
    for batch in batches:
        record_event(
            db,
            batch_id=batch.id,
            event_type=EVENT_TYPE_RECALL_ISSUED,
            actor=actor,
            organization=INSPECTOR_ORGANIZATION,
            payload={
                "action": EVENT_TYPE_RECALL_ISSUED,
                "batch_code": batch.batch_code or f"LOT-{batch.id:04d}",
                "order_id": order.id,
                "order_code": order.order_code,
                "title": order.title,
                "reason": order.reason,
                "target_organizations": list(target_organizations),
            },
        )


def _format_quantity(value: float) -> str:
    """Định dạng khối lượng gọn cho báo cáo (bỏ số 0 thừa: ``156.0`` → ``156``)."""
    return f"{round(value, 4):g}"


def build_recovery_plan(db: Session, order: RecallOrder) -> RecallRecoveryPlanResponse:
    """Dựng **kế hoạch thu hồi** của một lệnh: lô nào ở cửa hàng nào, còn bao nhiêu (S-20).

    Trả về từng lô kèm tổ chức đang giữ, địa điểm hiện tại, khối lượng còn lại
    (thu hồi được) và khối lượng đã bán cho người tiêu dùng, cùng phần **tổng hợp**
    tách bạch "còn thu hồi được" và "đã ra thị trường", và các lô gom theo địa điểm
    để đi thu hồi từng chỗ một.
    """
    batches = load_order_batches(db, order)
    rows: list[RecallBatchResponse] = []
    for batch in batches:
        rows.append(
            RecallBatchResponse(
                batch_id=batch.id,
                batch_code=batch.batch_code or f"LOT-{batch.id:04d}",
                product_name=batch.product_name,
                organization=batch.current_holder_org or "",
                location_id=batch.current_location_id,
                location_name=batch.current_location_name,
                location_address=batch.current_location_address,
                remaining_quantity=round(batch.quantity, 4),
                consumed_quantity=round(batch.consumed_quantity or 0.0, 4),
                total_quantity=batch.total_quantity,
                status=batch.status,
                is_recallable=batch.is_recallable,
            )
        )

    recallable_quantity = round(sum(row.remaining_quantity for row in rows), 4)
    consumed_quantity = round(sum(row.consumed_quantity for row in rows), 4)
    total_quantity = round(recallable_quantity + consumed_quantity, 4)

    grouped: "OrderedDict[tuple[Optional[int], str, Optional[str]], dict[str, float]]" = OrderedDict()
    for row in rows:
        key = (
            row.location_id,
            row.location_name or UNKNOWN_LOCATION_NAME,
            row.location_address,
        )
        group = grouped.setdefault(
            key,
            {"batch_count": 0, "remaining_quantity": 0.0, "consumed_quantity": 0.0},
        )
        group["batch_count"] += 1
        group["remaining_quantity"] += row.remaining_quantity
        group["consumed_quantity"] += row.consumed_quantity

    location_groups = [
        RecallLocationGroupResponse(
            location_id=location_id,
            location_name=location_name,
            location_address=location_address,
            batch_count=int(group["batch_count"]),
            remaining_quantity=round(group["remaining_quantity"], 4),
            consumed_quantity=round(group["consumed_quantity"], 4),
        )
        for (location_id, location_name, location_address), group in sorted(
            grouped.items(), key=lambda item: item[0][1]
        )
    ]

    return RecallRecoveryPlanResponse(
        order_id=order.id,
        order_code=order.order_code,
        status=order.status,
        total_batches=len(rows),
        recallable_batches=sum(1 for row in rows if row.is_recallable),
        consumed_batches=sum(1 for row in rows if row.status == BATCH_STATUS_CONSUMED),
        total_quantity=total_quantity,
        recallable_quantity=recallable_quantity,
        consumed_quantity=consumed_quantity,
        recallable_ratio=f"{_format_quantity(recallable_quantity)}/{_format_quantity(total_quantity)}",
        total_locations=len(location_groups),
        batches=rows,
        locations=location_groups,
    )


__all__ = [
    "INSPECTOR_ORGANIZATION",
    "RECALL_STATUS_IN_PROGRESS",
    "UNKNOWN_LOCATION_NAME",
    "attach_batches_to_order",
    "build_order_response",
    "build_recovery_plan",
    "find_open_order_for_batch",
    "generate_unique_order_code",
    "load_order_batches",
    "open_recall_order_for_batch",
    "resolve_recall_batches",
]
