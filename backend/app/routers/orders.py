"""Router quản lý lệnh thu hồi và kiểm tra chất lượng (Recall & Inspection Orders).

Đáp ứng User Story:
"Là cán bộ kiểm tra tôi muốn thấy tiến độ từng bên và biết khi nào lệnh coi là xong để đóng hồ sơ đúng lúc"

DoD / AC:
1. Giả sử lệnh có năm tổ chức liên quan và ba đã xác nhận,
   Khi xem (GET /orders/{order_id} hoặc GET /orders),
   Thì thấy 3/5 kèm danh sách tên các bên chưa xong.
2. Giả sử bên cuối cùng xác nhận (POST /orders/{order_id}/confirm),
   Khi lưu,
   Thì lệnh tự chuyển sang hoàn tất (status="COMPLETED", completed_at)
   và ghi sự kiện (RECALL_COMPLETED) vào chuỗi sự kiện bất biến (hash-chain).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Batch, BatchEvent, RecallOrder, RecallOrderTarget, User
from app.schemas import (
    RecallOrderConfirm,
    RecallOrderConfirmResponse,
    RecallOrderCreate,
    RecallOrderResponse,
    RecallTargetResponse,
)
from app.security import compute_event_hash, get_current_user, require_inspector
from app.tenant import get_tenant_org, require_tenant_context

router = APIRouter(prefix="/orders", tags=["Orders - Lệnh kiểm tra & thu hồi"])


def _build_order_response(order: RecallOrder) -> RecallOrderResponse:
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


@router.post(
    "",
    response_model=RecallOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ban hành lệnh kiểm tra / thu hồi mới (Cán bộ kiểm tra / Admin)",
)
def create_recall_order(
    payload: RecallOrderCreate,
    current_user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
) -> RecallOrderResponse:
    """Ban hành một lệnh kiểm tra hoặc thu hồi nông sản tới các tổ chức liên quan."""
    unique_orgs = list(dict.fromkeys([o.strip() for o in payload.target_organizations if o.strip()]))
    if not unique_orgs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cần ít nhất một tổ chức liên quan trong danh sách thực thi lệnh.",
        )

    # Kiểm tra lô hàng nếu có chỉ định
    if payload.batch_id:
        batch = db.get(Batch, payload.batch_id)
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy lô hàng có id={payload.batch_id}.",
            )

    now_iso = datetime.now(timezone.utc).isoformat()
    order_count = db.scalar(select(RecallOrder.id).order_by(RecallOrder.id.desc()).limit(1)) or 0
    order_code = f"ORD-REC-{order_count + 1:04d}"

    order = RecallOrder(
        order_code=order_code,
        title=payload.title,
        description=payload.description or "",
        reason=payload.reason,
        batch_id=payload.batch_id,
        issuer_username=current_user.username,
        created_at=now_iso,
        status="IN_PROGRESS",
    )
    db.add(order)
    db.flush()

    for org_name in unique_orgs:
        target = RecallOrderTarget(
            order_id=order.id,
            org_name=org_name,
            status="PENDING",
        )
        db.add(target)

    # Ghi nhận sự kiện khởi tạo lệnh RECALL_ISSUED nếu có lô hàng liên quan
    if payload.batch_id:
        last_ev_stmt = (
            select(BatchEvent)
            .where(BatchEvent.batch_id == payload.batch_id)
            .order_by(BatchEvent.id.desc())
            .limit(1)
        )
        last_ev = db.scalars(last_ev_stmt).first()
        prev_hash = last_ev.hash if last_ev else "0" * 64

        ev_payload = {
            "action": "RECALL_ISSUED",
            "order_id": order.id,
            "order_code": order_code,
            "title": payload.title,
            "reason": payload.reason,
            "target_organizations": unique_orgs,
        }
        ev_payload_str = json.dumps(ev_payload, ensure_ascii=False)
        h = compute_event_hash(
            event_type="RECALL_ISSUED",
            payload=ev_payload_str,
            actor=current_user.username,
            organization="Cơ quan kiểm tra chất lượng",
            timestamp=now_iso,
            previous_hash=prev_hash,
        )
        db.add(
            BatchEvent(
                batch_id=payload.batch_id,
                event_type="RECALL_ISSUED",
                payload=ev_payload_str,
                actor=current_user.username,
                organization="Cơ quan kiểm tra chất lượng",
                timestamp=now_iso,
                hash=h,
                previous_hash=prev_hash,
            )
        )

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể lưu lệnh kiểm tra vào cơ sở dữ liệu.",
        ) from exc

    db.refresh(order)
    # Tải lại kèm targets
    order = db.scalar(
        select(RecallOrder)
        .options(selectinload(RecallOrder.targets))
        .where(RecallOrder.id == order.id)
    )
    return _build_order_response(order)


@router.get(
    "",
    response_model=list[RecallOrderResponse],
    status_code=status.HTTP_200_OK,
    summary="Xem danh sách tất cả các lệnh và tiến độ thực thi",
)
def list_recall_orders(
    status_filter: Optional[str] = Query(None, alias="status", description="Lọc theo trạng thái lệnh: IN_PROGRESS hoặc COMPLETED"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[RecallOrderResponse]:
    """Lấy danh sách các lệnh kiểm tra / thu hồi kèm tiến độ từng bên."""
    stmt = select(RecallOrder).options(selectinload(RecallOrder.targets)).order_by(RecallOrder.id.desc())
    if status_filter:
        stmt = stmt.where(RecallOrder.status == status_filter)

    orders = list(db.scalars(stmt).all())
    return [_build_order_response(o) for o in orders]


@router.get(
    "/{order_id}",
    response_model=RecallOrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Xem chi tiết tiến độ một lệnh (kèm danh sách bên đã xong / chưa xong)",
)
def get_recall_order(
    order_id: int = Path(..., gt=0, description="ID của lệnh kiểm tra / thu hồi"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecallOrderResponse:
    """Lấy chi tiết tiến độ lệnh: thấy rõ tỷ lệ đã xác nhận (ví dụ 3/5) và tên các bên chưa xong."""
    order = db.scalar(
        select(RecallOrder)
        .options(selectinload(RecallOrder.targets))
        .where(RecallOrder.id == order_id)
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lệnh có id={order_id}.",
        )
    return _build_order_response(order)


@router.post(
    "/{order_id}/confirm",
    response_model=RecallOrderConfirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Xác nhận thực thi lệnh từ một tổ chức liên quan",
)
def confirm_recall_order(
    order_id: int = Path(..., gt=0, description="ID của lệnh kiểm tra / thu hồi"),
    payload: RecallOrderConfirm = ...,
    current_user: User = Depends(get_current_user),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> RecallOrderConfirmResponse:
    """Tổ chức liên quan xác nhận đã nhận và hoàn thành yêu cầu trong lệnh.

    Khi bên cuối cùng xác nhận, lệnh tự động chuyển sang hoàn tất (status='COMPLETED')
    và ghi sự kiện kết thúc vào chuỗi hash-chain nếu có lô hàng liên quan.
    """
    order = db.scalar(
        select(RecallOrder)
        .options(selectinload(RecallOrder.targets))
        .where(RecallOrder.id == order_id)
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lệnh có id={order_id}.",
        )

    if order.status == "COMPLETED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lệnh này đã hoàn tất đóng hồ sơ trước đó.",
        )

    # Tìm target tương ứng với tổ chức gọi API
    # Ưu tiên org_name truyền trong payload body (tránh lỗi ASCII header với tiếng Việt), sau đó đến current_org
    effective_org = (payload.org_name or "").strip() or current_org
    target = next((t for t in order.targets if t.org_name == effective_org), None)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tổ chức '{effective_org}' không nằm trong danh sách các bên liên quan của lệnh này.",
        )

    if target.status == "CONFIRMED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tổ chức '{effective_org}' đã xác nhận hoàn thành lệnh này trước đó.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    target.status = "CONFIRMED"
    target.confirmed_at = now_iso
    target.confirmed_by = current_user.username
    target.note = payload.note

    # Kiểm tra xem đây có phải là bên cuối cùng xác nhận không
    all_confirmed = all(t.status == "CONFIRMED" for t in order.targets)
    event_id = None
    event_hash = None

    if all_confirmed:
        order.status = "COMPLETED"
        order.completed_at = now_iso

        # Ghi sự kiện RECALL_COMPLETED vào chuỗi bất biến nếu có gắn với lô hàng
        if order.batch_id:
            last_ev_stmt = (
                select(BatchEvent)
                .where(BatchEvent.batch_id == order.batch_id)
                .order_by(BatchEvent.id.desc())
                .limit(1)
            )
            last_ev = db.scalars(last_ev_stmt).first()
            prev_hash = last_ev.hash if last_ev else "0" * 64

            ev_payload = {
                "action": "RECALL_COMPLETED",
                "order_id": order.id,
                "order_code": order.order_code,
                "completed_at": now_iso,
                "all_targets_confirmed": [t.org_name for t in order.targets],
                "final_confirming_org": effective_org,
            }
            ev_payload_str = json.dumps(ev_payload, ensure_ascii=False)
            h = compute_event_hash(
                event_type="RECALL_COMPLETED",
                payload=ev_payload_str,
                actor=current_user.username,
                organization=effective_org,
                timestamp=now_iso,
                previous_hash=prev_hash,
            )
            comp_ev = BatchEvent(
                batch_id=order.batch_id,
                event_type="RECALL_COMPLETED",
                payload=ev_payload_str,
                actor=current_user.username,
                organization=effective_org,
                timestamp=now_iso,
                hash=h,
                previous_hash=prev_hash,
            )
            db.add(comp_ev)
            db.flush()
            event_id = comp_ev.id
            event_hash = comp_ev.hash

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi khi lưu xác nhận hoàn thành lệnh.",
        ) from exc

    confirmed_count = sum(1 for t in order.targets if t.status == "CONFIRMED")
    total_count = len(order.targets)

    msg = (
        f"Tổ chức '{effective_org}' đã xác nhận lệnh #{order.id} ({order.order_code}). "
        + ("Tất cả các bên đã xác nhận! Lệnh đã tự động chuyển sang hoàn tất và đóng hồ sơ."
           if all_confirmed else f"Tiến độ hiện tại: {confirmed_count}/{total_count} bên đã xong.")
    )

    return RecallOrderConfirmResponse(
        order_id=order.id,
        order_code=order.order_code,
        org_name=effective_org,
        target_status="CONFIRMED",
        order_status=order.status,
        progress_ratio=f"{confirmed_count}/{total_count}",
        is_order_completed=all_confirmed,
        message=msg,
        event_id=event_id,
        event_hash=event_hash,
    )
