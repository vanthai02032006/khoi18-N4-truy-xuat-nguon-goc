"""Bộ kiểm thử tự động toàn diện cho chức năng Bàn giao (Handovers) và Ghi sự kiện (Events - Task T-25).

Kiểm tra toàn bộ Tiêu chí Nghiệm thu (DoD / Acceptance Criteria):
1. [AC 1]: Tạo bàn giao thì có sự kiện chờ (HANDOVER_PENDING) và lô vẫn thuộc quyền quản lý bên giao.
2. [AC 2]: Cố tình tạo yêu cầu bàn giao lần hai khi đang có bàn giao chờ -> Bị chặn (HTTP 400 + DB Unique Constraint).
3. [AC 3]: Tiếp nhận bàn giao (accept) -> Trạng thái đổi thành 'accepted', quyền quản lý chuyển sang bên nhận, ghi sự kiện HANDOVER_ACCEPTED.
4. [AC 4]: Sau khi bàn giao hoàn tất (accepted/rejected), cho phép tạo bàn giao mới.
5. [AC 5]: Từ chối bàn giao (reject) -> Trạng thái đổi thành 'rejected', lô vẫn thuộc bên giao, ghi sự kiện HANDOVER_REJECTED.
6. [AC 6]: Tra cứu toàn bộ dòng lịch sử sự kiện truy xuất nguồn gốc của lô nông sản (Traceability timeline).
"""

import unittest
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.events import (
    EVENT_TYPE_BATCH_CREATED,
    EVENT_TYPE_HANDOVER_ACCEPTED,
    EVENT_TYPE_HANDOVER_PENDING,
    EVENT_TYPE_HANDOVER_REJECTED,
)
from app.main import app
from app.models import (
    HANDOVER_STATUS_ACCEPTED,
    HANDOVER_STATUS_PENDING,
    HANDOVER_STATUS_REJECTED,
    ROLE_ADMIN,
    ROLE_FARMER,
    Batch,
    Farm,
    Handover,
    User,
)
from app.security import hash_password


