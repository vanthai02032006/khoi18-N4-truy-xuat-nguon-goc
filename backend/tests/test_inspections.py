"""Bộ kiểm thử đơn vị & kiểm thử chấp nhận (DoD / Acceptance Criteria) cho Task T-28 (SCRUM-44).

Tiêu chí nghiệm thu:
1. Chỉ vai trò cán bộ kiểm tra (inspector) và vai trò cao hơn (admin) mới được phép thực hiện.
2. Lô nguyên vẹn: trả về is_valid = True (hiển thị màu xanh).
3. Lô bị can thiệp (sửa lén payload hoặc đứt gãy chuỗi): trả về is_valid = False (cảnh báo đỏ) kèm vị trí và loại lỗi.
4. Mọi lần kiểm định đều được ghi log vào database kèm thời điểm (timestamp) để đối chiếu về sau.
"""

from datetime import date, datetime, timezone
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Đảm bảo import package `app`
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.models import (
    Base,
    Batch,
    BatchEvent,
    Farm,
    InspectionLog,
    ROLE_ADMIN,
    ROLE_FARMER,
    ROLE_INSPECTOR,
    User,
)
from app.routers.inspections import verify_batch_integrity
from app.security import compute_event_hash, hash_password
from fastapi import HTTPException


@pytest.fixture
def db_session():
    """Tạo in-memory SQLite database cho kiểm thử độc lập."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionTest = sessionmaker(bind=engine)
    session = SessionTest()

    # Nạp người dùng mẫu cho các vai trò
    admin = User(username="admin_user", password=hash_password("123456"), role=ROLE_ADMIN)
    inspector = User(username="inspector_user", password=hash_password("123456"), role=ROLE_INSPECTOR)
    farmer = User(username="farmer_user", password=hash_password("123456"), role=ROLE_FARMER)

    # Nạp Farm và Batch mẫu
    farm = Farm(name="Vườn xoài mẫu", location="Đồng Tháp", area=3.5, owner="Hộ nông dân A")
    session.add_all([admin, inspector, farmer, farm])
    session.commit()

    batch = Batch(farm_id=farm.id, product_name="Xoài Cát Chu", quantity=500.0, harvest_date=date(2026, 1, 15), code="ABC23456")
    session.add(batch)
    session.commit()

    yield session
    session.close()


def create_valid_event_chain(db: Session, batch_id: int) -> list[BatchEvent]:
    """Tạo một chuỗi 3 sự kiện hợp lệ có mã băm liên kết chặt chẽ."""
    events_data = [
        ("HARVEST", '{"action": "Thu hoạch", "weight": 500}'),
        ("PROCESSING", '{"action": "Làm sạch & đóng gói", "standard": "VietGAP"}'),
        ("HANDOVER", '{"action": "Bàn giao xe lạnh", "temp": "4.5C"}'),
    ]

    prev_hash = "0" * 64
    created_events = []

    for idx, (etype, payload) in enumerate(events_data):
        now_ts = datetime.now(timezone.utc).isoformat()
        ev_hash = compute_event_hash(
            event_type=etype,
            payload=payload,
            actor="farmer_user",
            organization="HTX Nông Nghiệp",
            timestamp=now_ts,
            previous_hash=prev_hash,
        )
        ev = BatchEvent(
            batch_id=batch_id,
            event_type=etype,
            payload=payload,
            actor="farmer_user",
            organization="HTX Nông Nghiệp",
            timestamp=now_ts,
            hash=ev_hash,
            previous_hash=prev_hash,
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)
        created_events.append(ev)
        prev_hash = ev_hash

    return created_events


class TestInspectionDoD:
    """Kiểm thử tiêu chí nghiệm thu Task T-28 (SCRUM-44)."""

    def test_permission_guard(self, db_session: Session):
        """Chỉ vai trò cán bộ kiểm tra (inspector) và cao hơn (admin) mới được phép thẩm định."""
        from app.security import require_inspector

        farmer = db_session.query(User).filter_by(role=ROLE_FARMER).first()
        inspector = db_session.query(User).filter_by(role=ROLE_INSPECTOR).first()
        admin = db_session.query(User).filter_by(role=ROLE_ADMIN).first()

        # Farmer bị chặn 403 Forbidden
        with pytest.raises(HTTPException) as exc_info:
            require_inspector(current_user=farmer)
        assert exc_info.value.status_code == 403

        # Inspector được phép qua
        assert require_inspector(current_user=inspector).username == "inspector_user"

        # Admin (vai trò cao hơn) được phép qua
        assert require_inspector(current_user=admin).username == "admin_user"

    def test_valid_batch_shows_green_and_records_log(self, db_session: Session):
        """DoD 1: Lô nguyên vẹn hiện kết quả hợp lệ (xanh) và ghi log kèm thời điểm."""
        batch = db_session.query(Batch).first()
        inspector = db_session.query(User).filter_by(role=ROLE_INSPECTOR).first()

        # Tạo chuỗi sự kiện nguyên vẹn
        create_valid_event_chain(db_session, batch.id)

        # Cán bộ kiểm tra thực hiện thẩm định
        result = verify_batch_integrity(identifier=batch.code, current_user=inspector, db=db_session)

        # Khẳng định kết quả hợp lệ
        assert result.is_valid is True
        assert result.error_type is None
        assert result.tampered_index is None
        assert result.total_events == 3
        assert "nguyên vẹn và hợp lệ 100%" in result.details

        # Khẳng định đã ghi log vào database kèm thời điểm để đối chiếu về sau
        logs = db_session.query(InspectionLog).filter_by(batch_id=batch.id).all()
        assert len(logs) == 1
        log = logs[0]
        assert log.is_valid is True
        assert log.inspector == inspector.username
        assert log.batch_code == batch.code
        assert log.timestamp is not None

    def test_tampered_payload_shows_red_and_identifies_position(self, db_session: Session):
        """DoD 2: Lô bị can thiệp sửa lén nội dung hiển thị cảnh báo đỏ và chỉ rõ vị trí và loại lỗi."""
        batch = db_session.query(Batch).first()
        inspector = db_session.query(User).filter_by(role=ROLE_INSPECTOR).first()

        events = create_valid_event_chain(db_session, batch.id)

        # Cố tình sửa lén nội dung (payload) của sự kiện số 2 (index 1)
        tampered_event = events[1]
        tampered_event.payload = '{"action": "DA BI SUA LEN GIA MAO"}'
        db_session.commit()

        # Cán bộ kiểm tra thẩm định
        result = verify_batch_integrity(identifier=batch.code, current_user=inspector, db=db_session)

        # Khẳng định phát hiện vi phạm cảnh báo đỏ
        assert result.is_valid is False
        assert result.error_type == "TAMPERED_PAYLOAD"
        assert result.tampered_index == 1  # Vị trí sự kiện số 2 (index 1)
        assert result.tampered_event_id == tampered_event.id
        assert "Phát hiện sai lệch mã băm nội dung" in result.details

        # Khẳng định log đã ghi nhận cảnh báo vi phạm
        latest_log = db_session.query(InspectionLog).order_by(InspectionLog.id.desc()).first()
        assert latest_log.is_valid is False
        assert latest_log.error_type == "TAMPERED_PAYLOAD"
        assert latest_log.tampered_index == 1

    def test_broken_chain_link_shows_red_and_identifies_position(self, db_session: Session):
        """DoD 3: Lô bị đứt gãy liên kết chuỗi (previous_hash bị sai) hiển thị cảnh báo đỏ và chỉ rõ vị trí."""
        batch = db_session.query(Batch).first()
        inspector = db_session.query(User).filter_by(role=ROLE_INSPECTOR).first()

        events = create_valid_event_chain(db_session, batch.id)

        # Cố tình phá vỡ previous_hash của sự kiện số 3 (index 2)
        events[2].previous_hash = "f" * 64
        db_session.commit()

        result = verify_batch_integrity(identifier=batch.code, current_user=inspector, db=db_session)

        assert result.is_valid is False
        assert result.error_type == "BROKEN_CHAIN"
        assert result.tampered_index == 2  # Vị trí sự kiện số 3 (index 2)
        assert "Phát hiện đứt gãy liên kết chuỗi" in result.details
