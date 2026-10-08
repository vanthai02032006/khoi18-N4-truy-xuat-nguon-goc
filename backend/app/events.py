"""Ghi nhận sự kiện vòng đời lô nông sản vào **chuỗi băm bất biến** ``batch_events``.

Module này là lớp dùng chung cho mọi nghiệp vụ cần ghi nhật ký (bàn giao, tách/gộp
lô, thu hoạch...), thay vì mỗi router tự sinh hash theo một kiểu. Nó dùng **đúng
cơ chế canonical của T-25** đã có trên ``develop``:

- Model ``BatchEvent`` (``payload`` / ``actor`` / ``organization`` / ``timestamp`` /
  ``hash`` / ``previous_hash``);
- ``app.security.compute_event_hash()`` để sinh mã băm liên kết với sự kiện liền trước.

Nhờ vậy sự kiện do bàn giao ghi ra cũng được ``GET /batches/{id}/events`` (endpoint
có sẵn) quét và xác thực toàn vẹn - không sinh ra chuỗi băm thứ hai song song.

Hai điểm quan trọng:

- ``record_event`` **không tự commit**: hàm chỉ ``db.add()`` rồi trả về, để router
  gom mọi thay đổi của một thao tác vào **cùng một transaction** và commit một lần.
  Lỗi giữa chừng thì ``db.rollback()`` ở router huỷ toàn bộ.
- Trước khi truy vấn sự kiện liền trước, hàm gọi ``db.flush()``: session của dự án
  đặt ``autoflush=False``, nên nếu không flush thì sự kiện thứ hai ghi trong cùng
  một transaction sẽ không "thấy" sự kiện thứ nhất và chuỗi băm sẽ bị sai.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BatchEvent
from app.security import compute_event_hash

#: Tổ chức mặc định ghi nhận sự kiện (khớp default của ``BatchEvent.organization``).
DEFAULT_ORGANIZATION: str = "HTX Nông Nghiệp Số 4"

#: ``previous_hash`` của sự kiện đầu tiên trong chuỗi.
GENESIS_HASH: str = "0" * 64

#: Độ dài tối đa của ``actor`` (khớp ``BatchEvent.actor`` VARCHAR(100)).
MAX_ACTOR_LENGTH: int = 100


def record_event(
    db: Session,
    *,
    batch_id: int,
    event_type: str,
    actor: str,
    payload: dict[str, Any] | str | None = None,
    organization: str = DEFAULT_ORGANIZATION,
) -> BatchEvent:
    """Thêm một sự kiện vào chuỗi băm của lô (chưa commit).

    Args:
        db: Session SQLAlchemy đang mở transaction của request.
        batch_id: ID lô nông sản phát sinh sự kiện.
        event_type: Loại sự kiện, nên dùng hằng số trong ``app.models``
            (``EVENT_TYPE_HANDOVER_ACCEPTED``, ``EVENT_TYPE_OWNER_CHANGED``...).
        actor: Tài khoản thực hiện hành động (``users.username``). Giới hạn 100
            ký tự - không truyền tên tổ chức dài vào đây.
        payload: Dữ liệu chi tiết của sự kiện. Truyền ``dict`` (khuyến nghị) để
            hàm tự serialize JSON, hoặc ``str`` JSON nếu đã chuẩn hoá sẵn.
        organization: Tên tổ chức ghi nhận (mặc định theo ``BatchEvent``).

    Returns:
        BatchEvent: Bản ghi sự kiện đã có ``hash``/``previous_hash`` (chưa commit).

    Raises:
        ValueError: Nếu ``actor`` rỗng hoặc vượt quá :data:`MAX_ACTOR_LENGTH` ký tự.
    """
    if not actor or len(actor) > MAX_ACTOR_LENGTH:
        raise ValueError(
            f"Tên tài khoản ghi nhận sự kiện phải từ 1 đến {MAX_ACTOR_LENGTH} ký tự "
            "(cột batch_events.actor)."
        )

    # Ghi các thay đổi đang chờ xuống transaction để truy vấn "sự kiện liền trước"
    # nhìn thấy chúng (session đặt autoflush=False nên phải flush thủ công).
    db.flush()

    last_event = db.scalars(
        select(BatchEvent)
        .where(BatchEvent.batch_id == batch_id)
        .order_by(BatchEvent.id.desc())
        .limit(1)
    ).first()
    previous_hash = last_event.hash if last_event is not None else GENESIS_HASH

    if isinstance(payload, str):
        serialized_payload = payload
    else:
        serialized_payload = json.dumps(payload or {}, ensure_ascii=False)

    timestamp = datetime.now(timezone.utc).isoformat()
    event_hash = compute_event_hash(
        event_type=event_type,
        payload=serialized_payload,
        actor=actor,
        organization=organization,
        timestamp=timestamp,
        previous_hash=previous_hash,
    )

    event = BatchEvent(
        batch_id=batch_id,
        event_type=event_type,
        payload=serialized_payload,
        actor=actor,
        organization=organization,
        timestamp=timestamp,
        hash=event_hash,
        previous_hash=previous_hash,
    )
    db.add(event)
    return event


def get_events_for_batch(db: Session, batch_id: int) -> list[BatchEvent]:
    """Lấy chuỗi sự kiện của một lô, sắp xếp theo thứ tự đã ghi.

    Args:
        db: Session SQLAlchemy.
        batch_id: ID lô nông sản cần tra cứu.

    Returns:
        list[BatchEvent]: Danh sách sự kiện (rỗng nếu lô chưa có sự kiện nào).
    """
    return list(
        db.scalars(
            select(BatchEvent)
            .where(BatchEvent.batch_id == batch_id)
            .order_by(BatchEvent.id.asc())
        ).all()
    )


__all__ = [
    "DEFAULT_ORGANIZATION",
    "GENESIS_HASH",
    "MAX_ACTOR_LENGTH",
    "get_events_for_batch",
    "record_event",
]
