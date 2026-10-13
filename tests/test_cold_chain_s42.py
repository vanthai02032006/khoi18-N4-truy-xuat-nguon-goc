"""Bộ kiểm thử đơn vị & tích hợp chuẩn S-42 (Cold Chain & Telemetry Subsystem).

Bao phủ 100% 5 kịch bản User Stories (S-42.1 đến S-42.5 / 17 SP):
1. S-42.1: Nhận luồng telemetry IoT, khử trùng lặp (Deduplication), lọc mốc cũ và sắp xếp đúng trình tự.
2. S-42.2: Vòng đời chuyến xe và tự động sinh sự kiện ON-BOARD / OFF-BOARD vào chuỗi băm bất biến của lô hàng.
3. S-42.3: Lõi đánh giá vi phạm (Core Engine):
   - Ngưỡng nhiệt độ [temp_min, temp_max].
   - Cửa sổ thời gian 30 phút (dao động ngắn < 30m không vi phạm).
   - Debounce Gap 5 phút (nhiệt độ về bình thường <= 5m không ngắt quãng đợt vi phạm).
   - Tính toán Peak Temperature chính xác.
4. S-42.4: Cảnh báo đỏ chuỗi lạnh khi bàn giao lô hàng (Handover Warning - RED / GREEN).
5. S-42.5: Báo cáo đánh giá hiệu suất nhà xe theo kỳ (Carrier Performance Report & Compliance Rate).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.cold_chain_engine import (
    deduplicate_and_sort_telemetry,
    evaluate_cold_chain_violations,
)
from app.database import Base, get_db
from app.main import app
from app.models import (
    Batch,
    BatchEvent,
    Farm,
    ROLE_ADMIN,
    ROLE_FARMER,
    TelemetryReading,
    Trip,
    TripConsignment,
    TripViolation,
    User,
)
from app.security import hash_password


@pytest.fixture
def cold_chain_s42_env():
    """Thiết lập môi trường kiểm thử SQLite in-memory cho bộ test chuẩn S-42."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        # 1. Tạo tài khoản người dùng
        user_farmer = User(
            username="farmer_cold",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        user_admin = User(
            username="admin_cold",
            password=hash_password("123456"),
            role=ROLE_ADMIN,
        )
        db.add_all([user_farmer, user_admin])

        # 2. Tạo nông trại và lô hàng kiểm thử
        farm = Farm(
            name="Nông trại Rau sạch Đà Lạt",
            location="Phường 7, TP. Đà Lạt, Lâm Đồng",
            area=5.0,
            owner="Hợp tác xã Nông sản Lạnh",
        )
        db.add(farm)
        db.flush()

        batch_1 = Batch(
            farm_id=farm.id,
            batch_code="BATCH-S42-001",
            product_name="Xà lách thủy canh",
            quantity=500.0,
            harvest_date=date(2026, 10, 10),
            current_holder_org="HTX Rau sạch Đà Lạt",
        )
        batch_2 = Batch(
            farm_id=farm.id,
            batch_code="BATCH-S42-002",
            product_name="Dâu tây New Zealand",
            quantity=200.0,
            harvest_date=date(2026, 10, 11),
            current_holder_org="HTX Rau sạch Đà Lạt",
        )
        batch_3 = Batch(
            farm_id=farm.id,
            batch_code="BATCH-S42-003",
            product_name="Cà chua bi hữu cơ",
            quantity=350.0,
            harvest_date=date(2026, 10, 12),
            current_holder_org="HTX Rau sạch Đà Lạt",
        )
        db.add_all([batch_1, batch_2, batch_3])
        db.commit()

        b1_id, b2_id, b3_id = batch_1.id, batch_2.id, batch_3.id

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "client": client,
        "batch_ids": [b1_id, b2_id, b3_id],
        "db_session_factory": TestingSessionLocal,
    }

    app.dependency_overrides.clear()


