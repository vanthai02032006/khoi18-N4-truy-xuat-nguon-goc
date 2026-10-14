"""Kiểm thử nghiệp vụ S-19: đánh dấu lô có sự cố và mở lệnh thu hồi (POST /batches/{id}/incident).

Đáp ứng User Story & Tiêu chí nghiệm thu (DoD / AC):
1. Giả sử tôi là cán bộ kiểm tra hoặc quản trị tổ chức đang giữ lô,
   Khi đánh dấu lô có sự cố kèm lý do,
   Thì một lệnh thu hồi được tạo với mã riêng và sự kiện được ghi vào chuỗi của lô.
2. Giả sử vai trò khác thử mở lệnh thu hồi, Khi gọi, Thì bị 403.
3. Giả sử lô đã có lệnh thu hồi đang mở, Khi mở lệnh mới,
   Thì bị chặn và trỏ tới lệnh đang mở.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    ROLE_ADMIN,
    ROLE_FARMER,
    ROLE_INSPECTOR,
    Batch,
    Farm,
    RecallOrder,
    User,
)
from app.security import hash_password

HOLDER_ORG = "HTX Nông Sản 1"


@pytest.fixture
def incident_env():
    """Môi trường SQLite in-memory cho nghiệp vụ đánh dấu sự cố & mở lệnh thu hồi."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        db.add_all(
            [
                User(username="inspector_lead", password=hash_password("123456"), role=ROLE_INSPECTOR),
                User(username="admin_org", password=hash_password("123456"), role=ROLE_ADMIN),
                User(username="farmer_user", password=hash_password("123456"), role=ROLE_FARMER),
            ]
        )
        farm = Farm(
            name="Vùng Trồng Xoài Sự Cố",
            location="Cao Lãnh, Đồng Tháp",
            area=4.0,
            owner=HOLDER_ORG,
        )
        db.add(farm)
        db.commit()

        batch = Batch(
            farm_id=farm.id,
            product_name="Xoài Cát Chu",
            quantity=300.0,
            harvest_date=date(2026, 6, 1),
            batch_code="LOT-INCIDENT-01",
            current_holder_org=HOLDER_ORG,
        )
        db.add(batch)
        db.commit()
        batch_id = batch.id

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


def test_inspector_flags_incident_opens_recall_order_and_records_event(incident_env):
    """AC 1: Cán bộ kiểm tra đánh dấu sự cố -> lệnh thu hồi có mã riêng + sự kiện vào chuỗi lô."""
    client = incident_env["client"]
    batch_id = incident_env["batch_id"]
    inspector_auth = ("inspector_lead", "123456")

    res = client.post(
        f"/batches/{batch_id}/incident",
        json={"reason": "Phát hiện dư lượng thuốc BVTV vượt ngưỡng cho phép"},
        auth=inspector_auth,
    )
    assert res.status_code == 201, res.text
    order = res.json()

    # Lệnh được tạo với mã riêng, gắn đúng lô và đang mở
    assert order["order_code"].startswith("ORD-REC-")
    assert order["order_code"]
    assert order["batch_id"] == batch_id
    assert order["status"] == "IN_PROGRESS"
    assert order["is_completed"] is False
    assert order["reason"] == "Phát hiện dư lượng thuốc BVTV vượt ngưỡng cho phép"
    # Mặc định nhắm tới tổ chức đang giữ lô
    assert order["total_targets"] == 1
    assert order["pending_organizations"] == [HOLDER_ORG]

    # Sự kiện INCIDENT_FLAGGED được ghi vào chuỗi băm và chuỗi còn toàn vẹn
    timeline = client.get(f"/batches/{batch_id}/events", auth=inspector_auth).json()
    assert timeline["is_valid"] is True
    incident_events = [ev for ev in timeline["events"] if ev["event_type"] == "INCIDENT_FLAGGED"]
    assert len(incident_events) == 1
    payload = json.loads(incident_events[0]["payload"])
    assert payload["action"] == "INCIDENT_FLAGGED"
    assert payload["order_id"] == order["id"]
    assert payload["order_code"] == order["order_code"]
    assert payload["batch_code"] == "LOT-INCIDENT-01"


