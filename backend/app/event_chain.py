"""Cơ chế kiểm tra tính toàn vẹn và chống sửa lén chuỗi sự kiện lô nông sản (T-28 / T-29 / T-31 / SCRUM-47).

Sử dụng Cryptographic Hash Chain (chuỗi băm mật mã SHA-256) tương tự blockchain
nhưng tinh gọn, lưu trực tiếp trong cơ sở dữ liệu quan hệ (SQLite).

Mỗi bản ghi BatchEvent lưu:
- `sequence`: Thứ tự tăng dần liên tục (1, 2, 3...)
- `event_type`: Loại sự kiện trong chuỗi cung ứng
- `organization`: Tên tổ chức/đơn vị thực hiện
- `data`: Nội dung chi tiết (JSON / chuỗi)
- `timestamp`: Thời điểm ghi nhận (ISO-8601)
- `prev_hash`: Hash của bản ghi sự kiện liền trước (hoặc GENESIS_HASH nếu là sự kiện đầu tiên).
- `hash`: SHA-256(batch_id + sequence + event_type + organization + data + timestamp + prev_hash).

Hàm kiểm tra T-28 (`verify_batch_events_integrity` / `verify_batch_chain`):
1. Sửa lén dữ liệu: hash lưu trữ sẽ không khớp với hash tính toán lại từ nội dung (DATA_MODIFIED).
2. Xoá bản ghi / đứt gãy: prev_hash của bản ghi kế tiếp sẽ không khớp với hash bản ghi trước (RECORD_DELETED_OR_CHAIN_BROKEN).
3. Bị nhảy cóc thứ tự sequence (SEQUENCE_BROKEN).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Batch, BatchEvent

# Hash khởi nguyên (Genesis Hash) cho sự kiện đầu tiên của mỗi lô
GENESIS_HASH: str = "0" * 64


class TamperType(str, Enum):
    """Phân loại trạng thái vi phạm toàn vẹn dữ liệu."""

    NONE = "NONE"
    DATA_MODIFIED = "DATA_MODIFIED"  # Bị sửa lén nội dung bằng SQL trực tiếp
    RECORD_DELETED_OR_CHAIN_BROKEN = "RECORD_DELETED_OR_CHAIN_BROKEN"  # Bị xoá bản ghi hoặc đứt gãy liên kết
    GENESIS_HASH_TAMPERED = "GENESIS_HASH_TAMPERED"  # Mã băm khởi nguyên bị sai lệch
    SEQUENCE_BROKEN = "SEQUENCE_BROKEN"  # Thứ tự sự kiện bị nhảy cóc


class IntegrityVerificationReport(BaseModel):
    """Báo cáo kết quả kiểm tra tính toàn vẹn chuỗi sự kiện của lô nông sản (T-28)."""

    batch_id: int
    is_valid: bool
    status: str  # "VERIFIED" | "TAMPERED"
    total_events: int
    verified_count: int
    tamper_type: Optional[str] = None
    tampered_event_id: Optional[int] = None
    tampered_sequence: Optional[int] = None
    detail: str
    recorded_hash: Optional[str] = None
    expected_hash: Optional[str] = None
    recorded_prev_hash: Optional[str] = None
    expected_prev_hash: Optional[str] = None


def calculate_event_hash(
    batch_id: int,
    sequence: int,
    event_type: str,
    organization: str,
    data: str,
    timestamp: str,
    prev_hash: str,
) -> str:
    """Tính mã băm mật mã SHA-256 cho một sự kiện trong chuỗi.

    Chuỗi chuẩn tắc (canonical representation) đảm bảo tính xác định (deterministic).
    Bao gồm loại sự kiện, tổ chức, nội dung, thời gian và mã băm mắt xích trước.
    """
    canonical_payload = (
        f"{batch_id}|{sequence}|{event_type.strip()}|{organization.strip()}|"
        f"{data.strip()}|{timestamp.strip()}|{prev_hash.strip()}"
    )
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def record_batch_event(
    db: Session,
    batch_id: int,
    event_type: str,
    data: str,
    organization: str = "Hợp tác xã Nông nghiệp Cao Lãnh",
    timestamp: Optional[str] = None,
    auto_commit: bool = True,
) -> BatchEvent:
    """Ghi nhận một sự kiện mới vào chuỗi của lô nông sản.

    Tự động liên kết `prev_hash` với sự kiện gần nhất và tính mã băm SHA-256.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()

    # Tìm sự kiện cuối cùng hiện có của lô
    last_event = (
        db.query(BatchEvent)
        .filter(BatchEvent.batch_id == batch_id)
        .order_by(BatchEvent.sequence.desc(), BatchEvent.id.desc())
        .first()
    )

    if last_event is None:
        sequence = 1
        prev_hash = GENESIS_HASH
    else:
        sequence = last_event.sequence + 1
        prev_hash = last_event.hash

    event_hash = calculate_event_hash(
        batch_id=batch_id,
        sequence=sequence,
        event_type=event_type,
        organization=organization,
        data=data,
        timestamp=timestamp,
        prev_hash=prev_hash,
    )

    event = BatchEvent(
        batch_id=batch_id,
        sequence=sequence,
        event_type=event_type,
        organization=organization,
        data=data,
        timestamp=timestamp,
        prev_hash=prev_hash,
        hash=event_hash,
    )

    db.add(event)
    db.flush()
    if auto_commit:
        db.commit()
        db.refresh(event)

    return event