# ==============================================================================
# S-42.1: KIỂM THỬ TIẾP NHẬN DỮ LIỆU IOT, LỌC MỐC CŨ & KHỬ TRÙNG LẶP
# ==============================================================================
def test_s42_1_telemetry_ingestion_and_deduplication(cold_chain_s42_env):
    """Kịch bản S-42.1:
    - Tạo chuyến xe với thời gian bắt đầu xác định.
    - Gửi gói telemetry gồm: 1 bản ghi thời gian cũ hơn chuyến xe, 2 bản ghi trùng lặp,
      và các bản ghi bị xáo trộn thứ tự thời gian.
    - Kết quả mong đợi:
      * Bản ghi cũ bị loại bỏ.
      * Bản ghi trùng lặp bị khử chỉ còn 1.
      * Dữ liệu lưu trong CSDL sắp xếp tuần tự theo thời gian.
    """
    client: TestClient = cold_chain_s42_env["client"]
    b1_id = cold_chain_s42_env["batch_ids"][0]

    # 1. Khởi tạo chuyến xe
    trip_res = client.post(
        "/cold-chain/trips",
        auth=("farmer_cold", "123456"),
        json={
            "trip_code": "TRIP-S42-01",
            "carrier_name": "Công ty Vận tải Lạnh Hoàng Long",
            "vehicle_plate": "49A-123.45",
            "driver_name": "Nguyễn Văn Tài",
            "driver_phone": "0912345678",
            "start_location": "Đà Lạt, Lâm Đồng",
            "end_location": "Chợ đầu mối Thủ Đức, TP.HCM",
            "batch_ids": [b1_id],
            "temp_min_threshold": 2.0,
            "temp_max_threshold": 8.0,
            "max_allowed_breach_minutes": 30,
            "started_at": "2026-10-13T08:00:00Z",
        },
    )
    assert trip_res.status_code == 201
    trip_data = trip_res.json()
    trip_started_at = trip_data["started_at"]

    # 2. Chuẩn bị luồng telemetry:
    # - 1 bản ghi cũ (trước trip_started_at 1 tiếng)
    # - 2 bản ghi trùng lặp (cùng timestamp 08:30)
    # - Các bản ghi xáo trộn thời gian (08:40 gửi trước 08:20)
    telemetry_payload = {
        "readings": [
            {
                "trip_code": "TRIP-S42-01",
                "device_id": "SENSOR-HL-01",
                "temperature": 5.0,
                "humidity": 85.0,
                "recorded_at": "2026-10-01T00:00:00Z",  # Rất cũ -> Phải bị lọc
            },
            {
                "trip_code": "TRIP-S42-01",
                "device_id": "SENSOR-HL-01",
                "temperature": 4.5,
                "humidity": 82.0,
                "recorded_at": "2026-10-13T08:40:00Z",
            },
            {
                "trip_code": "TRIP-S42-01",
                "device_id": "SENSOR-HL-01",
                "temperature": 4.0,
                "humidity": 80.0,
                "recorded_at": "2026-10-13T08:20:00Z",
            },
            {
                "trip_code": "TRIP-S42-01",
                "device_id": "SENSOR-HL-01",
                "temperature": 4.2,
                "humidity": 81.0,
                "recorded_at": "2026-10-13T08:30:00Z",  # Bản ghi trùng 1
            },
            {
                "trip_code": "TRIP-S42-01",
                "device_id": "SENSOR-HL-01",
                "temperature": 4.2,
                "humidity": 81.0,
                "recorded_at": "2026-10-13T08:30:00Z",  # Bản ghi trùng 2 -> Phải bị khử
            },
        ]
    }

    ingest_res = client.post("/cold-chain/telemetry", json=telemetry_payload)
    assert ingest_res.status_code == 200
    res_data = ingest_res.json()

    assert res_data["received_count"] == 5
    # 1 bản ghi cũ bị lọc + 1 bản ghi trùng bị lọc -> 3 bản ghi được chấp nhận
    assert res_data["accepted_count"] == 3
    assert res_data["filtered_count"] == 2

    # 3. Kiểm tra trong CSDL đảm bảo thứ tự thời gian tăng dần
    db_factory = cold_chain_s42_env["db_session_factory"]
    with db_factory() as db:
        saved_readings = list(
            db.scalars(
                select(TelemetryReading)
                .where(TelemetryReading.trip_id == trip_data["id"])
                .order_by(TelemetryReading.recorded_at.asc())
            ).all()
        )
        assert len(saved_readings) == 3
        timestamps = [r.recorded_at for r in saved_readings]
        assert timestamps[0] == "2026-10-13T08:20:00+00:00"
        assert timestamps[1] == "2026-10-13T08:30:00+00:00"
        assert timestamps[2] == "2026-10-13T08:40:00+00:00"


