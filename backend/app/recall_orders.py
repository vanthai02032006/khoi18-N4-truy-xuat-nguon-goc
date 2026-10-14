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

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.events import record_event
from app.models import (
    EVENT_TYPE_INCIDENT_FLAGGED,
    Batch,
    RecallOrder,
    RecallOrderTarget,
)
from app.schemas import RecallOrderResponse, RecallTargetResponse

#: Trạng thái lệnh đang thực thi (chưa hoàn tất) - lệnh "đang mở".
RECALL_STATUS_IN_PROGRESS: str = "IN_PROGRESS"

#: Tổ chức ghi nhận sự kiện do cán bộ kiểm tra phát hành (khớp ``orders.py``).
INSPECTOR_ORGANIZATION: str = "Cơ quan kiểm tra chất lượng"


def build_order_response(order: RecallOrder) -> RecallOrderResponse:
    """Tính toán tiến độ xác nhận từ các bên liên quan và xây dựng model response."""
    targets = order.targets or []
    total = len(targets)
    confirmed = sum(1 for t in targets if t.status == "CONFIRMED")
    pending = total - confirmed
    pending_orgs = [t.org_name for t in targets if t.status != "CONFIRMED"]

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


__all__ = [
    "INSPECTOR_ORGANIZATION",
    "RECALL_STATUS_IN_PROGRESS",
    "build_order_response",
    "find_open_order_for_batch",
    "generate_unique_order_code",
    "open_recall_order_for_batch",
]
