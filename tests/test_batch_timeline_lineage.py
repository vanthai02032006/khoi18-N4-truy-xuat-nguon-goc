"""Kiểm thử dòng thời gian truy xuất nguồn gốc lô mẹ và lô con khi tách hoặc gộp (AC Verification).

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
1. Giả sử một lô mẹ vừa tách thành ba lô con:
   - Khi xem dòng thời gian lô mẹ (GET /batches/{id}/events), thấy một sự kiện tách (SPLIT) nêu ba mã lô con.
   - Khi xem dòng thời gian từng lô con, thấy sự kiện khai sinh (BIRTH) nêu mã lô mẹ.
2. Giả sử ba lô vừa gộp:
   - Khi xem dòng thời gian lô mới, thấy sự kiện gộp (MERGE) nêu cả ba lô mẹ và khối lượng lấy từ mỗi lô.
   - Khi xem dòng thời gian từng lô mẹ, thấy sự kiện (MERGE_PARENT) trỏ tới lô gộp mới.
3. Giả sử vừa tách hoặc gộp xong:
   - Khi chạy kiểm tra toàn vẹn mọi lô liên quan (GET /batches/{id}/events -> is_valid, tampered_index),
     thì tất cả đều hợp lệ (is_valid == True, tampered_index is None).
"""

from __future__ import annotations

import json
from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, Farm, User, ROLE_FARMER
from app.security import hash_password