# ==============================================================================
# S-42.2: KIỂM THỬ VÒNG ĐỜI CHUYẾN XE & TỰ SINH SỰ KIỆN ON-BOARD / OFF-BOARD
# ==============================================================================
def test_s42_2_trip_lifecycle_and_batch_events(cold_chain_s42_env):
    """Kịch bản S-42.2:
    - Khi tạo chuyến xe chở lô hàng: hệ thống tự động sinh sự kiện ON-BOARD cho lô hàng.
    - Khi hoàn tất chuyến xe (complete): hệ thống tự động sinh sự kiện OFF-BOARD cho lô hàng.
    - Cả hai sự kiện đều tham gia vào chuỗi băm bất biến của BatchEvent.
    """
    client: TestClient = cold_chain_s42_env["client"]
    b1_id, b2_id = cold_chain_s42_env["batch_ids"][:2]

    # 1. Tạo chuyến xe chở 2 lô hàng
    create_res = client.post(
        "/cold-chain/trips",
        auth=("farmer_cold", "123456"),
        json={
            "trip_code": "TRIP-S42-02",
            "carrier_name": "Công ty Vận tải Phương Trang Cold",
            "vehicle_plate": "51B-888.99",
            "driver_name": "Lê Văn Lộc",
            "start_location": "Đà Lạt",
            "end_location": "Bình Dương",
            "batch_ids": [b1_id, b2_id],
        },
    )
    assert create_res.status_code == 201

    # Kiểm tra sự kiện ON-BOARD đã được tự động sinh cho cả 2 lô hàng
    db_factory = cold_chain_s42_env["db_session_factory"]
    with db_factory() as db:
        for bid in [b1_id, b2_id]:
            on_board_ev = db.scalar(
                select(BatchEvent)
                .where(BatchEvent.batch_id == bid, BatchEvent.event_type == "ON_BOARD")
            )
            assert on_board_ev is not None
            assert on_board_ev.organization == "Công ty Vận tải Phương Trang Cold"
            assert "TRIP-S42-02" in on_board_ev.payload
            assert "51B-888.99" in on_board_ev.payload
            assert on_board_ev.hash is not None and len(on_board_ev.hash) == 64

    # 2. Hoàn tất chuyến xe
    complete_res = client.put(
        "/cold-chain/trips/TRIP-S42-02/complete",
        auth=("farmer_cold", "123456"),
    )
    assert complete_res.status_code == 200
    comp_data = complete_res.json()
    assert comp_data["status"] == "COMPLETED"
    assert set(comp_data["batches_offboarded"]) == {b1_id, b2_id}

    # Kiểm tra sự kiện OFF-BOARD đã được tự động sinh cho cả 2 lô hàng
    with db_factory() as db:
        for bid in [b1_id, b2_id]:
            off_board_ev = db.scalar(
                select(BatchEvent)
                .where(BatchEvent.batch_id == bid, BatchEvent.event_type == "OFF_BOARD")
            )
            assert off_board_ev is not None
            assert off_board_ev.organization == "Công ty Vận tải Phương Trang Cold"
            assert "COMPLETED" in off_board_ev.payload
            # Chuỗi băm liên kết previous_hash với sự kiện trước đó
            assert off_board_ev.previous_hash != "0" * 64


