"""Toàn bộ test suite kiểm tra tích hợp các endpoint API của hệ thống."""

import unittest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Farm, ROLE_ADMIN, ROLE_FARMER, User
from app.security import hash_password


class TestAllAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        cls.TestingSessionLocal = sessionmaker(
            bind=cls.test_engine,
            autocommit=False,
            autoflush=False,
        )

    def setUp(self):
        Base.metadata.create_all(bind=self.test_engine)
        self.db: Session = self.TestingSessionLocal()

        # Thêm tài khoản admin và farmer
        self.admin_user = User(
            username="admin",
            password=hash_password("123456"),
            role=ROLE_ADMIN,
        )
        self.farmer_user = User(
            username="farmer",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        self.db.add_all([self.admin_user, self.farmer_user])
        self.db.commit()

        def override_get_db():
            db_session = self.TestingSessionLocal()
            try:
                yield db_session
            finally:
                db_session.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.test_engine)
        app.dependency_overrides.clear()

    def test_health(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "running"})

    def test_login(self):
        res = self.client.post("/auth/login", json={"username": "farmer", "password": "123456"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"username": "farmer", "role": "farmer"})

    def test_create_farm_and_batch_with_events(self):
        # 1. Tạo farm
        farm_res = self.client.post(
            "/farms",
            auth=("farmer", "123456"),
            json={
                "name": "Vườn Mẫu",
                "location": "Tiền Giang",
                "area": 2.5,
                "owner": "HTX Tiền Giang",
            },
        )
        self.assertEqual(farm_res.status_code, 201)
        farm_id = farm_res.json()["id"]

        # 2. Tạo batch (T-20 + T-24 single transaction)
        batch_res = self.client.post(
            "/batches",
            auth=("farmer", "123456"),
            json={
                "farm_id": farm_id,
                "product_name": "Sầu Riêng Ri6",
                "quantity": 2500.0,
                "harvest_date": "2026-10-06",
            },
        )
        self.assertEqual(batch_res.status_code, 201)
        batch_id = batch_res.json()["id"]

        # 3. Lấy events của batch
        events_res = self.client.get(f"/batches/{batch_id}/events")
        self.assertEqual(events_res.status_code, 200)
        events = events_res.json()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "HARVEST")
        self.assertEqual(events[0]["actor"], "farmer")
        self.assertEqual(len(events[0]["hash"]), 64)
        self.assertEqual(events[0]["previous_hash"], "0" * 64)

    def test_delete_farm_cascades_batches_and_events(self):
        # Tạo farm
        f_res = self.client.post(
            "/farms",
            auth=("farmer", "123456"),
            json={"name": "Vườn Test", "location": "Cần Thơ", "area": 1.0, "owner": "Nông Dân A"},
        )
        f_id = f_res.json()["id"]

        # Tạo batch
        b_res = self.client.post(
            "/batches",
            auth=("farmer", "123456"),
            json={"farm_id": f_id, "product_name": "Xoài", "quantity": 100.0, "harvest_date": "2026-10-06"},
        )
        b_id = b_res.json()["id"]

        # Xoá farm với quyền admin
        del_res = self.client.delete(f"/farms/{f_id}", auth=("admin", "123456"))
        self.assertEqual(del_res.status_code, 200)

        # Kiểm tra batch và events không còn
        get_b = self.client.get(f"/batches/{b_id}")
        self.assertEqual(get_b.status_code, 404)
        get_e = self.client.get(f"/batches/{b_id}/events")
        self.assertEqual(get_e.status_code, 404)


if __name__ == "__main__":
    unittest.main()
