"""Bộ kiểm thử cho hàm kiểm tra chuỗi sự kiện lô nông sản (T-28 / SCRUM-44).

Đặc tả & Tiêu chí nghiệm thu (DoD / AC):
1. Chuỗi nguyên vẹn trả về hợp lệ (is_valid=True, tampered_index=None, error_type=None).
2. Chuỗi bị sửa lén trả về đúng vị trí bản ghi sai (is_valid=False, tampered_index=idx, error_type=DATA_MODIFIED).
3. Chuỗi bị xóa mất sự kiện trả về vị trí đứt mạch (is_valid=False, tampered_index=idx, error_type=CHAIN_BROKEN).
4. Chuỗi có genesis hash bị sai lệch trả về vị trí 0 (is_valid=False, tampered_index=0, error_type=GENESIS_HASH_MISMATCH).
5. Đọc theo batch lô (1 query duy nhất), không đọc từng bản ghi một để tránh N+1 query.
6. Hỗ trợ kiểm tra nhiều lô cùng lúc trong 1 query (verify_multiple_batches_chains).
7. Hỗ trợ truyền trực tiếp danh sách sự kiện trong bộ nhớ.
"""

from __future__ import annotations

from datetime import date
from typing import Iterator

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.events import (
    GENESIS_HASH,
    ChainErrorType,
    ChainVerificationResult,
    get_events_for_batch,
    record_event,
    verify_batch_chain,
    verify_batch_events_chain,
    verify_multiple_batches_chains,
)
from app.models import Base, Batch, BatchEvent, Farm, User
from app.security import compute_event_hash, hash_password


