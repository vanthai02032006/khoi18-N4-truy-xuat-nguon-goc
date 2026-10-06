"""Module xử lý sự kiện truy xuất nguồn gốc và hàm băm chuỗi (T-24 / SCRUM-40).

Module này cung cấp:
- Hằng số định danh loại sự kiện: `EVENT_TYPE_HARVEST`, `GENESIS_HASH`.
- Hàm băm sự kiện `calculate_event_hash` (T-24 / SCRUM-40): băm dữ liệu sự kiện
  kết hợp với `previous_hash` bằng SHA-256 để tạo chuỗi liên kết chống sửa lén (anti-tamper hash chain).
- Hàm ghi nhận sự kiện `record_event`: nhận `db: Session` (transaction) từ bên gọi,
  thêm bản ghi sự kiện vào transaction mà KHÔNG tự mở transaction hay commit riêng,
  phục vụ xử lý transaction nguyên tử (atomic transaction) ở T-20.
"""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event

# Mã băm khởi tạo (Genesis Hash) cho sự kiện đầu tiên của lô: 64 ký tự '0'
GENESIS_HASH: str = "0" * 64

# Loại sự kiện thu hoạch nông sản
EVENT_TYPE_HARVEST: str = "HARVEST"


def format_timestamp(ts: datetime | str) -> str:
    """Chuẩn hoá thời gian thành chuỗi ISO UTC dạng '%Y-%m-%dT%H:%M:%S.%fZ'.

    Đảm bảo tính nhất quán giữa datetime dạng timezone-aware (khi tạo) và
    timezone-naive (khi nạp từ SQLite/database).
    """
    if isinstance(ts, str):
        return ts
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def calculate_event_hash(
    batch_id: int,
    event_type: str,
    actor: str,
    description: str,
    data: str | None,
    previous_hash: str,
    created_at: datetime | str,
) -> str:
    """Hàm băm sự kiện chuỗi truy xuất (T-24 / SCRUM-40) bằng thuật toán SHA-256.

    Chuẩn hoá toàn bộ thông tin cốt lõi của sự kiện và `previous_hash` thành chuỗi đại diện
    (canonical payload) rồi tính chuỗi hex SHA-256 độ dài 64 ký tự.

    Args:
        batch_id: ID lô nông sản.
        event_type: Loại sự kiện (ví dụ: "HARVEST").
        actor: Tên người/tài khoản ghi nhận sự kiện.
        description: Mô tả sự kiện.
        data: Dữ liệu chi tiết đính kèm (chuỗi JSON hoặc text).
        previous_hash: Mã băm của sự kiện trước đó trong chuỗi (64 ký tự hex).
        created_at: Thời điểm ghi nhận sự kiện (datetime hoặc chuỗi ISO).

    Returns:
        str: Chuỗi hex SHA-256 (64 ký tự) của sự kiện.
    """
    time_str = format_timestamp(created_at)

    canonical_payload = (
        f"{batch_id}|{event_type}|{actor}|{description}|{data or ''}|{previous_hash}|{time_str}"
    )
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def record_event(
    db: Session,
    batch_id: int,
    event_type: str,
    actor: str,
    description: str,
    data: dict | list | str | None = None,
    created_at: datetime | None = None,
) -> Event:
    """Ghi nhận sự kiện truy xuất nguồn gốc vào chuỗi sự kiện của lô nông sản.

    RÀNG BUỘC KỸ THUẬT QUAN TRỌNG:
    - Hàm nhận `db: Session` (transaction) từ bên gọi.
    - TUYỆT ĐỐI KHÔNG tự mở transaction riêng, KHÔNG gọi `db.commit()` và KHÔNG gọi `db.close()`.
    - Bên gọi chịu trách nhiệm quản lý transaction (commit khi toàn bộ các bước thành công,
      hoặc rollback nếu có bất kỳ lỗi nào xảy ra) để đảm bảo tính nguyên tử (Atomicity).

    Quy trình xử lý:
    1. Chuẩn hoá dữ liệu đính kèm `data` thành JSON string (nếu là dict/list).
    2. Truy vấn sự kiện mới nhất của lô để lấy `previous_hash` (hoặc `GENESIS_HASH` nếu là sự kiện đầu tiên).
    3. Tính mã băm `hash` bằng hàm băm T-24 (`calculate_event_hash`).
    4. Khởi tạo `Event` và đưa vào session bằng `db.add(event)`.

    Args:
        db: Session SQLAlchemy (transaction) từ bên gọi.
        batch_id: ID lô nông sản.
        event_type: Loại sự kiện (ví dụ: "HARVEST").
        actor: Tên người/tài khoản thực hiện.
        description: Mô tả chi tiết sự kiện.
        data: Dữ liệu chi tiết đính kèm.
        created_at: Thời điểm diễn ra sự kiện (mặc định là UTC now).

    Returns:
        Event: Đối tượng sự kiện đã được add vào session (chưa commit).
    """
    if created_at is None:
        created_at = datetime.now(timezone.utc)

    # Chuẩn hoá dữ liệu đính kèm thành chuỗi JSON nếu là dict/list
    data_str: str | None = None
    if data is not None:
        if isinstance(data, (dict, list)):
            data_str = json.dumps(data, ensure_ascii=False, sort_keys=True)
        else:
            data_str = str(data)

    # Tìm sự kiện gần nhất của lô này để lấy previous_hash:
    # Ưu tiên kiểm tra các sự kiện vừa được thêm trong session hiện tại (chưa commit/flush),
    # nếu không có thì truy vấn từ database.
    pending_events = [
        obj for obj in db.new
        if isinstance(obj, Event) and obj.batch_id == batch_id
    ]
    if pending_events:
        previous_hash = pending_events[-1].hash
    else:
        last_event = db.scalar(
            select(Event)
            .where(Event.batch_id == batch_id)
            .order_by(Event.id.desc())
            .limit(1)
        )
        previous_hash = last_event.hash if last_event is not None else GENESIS_HASH

    # Tính mã băm SHA-256 theo chuẩn T-24 (SCRUM-40)
    event_hash = calculate_event_hash(
        batch_id=batch_id,
        event_type=event_type,
        actor=actor,
        description=description,
        data=data_str,
        previous_hash=previous_hash,
        created_at=created_at,
    )

    event = Event(
        batch_id=batch_id,
        event_type=event_type,
        actor=actor,
        description=description,
        data=data_str,
        previous_hash=previous_hash,
        hash=event_hash,
        created_at=created_at,
    )

    db.add(event)
    db.flush()  # Cấp event.id trong transaction hiện tại (KHÔNG commit)
    return event


__all__ = [
    "EVENT_TYPE_HARVEST",
    "GENESIS_HASH",
    "calculate_event_hash",
    "record_event",
]
