"""Kiểm thử tính năng tách nhập nhiều dòng lô con (SCRUM-56 / T-40).

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
1. Tách thành công tạo nhiều lô con theo các dòng khối lượng gửi lên.
2. Trừ chính xác khối lượng của lô mẹ, không để số dư âm.
3. Từ chối yêu cầu (400 Bad Request) nếu tổng khối lượng con vượt quá số dư còn lại của lô mẹ.
4. Ghi nhận sự kiện SPLIT vào chuỗi sự kiện bất biến (hash-chain).
5. Trả về đầy đủ danh sách mã lô con (child_batches).
"""

from __future__ import annotations

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
def split_test_env():
    """Thiết lập môi trường kiểm thử cho API tách lô nhiều dòng."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        user = User(
            username="farmer_split_test",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        db.add(user)

        farm = Farm(
            name="Vùng Trồng Sầu Riêng Bến Tre",
            location="Bến Tre",
            area=3.5,
            owner="HTX Sầu Riêng",
        )
        db.add(farm)
        db.commit()

        # Tạo lô mẹ có khối lượng 100.0 kg
        parent_batch = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6",
            quantity=100.0,
            harvest_date=date(2026, 6, 1),
        )
        db.add(parent_batch)
        db.commit()

        batch_id = parent_batch.id

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "client": client,
        "batch_id": batch_id,
        "session_factory": TestingSessionLocal,
    }

    app.dependency_overrides.clear()


def test_split_batch_success_with_multiple_items(split_test_env):
    """AC 1: Tách thành công nhiều dòng lô con (30kg, 25kg, 15kg = tổng 70kg <= 100kg).

    - Lô mẹ còn lại 30kg.
    - Sinh ra 3 lô con với mã cụ thể.
    """
    client = split_test_env["client"]
    batch_id = split_test_env["batch_id"]

    payload = {
        "items": [
            {"quantity": 30.0, "note": "Kiện 1 - Xuất khẩu Nhật"},
            {"quantity": 25.0, "note": "Kiện 2 - Siêu thị loại 1"},
            {"quantity": 15.0, "note": "Kiện 3 - Bán lẻ nội địa"},
        ]
    }

    res = client.post(
        f"/batches/{batch_id}/split",
        json=payload,
        auth=("farmer_split_test", "123456"),
    )
    assert res.status_code == 201, res.text
    data = res.json()

    assert data["parent_batch_id"] == batch_id
    assert data["parent_remaining_quantity"] == 30.0
    assert len(data["child_batches"]) == 3

    child_quantities = [c["quantity"] for c in data["child_batches"]]
    assert child_quantities == [30.0, 25.0, 15.0]

    # Kiểm tra mã lô con là số nguyên hợp lệ > 0
    child_ids = [c["id"] for c in data["child_batches"]]
    assert len(set(child_ids)) == 3
    for cid in child_ids:
        assert cid > batch_id

    # Kiểm tra số dư lô mẹ trong DB
    with split_test_env["session_factory"]() as db:
        parent = db.get(Batch, batch_id)
        assert parent.quantity == 30.0


def test_reject_split_when_total_exceeds_remaining(split_test_env):
    """AC 2: Tổng nhập vượt quá phần còn lại của lô mẹ -> bị từ chối 400 Bad Request."""
    client = split_test_env["client"]
    batch_id = split_test_env["batch_id"]

    # Lô mẹ 100kg, yêu cầu tách tổng 120kg (60 + 60)
    payload = {
        "items": [
            {"quantity": 60.0, "note": "Kiện A"},
            {"quantity": 60.0, "note": "Kiện B"},
        ]
    }

    res = client.post(
        f"/batches/{batch_id}/split",
        json=payload,
        auth=("farmer_split_test", "123456"),
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "vượt quá" in detail.lower()

    # Khối lượng lô mẹ không bị trừ
    with split_test_env["session_factory"]() as db:
        parent = db.get(Batch, batch_id)
        assert parent.quantity == 100.0


def test_reject_invalid_items_quantity(split_test_env):
    """Từ chối khối lượng không hợp lệ (<= 0)."""
    client = split_test_env["client"]
    batch_id = split_test_env["batch_id"]

    payload = {
        "items": [
            {"quantity": 0.0, "note": "Kiện lỗi 0kg"},
        ]
    }
    res = client.post(
        f"/batches/{batch_id}/split",
        json=payload,
        auth=("farmer_split_test", "123456"),
    )
    assert res.status_code == 422
