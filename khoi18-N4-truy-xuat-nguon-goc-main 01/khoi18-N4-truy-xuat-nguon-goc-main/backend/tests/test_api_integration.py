"""Kiểm tra tích hợp toàn bộ các Router API và Luồng Nghiệp Vụ Truy Xuất Nguồn Gốc."""

import unittest
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    EVENT_TYPE_BATCH_CREATED,
    EVENT_TYPE_HANDOVER_ACCEPTED,
    EVENT_TYPE_HANDOVER_PENDING,
    EVENT_TYPE_HANDOVER_REJECTED,
    HANDOVER_STATUS_ACCEPTED,
    HANDOVER_STATUS_PENDING,
    HANDOVER_STATUS_REJECTED,
    ROLE_ADMIN,
    ROLE_FARMER,
    User,
)
from app.security import hash_password


class TestFullApiIntegration(unittest.TestCase):
    """Test suite tích hợp toàn diện hệ thống."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        cls.TestingSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=cls.engine
        )

        def override_get_db():
            db = cls.TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)

        db = self.TestingSessionLocal()
        try:
            admin = User(
                username="admin",
                password=hash_password("123456"),
                role=ROLE_ADMIN,
            )
            farmer = User(
                username="farmer",
                password=hash_password("123456"),
                role=ROLE_FARMER,
            )
            db.add_all([admin, farmer])
            db.commit()
        finally:
            db.close()

        self.auth_farmer = ("farmer", "123456")
        self.auth_admin = ("admin", "123456")

    def test_health_check(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "running"})

    def test_complete_supply_chain_lifecycle(self):
        # 1. Tạo vùng trồng
        farm_resp = self.client.post(
            "/farms",
            json={
                "name": "Vùng trồng Bưởi Da Xanh",
                "location": "Bến Tre",
                "area": 10.5,
                "owner": "HTX Nông Nghiệp Bến Tre",
            },
            auth=self.auth_farmer,
        )
        self.assertEqual(farm_resp.status_code, 201)
        farm_id = farm_resp.json()["id"]

        # 2. Tạo lô nông sản -> kiểm tra tự động ghi sự kiện BATCH_CREATED
        batch_resp = self.client.post(
            "/batches",
            json={
                "farm_id": farm_id,
                "product_name": "Bưởi Da Xanh Loại 1",
                "quantity": 3000.0,
                "harvest_date": "2026-10-06",
            },
            auth=self.auth_farmer,
        )
        self.assertEqual(batch_resp.status_code, 201)
        batch_id = batch_resp.json()["id"]
        self.assertEqual(batch_resp.json()["current_owner"], "HTX Nông Nghiệp Bến Tre")

        # 3. Khởi tạo bàn giao ở trạng thái chờ
        handover_resp = self.client.post(
            "/handovers",
            json={
                "batch_id": batch_id,
                "receiver_name": "Công ty Thu Mua Nông Sản VinaExport",
                "notes": "Bàn giao tại kho lạnh",
            },
            auth=self.auth_farmer,
        )
        self.assertEqual(handover_resp.status_code, 201)
        handover_id = handover_resp.json()["id"]
        self.assertEqual(handover_resp.json()["status"], HANDOVER_STATUS_PENDING)
        self.assertEqual(handover_resp.json()["sender_name"], "HTX Nông Nghiệp Bến Tre")
        self.assertEqual(handover_resp.json()["current_batch_owner"], "HTX Nông Nghiệp Bến Tre")

        # 4. Kiểm tra lô hàng vẫn thuộc bên giao trong thời gian chờ
        b_check = self.client.get(f"/batches/{batch_id}")
        self.assertEqual(b_check.json()["current_owner"], "HTX Nông Nghiệp Bến Tre")

        # 5. Cố tình tạo yêu cầu bàn giao thứ hai cho cùng lô -> Phải bị chặn
        dup_resp = self.client.post(
            "/handovers",
            json={
                "batch_id": batch_id,
                "receiver_name": "Bên Nhận Khác",
                "notes": "Yêu cầu trùng",
            },
            auth=self.auth_farmer,
        )
        self.assertEqual(dup_resp.status_code, 400)
        self.assertIn("đang có một yêu cầu bàn giao ở trạng thái chờ", dup_resp.json()["detail"])

        # 6. Tiếp nhận bàn giao -> Quyền quản lý chuyển sang bên nhận
        acc_resp = self.client.post(
            f"/handovers/{handover_id}/accept",
            json={"notes": "Đã nhận đủ 3000kg bưởi."},
            auth=self.auth_farmer,
        )
        self.assertEqual(acc_resp.status_code, 200)
        self.assertEqual(acc_resp.json()["status"], HANDOVER_STATUS_ACCEPTED)
        self.assertEqual(acc_resp.json()["current_batch_owner"], "Công ty Thu Mua Nông Sản VinaExport")

        b_check2 = self.client.get(f"/batches/{batch_id}")
        self.assertEqual(b_check2.json()["current_owner"], "Công ty Thu Mua Nông Sản VinaExport")

        # 7. Kiểm tra dòng sự kiện qua endpoint T-25
        events_resp = self.client.get(f"/events/batch/{batch_id}")
        self.assertEqual(events_resp.status_code, 200)
        ev_types = [e["event_type"] for e in events_resp.json()]
        self.assertIn(EVENT_TYPE_BATCH_CREATED, ev_types)
        self.assertIn(EVENT_TYPE_HANDOVER_PENDING, ev_types)
        self.assertIn(EVENT_TYPE_HANDOVER_ACCEPTED, ev_types)


if __name__ == "__main__":
    unittest.main()