def verify_batch_events_integrity(db: Session, batch_id: int) -> IntegrityVerificationReport:
    """Quét toàn bộ chuỗi sự kiện của một lô nông sản để kiểm tra tính toàn vẹn (Hàm T-28).

    Quy trình kiểm tra 2 lớp:
    1. Kiểm tra tính toàn vẹn nội dung (Content Integrity): hash của bản ghi phải
       khớp 100% với giá trị tính toán lại từ nội dung các trường.
    2. Kiểm tra chuỗi hash (Chain Continuity): prev_hash của bản ghi hiện tại
       phải khớp 100% với hash của bản ghi trước đó và sequence liên tục.
    """
    events: List[BatchEvent] = (
        db.query(BatchEvent)
        .filter(BatchEvent.batch_id == batch_id)
        .order_by(BatchEvent.sequence.asc(), BatchEvent.id.asc())
        .all()
    )

    total_events = len(events)
    if total_events == 0:
        return IntegrityVerificationReport(
            batch_id=batch_id,
            is_valid=True,
            status="VERIFIED",
            total_events=0,
            verified_count=0,
            detail="Lô nông sản chưa có sự kiện nào được ghi nhận trong chuỗi.",
        )

    verified_count = 0
    previous_event: Optional[BatchEvent] = None

    for idx, event in enumerate(events):
        # 1. Kiểm tra tính toàn vẹn nội dung của chính bản ghi này trước
        recomputed_hash = calculate_event_hash(
            batch_id=event.batch_id,
            sequence=event.sequence,
            event_type=event.event_type,
            organization=event.organization,
            data=event.data,
            timestamp=event.timestamp,
            prev_hash=event.prev_hash,
        )

        if event.hash != recomputed_hash:
            return IntegrityVerificationReport(
                batch_id=batch_id,
                is_valid=False,
                status="TAMPERED",
                total_events=total_events,
                verified_count=verified_count,
                tamper_type=TamperType.DATA_MODIFIED.value,
                tampered_event_id=event.id,
                tampered_sequence=event.sequence,
                recorded_hash=event.hash,
                expected_hash=recomputed_hash,
                detail=(
                    f"Phát hiện sửa lén dữ liệu tại sự kiện #{event.id} (loại: {event.event_type}, "
                    f"thứ tự #{event.sequence}): Hash lưu trữ ({event.hash[:16]}...) không khớp "
                    f"với Hash tính toán lại từ nội dung ({recomputed_hash[:16]}...). "
                    f"Bản ghi đã bị can thiệp trái phép trực tiếp bằng câu lệnh SQL."
                ),
            )

        # 2. Kiểm tra mắt xích khởi nguyên (Genesis) hoặc nối tiếp (Continuity)
        if idx == 0:
            if event.prev_hash != GENESIS_HASH:
                return IntegrityVerificationReport(
                    batch_id=batch_id,
                    is_valid=False,
                    status="TAMPERED",
                    total_events=total_events,
                    verified_count=verified_count,
                    tamper_type=TamperType.GENESIS_HASH_TAMPERED.value,
                    tampered_event_id=event.id,
                    tampered_sequence=event.sequence,
                    recorded_prev_hash=event.prev_hash,
                    expected_prev_hash=GENESIS_HASH,
                    detail=f"Mã băm khởi nguyên của sự kiện #{event.id} bị sai lệch (mong đợi Genesis Hash 64 số 0).",
                )
            if event.sequence != 1:
                return IntegrityVerificationReport(
                    batch_id=batch_id,
                    is_valid=False,
                    status="TAMPERED",
                    total_events=total_events,
                    verified_count=verified_count,
                    tamper_type=TamperType.RECORD_DELETED_OR_CHAIN_BROKEN.value,
                    tampered_event_id=event.id,
                    tampered_sequence=event.sequence,
                    detail=f"Sự kiện đầu tiên có số thứ tự là {event.sequence} (mong đợi là 1). Có thể bản ghi đầu tiên đã bị xoá.",
                )
        else:
            assert previous_event is not None
            # Kiểm tra xem prev_hash có khớp với hash bản ghi trước không
            if event.prev_hash != previous_event.hash:
                return IntegrityVerificationReport(
                    batch_id=batch_id,
                    is_valid=False,
                    status="TAMPERED",
                    total_events=total_events,
                    verified_count=verified_count,
                    tamper_type=TamperType.RECORD_DELETED_OR_CHAIN_BROKEN.value,
                    tampered_event_id=event.id,
                    tampered_sequence=event.sequence,
                    recorded_prev_hash=event.prev_hash,
                    expected_prev_hash=previous_event.hash,
                    detail=(
                        f"Phát hiện đứt gãy chuỗi băm tại sự kiện #{event.id} (thứ tự #{event.sequence}): "
                        f"prev_hash không khớp với hash của sự kiện trước đó #{previous_event.id}. "
                        f"Dấu hiệu bản ghi trung gian đã bị xoá khỏi cơ sở dữ liệu."
                    ),
                )
            # Kiểm tra sequence liên tục
            if event.sequence != previous_event.sequence + 1:
                return IntegrityVerificationReport(
                    batch_id=batch_id,
                    is_valid=False,
                    status="TAMPERED",
                    total_events=total_events,
                    verified_count=verified_count,
                    tamper_type=TamperType.RECORD_DELETED_OR_CHAIN_BROKEN.value,
                    tampered_event_id=event.id,
                    tampered_sequence=event.sequence,
                    detail=(
                        f"Số thứ tự sequence bị nhảy cóc từ #{previous_event.sequence} sang #{event.sequence}. "
                        f"Phát hiện bản ghi đã bị xoá."
                    ),
                )

        verified_count += 1
        previous_event = event

    return IntegrityVerificationReport(
        batch_id=batch_id,
        is_valid=True,
        status="VERIFIED",
        total_events=total_events,
        verified_count=verified_count,
        detail=f"Toàn bộ {verified_count}/{total_events} sự kiện hoàn toàn toàn vẹn, chuỗi băm SHA-256 hợp lệ 100%.",
    )


