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
from enum import Enum
from typing import Any, Optional, Sequence

from pydantic import BaseModel, Field
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


class ChainErrorType(str, Enum):
    """Phân loại lỗi toàn vẹn của chuỗi sự kiện lô nông sản."""

    DATA_MODIFIED = "DATA_MODIFIED"  # Bị sửa lén nội dung (hash tính lại không khớp)
    CHAIN_BROKEN = "CHAIN_BROKEN"  # Đứt gãy chuỗi / bị xoá mất sự kiện (previous_hash không khớp)
    GENESIS_HASH_MISMATCH = "GENESIS_HASH_MISMATCH"  # Sự kiện đầu tiên sai previous_hash (khác 64 số 0)

    # Bí danh tiện ích
    RECORD_DELETED = "CHAIN_BROKEN"
    HASH_MISMATCH = "DATA_MODIFIED"


class ChainVerificationResult(BaseModel):
    """Kết quả kiểm tra tính toàn vẹn chuỗi sự kiện của một lô nông sản."""

    batch_id: Optional[int] = None
    is_valid: bool = Field(..., description="True nếu toàn bộ chuỗi toàn vẹn, False nếu có vi phạm.")
    tampered_index: Optional[int] = Field(None, description="Vị trí (0-based index) của sự kiện đầu tiên bị sai lệch.")
    error_type: Optional[str] = Field(None, description="Loại lỗi: DATA_MODIFIED, CHAIN_BROKEN, GENESIS_HASH_MISMATCH.")
    error_detail: Optional[str] = Field(None, description="Mô tả chi tiết nguyên nhân vi phạm.")
    total_events: int = Field(default=0, description="Tổng số sự kiện của lô.")
    verified_count: int = Field(default=0, description="Số lượng sự kiện hợp lệ liên tiếp tính từ đầu chuỗi.")
    tampered_event_id: Optional[int] = Field(None, description="ID của bản ghi sự kiện đầu tiên bị lỗi (nếu có).")
    recorded_hash: Optional[str] = Field(None, description="Hash đã lưu trong CSDL của bản ghi bị lỗi.")
    expected_hash: Optional[str] = Field(None, description="Hash tính toán lại từ nội dung bản ghi.")
    recorded_previous_hash: Optional[str] = Field(None, description="previous_hash đã lưu trong CSDL của bản ghi bị lỗi.")
    expected_previous_hash: Optional[str] = Field(None, description="previous_hash kỳ vọng (hash của sự kiện liền trước).")

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def __iter__(self):
        # Hỗ trợ unpack dạng tuple: is_valid, tampered_index, error_type = verify_batch_chain(...)
        return iter((self.is_valid, self.tampered_index, self.error_type))


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