# ==============================================================================
# S-42.3: KIỂM THỬ CORE ENGINE S-42 (CỬA SỔ 30 PHÚT, DEBOUNCE GAP 5 PHÚT, PEAK TEMP)
# ==============================================================================
def test_s42_3_core_engine_violation_detection():
    """Kịch bản S-42.3: Kiểm thử độc lập và toàn diện lõi thuật toán Core Engine S-42."""

    # --------------------------------------------------------------------------
    # Subtest 3A: Vượt ngưỡng ngắn (< 30 phút) -> KHÔNG VI PHẠM
    # --------------------------------------------------------------------------
    transient_readings = [
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T09:00:00Z", "temperature": 9.5},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T09:10:00Z", "temperature": 10.2},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T09:20:00Z", "temperature": 9.0},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T09:30:00Z", "temperature": 5.0},  # Về an toàn
    ]
    # Kéo dài từ 09:00 đến 09:20 = 20 phút (< 30 phút)
    v_transient = evaluate_cold_chain_violations(
        readings=transient_readings,
        temp_min=2.0,
        temp_max=8.0,
        breach_window_minutes=30.0,
        debounce_gap_minutes=5.0,
    )
    assert len(v_transient) == 0, "Dao động ngắn < 30 phút không được kết luận vi phạm!"

    # --------------------------------------------------------------------------
    # Subtest 3B: Vượt ngưỡng kéo dài (45 phút >= 30 phút) -> GHI NHẬN VI PHẠM & PEAK TEMP
    # --------------------------------------------------------------------------
    prolonged_readings = [
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T10:00:00Z", "temperature": 9.0},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T10:15:00Z", "temperature": 11.5},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T10:30:00Z", "temperature": 13.8},  # Peak temp
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T10:45:00Z", "temperature": 10.0},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T10:55:00Z", "temperature": 5.0},  # Về an toàn
    ]
    # Kéo dài từ 10:00 đến 10:45 = 45 phút (>= 30 phút)
    v_prolonged = evaluate_cold_chain_violations(
        readings=prolonged_readings,
        temp_min=2.0,
        temp_max=8.0,
        breach_window_minutes=30.0,
        debounce_gap_minutes=5.0,
    )
    assert len(v_prolonged) == 1
    viol = v_prolonged[0]
    assert viol["violation_type"] == "HIGH_TEMP_BREACH"
    assert viol["duration_minutes"] == 45.0
    assert viol["peak_temperature"] == 13.8
    assert viol["threshold_temp"] == 8.0
    assert viol["severity"] == "CRITICAL"  # abs(13.8 - 8.0) >= 5.0

    # --------------------------------------------------------------------------
    # Subtest 3C: Debounce Gap <= 5 phút (Gộp liền mạch đợt vi phạm)
    # --------------------------------------------------------------------------
    # 11:00 (10°C) -> 11:20 (11°C) -> 11:23 (6°C - về an toàn 3 phút) -> 11:25 (10.5°C) -> 11:35 (10°C)
    # Tổng thời gian từ 11:00 đến 11:35 = 35 phút (>= 30 phút).
    # Khoảng về an toàn là 3 phút (<= 5 phút debounce gap) nên được gộp, KHÔNG bị chia tách!
    debounced_readings = [
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T11:00:00Z", "temperature": 10.0},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T11:20:00Z", "temperature": 11.0},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T11:23:00Z", "temperature": 6.0},  # Hạ trong 3 phút
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T11:25:00Z", "temperature": 10.5},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T11:35:00Z", "temperature": 10.0},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T11:45:00Z", "temperature": 5.0},  # Về an toàn hẳn
    ]
    v_debounced = evaluate_cold_chain_violations(
        readings=debounced_readings,
        temp_min=2.0,
        temp_max=8.0,
        breach_window_minutes=30.0,
        debounce_gap_minutes=5.0,
    )
    assert len(v_debounced) == 1, "Debounce gap <= 5 phút phải gộp liền mạch thành 1 vi phạm duy nhất!"
    assert v_debounced[0]["duration_minutes"] == 35.0
    assert v_debounced[0]["peak_temperature"] == 11.0

    # --------------------------------------------------------------------------
    # Subtest 3D: Gap bình thường > 5 phút (Chia tách thành 2 đợt riêng biệt)
    # --------------------------------------------------------------------------
    # 12:00 -> 12:20 (20 phút vượt ngưỡng < 30m)
    # 12:20 -> 12:35 (15 phút bình thường > 5m debounce gap)
    # 12:35 -> 12:55 (20 phút vượt ngưỡng < 30m)
    # Vì khoảng giữa là 15 phút > 5 phút, đây là 2 đợt riêng biệt, mỗi đợt 20 phút -> Không có đợt nào đạt 30m!
    separated_readings = [
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T12:00:00Z", "temperature": 10.0},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T12:20:00Z", "temperature": 10.0},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T12:30:00Z", "temperature": 5.0},  # Bình thường 15m
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T12:35:00Z", "temperature": 10.0},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T12:55:00Z", "temperature": 10.0},
        {"device_id": "DEV-01", "recorded_at": "2026-10-13T13:05:00Z", "temperature": 5.0},
    ]
    v_separated = evaluate_cold_chain_violations(
        readings=separated_readings,
        temp_min=2.0,
        temp_max=8.0,
        breach_window_minutes=30.0,
        debounce_gap_minutes=5.0,
    )
    assert len(v_separated) == 0, "Hai đợt riêng biệt dưới 30 phút không được cộng dồn thành vi phạm!"


