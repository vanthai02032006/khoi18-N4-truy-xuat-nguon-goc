"""Pytest Unit Tests cho T-29 (SCRUM-45): Cơ chế chống sửa lén bản ghi chuỗi sự kiện.

Nghiệm thu cả 3 ca kiểm thử trong CI:
1. Sửa lén 1 bản ghi bằng SQL trực tiếp -> Khẳng định phát hiện sửa lén (DATA_MODIFIED).
2. Xoá 1 bản ghi bằng SQL trực tiếp -> Khẳng định phát hiện đứt chuỗi (RECORD_DELETED_OR_CHAIN_BROKEN).
3. Giữ nguyên vẹn 10 sự kiện -> Khẳng định chuỗi toàn vẹn 100% (VERIFIED).
"""

from __future__ import annotations

from datetime import date
from typing import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine, init_db
from app.event_chain import (
    GENESIS_HASH,
    TamperType,
    build_10_events_for_batch,
    calculate_event_hash,
    record_batch_event,
    verify_batch_events_integrity,
)
from app.models import Batch, BatchEvent, Farm


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Khởi tạo database và cấu trúc bảng trước khi chạy tests."""
    init_db()
    yield


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Cung cấp session SQLAlchemy sạch cho mỗi test."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def sample_farm(db_session: Session) -> Farm:
    """Fixture cung cấp một farm cho test."""
    farm = db_session.query(Farm).first()
    if not farm:
        farm = Farm(
            name="Vùng trồng Kiểm Thử CI",
            location="Đồng Tháp",
            area=3.0,
            owner="HTX Nông Nghiệp",
        )
        db_session.add(farm)
        db_session.commit()
        db_session.refresh(farm)
    return farm


def test_tamper_case_1_sql_modified(db_session: Session, sample_farm: Farm):
    """Ca 1: Sửa lén 1 bản ghi bằng SQL trực tiếp -> Khẳng định phát hiện 100%."""
    # 1. Dựng lô và 10 sự kiện
    batch = Batch(
        farm_id=sample_farm.id,
        product_name="Xoài Cát Chu CI - Ca 1",
        quantity=1000.0,
        harvest_date=date(2026, 9, 25),
    )
    db_session.add(batch)
    db_session.commit()
    db_session.refresh(batch)

    try:
        events = build_10_events_for_batch(db_session, batch.id)
        assert len(events) == 10

        # 2. Sửa lén bản ghi thứ 6 bằng câu lệnh SQL thô
        target_event = events[5]
        target_id = target_event.id
        tampered_data = '{"stage": "Giám sát chuỗi lạnh", "temp_c": 19.9, "hacked": true}'

        db_session.execute(
            text("UPDATE batch_events SET data = :new_data WHERE id = :event_id"),
            {"new_data": tampered_data, "event_id": target_id},
        )
        db_session.commit()

        # 3. Kiểm tra tính toàn vẹn
        report = verify_batch_events_integrity(db_session, batch.id)

        # 4. Khẳng định kết quả nghiệm thu
        assert report.is_valid is False
        assert report.status == "TAMPERED"
        assert report.tamper_type == TamperType.DATA_MODIFIED.value
        assert report.tampered_event_id == target_id
        assert report.tampered_sequence == 6
        assert report.recorded_hash != report.expected_hash
    finally:
        db_session.query(BatchEvent).filter(BatchEvent.batch_id == batch.id).delete()
        db_session.query(Batch).filter(Batch.id == batch.id).delete()
        db_session.commit()


def test_tamper_case_2_sql_deleted(db_session: Session, sample_farm: Farm):
    """Ca 2: Xoá 1 bản ghi bằng SQL trực tiếp -> Khẳng định phát hiện đứt gãy 100%."""
    # 1. Dựng lô và 10 sự kiện
    batch = Batch(
        farm_id=sample_farm.id,
        product_name="Xoài Cát Chu CI - Ca 2",
        quantity=1000.0,
        harvest_date=date(2026, 9, 25),
    )
    db_session.add(batch)
    db_session.commit()
    db_session.refresh(batch)

    try:
        events = build_10_events_for_batch(db_session, batch.id)
        assert len(events) == 10

        # 2. Xoá bản ghi thứ 4 (PACKAGING) bằng câu lệnh SQL thô
        deleted_event = events[3]
        deleted_id = deleted_event.id
        subsequent_event = events[4]
        subsequent_id = subsequent_event.id

        db_session.execute(
            text("DELETE FROM batch_events WHERE id = :event_id"),
            {"event_id": deleted_id},
        )
        db_session.commit()

        # 3. Kiểm tra tính toàn vẹn
        report = verify_batch_events_integrity(db_session, batch.id)

        # 4. Khẳng định kết quả nghiệm thu
        assert report.is_valid is False
        assert report.status == "TAMPERED"
        assert report.tamper_type == TamperType.RECORD_DELETED_OR_CHAIN_BROKEN.value
        assert report.tampered_event_id == subsequent_id
    finally:
        db_session.query(BatchEvent).filter(BatchEvent.batch_id == batch.id).delete()
        db_session.query(Batch).filter(Batch.id == batch.id).delete()
        db_session.commit()


def test_tamper_case_3_intact_chain(db_session: Session, sample_farm: Farm):
    """Ca 3: Giữ nguyên vẹn 10 sự kiện -> Khẳng định toàn vẹn 100%."""
    # 1. Dựng lô và 10 sự kiện
    batch = Batch(
        farm_id=sample_farm.id,
        product_name="Xoài Cát Chu CI - Ca 3",
        quantity=1000.0,
        harvest_date=date(2026, 9, 25),
    )
    db_session.add(batch)
    db_session.commit()
    db_session.refresh(batch)

    try:
        events = build_10_events_for_batch(db_session, batch.id)
        assert len(events) == 10

        # 2. Giữ nguyên vẹn, không chỉnh sửa gì

        # 3. Kiểm tra tính toàn vẹn
        report = verify_batch_events_integrity(db_session, batch.id)

        # 4. Khẳng định kết quả nghiệm thu
        assert report.is_valid is True
        assert report.status == "VERIFIED"
        assert report.tamper_type is None
        assert report.total_events == 10
        assert report.verified_count == 10
        assert report.tampered_event_id is None
    finally:
        db_session.query(BatchEvent).filter(BatchEvent.batch_id == batch.id).delete()
        db_session.query(Batch).filter(Batch.id == batch.id).delete()
        db_session.commit()