def verify_batch_chain(
    db_or_events: Session | Sequence[BatchEvent] | None = None,
    batch_id: int | None = None,
    *,
    events: Sequence[BatchEvent] | None = None,
    db: Session | None = None,
) -> ChainVerificationResult:
    """Kiểm tra chuỗi sự kiện của một lô, trả về hợp lệ hoặc vị trí sự kiện đầu tiên sai kèm loại lỗi.

    Đặc tả kỹ thuật (SCRUM-44 / T-28):
    1. Đọc sự kiện của lô theo thứ tự trong 1 lượt truy vấn duy nhất (Batch loading, không N+1 query).
    2. Duyệt từng sự kiện:
       - Kiểm tra previous_hash có khớp với hash sự kiện liền trước (hoặc GENESIS_HASH với sự kiện đầu tiên).
         Nếu không khớp: chuỗi bị xóa mất sự kiện hoặc đứt mạch -> trả về vị trí đứt mạch kèm loại lỗi CHAIN_BROKEN.
       - Tính lại hash bằng hàm compute_event_hash ở T-24 (SCRUM-40) và so với hash đã lưu.
         Nếu không khớp: chuỗi bị sửa lén -> trả về đúng vị trí bản ghi sai kèm loại lỗi DATA_MODIFIED.
    3. Chuỗi nguyên vẹn trả về hợp lệ (is_valid=True).

    Args:
        db_or_events: SQLAlchemy Session hoặc danh sách BatchEvent.
        batch_id: ID lô nông sản cần kiểm tra.
        events: Danh sách bản ghi BatchEvent nạp sẵn (nếu đã có trong bộ nhớ).
        db: SQLAlchemy Session.

    Returns:
        ChainVerificationResult: Kết quả kiểm tra toàn vẹn.
    """
    resolved_db: Session | None = None
    resolved_batch_id: int | None = batch_id
    resolved_events: list[BatchEvent] | None = None

    if events is not None:
        resolved_events = list(events)
    elif isinstance(db_or_events, (list, tuple, Sequence)):
        resolved_events = list(db_or_events)
    elif db_or_events is not None:
        resolved_db = db_or_events
    elif db is not None:
        resolved_db = db

    if resolved_events is None:
        if resolved_db is None or resolved_batch_id is None:
            raise ValueError(
                "Cần cung cấp (db, batch_id) hoặc truyền trực tiếp danh sách sự kiện qua tham số events."
            )
        # Đọc toàn bộ sự kiện theo batch trong 1 câu truy vấn duy nhất (Tránh N+1 query)
        stmt = (
            select(BatchEvent)
            .where(BatchEvent.batch_id == resolved_batch_id)
            .order_by(BatchEvent.timestamp.asc(), BatchEvent.id.asc())
        )
        resolved_events = list(resolved_db.scalars(stmt).all())

    total = len(resolved_events)
    if total == 0:
        return ChainVerificationResult(
            batch_id=resolved_batch_id,
            is_valid=True,
            tampered_index=None,
            error_type=None,
            error_detail="Lô nông sản chưa có sự kiện nào trong chuỗi.",
            total_events=0,
            verified_count=0,
        )

    expected_prev_hash = GENESIS_HASH

    for idx, ev in enumerate(resolved_events):
        # 1. Kiểm tra tính liên kết chuỗi (previous_hash có khớp với hash liền trước không)
        # Nếu sự kiện liền trước bị xóa mất hoặc previous_hash bị sửa -> đứt mạch chuỗi
        if ev.previous_hash != expected_prev_hash:
            if idx == 0:
                err_type = ChainErrorType.GENESIS_HASH_MISMATCH.value
                detail = (
                    f"Sự kiện khởi nguyên #{ev.id} (vị trí {idx}) có previous_hash={ev.previous_hash[:16]}... "
                    f"không khớp với Genesis Hash mong đợi ({GENESIS_HASH[:16]}...)."
                )
            else:
                err_type = ChainErrorType.CHAIN_BROKEN.value
                detail = (
                    f"Phát hiện đứt gãy chuỗi tại sự kiện #{ev.id} (vị trí {idx}): "
                    f"previous_hash={ev.previous_hash[:16]}... không khớp với hash sự kiện liền trước "
                    f"({expected_prev_hash[:16]}...). Bản ghi trước đó có thể đã bị xóa mất khỏi CSDL."
                )

            return ChainVerificationResult(
                batch_id=resolved_batch_id or ev.batch_id,
                is_valid=False,
                tampered_index=idx,
                error_type=err_type,
                error_detail=detail,
                total_events=total,
                verified_count=idx,
                tampered_event_id=ev.id,
                recorded_previous_hash=ev.previous_hash,
                expected_previous_hash=expected_prev_hash,
            )

        # 2. Tính lại hash bằng hàm T-24 (compute_event_hash) và so với hash đã lưu
        # Nếu nội dung (payload, actor, organization, timestamp...) bị sửa lén -> hash không khớp
        recomputed_hash = compute_event_hash(
            event_type=ev.event_type,
            payload=ev.payload,
            actor=ev.actor,
            organization=ev.organization,
            timestamp=ev.timestamp,
            previous_hash=ev.previous_hash,
        )

        if ev.hash != recomputed_hash:
            detail = (
                f"Phát hiện sửa lén dữ liệu tại sự kiện #{ev.id} (vị trí {idx}, loại {ev.event_type}): "
                f"hash lưu trữ ({ev.hash[:16]}...) không khớp với hash tính toán lại ({recomputed_hash[:16]}...). "
                f"Nội dung bản ghi đã bị can thiệp sửa đổi trái phép."
            )
            return ChainVerificationResult(
                batch_id=resolved_batch_id or ev.batch_id,
                is_valid=False,
                tampered_index=idx,
                error_type=ChainErrorType.DATA_MODIFIED.value,
                error_detail=detail,
                total_events=total,
                verified_count=idx,
                tampered_event_id=ev.id,
                recorded_hash=ev.hash,
                expected_hash=recomputed_hash,
                recorded_previous_hash=ev.previous_hash,
                expected_previous_hash=expected_prev_hash,
            )

        expected_prev_hash = ev.hash

    return ChainVerificationResult(
        batch_id=resolved_batch_id or (resolved_events[0].batch_id if resolved_events else None),
        is_valid=True,
        tampered_index=None,
        error_type=None,
        error_detail=f"Toàn bộ {total}/{total} sự kiện toàn vẹn, chuỗi băm SHA-256 hợp lệ 100%.",
        total_events=total,
        verified_count=total,
    )


# Các bí danh hàm dùng linh hoạt
verify_batch_events_chain = verify_batch_chain
verify_batch_events = verify_batch_chain
check_batch_event_chain = verify_batch_chain
check_batch_events = verify_batch_chain


def verify_multiple_batches_chains(
    db: Session,
    batch_ids: Sequence[int],
) -> dict[int, ChainVerificationResult]:
    """Kiểm tra chuỗi sự kiện của nhiều lô theo batch (1 query duy nhất, không N+1).

    Args:
        db: Session SQLAlchemy.
        batch_ids: Danh sách ID các lô nông sản cần kiểm tra.

    Returns:
        dict[int, ChainVerificationResult]: Bản đồ kết quả kiểm tra theo từng batch_id.
    """
    if not batch_ids:
        return {}

    unique_batch_ids = list(dict.fromkeys(batch_ids))
    stmt = (
        select(BatchEvent)
        .where(BatchEvent.batch_id.in_(unique_batch_ids))
        .order_by(BatchEvent.batch_id.asc(), BatchEvent.timestamp.asc(), BatchEvent.id.asc())
    )
    all_events = list(db.scalars(stmt).all())

    grouped: dict[int, list[BatchEvent]] = {bid: [] for bid in unique_batch_ids}
    for ev in all_events:
        if ev.batch_id in grouped:
            grouped[ev.batch_id].append(ev)

    results: dict[int, ChainVerificationResult] = {}
    for bid in unique_batch_ids:
        results[bid] = verify_batch_chain(batch_id=bid, events=grouped[bid])

    return results


__all__ = [
    "ChainErrorType",
    "ChainVerificationResult",
    "DEFAULT_ORGANIZATION",
    "GENESIS_HASH",
    "MAX_ACTOR_LENGTH",
    "check_batch_event_chain",
    "check_batch_events",
    "get_events_for_batch",
    "record_event",
    "verify_batch_chain",
    "verify_batch_events",
    "verify_batch_events_chain",
    "verify_multiple_batches_chains",
]