def test_admin_can_flag_incident_with_explicit_targets(incident_env):
    """AC 1: Quản trị viên cũng mở được lệnh; danh sách bên liên quan truyền tay được tôn trọng."""
    client = incident_env["client"]
    batch_id = incident_env["batch_id"]
    admin_auth = ("admin_org", "123456")

    res = client.post(
        f"/batches/{batch_id}/incident",
        json={
            "reason": "Nghi ngờ nhiễm khuẩn",
            "title": "Thu hồi khẩn cấp lô xoài",
            "description": "Niêm phong và lấy mẫu xét nghiệm",
            "target_organizations": ["Kho Lạnh Miền Tây", "Siêu Thị Nông Sản Sạch"],
        },
        auth=admin_auth,
    )
    assert res.status_code == 201, res.text
    order = res.json()
    assert order["title"] == "Thu hồi khẩn cấp lô xoài"
    assert order["description"] == "Niêm phong và lấy mẫu xét nghiệm"
    assert order["total_targets"] == 2
    assert sorted(order["pending_organizations"]) == ["Kho Lạnh Miền Tây", "Siêu Thị Nông Sản Sạch"]


def test_other_role_cannot_open_recall_order(incident_env):
    """AC 2: Vai trò khác (farmer) gọi -> 403 và không tạo lệnh nào."""
    client = incident_env["client"]
    batch_id = incident_env["batch_id"]
    farmer_auth = ("farmer_user", "123456")

    res = client.post(
        f"/batches/{batch_id}/incident",
        json={"reason": "Cố gắng mở lệnh trái phép"},
        auth=farmer_auth,
    )
    assert res.status_code == 403, res.text

    with incident_env["session_factory"]() as db:
        assert db.scalar(select(func.count()).select_from(RecallOrder)) == 0


def test_second_order_blocked_and_points_to_open_order(incident_env):
    """AC 3: Lô đã có lệnh đang mở -> mở lệnh mới bị chặn (409) kèm mã lệnh đang mở."""
    client = incident_env["client"]
    batch_id = incident_env["batch_id"]
    inspector_auth = ("inspector_lead", "123456")

    first = client.post(
        f"/batches/{batch_id}/incident",
        json={"reason": "Sự cố lần 1"},
        auth=inspector_auth,
    )
    assert first.status_code == 201, first.text
    first_code = first.json()["order_code"]

    second = client.post(
        f"/batches/{batch_id}/incident",
        json={"reason": "Sự cố lần 2"},
        auth=inspector_auth,
    )
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["open_order_code"] == first_code
    assert detail["open_order_status"] == "IN_PROGRESS"
    assert first_code in detail["message"]

    # Không phát sinh lệnh hay sự kiện trùng
    with incident_env["session_factory"]() as db:
        assert db.scalar(select(func.count()).select_from(RecallOrder)) == 1
    timeline = client.get(f"/batches/{batch_id}/events", auth=inspector_auth).json()
    assert len([ev for ev in timeline["events"] if ev["event_type"] == "INCIDENT_FLAGGED"]) == 1


def test_can_open_new_order_after_previous_completed(incident_env):
    """AC 3 (biên): lệnh cũ hoàn tất thì lô được mở lệnh thu hồi mới."""
    client = incident_env["client"]
    batch_id = incident_env["batch_id"]
    inspector_auth = ("inspector_lead", "123456")

    first = client.post(
        f"/batches/{batch_id}/incident",
        json={"reason": "Sự cố lần 1"},
        auth=inspector_auth,
    )
    assert first.status_code == 201
    first_order = first.json()

    # Bên đang giữ lô (mặc định trong target) xác nhận -> lệnh hoàn tất
    confirm = client.post(
        f"/orders/{first_order['id']}/confirm",
        json={"org_name": HOLDER_ORG, "note": "Đã xử lý xong"},
        auth=inspector_auth,
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["order_status"] == "COMPLETED"

    second = client.post(
        f"/batches/{batch_id}/incident",
        json={"reason": "Sự cố mới phát sinh"},
        auth=inspector_auth,
    )
    assert second.status_code == 201, second.text
    assert second.json()["order_code"] != first_order["order_code"]


def test_unknown_batch_returns_404(incident_env):
    """Lô không tồn tại -> 404 (không tạo lệnh mồ côi)."""
    client = incident_env["client"]
    inspector_auth = ("inspector_lead", "123456")

    res = client.post(
        "/batches/999999/incident",
        json={"reason": "Lô không tồn tại"},
        auth=inspector_auth,
    )
    assert res.status_code == 404, res.text


def test_requires_authentication(incident_env):
    """Chưa đăng nhập -> 401."""
    client = incident_env["client"]
    batch_id = incident_env["batch_id"]

    res = client.post(f"/batches/{batch_id}/incident", json={"reason": "Không có thông tin đăng nhập"})
    assert res.status_code == 401