# Bí danh dùng cho T-28
verify_batch_chain = verify_batch_events_integrity


def build_10_events_for_batch(db: Session, batch_id: int) -> List[BatchEvent]:
    """Dựng một chuỗi mẫu gồm đúng 10 sự kiện chuẩn cho một lô nông sản theo quy trình chuỗi lạnh."""
    # Xoá sự kiện cũ của lô (nếu có) trước khi dựng lại
    db.query(BatchEvent).filter(BatchEvent.batch_id == batch_id).delete()
    db.commit()

    scenario_events = [
        (
            "HARVEST",
            "Hợp tác xã Nông nghiệp Cao Lãnh",
            '{"stage": "Thu hoạch", "weight_kg": 1500.0, "quality": "VietGAP Hạng A", "field": "Thửa đất #01", "humidity": "75%"}',
        ),
        (
            "QUALITY_INSPECTION",
            "Chi cục Trồng trọt & BVTV Tỉnh Đồng Tháp",
            '{"stage": "Kiểm định chất lượng", "inspector": "KTV Nguyễn Văn An", "result": "Đạt chuẩn xuất khẩu", "cert_no": "GAP-2026-DT"}',
        ),
        (
            "WASH_AND_SORT",
            "Trung tâm Sơ chế Nông sản Mỹ Xương",
            '{"stage": "Sơ chế & Phân loại", "method": "Rửa ozone tiệt trùng & phân loại kích cỡ đồng đều", "standard": "Grade 1"}',
        ),
        (
            "PACKAGING",
            "Xưởng Đóng gói Xuất khẩu Mekong",
            '{"stage": "Đóng gói & Dán nhãn", "package_type": "Thùng carton 5 lớp có túi hút khí", "qr_batch": "LOT-VN-2026-01"}',
        ),
        (
            "COLD_STORAGE_IN",
            "Kho lạnh Bảo quản Nông sản Mỹ Xương #2",
            '{"stage": "Nhập kho lạnh", "facility": "Kho lạnh Mỹ Xương #2", "temp_c": 4.0, "humidity_pct": 90, "rack": "Khu A-03"}',
        ),
        (
            "TEMPERATURE_LOG",
            "Hệ thống Giám sát Cảm biến IoT Cold-Chain",
            '{"stage": "Giám sát chuỗi lạnh", "sensor_id": "SN-TEMP-04", "temp_c": 3.8, "humidity_pct": 88, "status": "OPTIMAL"}',
        ),
        (
            "TRANSPORT_DISPATCH",
            "Công ty Vận tải Lạnh Á Châu",
            '{"stage": "Xuất kho vận chuyển", "vehicle_plate": "66C-123.45", "driver": "Trần Quốc Toản", "reefer_temp_c": 4.0}',
        ),
        (
            "TRANSIT_TELEMETRY",
            "Đội xe Lạnh Mekong Express",
            '{"stage": "Hành trình xe lạnh", "location": "Tiền Giang", "temp_c": 4.2, "door_opened": false, "gps_speed_kmh": 65}',
        ),
        (
            "DISTRIBUTION_CENTER",
            "Trung tâm Logistics Thủ Đức (TP.HCM)",
            '{"stage": "Nhập trung tâm phân phối", "hub": "DC Logistics Thủ Đức", "seal_intact": true, "received_temp_c": 4.1}',
        ),
        (
            "RETAIL_HANDOVER",
            "Hệ thống Siêu thị WinMart",
            '{"stage": "Bàn giao điểm bán lẻ", "retailer": "Hệ thống Siêu thị WinMart", "received_by": "Lê Thị Thu", "shelf_status": "Sẵn sàng lên kệ"}',
        ),
    ]

    created_events: List[BatchEvent] = []
    base_time = datetime(2026, 9, 25, 6, 30, 0, tzinfo=timezone.utc)

    for idx, (ev_type, ev_org, ev_data) in enumerate(scenario_events, start=1):
        # Mỗi sự kiện cách nhau thời gian tăng dần
        hour_offset = idx * 2
        ev_time = base_time.replace(hour=(base_time.hour + hour_offset) % 24).isoformat()
        ev = record_batch_event(
            db=db,
            batch_id=batch_id,
            event_type=ev_type,
            organization=ev_org,
            data=ev_data,
            timestamp=ev_time,
            auto_commit=False,
        )
        created_events.append(ev)

    db.commit()
    for ev in created_events:
        db.refresh(ev)

    return created_events


