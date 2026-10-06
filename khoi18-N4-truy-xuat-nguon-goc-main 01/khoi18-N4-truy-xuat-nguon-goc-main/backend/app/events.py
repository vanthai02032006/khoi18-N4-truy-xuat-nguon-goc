"""Hệ thống ghi nhận sự kiện vòng đời lô nông sản (Task T-25).

Cung cấp hàm chuẩn hóa ``record_event`` để ghi lại mọi diễn biến quan trọng
(tạo lô, khởi tạo bàn giao, tiếp nhận bàn giao, từ chối bàn giao, vận chuyển...)
vào bảng ``events`` phục vụ truy xuất nguồn gốc chuỗi cung ứng minh bạch.
"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    EVENT_TYPE_BATCH_CREATED,
    EVENT_TYPE_HANDOVER_ACCEPTED,
    EVENT_TYPE_HANDOVER_PENDING,
    EVENT_TYPE_HANDOVER_REJECTED,
    EVENT_TYPES,
    Event,
)


def record_event(
    db: Session,
    batch_id: int,
    event_type: str,
    actor_name: str | None = None,
    actor_id: int | None = None,
    description: str | None = None,
    metadata_info: dict[str, Any] | str | None = None,
) -> Event:
    """Ghi nhận sự kiện vòng đời lô nông sản (Hàm chuẩn hóa theo Task T-25).

    Args:
        db: Session SQLAlchemy đang thực thi.
        batch_id: ID lô nông sản liên quan.
        event_type: Loại sự kiện (chuẩn từ ``EVENT_TYPES``).
        actor_name: Tên người / chủ thể thực hiện hành động.
        actor_id: ID tài khoản người thực hiện (nếu có).
        description: Mô tả chi tiết về sự kiện.
        metadata_info: Dữ liệu bổ sung (dict hoặc chuỗi JSON/text).

    Returns:
        Event: Bản ghi sự kiện đã được khởi tạo và thêm vào session.
    """
    serialized_metadata: str | None = None
    if isinstance(metadata_info, (dict, list)):
        serialized_metadata = json.dumps(metadata_info, ensure_ascii=False)
    elif isinstance(metadata_info, str):
        serialized_metadata = metadata_info

    event = Event(
        batch_id=batch_id,
        event_type=event_type,
        actor_id=actor_id,
        actor_name=actor_name,
        description=description,
        metadata_info=serialized_metadata,
    )
    db.add(event)
    return event


def get_events_for_batch(db: Session, batch_id: int) -> list[Event]:
    """Lấy danh sách sự kiện của một lô nông sản theo thứ tự thời gian tăng dần."""
    return list(
        db.scalars(
            select(Event)
            .where(Event.batch_id == batch_id)
            .order_by(Event.created_at.asc(), Event.id.asc())
        ).all()
    )


__all__ = [
    "EVENT_TYPE_BATCH_CREATED",
    "EVENT_TYPE_HANDOVER_ACCEPTED",
    "EVENT_TYPE_HANDOVER_PENDING",
    "EVENT_TYPE_HANDOVER_REJECTED",
    "EVENT_TYPES",
    "get_events_for_batch",
    "record_event",
]
