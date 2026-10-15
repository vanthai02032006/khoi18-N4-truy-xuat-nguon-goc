"""Kiểm thử tính toàn vẹn mọi lô liên quan sau tách và sau gộp (SCRUM-64 / T-47 / T-28 / T-30).

🎯 Mục tiêu & Mô tả công việc:
Mở rộng kịch bản ở T-30: sau khi tách và gộp, chạy hàm kiểm tra ở T-28 (GET /batches/{id}/events)
cho mọi lô liên quan và khẳng định tất cả đều hợp lệ.
Thêm ca: sửa lén sự kiện khai sinh của một lô con thì chỉ đúng lô con đó báo lỗi,
các lô mẹ, lô con khác và lô gộp liên quan vẫn hoàn toàn toàn vẹn.

✅ Tiêu chí nghiệm thu (DoD / AC):
• Mọi lô liên quan đều hợp lệ sau khi tách và gộp (is_valid == True, tampered_index is None).
• Ca sửa lén chỉ ra chính xác đúng lô con bị can thiệp dữ liệu (is_valid == False, tampered_index không None),
  các lô còn lại trong chuỗi phả hệ không bị ảnh hưởng.
"""

from __future__ import annotations

import json
from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, BatchEvent, Farm, User, ROLE_FARMER
from app.security import hash_password


