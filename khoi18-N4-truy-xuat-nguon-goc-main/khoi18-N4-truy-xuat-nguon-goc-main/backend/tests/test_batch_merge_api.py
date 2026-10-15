"""Integration Tests cho Endpoint POST /batches/merge (FastAPI TestClient)."""

import base64
from datetime import date
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, Farm, User
from app.security import hash_password


class TestBatchMergeAPI(unittest.TestCase):
    """Kiểm thử tích hợp API gộp lô với database in-memory."""

    @classmethod
    def setUpClass(cls) -> None:
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

    def setUp(self) -> None:
        Base.metadata.create_all(bind=self.engine)
        self.db = self.TestingSessionLocal()

        # Tạo user farmer
        self.farmer_user = User(
            username="farmer1",
            password=hash_password("123456"),
            role="farmer",
        )
        self.db.add(self.farmer_user)

        # Tạo 2 Farm (thuộc 2 chủ sở hữu / tổ chức khác nhau)
        self.farm_1 = Farm(
            name="Vùng trồng Xoài 1",
            location="Đồng Tháp",
            area=5.0,
            owner="HTX Mỹ Xương",
        )
        self.farm_2 = Farm(
            name="Vùng trồng Khác 2",
            location="Tiền Giang",
            area=3.0,
            owner="HTX Khác",
        )
        self.db.add_all([self.farm_1, self.farm_2])
        self.db.commit()

        # Tạo các Batch
        # Batch 1 & 2: Cùng farm 1, cùng xoài cát Chu, số lượng 100 và 80
        self.b1 = Batch(
            farm_id=self.farm_1.id,
            product_name="Xoài cát Chu",
            quantity=100.0,
            harvest_date=date.today(),
        )
        self.b2 = Batch(
            farm_id=self.farm_1.id,
            product_name="Xoài cát Chu",
            quantity=80.0,
            harvest_date=date.today(),
        )
        # Batch 3: Khác sản phẩm (Thanh long)
        self.b3_diff_prod = Batch(
            farm_id=self.farm_1.id,
            product_name="Thanh long ruột đỏ",
            quantity=50.0,
            harvest_date=date.today(),
        )
        # Batch 4: Khác tổ chức (thuộc farm_2)
        self.b4_other_org = Batch(
            farm_id=self.farm_2.id,
            product_name="Xoài cát Chu",
            quantity=60.0,
            harvest_date=date.today(),
        )
        self.db.add_all([self.b1, self.b2, self.b3_diff_prod, self.b4_other_org])
        self.db.commit()

        # Tạo Basic Auth header
        auth_str = base64.b64encode(b"farmer1:123456").decode("utf-8")
        self.headers = {"Authorization": f"Basic {auth_str}"}

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_merge_success(self) -> None:
        """Gộp thành công 2 lô hợp lệ."""
        payload = {
            "source_batches": [
                {"batch_id": self.b1.id, "take_quantity": 40.0},
                {"batch_id": self.b2.id, "take_quantity": 30.0},
            ],
            "new_batch_name": "Lô Xoài gộp xuất khẩu",
        }
        resp = self.client.post("/batches/merge", json=payload, headers=self.headers)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["total_quantity"], 70.0)
        self.assertIn("thành công", data["message"])

    def test_reject_diff_product(self) -> None:
        """Ca 1: Từ chối khi khác sản phẩm và trả đúng violating_batch_ids."""
        payload = {
            "source_batches": [
                {"batch_id": self.b1.id, "take_quantity": 20.0},
                {"batch_id": self.b3_diff_prod.id, "take_quantity": 10.0},
            ]
        }
        resp = self.client.post("/batches/merge", json=payload, headers=self.headers)
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn(self.b3_diff_prod.id, detail["violating_batch_ids"])

    def test_reject_different_org(self) -> None:
        """Ca 2: Từ chối khi khác tổ chức sở hữu và trả đúng violating_batch_ids."""
        payload = {
            "source_batches": [
                {"batch_id": self.b1.id, "take_quantity": 20.0},
                {"batch_id": self.b4_other_org.id, "take_quantity": 10.0},
            ]
        }
        resp = self.client.post("/batches/merge", json=payload, headers=self.headers)
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn(self.b4_other_org.id, detail["violating_batch_ids"])

    def test_reject_excessive_quantity(self) -> None:
        """Ca 3: Từ chối khi lấy vượt số lượng còn lại."""
        payload = {
            "source_batches": [
                {"batch_id": self.b1.id, "take_quantity": 20.0},
                {"batch_id": self.b2.id, "take_quantity": 500.0},  # Vượt quá 80
            ]
        }
        resp = self.client.post("/batches/merge", json=payload, headers=self.headers)
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn(self.b2.id, detail["violating_batch_ids"])


if __name__ == "__main__":
    unittest.main()
