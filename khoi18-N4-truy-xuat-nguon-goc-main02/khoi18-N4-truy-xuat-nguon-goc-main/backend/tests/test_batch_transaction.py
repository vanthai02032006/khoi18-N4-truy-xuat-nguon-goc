"""Unit tests cho luồng tạo lô T-20 và ghi sự kiện thu hoạch T-24 trong cùng 1 transaction duy nhất.

Tiêu chí nghiệm thu (DoD / AC):
1. Tạo lô luôn có đúng 1 sự kiện thu hoạch (HARVEST) đi kèm.
2. Mã băm của sự kiện được tính bằng SHA-256 (T-24 / SCRUM-40) và previous_hash là GENESIS_HASH.
3. Nếu cố ý ném lỗi khi tạo lô thì cả lô lẫn sự kiện đều không được lưu (rollback sạch).
4. Hàm ghi sự kiện `record_event` nhận transaction từ bên gọi, không tự commit transaction riêng.
5. Xoá lô nông sản sẽ cascade xoá sạch các sự kiện liên quan.
"""

import hashlib
import json
import unittest
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.events import (
    EVENT_TYPE_HARVEST,
    GENESIS_HASH,
    calculate_event_hash,
    record_event,
)
from app.main import app
from app.models import Batch, Event, Farm, ROLE_FARMER, User
from app.security import hash_password