@pytest.fixture
def scrum64_env():
    """Thiết lập môi trường kiểm thử SQLite in-memory cô lập cho SCRUM-64."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        user = User(
            username="qa_auditor",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        db.add(user)

        farm1 = Farm(
            name="Vùng Trồng Xoài Cát Chu Tháp Mười",
            location="Đồng Tháp",
            area=3.5,
            owner="HTX Nông Nghiệp Số 4",
        )
        farm2 = Farm(
            name="Vùng Trồng Xoài Cát Chu Lấp Vò",
            location="Đồng Tháp",
            area=4.0,
            owner="HTX Nông Nghiệp Số 4",
        )
        db.add_all([farm1, farm2])
        db.commit()

        # Lô mẹ A (100kg)
        batch_parent_a = Batch(
            farm_id=farm1.id,
            product_name="Xoài Cát Chu",
            quantity=100.0,
            harvest_date=date(2026, 6, 1),
            batch_code="LOT-PARENT-A",
            current_holder_org="HTX Nông Nghiệp Số 4",
        )
        # Lô độc lập P2 (60kg) để chuẩn bị gộp chéo
        batch_parent_p2 = Batch(
            farm_id=farm2.id,
            product_name="Xoài Cát Chu",
            quantity=60.0,
            harvest_date=date(2026, 6, 2),
            batch_code="LOT-PARENT-P2",
            current_holder_org="HTX Nông Nghiệp Số 4",
        )
        db.add_all([batch_parent_a, batch_parent_p2])
        db.commit()

        parent_a_id = batch_parent_a.id
        parent_p2_id = batch_parent_p2.id

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "client": client,
        "parent_a_id": parent_a_id,
        "parent_p2_id": parent_p2_id,
        "session_factory": TestingSessionLocal,
    }

    app.dependency_overrides.clear()


def test_integrity_all_batches_after_split_and_merge_and_tamper_detection(scrum64_env):
    """SCRUM-64: Kiểm tra toàn vẹn mọi lô sau tách và gộp, kèm ca sửa lén sự kiện khai sinh chỉ đúng lô con báo lỗi."""
    client = scrum64_env["client"]
    parent_a_id = scrum64_env["parent_a_id"]
    parent_p2_id = scrum64_env["parent_p2_id"]
    session_factory = scrum64_env["session_factory"]
    auth = ("qa_auditor", "123456")

    # -------------------------------------------------------------------------
    # Bước 1: Tách lô mẹ A thành 3 lô con: C1 (25kg), C2 (35kg), C3 (20kg)
    # -------------------------------------------------------------------------
    split_payload = {
        "items": [
            {"quantity": 25.0, "note": "Phân lô loại 1 (C1)"},
            {"quantity": 35.0, "note": "Phân lô loại 2 (C2)"},
            {"quantity": 20.0, "note": "Phân lô loại 3 (C3)"},
        ]
    }
    split_res = client.post(f"/batches/{parent_a_id}/split", json=split_payload, auth=auth)
    assert split_res.status_code == 201, f"Tách lô thất bại: {split_res.text}"
    split_data = split_res.json()
    child_batches = split_data["child_batches"]
    assert len(child_batches) == 3

    c1_id = child_batches[0]["id"]
    c2_id = child_batches[1]["id"]
    c3_id = child_batches[2]["id"]

    c1_code = child_batches[0]["batch_code"]
    c2_code = child_batches[1]["batch_code"]
    c3_code = child_batches[2]["batch_code"]

    # -------------------------------------------------------------------------
    # Bước 2: Gộp lô con C1 (lấy 20kg) và lô P2 (lấy 30kg) thành lô gộp mới G1
    # -------------------------------------------------------------------------
    merge_payload = {
        "items": [
            {"batch_id": c1_id, "quantity": 20.0},
            {"batch_id": parent_p2_id, "quantity": 30.0},
        ],
        "product_name": "Xoài Cát Chu Thượng Hạng",
        "note": "Gộp từ lô con C1 và vườn Lấp Vò P2",
    }
    merge_res = client.post("/batches/merge", json=merge_payload, auth=auth)
    assert merge_res.status_code == 201, f"Gộp lô thất bại: {merge_res.text}"
    merge_data = merge_res.json()
    g1_id = merge_data["merged_batch"]["id"]
    g1_code = merge_data["merged_batch"]["batch_code"]

    # Tập hợp tất cả các lô có liên quan trong chuỗi:
    all_related_batches = [
        ("Lô mẹ A", parent_a_id, "LOT-PARENT-A"),
        ("Lô con C1", c1_id, c1_code),
        ("Lô con C2", c2_id, c2_code),
        ("Lô con C3", c3_id, c3_code),
        ("Lô nguồn P2", parent_p2_id, "LOT-PARENT-P2"),
        ("Lô gộp G1", g1_id, g1_code),
    ]

    # -------------------------------------------------------------------------
    # DoD / AC 1: Chạy hàm kiểm tra ở T-28 cho MỌI lô liên quan và khẳng định HỢP LỆ
    # -------------------------------------------------------------------------
    for label, b_id, b_code in all_related_batches:
        timeline_res = client.get(f"/batches/{b_id}/events", auth=auth)
        assert timeline_res.status_code == 200, f"Không lấy được sự kiện {label} #{b_id}"
        t_data = timeline_res.json()

        assert t_data["is_valid"] is True, f"{label} ({b_code}) phải hợp lệ sau tách và gộp nhưng báo is_valid=False"
        assert t_data["tampered_index"] is None, f"{label} ({b_code}) có tampered_index={t_data['tampered_index']}"
        assert t_data["total"] > 0, f"{label} ({b_code}) phải có ít nhất 1 sự kiện lịch sử"

    # -------------------------------------------------------------------------
    # DoD / AC 2: SỬA LÉN SỰ KIỆN KHAI SINH (BIRTH) CỦA LÔ CON C2
    # -------------------------------------------------------------------------
    # Trực tiếp cập nhật CSDL bằng SQLAlchemy session để mô phỏng kẻ tấn công sửa lén DB
    with session_factory() as db:
        birth_event = db.scalar(
            select(BatchEvent)
            .where(BatchEvent.batch_id == c2_id, BatchEvent.event_type == "BIRTH")
        )
        assert birth_event is not None, f"Lô con C2 #{c2_id} phải có sự kiện BIRTH"

        original_payload = birth_event.payload
        # Sửa lén payload (ví dụ can thiệp sửa mã lô mẹ hoặc khối lượng)
        tampered_dict = json.loads(original_payload)
        tampered_dict["parent_batch_code"] = "LOT-FAKE-HACKED-999"
        tampered_dict["note"] = "Dữ liệu bị sửa lén trực tiếp trong CSDL"
        birth_event.payload = json.dumps(tampered_dict, ensure_ascii=False)

        # Cố ý KHÔNG tính lại hash để chuỗi hash-chain bị sai lệch
        db.commit()

    # -------------------------------------------------------------------------
    # DoD / AC 2: Kiểm tra lại: CHỈ ĐÚNG LÔ CON C2 BÁO LỖI, CÁC LÔ KHÁC VẪN HỢP LỆ
    # -------------------------------------------------------------------------
    # 1. Kiểm tra lô con C2 bị sửa lén
    c2_check_res = client.get(f"/batches/{c2_id}/events", auth=auth)
    assert c2_check_res.status_code == 200
    c2_timeline = c2_check_res.json()

    assert c2_timeline["is_valid"] is False, "Lô con C2 bị sửa lén sự kiện BIRTH PHẢI báo is_valid == False"
    assert c2_timeline["tampered_index"] == 0, (
        f"Lô con C2 phải chỉ ra chính xác vị trí bị sửa lén là index 0 (sự kiện BIRTH), "
        f"thực tế: {c2_timeline['tampered_index']}"
    )

    # 2. Khẳng định tất cả các lô khác KHÔNG BỊ ẢNH HƯỞNG, vẫn hợp lệ 100%
    other_batches = [
        ("Lô mẹ A", parent_a_id, "LOT-PARENT-A"),
        ("Lô con C1", c1_id, c1_code),
        ("Lô con C3", c3_id, c3_code),
        ("Lô nguồn P2", parent_p2_id, "LOT-PARENT-P2"),
        ("Lô gộp G1", g1_id, g1_code),
    ]

    for label, b_id, b_code in other_batches:
        res = client.get(f"/batches/{b_id}/events", auth=auth)
        assert res.status_code == 200
        t_data = res.json()

        assert t_data["is_valid"] is True, (
            f"Lô {label} ({b_code}) không bị sửa nhưng bị báo lỗi sai! "
            f"is_valid={t_data['is_valid']}, tampered_index={t_data['tampered_index']}"
        )
        assert t_data["tampered_index"] is None, (
            f"Lô {label} ({b_code}) không được có tampered_index"
        )