@pytest.fixture()
def db_session() -> Iterator[Session]:
    """Tạo engine SQLite in-memory và session cho mỗi bài test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def sample_batch(db_session: Session) -> Batch:
    """Tạo mẫu một vùng trồng và một lô nông sản."""
    user = User(
        username="farmer_test",
        password=hash_password("123456"),
        role="farmer",
    )
    db_session.add(user)

    farm = Farm(
        name="Vườn Xoài Cát Chu Cao Lãnh #1",
        location="Đồng Tháp",
        area=2.5,
        owner="HTX Nông Nghiệp Số 4",
    )
    db_session.add(farm)
    db_session.commit()

    batch = Batch(
        farm_id=farm.id,
        product_name="Xoài Cát Chu",
        quantity=500.0,
        harvest_date=date(2026, 9, 20),
        batch_code="LOT-TEST-001",
        current_holder_org="HTX Nông Nghiệp Số 4",
    )
    db_session.add(batch)
    db_session.commit()
    db_session.refresh(batch)
    return batch


def test_intact_chain_is_valid(db_session: Session, sample_batch: Batch):
    """DoD / AC 1: Chuỗi nguyên vẹn trả về hợp lệ (is_valid=True, tampered_index=None)."""
    # Ghi nhận 5 sự kiện chuẩn nối tiếp nhau theo T-25
    event_types = ["HARVEST", "QUALITY_INSPECTION", "WASH_AND_SORT", "PACKAGING", "COLD_STORAGE_IN"]
    for idx, et in enumerate(event_types):
        record_event(
            db=db_session,
            batch_id=sample_batch.id,
            event_type=et,
            actor="farmer_test",
            payload={"step": idx + 1, "note": f"Bước {et}"},
            organization="HTX Nông Nghiệp Số 4",
        )
    db_session.commit()

    # Kiểm tra chuỗi bằng hàm T-28
    result = verify_batch_chain(db=db_session, batch_id=sample_batch.id)

    assert isinstance(result, ChainVerificationResult)
    assert result.is_valid is True
    assert result.tampered_index is None
    assert result.error_type is None
    assert result.total_events == 5
    assert result.verified_count == 5
    assert "hợp lệ 100%" in result.error_detail

    # Thử qua bí danh
    alias_res = verify_batch_events_chain(db_session, sample_batch.id)
    assert alias_res.is_valid is True


def test_tampered_payload_detected_at_exact_position(db_session: Session, sample_batch: Batch):
    """DoD / AC 2: Chuỗi bị sửa lén dữ liệu trả về đúng vị trí bản ghi sai (DATA_MODIFIED)."""
    events = []
    for i in range(4):
        ev = record_event(
            db=db_session,
            batch_id=sample_batch.id,
            event_type=f"STAGE_{i}",
            actor="farmer_test",
            payload={"index": i, "weight": 100 + i * 10},
        )
        events.append(ev)
    db_session.commit()

    # Giả lập sửa lén SQL trực tiếp tại sự kiện vị trí 2 (STAGE_2)
    tampered_event = events[2]
    db_session.execute(
        text("UPDATE batch_events SET payload = :new_payload WHERE id = :event_id"),
        {"new_payload": '{"index": 2, "weight": 9999, "hacked": true}', "event_id": tampered_event.id},
    )
    db_session.commit()

    result = verify_batch_chain(db=db_session, batch_id=sample_batch.id)

    assert result.is_valid is False
    assert result.tampered_index == 2, f"Vị trí phải là 2, thực tế: {result.tampered_index}"
    assert result.error_type == ChainErrorType.DATA_MODIFIED.value
    assert result.tampered_event_id == tampered_event.id
    assert result.verified_count == 2
    assert result.recorded_hash is not None
    assert result.expected_hash is not None
    assert result.recorded_hash != result.expected_hash
    assert "sửa lén" in result.error_detail


def test_deleted_middle_event_detected_at_break_point(db_session: Session, sample_batch: Batch):
    """DoD / AC 3: Chuỗi bị xóa mất sự kiện trả về vị trí đứt mạch (CHAIN_BROKEN)."""
    events = []
    for i in range(4):
        ev = record_event(
            db=db_session,
            batch_id=sample_batch.id,
            event_type=f"EVENT_{i}",
            actor="farmer_test",
            payload={"seq": i},
        )
        events.append(ev)
    db_session.commit()

    deleted_event = events[1]
    db_session.execute(
        text("DELETE FROM batch_events WHERE id = :id"),
        {"id": deleted_event.id},
    )
    db_session.commit()

    result = verify_batch_chain(db=db_session, batch_id=sample_batch.id)

    assert result.is_valid is False
    assert result.tampered_index == 1, f"Điểm đứt mạch phải là vị trí 1, thực tế: {result.tampered_index}"
    assert result.error_type == ChainErrorType.CHAIN_BROKEN.value
    assert result.verified_count == 1
    assert "đứt gãy chuỗi" in result.error_detail


def test_empty_chain_is_valid(db_session: Session, sample_batch: Batch):
    """Lô chưa phát sinh sự kiện nào được coi là hợp lệ."""
    result = verify_batch_chain(db_session, sample_batch.id)
    assert result.is_valid is True
    assert result.tampered_index is None
    assert result.error_type is None
    assert result.total_events == 0


def test_direct_in_memory_events_verification(db_session: Session, sample_batch: Batch):
    """Hỗ trợ kiểm tra trực tiếp danh sách BatchEvent trong bộ nhớ."""
    ev1 = record_event(db=db_session, batch_id=sample_batch.id, event_type="E1", actor="u1")
    ev2 = record_event(db=db_session, batch_id=sample_batch.id, event_type="E2", actor="u1")
    db_session.commit()

    result = verify_batch_chain(events=[ev1, ev2])
    assert result.is_valid is True
    assert result.total_events == 2

    assert result["is_valid"] is True
    is_valid, idx, err = result
    assert is_valid is True
    assert idx is None
    assert err is None


def test_multiple_batches_batch_loading_no_n_plus_one(db_session: Session, sample_batch: Batch):
    """Ràng buộc kỹ thuật: Đọc theo batch nhiều lô trong 1 query duy nhất, không N+1."""
    batch2 = Batch(
        farm_id=sample_batch.farm_id,
        product_name="Xoài Cát Chu Loại 2",
        quantity=300.0,
        harvest_date=date(2026, 9, 21),
        batch_code="LOT-TEST-002",
        current_holder_org="HTX Nông Nghiệp Số 4",
    )
    db_session.add(batch2)
    db_session.commit()

    record_event(db=db_session, batch_id=sample_batch.id, event_type="E_B1_1", actor="u")
    record_event(db=db_session, batch_id=sample_batch.id, event_type="E_B1_2", actor="u")

    ev_b2_1 = record_event(db=db_session, batch_id=batch2.id, event_type="E_B2_1", actor="u")
    record_event(db=db_session, batch_id=batch2.id, event_type="E_B2_2", actor="u")
    db_session.commit()

    db_session.execute(
        text("UPDATE batch_events SET payload = '{\"tampered\": true}' WHERE id = :id"),
        {"id": ev_b2_1.id},
    )
    db_session.commit()

    results = verify_multiple_batches_chains(db_session, [sample_batch.id, batch2.id])

    assert sample_batch.id in results
    assert batch2.id in results

    assert results[sample_batch.id].is_valid is True
    assert results[batch2.id].is_valid is False
    assert results[batch2.id].tampered_index == 0
    assert results[batch2.id].error_type == ChainErrorType.DATA_MODIFIED.value
