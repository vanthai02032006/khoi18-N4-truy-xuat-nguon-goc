"""Kiểm thử tính năng theo dõi tiến độ lệnh kiểm tra / thu hồi và đóng hồ sơ đúng lúc.

Đáp ứng User Story & Tiêu chí nghiệm thu (DoD / AC):
1. Giả sử lệnh có năm tổ chức liên quan và ba đã xác nhận,
   Khi xem,
   Thì thấy 3/5 kèm tên bên chưa xong.
2. Giả sử bên cuối cùng xác nhận,
   Khi lưu,
   Thì lệnh tự chuyển sang hoàn tất và ghi sự kiện.
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
from app.models import Batch, Farm, ROLE_ADMIN, ROLE_FARMER, ROLE_INSPECTOR, User
from app.security import hash_password


@pytest.fixture
def order_env():
    """Thiết lập môi trường kiểm thử SQLite in-memory cho Lệnh kiểm tra / thu hồi."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        # Tài khoản Cán bộ kiểm tra (inspector)
        user_inspector = User(
            username="inspector_lead",
            password=hash_password("123456"),
            role=ROLE_INSPECTOR,
        )
        # Tài khoản các tổ chức liên quan (farmer/partner)
        user_partner = User(
            username="partner_user",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        db.add_all([user_inspector, user_partner])

        farm = Farm(
            name="Vùng Trồng Xoài Thử Nghiệm",
            location="Đồng Tháp",
            area=5.0,
            owner="HTX Nông Sản 1",
        )
        db.add(farm)
        db.commit()

        # Tạo lô hàng có mã riêng
        batch = Batch(
            farm_id=farm.id,
            product_name="Xoài Cát Chu",
            quantity=500.0,
            harvest_date=date(2026, 6, 1),
            batch_code="LOT-INSPECT-01",
            current_holder_org="HTX Nông Sản 1",
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


def test_recall_order_progress_three_out_of_five(order_env):
    """AC 1: Giả sử lệnh có 5 tổ chức liên quan và 3 đã xác nhận:

    - Khi xem, thấy 3/5 kèm tên các bên chưa xong (pending_organizations).
    - Trạng thái lệnh vẫn là IN_PROGRESS.
    """
    client = order_env["client"]
    batch_id = order_env["batch_id"]
    inspector_auth = ("inspector_lead", "123456")
    partner_auth = ("partner_user", "123456")

    five_organizations = [
        "HTX Trồng Trọt Cao Lãnh",
        "Công Ty Vận Tải Lạnh Express",
        "Kho Lạnh Trung Chuyển Miền Tây",
        "Nhà Máy Đóng Gói Sơ Chế",
        "Chuỗi Siêu Thị Nông Sản Sạch",
    ]

    # 1. Cán bộ kiểm tra ban hành lệnh với 5 tổ chức liên quan
    create_payload = {
        "title": "Lệnh tạm ngừng xuất xưởng và rà soát tồn dư thuốc BVTV",
        "reason": "Phát hiện chỉ số cần xác minh tại điểm kiểm định số 2",
        "description": "Yêu cầu các bên niêm phong tạm thời lô hàng và xác nhận kết quả kiểm tra.",
        "batch_id": batch_id,
        "target_organizations": five_organizations,
    }
    create_res = client.post("/orders", json=create_payload, auth=inspector_auth)
    assert create_res.status_code == 201, create_res.text
    order_data = create_res.json()
    order_id = order_data["id"]

    assert order_data["total_targets"] == 5
    assert order_data["confirmed_count"] == 0
    assert order_data["progress_ratio"] == "0/5"
    assert order_data["status"] == "IN_PROGRESS"

    # 2. Cho 3 tổ chức đầu tiên xác nhận
    for org in five_organizations[:3]:
        confirm_res = client.post(
            f"/orders/{order_id}/confirm",
            json={
                "org_name": org,
                "note": f"Đã niêm phong và gửi mẫu xét nghiệm từ {org}",
            },
            auth=partner_auth,
        )
        assert confirm_res.status_code == 200, confirm_res.text

    # 3. Cán bộ kiểm tra xem chi tiết tiến độ lệnh
    view_res = client.get(f"/orders/{order_id}", auth=inspector_auth)
    assert view_res.status_code == 200
    progress_data = view_res.json()

    # Khẳng định thấy rõ tỷ lệ 3/5
    assert progress_data["total_targets"] == 5
    assert progress_data["confirmed_count"] == 3
    assert progress_data["pending_count"] == 2
    assert progress_data["progress_ratio"] == "3/5"
    assert progress_data["status"] == "IN_PROGRESS"
    assert progress_data["is_completed"] is False

    # Khẳng định nêu rõ tên các bên chưa xong
    expected_pending_orgs = five_organizations[3:]  # [Nhà Máy Đóng Gói Sơ Chế, Chuỗi Siêu Thị Nông Sản Sạch]
    assert progress_data["pending_organizations"] == expected_pending_orgs


def test_recall_order_auto_completes_when_last_target_confirms(order_env):
    """AC 2: Giả sử bên cuối cùng xác nhận:

    - Khi lưu, lệnh tự động chuyển sang hoàn tất (status="COMPLETED", completed_at).
    - Ghi sự kiện RECALL_COMPLETED vào chuỗi bất biến của lô hàng.
    """
    client = order_env["client"]
    batch_id = order_env["batch_id"]
    inspector_auth = ("inspector_lead", "123456")
    partner_auth = ("partner_user", "123456")

    three_orgs = [
        "HTX Sản Xuất Nông Nghiệp",
        "Công Ty Logistic Chuỗi Lạnh",
        "Hệ Thống Bán Lẻ Nông Sản",
    ]

    create_payload = {
        "title": "Kiểm tra dư lượng đợt thu hoạch",
        "reason": "Thanh tra định kỳ",
        "batch_id": batch_id,
        "target_organizations": three_orgs,
    }
    create_res = client.post("/orders", json=create_payload, auth=inspector_auth)
    assert create_res.status_code == 201
    order_id = create_res.json()["id"]

    # Tổ chức 1 xác nhận -> 1/3
    res1 = client.post(
        f"/orders/{order_id}/confirm",
        json={"org_name": three_orgs[0], "note": "Hoàn tất kiểm tra tại vườn"},
        auth=partner_auth,
    )
    assert res1.status_code == 200
    assert res1.json()["progress_ratio"] == "1/3"
    assert res1.json()["is_order_completed"] is False

    # Tổ chức 2 xác nhận -> 2/3
    res2 = client.post(
        f"/orders/{order_id}/confirm",
        json={"org_name": three_orgs[1], "note": "Nhiệt độ bảo quản đạt chuẩn 4°C"},
        auth=partner_auth,
    )
    assert res2.status_code == 200
    assert res2.json()["progress_ratio"] == "2/3"
    assert res2.json()["is_order_completed"] is False

    # Tổ chức 3 (BÊN CUỐI CÙNG) xác nhận -> 3/3 => Tự chuyển sang hoàn tất
    res3 = client.post(
        f"/orders/{order_id}/confirm",
        json={"org_name": three_orgs[2], "note": "Sản phẩm đạt chuẩn, cho phép lưu thông bình thường"},
        auth=partner_auth,
    )
    assert res3.status_code == 200
    confirm_data = res3.json()

    assert confirm_data["progress_ratio"] == "3/3"
    assert confirm_data["is_order_completed"] is True
    assert confirm_data["order_status"] == "COMPLETED"
    assert "tự động chuyển sang hoàn tất" in confirm_data["message"]
    assert confirm_data["event_id"] is not None

    # Xem lại lệnh từ API của cán bộ kiểm tra
    order_detail_res = client.get(f"/orders/{order_id}", auth=inspector_auth)
    assert order_detail_res.status_code == 200
    final_order = order_detail_res.json()

    assert final_order["status"] == "COMPLETED"
    assert final_order["is_completed"] is True
    assert final_order["progress_ratio"] == "3/3"
    assert final_order["pending_count"] == 0
    assert len(final_order["pending_organizations"]) == 0
    assert final_order["completed_at"] is not None

    # Khẳng định sự kiện RECALL_COMPLETED được ghi vào chuỗi của lô hàng
    events_res = client.get(f"/batches/{batch_id}/events", auth=inspector_auth)
    assert events_res.status_code == 200
    timeline = events_res.json()
    assert timeline["is_valid"] is True

    completed_events = [ev for ev in timeline["events"] if ev["event_type"] == "RECALL_COMPLETED"]
    assert len(completed_events) == 1
    ev_payload = json.loads(completed_events[0]["payload"])
    assert ev_payload["action"] == "RECALL_COMPLETED"
    assert ev_payload["order_id"] == order_id
    assert ev_payload["final_confirming_org"] == three_orgs[2]