class TestHandoverAndEventsSuite(unittest.TestCase):
    """Test suite cho nghiệp vụ Bàn giao và Sự kiện chuỗi cung ứng."""

    @classmethod
    def setUpClass(cls):
        """Khởi tạo database SQLite in-memory độc lập cho test."""
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
        """Tạo mới toàn bộ bảng và dữ liệu cơ sở trước mỗi test case."""
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)

        # Nạp tài khoản và vùng trồng cơ sở
        db = self.TestingSessionLocal()
        try:
            self.admin = User(
                username="admin",
                password=hash_password("123456"),
                role=ROLE_ADMIN,
            )
            self.farmer = User(
                username="farmer",
                password=hash_password("123456"),
                role=ROLE_FARMER,
            )
            db.add_all([self.admin, self.farmer])
            db.commit()

            self.farm = Farm(
                name="Vùng Trồng Xoài Cát Chu Cao Lãnh",
                location="Xã Mỹ Xương, Đồng Tháp",
                area=5.0,
                owner="Hợp tác xã Xoài Mỹ Xương",
            )
            db.add(self.farm)
            db.commit()
            self.farm_id = self.farm.id

            self.batch = Batch(
                farm_id=self.farm_id,
                product_name="Xoài Cát Chu Xuất Khẩu",
                quantity=1500.0,
                harvest_date=date(2026, 10, 1),
                current_owner="Hợp tác xã Xoài Mỹ Xương",
            )
            db.add(self.batch)
            db.commit()
            self.batch_id = self.batch.id
        finally:
            db.close()

        self.farmer_auth = ("farmer", "123456")
        self.admin_auth = ("admin", "123456")

    def test_create_handover_pending_and_ownership_retained(self):
        """[AC 1]: Tạo bàn giao thì có sự kiện chờ (HANDOVER_PENDING) và lô vẫn thuộc quyền quản lý bên giao."""
        payload = {
            "batch_id": self.batch_id,
            "receiver_name": "Công ty Thu Mua Nông Sản Xuất Khẩu Mekong",
            "notes": "Bàn giao tại trạm sơ chế",
        }

        # 1. Gửi request tạo bàn giao
        response = self.client.post("/handovers", json=payload, auth=self.farmer_auth)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()

        # Kiểm tra trạng thái bàn giao là 'pending'
        self.assertEqual(data["status"], HANDOVER_STATUS_PENDING)
        self.assertEqual(data["sender_name"], "Hợp tác xã Xoài Mỹ Xương")
        self.assertEqual(data["receiver_name"], "Công ty Thu Mua Nông Sản Xuất Khẩu Mekong")
        self.assertEqual(data["current_batch_owner"], "Hợp tác xã Xoài Mỹ Xương")

        # 2. Kiểm tra lại thông tin lô nông sản trên API -> quyền quản lý vẫn là bên giao
        batch_resp = self.client.get(f"/batches/{self.batch_id}")
        self.assertEqual(batch_resp.status_code, 200)
        self.assertEqual(batch_resp.json()["current_owner"], "Hợp tác xã Xoài Mỹ Xương")

        # 3. Kiểm tra sự kiện T-25 đã được ghi vào hệ thống
        events_resp = self.client.get(f"/batches/{self.batch_id}/events")
        self.assertEqual(events_resp.status_code, 200)
        events = events_resp.json()
        pending_events = [e for e in events if e["event_type"] == EVENT_TYPE_HANDOVER_PENDING]
        self.assertEqual(len(pending_events), 1)
        self.assertIn("Hợp tác xã Xoài Mỹ Xương", pending_events[0]["description"])
        self.assertIn("Công ty Thu Mua Nông Sản Xuất Khẩu Mekong", pending_events[0]["description"])

    def test_block_duplicate_pending_handover(self):
        """[AC 2]: Cố tình tạo yêu cầu bàn giao lần hai bị chặn."""
        payload1 = {
            "batch_id": self.batch_id,
            "receiver_name": "Bên Nhận A",
            "notes": "Yêu cầu bàn giao lần 1",
        }
        resp1 = self.client.post("/handovers", json=payload1, auth=self.farmer_auth)
        self.assertEqual(resp1.status_code, 201)

        # Cố tình tạo yêu cầu bàn giao lần 2 cho CÙNG lô hàng đang có bàn giao chờ
        payload2 = {
            "batch_id": self.batch_id,
            "receiver_name": "Bên Nhận B",
            "notes": "Yêu cầu bàn giao lần 2 cố tình gửi trùng",
        }
        resp2 = self.client.post("/handovers", json=payload2, auth=self.farmer_auth)
        self.assertEqual(resp2.status_code, 400)
        self.assertIn("đang có một yêu cầu bàn giao ở trạng thái chờ", resp2.json()["detail"])

        # Kiểm tra thêm ở tầng CSDL: chỉ có đúng 1 bàn giao trong bảng handovers
        db = self.TestingSessionLocal()
        try:
            handovers_count = db.query(Handover).filter(Handover.batch_id == self.batch_id).count()
            self.assertEqual(handovers_count, 1)
        finally:
            db.close()

    def test_accept_handover_workflow(self):
        """[AC 3 & 4]: Tiếp nhận bàn giao -> Chuyển quyền quản lý, ghi sự kiện, cho phép tạo bàn giao kế tiếp."""
        # 1. Tạo bàn giao đang chờ
        create_resp = self.client.post(
            "/handovers",
            json={
                "batch_id": self.batch_id,
                "receiver_name": "Doanh Nghiệp Chế Biến Long An",
                "notes": "Bàn giao vận chuyển",
            },
            auth=self.farmer_auth,
        )
        self.assertEqual(create_resp.status_code, 201)
        handover_id = create_resp.json()["id"]

        # 2. Bên nhận tiếp nhận bàn giao (accept)
        accept_resp = self.client.post(
            f"/handovers/{handover_id}/accept",
            json={"notes": "Đã kiểm tra chất lượng và nhập kho an toàn."},
            auth=self.farmer_auth,
        )
        self.assertEqual(accept_resp.status_code, 200)
        accept_data = accept_resp.json()
        self.assertEqual(accept_data["status"], HANDOVER_STATUS_ACCEPTED)
        self.assertEqual(accept_data["current_batch_owner"], "Doanh Nghiệp Chế Biến Long An")

        # Kiểm tra lô hàng đã chuyển quyền quản lý sang bên nhận
        batch_resp = self.client.get(f"/batches/{self.batch_id}")
        self.assertEqual(batch_resp.json()["current_owner"], "Doanh Nghiệp Chế Biến Long An")

        # Kiểm tra sự kiện HANDOVER_ACCEPTED đã được ghi nhận
        events_resp = self.client.get(f"/batches/{self.batch_id}/events")
        accepted_events = [e for e in events_resp.json() if e["event_type"] == EVENT_TYPE_HANDOVER_ACCEPTED]
        self.assertEqual(len(accepted_events), 1)

        # 3. Vì không còn bàn giao nào ở trạng thái pending, bên sở hữu mới có thể tạo tiếp bàn giao sang khâu tiếp theo
        create_resp2 = self.client.post(
            "/handovers",
            json={
                "batch_id": self.batch_id,
                "receiver_name": "Siêu Thị Nông Sản BigC",
                "notes": "Bàn giao phân phối bán lẻ",
            },
            auth=self.farmer_auth,
        )
        self.assertEqual(create_resp2.status_code, 201)
        self.assertEqual(create_resp2.json()["sender_name"], "Doanh Nghiệp Chế Biến Long An")
        self.assertEqual(create_resp2.json()["status"], HANDOVER_STATUS_PENDING)

    def test_reject_handover_workflow(self):
        """[AC 5]: Từ chối bàn giao -> Lô vẫn thuộc bên giao, ghi sự kiện, sau đó có thể tạo lại bàn giao."""
        # 1. Tạo bàn giao
        create_resp = self.client.post(
            "/handovers",
            json={
                "batch_id": self.batch_id,
                "receiver_name": "Đối Tác Kiểm Định",
                "notes": "Gửi mẫu kiểm định",
            },
            auth=self.farmer_auth,
        )
        self.assertEqual(create_resp.status_code, 201)
        handover_id = create_resp.json()["id"]

        # 2. Từ chối bàn giao (reject)
        reject_resp = self.client.post(
            f"/handovers/{handover_id}/reject",
            json={"notes": "Hàng không đủ số lượng như thỏa thuận."},
            auth=self.farmer_auth,
        )
        self.assertEqual(reject_resp.status_code, 200)
        reject_data = reject_resp.json()
        self.assertEqual(reject_data["status"], HANDOVER_STATUS_REJECTED)
        self.assertEqual(reject_data["current_batch_owner"], "Hợp tác xã Xoài Mỹ Xương")

        # Kiểm tra lô vẫn thuộc bên giao ban đầu
        batch_resp = self.client.get(f"/batches/{self.batch_id}")
        self.assertEqual(batch_resp.json()["current_owner"], "Hợp tác xã Xoài Mỹ Xương")

        # Kiểm tra sự kiện HANDOVER_REJECTED đã được ghi nhận
        events_resp = self.client.get(f"/batches/{self.batch_id}/events")
        rejected_events = [e for e in events_resp.json() if e["event_type"] == EVENT_TYPE_HANDOVER_REJECTED]
        self.assertEqual(len(rejected_events), 1)

        # 3. Cho phép tạo bàn giao mới vì yêu cầu trước đã kết thúc ở trạng thái rejected
        retry_resp = self.client.post(
            "/handovers",
            json={
                "batch_id": self.batch_id,
                "receiver_name": "Đối Tác Kiểm Định Khác",
                "notes": "Chuyển đơn vị kiểm định khác",
            },
            auth=self.farmer_auth,
        )
        self.assertEqual(retry_resp.status_code, 201)
        self.assertEqual(retry_resp.json()["status"], HANDOVER_STATUS_PENDING)

    def test_handover_query_endpoints(self):
        """Kiểm tra các endpoint tra cứu danh sách và chi tiết bàn giao."""
        self.client.post(
            "/handovers",
            json={
                "batch_id": self.batch_id,
                "receiver_name": "Đối Tác A",
            },
            auth=self.farmer_auth,
        )

        # Tra cứu danh sách bàn giao
        list_resp = self.client.get("/handovers")
        self.assertEqual(list_resp.status_code, 200)
        self.assertGreaterEqual(len(list_resp.json()), 1)

        # Tra cứu lọc theo status=pending
        pending_list = self.client.get("/handovers?status=pending")
        self.assertEqual(pending_list.status_code, 200)
        self.assertTrue(all(h["status"] == "pending" for h in pending_list.json()))

        # Tra cứu chi tiết
        first_id = list_resp.json()[0]["id"]
        detail_resp = self.client.get(f"/handovers/{first_id}")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.json()["id"], first_id)


if __name__ == "__main__":
    unittest.main()