@pytest.fixture
def timeline_env():
    """Thiết lập môi trường kiểm thử SQLite in-memory."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        user = User(
            username="inspector_user",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        db.add(user)

        farm = Farm(
            name="Vùng Trồng Cây Ăn Quả Tiêu Chuẩn",
            location="Đồng Nai",
            area=5.0,
            owner="HTX Nông Nghiệp Số 4",
        )
        db.add(farm)
        db.commit()

        # Tạo lô mẹ A (100kg)
        batch_parent = Batch(
            farm_id=farm.id,
            product_name="Xoài Cát Hoà Lộc",
            quantity=100.0,
            harvest_date=date(2026, 6, 15),
            batch_code="LOT-PARENT-001",
            current_holder_org="HTX Nông Nghiệp Số 4",
        )
        # Tạo 3 lô độc lập để phục vụ gộp
        batch_m1 = Batch(
            farm_id=farm.id,
            product_name="Bưởi Da Xanh",
            quantity=50.0,
            harvest_date=date(2026, 6, 10),
            batch_code="LOT-MERGE-P1",
            current_holder_org="HTX Nông Nghiệp Số 4",
        )
        batch_m2 = Batch(
            farm_id=farm.id,
            product_name="Bưởi Da Xanh",
            quantity=40.0,
            harvest_date=date(2026, 6, 11),
            batch_code="LOT-MERGE-P2",
            current_holder_org="HTX Nông Nghiệp Số 4",
        )
        batch_m3 = Batch(
            farm_id=farm.id,
            product_name="Bưởi Da Xanh",
            quantity=60.0,
            harvest_date=date(2026, 6, 12),
            batch_code="LOT-MERGE-P3",
            current_holder_org="HTX Nông Nghiệp Số 4",
        )
        db.add_all([batch_parent, batch_m1, batch_m2, batch_m3])
        db.commit()

        parent_id = batch_parent.id
        m1_id, m2_id, m3_id = batch_m1.id, batch_m2.id, batch_m3.id

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "client": client,
        "parent_id": parent_id,
        "m1_id": m1_id,
        "m2_id": m2_id,
        "m3_id": m3_id,
        "session_factory": TestingSessionLocal,
    }

    app.dependency_overrides.clear()


def test_split_timeline_and_lineage_integrity(timeline_env):
    """AC 1 & AC 3: Tách 1 lô mẹ thành 3 lô con:

    - Timeline lô mẹ: có sự kiện SPLIT nêu đủ 3 mã lô con.
    - Timeline từng lô con: có sự kiện BIRTH nêu rõ mã lô mẹ.
    - Kiểm tra toàn vẹn hash-chain: cả lô mẹ và 3 lô con đều is_valid == True.
    """
    client = timeline_env["client"]
    parent_id = timeline_env["parent_id"]
    auth = ("inspector_user", "123456")

    # 1. Gọi API tách lô mẹ thành 3 lô con: 20kg, 30kg, 15kg
    split_payload = {
        "items": [
            {"quantity": 20.0, "note": "Lô con 1"},
            {"quantity": 30.0, "note": "Lô con 2"},
            {"quantity": 15.0, "note": "Lô con 3"},
        ]
    }
    split_res = client.post(f"/batches/{parent_id}/split", json=split_payload, auth=auth)
    assert split_res.status_code == 201, split_res.text
    split_data = split_res.json()
    assert len(split_data["child_batches"]) == 3
    child_codes = [c["batch_code"] for c in split_data["child_batches"]]
    child_ids = [c["id"] for c in split_data["child_batches"]]

    # 2. Xem dòng thời gian lô mẹ
    parent_timeline_res = client.get(f"/batches/{parent_id}/events", auth=auth)
    assert parent_timeline_res.status_code == 200
    parent_timeline = parent_timeline_res.json()

    # Kiểm tra toàn vẹn lô mẹ
    assert parent_timeline["is_valid"] is True
    assert parent_timeline["tampered_index"] is None

    # Tìm sự kiện SPLIT trên lô mẹ
    split_events = [ev for ev in parent_timeline["events"] if ev["event_type"] == "SPLIT"]
    assert len(split_events) >= 1
    parent_split_ev = split_events[-1]
    parent_payload = json.loads(parent_split_ev["payload"])

    # Phải nêu rõ 3 mã lô con
    assert "child_batch_codes" in parent_payload
    for code in child_codes:
        assert code in parent_payload["child_batch_codes"]
    assert len(parent_payload["child_batch_codes"]) == 3

    # 3. Xem dòng thời gian từng lô con
    for cid, ccode in zip(child_ids, child_codes):
        child_timeline_res = client.get(f"/batches/{cid}/events", auth=auth)
        assert child_timeline_res.status_code == 200
        child_timeline = child_timeline_res.json()

        # Kiểm tra toàn vẹn lô con
        assert child_timeline["is_valid"] is True
        assert child_timeline["tampered_index"] is None

        # Tìm sự kiện BIRTH
        birth_events = [ev for ev in child_timeline["events"] if ev["event_type"] == "BIRTH"]
        assert len(birth_events) == 1
        birth_ev = birth_events[0]
        birth_payload = json.loads(birth_ev["payload"])

        # Phải nêu rõ mã lô mẹ
        assert birth_payload.get("parent_batch_code") == "LOT-PARENT-001"
        assert birth_payload.get("parent_batch_id") == parent_id
        assert birth_payload.get("batch_code") == ccode


def test_merge_timeline_and_lineage_integrity(timeline_env):
    """AC 2 & AC 3: Gộp 3 lô mẹ thành 1 lô mới:

    - Timeline lô mới: có sự kiện MERGE nêu cả 3 lô mẹ và khối lượng lấy từ mỗi lô.
    - Timeline từng lô mẹ: có sự kiện MERGE_PARENT nêu mã lô gộp mới.
    - Kiểm tra toàn vẹn hash-chain: cả lô mới và 3 lô mẹ đều is_valid == True.
    """
    client = timeline_env["client"]
    m1_id = timeline_env["m1_id"]
    m2_id = timeline_env["m2_id"]
    m3_id = timeline_env["m3_id"]
    auth = ("inspector_user", "123456")

    # 1. Gọi API gộp 3 lô: lấy từ m1 15kg, m2 20kg, m3 25kg (tổng 60kg)
    merge_payload = {
        "items": [
            {"batch_id": m1_id, "quantity": 15.0},
            {"batch_id": m2_id, "quantity": 20.0},
            {"batch_id": m3_id, "quantity": 25.0},
        ],
        "product_name": "Bưởi Da Xanh Đóng Thùng",
        "note": "Gộp 3 vườn đóng thùng xuất khẩu",
    }
    merge_res = client.post("/batches/merge", json=merge_payload, auth=auth)
    assert merge_res.status_code == 201, merge_res.text
    merge_data = merge_res.json()

    merged_batch_id = merge_data["merged_batch"]["id"]
    merged_batch_code = merge_data["merged_batch"]["batch_code"]
    assert merge_data["merged_batch"]["quantity"] == 60.0

    # 2. Xem dòng thời gian lô gộp mới
    merged_timeline_res = client.get(f"/batches/{merged_batch_id}/events", auth=auth)
    assert merged_timeline_res.status_code == 200
    merged_timeline = merged_timeline_res.json()

    # Kiểm tra toàn vẹn lô gộp mới
    assert merged_timeline["is_valid"] is True
    assert merged_timeline["tampered_index"] is None

    # Tìm sự kiện MERGE
    merge_events = [ev for ev in merged_timeline["events"] if ev["event_type"] == "MERGE"]
    assert len(merge_events) == 1
    merge_ev = merge_events[0]
    merge_payload_dict = json.loads(merge_ev["payload"])

    # Sự kiện MERGE phải nêu cả 3 mã lô mẹ và khối lượng lấy từ mỗi lô
    assert merge_payload_dict.get("total_merged_quantity") == 60.0
    parent_batches_info = merge_payload_dict.get("parent_batches", [])
    assert len(parent_batches_info) == 3

    p_dict = {p["batch_code"]: p["quantity"] for p in parent_batches_info}
    assert p_dict.get("LOT-MERGE-P1") == 15.0
    assert p_dict.get("LOT-MERGE-P2") == 20.0
    assert p_dict.get("LOT-MERGE-P3") == 25.0
    assert "LOT-MERGE-P1" in merge_payload_dict.get("parent_batch_codes", [])
    assert "LOT-MERGE-P2" in merge_payload_dict.get("parent_batch_codes", [])
    assert "LOT-MERGE-P3" in merge_payload_dict.get("parent_batch_codes", [])

    # 3. Xem dòng thời gian của TỪNG lô mẹ
    for pid, pcode in [(m1_id, "LOT-MERGE-P1"), (m2_id, "LOT-MERGE-P2"), (m3_id, "LOT-MERGE-P3")]:
        p_timeline_res = client.get(f"/batches/{pid}/events", auth=auth)
        assert p_timeline_res.status_code == 200
        p_timeline = p_timeline_res.json()

        # Kiểm tra toàn vẹn từng lô mẹ
        assert p_timeline["is_valid"] is True
        assert p_timeline["tampered_index"] is None

        # Tìm sự kiện MERGE_PARENT
        mp_events = [ev for ev in p_timeline["events"] if ev["event_type"] == "MERGE_PARENT"]
        assert len(mp_events) >= 1
        mp_ev = mp_events[-1]
        mp_payload = json.loads(mp_ev["payload"])

        # Phải nêu mã lô gộp mới
        assert mp_payload.get("target_merged_batch_code") == merged_batch_code
        assert mp_payload.get("target_merged_batch_id") == merged_batch_id
        assert mp_payload.get("parent_batch_code") == pcode
