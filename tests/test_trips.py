"""Bộ kiểm thử tự động cho Module Vận Chuyển (Trips) & Telemetry Cảm Biến IoT.

Kiểm thử 4 tiêu chí cốt lõi:
1. Test 1: Tạo chuyến xe mới (POST /trips) -> Sự kiện BATCH_LOADED_TO_TRIP được tự động ghi vào chuỗi Hash-Chain SHA-256.
2. Test 2: Kết thúc chuyến xe (PATCH /trips/{id}/status) -> Sự kiện BATCH_UNLOADED_FROM_TRIP được tự động ghi nhận nối chuỗi băm.
3. Test 3: Telemetry nhiệt độ (POST /telemetry/temperature) -> Gửi mốc thời gian mới (status 'recorded'), gửi trùng/cũ hơn (status 'ignored').
4. Test 4: Phân quyền Multi-tenant -> Đảm bảo tổ chức này không thể can thiệp chuyến xe hoặc lô hàng của tổ chức khác.
"""

from __future__ import annotations

import base64
from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Batch,
    BatchEvent,
    Farm,
    ROLE_FARMER,
    Trip,
    User,
    EVENT_TYPE_BATCH_LOADED_TO_TRIP,
    EVENT_TYPE_BATCH_UNLOADED_FROM_TRIP,
    TRIP_STATUS_COMPLETED,
    TRIP_STATUS_IN_TRANSIT,
)
from app.security import hash_password


def _auth_header(username: str, password: str = "123456") -> str:
    creds = f"{username}:{password}".encode("utf-8")
    return f"Basic {base64.b64encode(creds).decode('utf-8')}"