# ==============================================================================
# S-42.4: KIỂM THỬ CẢNH BÁO ĐỎ CHUỖI LẠNH KHI BÀN GIAO LÔ HÀNG (HANDOVER WARNING)
# ==============================================================================
def test_s42_4_handover_warning_red_and_green(cold_chain_s42_env):
    """Kịch bản S-42.4:
    - Lô 1 (batch_1): Chở trên chuyến xe an toàn -> Cảnh báo XANH (GREEN), 0 vi phạm.
    - Lô 3 (batch_3): Chở trên chuyến xe bị sự cố vượt ngưỡng 40 phút -> CẢNH BÁO ĐỎ (RED),
      kèm chi tiết nhiệt độ đỉnh và khuyến cáo từ chối tiếp nhận/cách ly.
    """
    client: TestClient = cold_chain_s42_env["client"]
    b1_id, _, b3_id = cold_chain_s42_env["batch_ids"]

    # 1. Chuyến xe an toàn cho Lô 1
    t1_res = client.post(
        "/cold-chain/trips",
        auth=("farmer_cold", "123456"),
        json={
            "trip_code": "TRIP-SAFE-01",
            "carrier_name": "Đội xe Tiêu Chuẩn Xanh",
            "vehicle_plate": "29H-111.22",
            "driver_name": "Trần An",
            "start_location": "Đà Lạt",
            "end_location": "TP.HCM",
            "batch_ids": [b1_id],
            "started_at": "2026-10-13T09:00:00Z",
        },
    )
    assert t1_res.status_code == 201

    # Nạp telemetry an toàn (4.5°C)
    client.post(
        "/cold-chain/telemetry",
        json={
            "readings": [
                {"trip_code": "TRIP-SAFE-01", "device_id": "TC-01", "temperature": 4.5, "recorded_at": "2026-10-13T10:00:00Z"},
                {"trip_code": "TRIP-SAFE-01", "device_id": "TC-01", "temperature": 4.8, "recorded_at": "2026-10-13T11:00:00Z"},
            ]
        },
    )

    # Kiểm tra Handover Warning của Lô 1 -> GREEN
    warn1_res = client.get(f"/cold-chain/batches/{b1_id}/handover-warning")
    assert warn1_res.status_code == 200
    w1_data = warn1_res.json()
    assert w1_data["warning_level"] == "GREEN"
    assert w1_data["has_violations"] is False
    assert w1_data["total_violations"] == 0
    assert "AN TOÀN BÀN GIAO" in w1_data["recommendation"]

    # 2. Chuyến xe sự cố vi phạm cho Lô 3 (40 phút ở nhiệt độ 14°C)
    t2_res = client.post(
        "/cold-chain/trips",
        auth=("farmer_cold", "123456"),
        json={
            "trip_code": "TRIP-BREACH-01",
            "carrier_name": "Nhà xe Cẩu Thả Logistics",
            "vehicle_plate": "60C-999.00",
            "driver_name": "Bùi Hỏng",
            "start_location": "Đà Lạt",
            "end_location": "Bình Dương",
            "batch_ids": [b3_id],
            "temp_min_threshold": 2.0,
            "temp_max_threshold": 8.0,
            "max_allowed_breach_minutes": 30,
            "started_at": "2026-10-13T13:00:00Z",
        },
    )
    assert t2_res.status_code == 201

    # Nạp telemetry vi phạm kéo dài 40 phút
    client.post(
        "/cold-chain/telemetry",
        json={
            "readings": [
                {"trip_code": "TRIP-BREACH-01", "device_id": "BH-01", "temperature": 11.0, "recorded_at": "2026-10-13T14:00:00Z"},
                {"trip_code": "TRIP-BREACH-01", "device_id": "BH-01", "temperature": 14.5, "recorded_at": "2026-10-13T14:20:00Z"},
                {"trip_code": "TRIP-BREACH-01", "device_id": "BH-01", "temperature": 12.0, "recorded_at": "2026-10-13T14:40:00Z"},
                {"trip_code": "TRIP-BREACH-01", "device_id": "BH-01", "temperature": 5.0, "recorded_at": "2026-10-13T14:50:00Z"},
            ]
        },
    )

    # Kiểm tra Handover Warning của Lô 3 -> RED
    warn3_res = client.get(f"/cold-chain/batches/{b3_id}/handover-warning")
    assert warn3_res.status_code == 200
    w3_data = warn3_res.json()
    assert w3_data["warning_level"] == "RED"
    assert w3_data["has_violations"] is True
    assert w3_data["total_violations"] == 1
    assert w3_data["worst_peak_temperature"] == 14.5
    assert w3_data["max_violation_duration_minutes"] == 40.0
    assert "CẢNH BÁO ĐỎ BÀN GIAO" in w3_data["recommendation"]
    assert "Nhà xe Cẩu Thả Logistics" in w3_data["carrier_names"]
    assert "60C-999.00" in w3_data["vehicle_plates"]