def simulate_tamper_event(db: Session, batch_id: int, event_id: Optional[int] = None) -> Optional[BatchEvent]:
    """Giả lập hành vi sửa lén SQL trực tiếp vào dữ liệu sự kiện để test tính năng cảnh báo T-28."""
    events = (
        db.query(BatchEvent)
        .filter(BatchEvent.batch_id == batch_id)
        .order_by(BatchEvent.sequence.asc())
        .all()
    )
    if not events:
        # Nếu chưa có sự kiện thì dựng trước 10 sự kiện
        events = build_10_events_for_batch(db, batch_id)

    target_event = None
    if event_id:
        target_event = db.get(BatchEvent, event_id)

    if target_event is None:
        # Chọn sự kiện lưu kho lạnh (thứ 5) để sửa lén
        target_event = events[4] if len(events) >= 5 else events[0]

    # Sửa lén dữ liệu bằng SQL thô (bỏ qua hàm băm để giả lập hacker/kẻ xấu can thiệp trực tiếp CSDL)
    tampered_data = '{"stage": "Lưu kho lạnh", "facility": "Kho lậu không phép", "temp_c": 18.5, "status": "VI PHẠM NHIỆT ĐỘ"}'
    db.execute(
        text("UPDATE batch_events SET data = :new_data WHERE id = :event_id"),
        {"new_data": tampered_data, "event_id": target_event.id},
    )
    db.commit()
    db.refresh(target_event)
    return target_event


def reset_batch_events(db: Session, batch_id: int) -> List[BatchEvent]:
    """Khôi phục lại chuỗi 10 sự kiện chuẩn nguyên vẹn 100%."""
    return build_10_events_for_batch(db, batch_id)