class TestBatchTransactionAndEvents(unittest.TestCase):
    """Kiểm thử tính nguyên tử của transaction tạo lô và ghi sự kiện thu hoạch."""

    @classmethod
    def setUpClass(cls):
        # Tạo database SQLite in-memory để kiểm thử độc lập (dùng StaticPool để chia sẻ bộ nhớ)
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
        # Tạo lại toàn bộ bảng trước mỗi test case
        Base.metadata.create_all(bind=self.test_engine)
        self.db: Session = self.TestingSessionLocal()

        # Tạo tài khoản farmer và admin
        self.farmer_user = User(
            username="farmer_test",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        self.db.add(self.farmer_user)

        # Tạo 1 vùng trồng mẫu
        self.sample_farm = Farm(
            name="Vùng Trồng Xoài Cát Chu Cao Lãnh",
            location="Đồng Tháp",
            area=5.0,
            owner="HTX Xoài",
        )
        self.db.add(self.sample_farm)
        self.db.commit()
        self.db.refresh(self.sample_farm)

        # Override dependency get_db của FastAPI app
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

    def test_01_create_batch_generates_exactly_one_harvest_event(self):
        """AC 1: Tạo lô luôn có đúng 1 sự kiện thu hoạch đi kèm."""
        response = self.client.post(
            "/batches",
            auth=("farmer_test", "123456"),
            json={
                "farm_id": self.sample_farm.id,
                "product_name": "Xoài Cát Chu Xuất Khẩu",
                "quantity": 1500.0,
                "harvest_date": "2026-10-01",
            },
        )
        self.assertEqual(response.status_code, 201)
        batch_data = response.json()
        batch_id = batch_data["id"]

        # Kiểm tra lô đã được lưu
        batch = self.db.get(Batch, batch_id)
        self.assertIsNotNone(batch)
        self.assertEqual(batch.product_name, "Xoài Cát Chu Xuất Khẩu")

        # Kiểm tra đúng 1 sự kiện thu hoạch đi kèm
        events = list(
            self.db.scalars(
                select(Event).where(Event.batch_id == batch_id).order_by(Event.id)
            ).all()
        )
        self.assertEqual(len(events), 1, "Phải có chính xác 1 sự kiện thu hoạch đi kèm!")
        harvest_event = events[0]
        self.assertEqual(harvest_event.event_type, EVENT_TYPE_HARVEST)
        self.assertEqual(harvest_event.actor, "farmer_test")
        self.assertEqual(harvest_event.previous_hash, GENESIS_HASH)
        self.assertIn("Thu hoạch 1500.0 kg", harvest_event.description)

        # Gọi endpoint GET /batches/{batch_id}/events
        events_resp = self.client.get(f"/batches/{batch_id}/events")
        self.assertEqual(events_resp.status_code, 200)
        events_json = events_resp.json()
        self.assertEqual(len(events_json), 1)
        self.assertEqual(events_json[0]["event_type"], "HARVEST")
        self.assertEqual(events_json[0]["hash"], harvest_event.hash)

    def test_02_hash_calculation_matches_t24_sha256(self):
        """AC 2: Hàm băm ở T-24 (SCRUM-40) tạo chuỗi SHA-256 chính xác từ các trường dữ liệu."""
        now = datetime.now(timezone.utc)
        payload_data = json.dumps({"farm_id": 1, "product": "Xoài"}, sort_keys=True)
        computed_hash = calculate_event_hash(
            batch_id=1,
            event_type="HARVEST",
            actor="farmer",
            description="Thu hoạch xoài",
            data=payload_data,
            previous_hash=GENESIS_HASH,
            created_at=now,
        )

        expected_time_str = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        expected_canonical = f"1|HARVEST|farmer|Thu hoạch xoài|{payload_data}|{GENESIS_HASH}|{expected_time_str}"
        expected_hash = hashlib.sha256(expected_canonical.encode("utf-8")).hexdigest()

        self.assertEqual(len(computed_hash), 64)
        self.assertEqual(computed_hash, expected_hash)

    def test_03_deliberate_error_causes_clean_rollback(self):
        """AC 3: Nếu cố ý ném lỗi khi tạo lô thì cả lô lẫn sự kiện đều không được lưu (rollback sạch)."""
        initial_batches_count = self.db.scalar(select(func.count()).select_from(Batch))
        initial_events_count = self.db.scalar(select(func.count()).select_from(Event))

        # Mô phỏng nghiệp vụ tạo lô và ném lỗi bất ngờ trước khi commit
        try:
            # Bắt đầu transaction
            batch = Batch(
                farm_id=self.sample_farm.id,
                product_name="Lô Lỗi Cố Ý",
                quantity=500.0,
                harvest_date=date(2026, 10, 2),
            )
            self.db.add(batch)
            self.db.flush()

            # Ghi sự kiện trong cùng transaction
            record_event(
                db=self.db,
                batch_id=batch.id,
                event_type=EVENT_TYPE_HARVEST,
                actor="farmer_test",
                description="Thu hoạch lô lỗi cố ý",
                data={"test": "deliberate_error"},
            )

            # Cố ý ném lỗi mô phỏng sự cố hệ thống / validation nghiệp vụ
            raise RuntimeError("Lỗi mô phỏng cố ý để kiểm tra rollback sạch!")

            # db.commit() sẽ không bao giờ được gọi
            self.db.commit()
        except RuntimeError:
            # Bắt lỗi và rollback sạch
            self.db.rollback()

        # Kiểm tra lại số lượng bản ghi trong database
        batches_after = self.db.scalar(select(func.count()).select_from(Batch))
        events_after = self.db.scalar(select(func.count()).select_from(Event))

        self.assertEqual(
            batches_after,
            initial_batches_count,
            "Lô không được lưu vào database khi xảy ra lỗi (rollback sạch)!",
        )
        self.assertEqual(
            events_after,
            initial_events_count,
            "Sự kiện không được lưu vào database khi xảy ra lỗi (rollback sạch)!",
        )

    def test_04_record_event_does_not_commit_independently(self):
        """AC 4: Hàm ghi sự kiện nhận transaction từ bên gọi, không tự commit transaction riêng."""
        batch = Batch(
            farm_id=self.sample_farm.id,
            product_name="Lô Kiểm Tra Session",
            quantity=800.0,
            harvest_date=date(2026, 10, 3),
        )
        self.db.add(batch)
        self.db.flush()

        # Gọi hàm ghi sự kiện
        event = record_event(
            db=self.db,
            batch_id=batch.id,
            event_type=EVENT_TYPE_HARVEST,
            actor="farmer_test",
            description="Thu hoạch lô kiểm tra session",
        )

        # Kiểm tra: transaction vẫn đang hoạt động chưa commit
        self.assertTrue(self.db.in_transaction())

        # Nếu rollback thì không lưu lại gì vào database
        self.db.rollback()

        # Mở session mới để xác nhận
        fresh_db = self.TestingSessionLocal()
        try:
            saved_event = fresh_db.get(Event, event.id) if event.id else None
            self.assertIsNone(
                saved_event,
                "Hàm record_event không được tự commit, khi bên ngoài rollback thì không được có dữ liệu!",
            )
        finally:
            fresh_db.close()

    def test_05_cascade_deletion_cleans_events(self):
        """AC 5: Khi xoá lô nông sản, các sự kiện liên quan cũng được cascade xoá theo."""
        # Tạo 1 lô và 1 sự kiện
        response = self.client.post(
            "/batches",
            auth=("farmer_test", "123456"),
            json={
                "farm_id": self.sample_farm.id,
                "product_name": "Lô Sẽ Bị Xoá",
                "quantity": 300.0,
                "harvest_date": "2026-10-04",
            },
        )
        self.assertEqual(response.status_code, 201)
        batch_id = response.json()["id"]

        # Kiểm tra sự kiện tồn tại
        events_before = self.db.scalars(
            select(Event).where(Event.batch_id == batch_id)
        ).all()
        self.assertEqual(len(list(events_before)), 1)

        # Tạo admin để xoá
        admin_user = User(
            username="admin_test",
            password=hash_password("123456"),
            role="admin",
        )
        self.db.add(admin_user)
        self.db.commit()

        # Xoá lô nông sản bằng DELETE /batches/{id}
        del_resp = self.client.delete(
            f"/batches/{batch_id}",
            auth=("admin_test", "123456"),
        )
        self.assertEqual(del_resp.status_code, 200)

        # Kiểm tra sự kiện của lô này đã bị xoá cascade
        events_after = self.db.scalars(
            select(Event).where(Event.batch_id == batch_id)
        ).all()
        self.assertEqual(len(list(events_after)), 0, "Các sự kiện của lô phải được tự động xoá cascade!")

    def test_06_event_chaining_and_tampering_detection(self):
        """Kiểm tra tính toàn vẹn của chuỗi băm (hash chain) qua nhiều sự kiện và phát hiện sửa đổi lén."""
        batch = Batch(
            farm_id=self.sample_farm.id,
            product_name="Xoài Cát Chu",
            quantity=1000.0,
            harvest_date=date(2026, 10, 5),
        )
        self.db.add(batch)
        self.db.flush()

        # Sự kiện 1: Thu hoạch
        event1 = record_event(
            db=self.db,
            batch_id=batch.id,
            event_type="HARVEST",
            actor="farmer_test",
            description="Thu hoạch tại vườn",
        )
        self.assertEqual(event1.previous_hash, GENESIS_HASH)

        # Sự kiện 2: Đóng gói
        event2 = record_event(
            db=self.db,
            batch_id=batch.id,
            event_type="PACKAGING",
            actor="packer_test",
            description="Đóng gói thùng carton xuất khẩu",
        )
        # Sự kiện 2 phải liên kết trực tiếp với mã băm của sự kiện 1
        self.assertEqual(event2.previous_hash, event1.hash)

        # Sự kiện 3: Vận chuyển chuỗi lạnh
        event3 = record_event(
            db=self.db,
            batch_id=batch.id,
            event_type="TRANSPORT",
            actor="driver_test",
            description="Vận chuyển xe lạnh nhiệt độ 4.5°C",
        )
        self.assertEqual(event3.previous_hash, event2.hash)
        self.db.commit()

        # Kiểm tra tính toàn vẹn: nếu dữ liệu sự kiện 1 bị sửa lén thì hàm băm T-24 sẽ không khớp
        recalculated_hash1 = calculate_event_hash(
            batch_id=event1.batch_id,
            event_type=event1.event_type,
            actor=event1.actor,
            description=event1.description,
            data=event1.data,
            previous_hash=event1.previous_hash,
            created_at=event1.created_at,
        )
        self.assertEqual(event1.hash, recalculated_hash1)

        # Giả lập sửa lén mô tả của sự kiện 1 -> mã băm tính lại sẽ bị sai khác
        tampered_hash = calculate_event_hash(
            batch_id=event1.batch_id,
            event_type=event1.event_type,
            actor=event1.actor,
            description="SỬA LÉN: Thu hoạch xoài loại 3 đổi tên",
            data=event1.data,
            previous_hash=event1.previous_hash,
            created_at=event1.created_at,
        )
        self.assertNotEqual(event1.hash, tampered_hash, "Mã băm T-24 phải phát hiện được sửa lén bản ghi!")


if __name__ == "__main__":
    unittest.main()