# ==============================================================================
# S-42.5: KIỂM THỬ BÁO CÁO HIỆU SUẤT NHÀ XE THEO KỲ (CARRIER REPORT)
# ==============================================================================
def test_s42_5_carrier_performance_report(cold_chain_s42_env):
    """Kịch bản S-42.5:
    - Kiểm tra báo cáo tổng hợp chất lượng các nhà xe:
      * Tổng số chuyến xe đã thực hiện.
      * Tỷ lệ tuân thủ (%) và số vụ vi phạm.
      * Xếp hạng đánh giá (XUẤT SẮC, ĐẠT CHUẨN, CẢNH CÁO, ĐÌNH CHỈ HỢP ĐỒNG).
    """
    client: TestClient = cold_chain_s42_env["client"]
    b1_id, b2_id = cold_chain_s42_env["batch_ids"][:2]

    # Tạo chuyến cho Nhà xe Uy Tín (Hoàn tất 100% không vi phạm)
    client.post(
        "/cold-chain/trips",
        auth=("farmer_cold", "123456"),
        json={
            "trip_code": "TRIP-REP-GOOD",
            "carrier_name": "Nhà Xe Uy Tín 5 Sao",
            "vehicle_plate": "50E-111.11",
            "driver_name": "Nguyễn Chuẩn",
            "start_location": "Đà Lạt",
            "end_location": "TP.HCM",
            "batch_ids": [b1_id],
        },
    )
    client.put("/cold-chain/trips/TRIP-REP-GOOD/complete", auth=("farmer_cold", "123456"))

    # Lấy báo cáo nhà xe
    rep_res = client.get("/cold-chain/reports/carriers")
    assert rep_res.status_code == 200
    rep_data = rep_res.json()
    assert rep_data["total_carriers"] >= 1

    good_carrier = next((c for c in rep_data["carriers"] if c["carrier_name"] == "Nhà Xe Uy Tín 5 Sao"), None)
    assert good_carrier is not None
    assert good_carrier["total_trips"] == 1
    assert good_carrier["compliant_trips"] == 1
    assert good_carrier["violated_trips"] == 0
    assert good_carrier["compliance_rate"] == 100.0
    assert good_carrier["rating"] == "XUẤT SẮC"

    # Lọc theo nhà xe cụ thể
    filter_res = client.get("/cold-chain/reports/carriers?carrier_name=Nhà%20Xe%20Uy%20Tín%205%20Sao")
    assert filter_res.status_code == 200
    filtered_data = filter_res.json()
    assert filtered_data["total_carriers"] == 1
    assert filtered_data["carriers"][0]["carrier_name"] == "Nhà Xe Uy Tín 5 Sao"