@pytest.fixture
def trips_test_env():
    """Tạo môi trường test độc lập với CSDL SQLite in-memory và TestClient."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        # Tài khoản Nông dân HTX A
        user_a = User(
            username="farmer_org_a",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        # Tài khoản Doanh nghiệp HTX B (Tổ chức khác)
        user_b = User(
            username="transporter_org_b",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        db.add_all([user_a, user_b])

        # Vùng trồng của Tổ chức A
        farm_a = Farm(
            name="Vùng Trồng Xoài Đồng Tháp",
            location="Cao Lãnh, Đồng Tháp",
            area=3.5,
            owner="HTX Nong Nghiep Cao Lanh",
        )
        # Vùng trồng của Tổ chức B
        farm_b = Farm(
            name="Vùng Trồng Nhãn Tiền Giang",
            location="Cái Bè, Tiền Giang",
            area=2.0,
            owner="Cong Ty Van Tai Lanh Mekong",
        )
        db.add_all([farm_a, farm_b])
        db.commit()

        # Lô hàng của Tổ chức A
        batch_a = Batch(
            farm_id=farm_a.id,
            product_name="Xoài Cát Chu Chuẩn VietGAP",
            quantity=1200.0,
            harvest_date=date(2026, 10, 10),
            current_holder_org="HTX Nong Nghiep Cao Lanh",
        )
        # Lô hàng của Tổ chức B
        batch_b = Batch(
            farm_id=farm_b.id,
            product_name="Nhãn Xuồng Cơm Vàng",
            quantity=800.0,
            harvest_date=date(2026, 10, 12),
            current_holder_org="Cong Ty Van Tai Lanh Mekong",
        )
        db.add_all([batch_a, batch_b])
        db.commit()
        db.refresh(batch_a)
        db.refresh(batch_b)

        batch_a_id = batch_a.id
        batch_b_id = batch_b.id

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "client": client,
        "TestingSessionLocal": TestingSessionLocal,
        "batch_a_id": batch_a_id,
        "batch_b_id": batch_b_id,
    }

    app.dependency_overrides.clear()


# ==============================================================================
# TEST 1: Tạo chuyến xe mới (POST /trips) & Ghi sự kiện BATCH_LOADED_TO_TRIP
# ==============================================================================
def test_create_trip_and_verify_loaded_hash_chain(trips_test_env):
    """Test 1: Tạo chuyến xe mới (POST /trips).

    Kiểm tra sự kiện BATCH_LOADED_TO_TRIP được tự động ghi vào chuỗi Hash-Chain SHA-256
    của lô hàng kèm đầy đủ thông tin phương tiện và tài xế.
    """
    client: TestClient = trips_test_env["client"]
    batch_id: int = trips_test_env["batch_a_id"]
    SessionLocal = trips_test_env["TestingSessionLocal"]

    headers = {
        "Authorization": _auth_header("farmer_org_a"),
        "X-Organization-Id": "HTX Nong Nghiep Cao Lanh",
    }
    payload = {
        "code": "TRIP-2026-VN01",
        "container_code": "CONT-COLD-01",
        "vehicle_plate": "66C-123.45",
        "driver_name": "Nguyễn Văn Chuyển",
        "batch_ids": [batch_id],
    }

    response = client.post("/trips", json=payload, headers=headers)
    assert response.status_code == 201, f"Tạo chuyến xe thất bại: {response.text}"
    data = response.json()
    assert data["code"] == "TRIP-2026-VN01"
    assert data["status"] == TRIP_STATUS_IN_TRANSIT
    assert batch_id in data["batch_ids"]

    # Kiểm tra sự kiện BATCH_LOADED_TO_TRIP đã ghi vào CSDL
    with SessionLocal() as db:
        events = list(
            db.scalars(
                select(BatchEvent)
                .where(BatchEvent.batch_id == batch_id)
                .order_by(BatchEvent.id.asc())
            ).all()
        )
        assert len(events) >= 1
        loaded_event = events[-1]
        assert loaded_event.event_type == EVENT_TYPE_BATCH_LOADED_TO_TRIP
        assert loaded_event.actor == "farmer_org_a"
        assert loaded_event.organization == "HTX Nong Nghiep Cao Lanh"
        assert len(loaded_event.hash) == 64  # SHA-256 hex string
        assert "66C-123.45" in loaded_event.payload
        assert "TRIP-2026-VN01" in loaded_event.payload


# ==============================================================================
# TEST 2: Kết thúc chuyến xe (PATCH /trips/{id}/status) & Ghi sự kiện BATCH_UNLOADED_FROM_TRIP
# ==============================================================================
def test_end_trip_and_verify_unloaded_hash_chain(trips_test_env):
    """Test 2: Kết thúc chuyến xe (PATCH /trips/{id}/status).

    Kiểm tra sự kiện BATCH_UNLOADED_FROM_TRIP được tự động ghi nhận và liên kết
    chặt chẽ vào chuỗi SHA-256 Hash-Chain (previous_hash trỏ tới hash của sự kiện trước).
    """
    client: TestClient = trips_test_env["client"]
    batch_id: int = trips_test_env["batch_a_id"]
    SessionLocal = trips_test_env["TestingSessionLocal"]

    headers = {
        "Authorization": _auth_header("farmer_org_a"),
        "X-Organization-Id": "HTX Nong Nghiep Cao Lanh",
    }

    # 1. Tạo chuyến xe
    create_res = client.post(
        "/trips",
        json={
            "code": "TRIP-2026-VN02",
            "container_code": "CONT-COLD-02",
            "vehicle_plate": "66C-999.99",
            "driver_name": "Lê Văn Tải",
            "batch_ids": [batch_id],
        },
        headers=headers,
    )
    assert create_res.status_code == 201
    trip_id = create_res.json()["id"]

    # 2. Kết thúc chuyến xe
    patch_res = client.patch(
        f"/trips/{trip_id}/status",
        json={"status": TRIP_STATUS_COMPLETED},
        headers=headers,
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["status"] == TRIP_STATUS_COMPLETED

    # 3. Kiểm tra chuỗi băm 2 sự kiện: BATCH_LOADED_TO_TRIP -> BATCH_UNLOADED_FROM_TRIP
    with SessionLocal() as db:
        events = list(
            db.scalars(
                select(BatchEvent)
                .where(BatchEvent.batch_id == batch_id)
                .order_by(BatchEvent.id.asc())
            ).all()
        )
        assert len(events) >= 2
        loaded_event = events[-2]
        unloaded_event = events[-1]

        assert loaded_event.event_type == EVENT_TYPE_BATCH_LOADED_TO_TRIP
        assert unloaded_event.event_type == EVENT_TYPE_BATCH_UNLOADED_FROM_TRIP
        # Kiểm tra tính toàn vẹn của chuỗi băm (Hash-Chain integrity)
        assert unloaded_event.previous_hash == loaded_event.hash
        assert len(unloaded_event.hash) == 64


# ==============================================================================
# TEST 3: Telemetry nhiệt độ (POST /telemetry/temperature) - Recorded vs Ignored
# ==============================================================================
def test_telemetry_temperature_recorded_and_ignored(trips_test_env):
    """Test 3: Telemetry nhiệt độ (POST /telemetry/temperature).

    - Gửi mốc thời gian mới -> status 'recorded'
    - Gửi mốc thời gian trùng hoặc cũ hơn -> status 'ignored'
    """
    client: TestClient = trips_test_env["client"]
    batch_id: int = trips_test_env["batch_a_id"]

    headers = {
        "Authorization": _auth_header("farmer_org_a"),
        "X-Organization-Id": "HTX Nong Nghiep Cao Lanh",
    }

    # Tạo chuyến xe để gán telemetry
    create_res = client.post(
        "/trips",
        json={
            "code": "TRIP-TELEMETRY-01",
            "container_code": "CONT-IOT-88",
            "vehicle_plate": "66C-555.55",
            "driver_name": "Trần Cảm Biến",
            "batch_ids": [batch_id],
        },
        headers=headers,
    )
    assert create_res.status_code == 201
    trip_id = create_res.json()["id"]

    # 1. Gửi mốc nhiệt độ đầu tiên (T1: 10:00:00) -> Kỳ vọng status 'recorded'
    t1_res = client.post(
        "/telemetry/temperature",
        json={
            "trip_id": trip_id,
            "temperature": 4.2,
            "timestamp": "2026-10-19T10:00:00Z",
            "location": "Trạm Cao Lãnh",
        },
        headers=headers,
    )
    assert t1_res.status_code == 200
    t1_data = t1_res.json()
    assert t1_data["status"] == "recorded"
    assert t1_data["recorded_temperature"] == 4.2
    assert t1_data["log_id"] is not None

    # 2. Gửi mốc nhiệt độ mới hơn (T2: 10:05:00) -> Kỳ vọng status 'recorded'
    t2_res = client.post(
        "/telemetry/temperature",
        json={
            "trip_id": trip_id,
            "temperature": 4.6,
            "timestamp": "2026-10-19T10:05:00Z",
            "location": "Cầu Mỹ Thuận",
        },
        headers=headers,
    )
    assert t2_res.status_code == 200
    t2_data = t2_res.json()
    assert t2_data["status"] == "recorded"
    assert t2_data["recorded_temperature"] == 4.6

    # 3. Gửi mốc nhiệt độ TRÙNG mốc cũ (10:05:00) -> Kỳ vọng status 'ignored'
    t3_dup_res = client.post(
        "/telemetry/temperature",
        json={
            "trip_id": trip_id,
            "temperature": 5.0,
            "timestamp": "2026-10-19T10:05:00Z",
            "location": "Cầu Mỹ Thuận (Gửi lại)",
        },
        headers=headers,
    )
    assert t3_dup_res.status_code == 200
    t3_data = t3_dup_res.json()
    assert t3_data["status"] == "ignored"
    assert t3_data["log_id"] is None

    # 4. Gửi mốc nhiệt độ CŨ HƠN (T0: 10:02:00 < 10:05:00) -> Kỳ vọng status 'ignored'
    t4_old_res = client.post(
        "/telemetry/temperature",
        json={
            "trip_id": trip_id,
            "temperature": 4.3,
            "timestamp": "2026-10-19T10:02:00Z",
            "location": "Đoạn đường chậm tín hiệu",
        },
        headers=headers,
    )
    assert t4_old_res.status_code == 200
    t4_data = t4_old_res.json()
    assert t4_data["status"] == "ignored"
    assert t4_data["log_id"] is None


# ==============================================================================
# TEST 4: Phân quyền Multi-tenant giữa các tổ chức
# ==============================================================================
def test_multi_tenant_isolation_trips_and_batches(trips_test_env):
    """Test 4: Phân quyền Multi-tenant.

    - Tổ chức B không thể tạo chuyến xe chứa lô hàng của Tổ chức A (403 Forbidden).
    - Tổ chức B không thể xem hoặc kết thúc chuyến xe của Tổ chức A (403 Forbidden).
    - Tổ chức B không thể gửi telemetry vào chuyến xe của Tổ chức A (403 Forbidden).
    """
    client: TestClient = trips_test_env["client"]
    batch_a_id: int = trips_test_env["batch_a_id"]
    batch_b_id: int = trips_test_env["batch_b_id"]

    headers_org_a = {
        "Authorization": _auth_header("farmer_org_a"),
        "X-Organization-Id": "HTX Nong Nghiep Cao Lanh",
    }
    headers_org_b = {
        "Authorization": _auth_header("transporter_org_b"),
        "X-Organization-Id": "Cong Ty Van Tai Lanh Mekong",
    }

    # 1. Tổ chức B cố tình xếp lô của Tổ chức A lên xe của mình -> 403 Forbidden
    create_hijack_res = client.post(
        "/trips",
        json={
            "code": "TRIP-HIJACK-01",
            "vehicle_plate": "51D-999.88",
            "driver_name": "Tài Xế B",
            "batch_ids": [batch_a_id],  # Lô của Tổ chức A
        },
        headers=headers_org_b,
    )
    assert create_hijack_res.status_code == 403
    assert "tổ chức khác" in create_hijack_res.json()["detail"]

    # 2. Tổ chức A tạo chuyến xe hợp lệ của mình
    trip_a_res = client.post(
        "/trips",
        json={
            "code": "TRIP-ORG-A-VALID",
            "vehicle_plate": "66C-777.77",
            "driver_name": "Tài Xế A",
            "batch_ids": [batch_a_id],
        },
        headers=headers_org_a,
    )
    assert trip_a_res.status_code == 201
    trip_a_id = trip_a_res.json()["id"]

    # 3. Tổ chức B cố tình truy cập chuyến xe của Tổ chức A -> 403 Forbidden
    get_res = client.get(f"/trips/{trip_a_id}", headers=headers_org_b)
    assert get_res.status_code == 403

    # 4. Tổ chức B cố tình kết thúc chuyến xe của Tổ chức A -> 403 Forbidden
    patch_hijack_res = client.patch(
        f"/trips/{trip_a_id}/status",
        json={"status": TRIP_STATUS_COMPLETED},
        headers=headers_org_b,
    )
    assert patch_hijack_res.status_code == 403

    # 5. Tổ chức B cố tình gửi telemetry giả mạo vào chuyến xe của Tổ chức A -> 403 Forbidden
    fake_telemetry_res = client.post(
        "/telemetry/temperature",
        json={
            "trip_id": trip_a_id,
            "temperature": -20.0,
            "timestamp": "2026-10-19T11:00:00Z",
        },
        headers=headers_org_b,
    )
    assert fake_telemetry_res.status_code == 403
