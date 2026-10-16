"""Kiểm thử tính năng quản lý ngưỡng và độ trễ riêng cho từng loại sản phẩm (Cold Chain Thresholds).

Đáp ứng User Story & Tiêu chí nghiệm thu (DoD / AC):
1. Mỗi sản phẩm có ngưỡng trên (temp_max), ngưỡng dưới (temp_min) và độ trễ (delay_minutes):
   - Rau lá và thịt đông lạnh không dùng chung một con số.
   - Thêm / sửa cấu hình chỉ dành cho quản trị viên (Admin).
2. Chuyến chở nhiều sản phẩm áp ngưỡng chặt nhất:
   - temp_min_effective = max(temp_min các sản phẩm)
   - temp_max_effective = min(temp_max các sản phẩm)
   - delay_effective = min(delay_minutes các sản phẩm)
   - Nếu dải nhiệt độ không giao nhau (rỗng) -> phát sinh cảnh báo không tương thích (ví dụ rau lá và thịt đông lạnh).
3. Đổi cấu hình không tính lại vi phạm đã ghi:
   - Khi đã ghi một vi phạm với snapshot ngưỡng tại thời điểm đó.
   - Quản trị viên thay đổi ngưỡng sau đó (ví dụ nới lỏng hoặc siết chặt).
   - Bản ghi vi phạm đã ghi trước đó vẫn giữ nguyên các giá trị applied_temp_min, applied_temp_max, applied_delay_minutes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    ColdChainThreshold,
    ColdChainViolation,
    ROLE_ADMIN,
    ROLE_FARMER,
    User,
)
from app.security import hash_password


@pytest.fixture
def cold_chain_env():
    """Thiết lập môi trường kiểm thử SQLite in-memory cho module Cold Chain."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        # Tài khoản Quản trị hệ thống (admin)
        user_admin = User(
            username="admin_sys",
            password=hash_password("123456"),
            role=ROLE_ADMIN,
        )
        # Tài khoản Nông dân / Đối tác (farmer)
        user_farmer = User(
            username="farmer_test",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        db.add_all([user_admin, user_farmer])

        # Cấu hình sẵn ngưỡng cho 2 loại sản phẩm điển hình:
        # 1. Rau lá: 2.0°C đến 8.0°C, độ trễ 15 phút
        t_rau = ColdChainThreshold(
            product_type="Rau lá",
            temp_min=2.0,
            temp_max=8.0,
            delay_minutes=15,
            description="Rau cải, xà lách tươi",
            created_at="2026-10-07T00:00:00Z",
            updated_at="2026-10-07T00:00:00Z",
        )
        # 2. Thịt đông lạnh: -22.0°C đến -18.0°C, độ trễ 5 phút
        t_thit = ColdChainThreshold(
            product_type="Thịt đông lạnh",
            temp_min=-22.0,
            temp_max=-18.0,
            delay_minutes=5,
            description="Thịt bò đông sâu",
            created_at="2026-10-07T00:00:00Z",
            updated_at="2026-10-07T00:00:00Z",
        )
        # 3. Trái cây ôn đới: 4.0°C đến 10.0°C, độ trễ 20 phút
        t_traicay = ColdChainThreshold(
            product_type="Trái cây ôn đới",
            temp_min=4.0,
            temp_max=10.0,
            delay_minutes=20,
            description="Táo, dâu tây",
            created_at="2026-10-07T00:00:00Z",
            updated_at="2026-10-07T00:00:00Z",
        )
        db.add_all([t_rau, t_thit, t_traicay])
        db.commit()

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
        "admin_auth": ("admin_sys", "123456"),
        "farmer_auth": ("farmer_test", "123456"),
        "session_factory": TestingSessionLocal,
    }

    app.dependency_overrides.clear()


def test_product_specific_thresholds_configuration(cold_chain_env):
    """Test 1: Mỗi sản phẩm có ngưỡng trên, ngưỡng dưới và độ trễ riêng; phân quyền cấu hình chỉ cho Admin."""
    client: TestClient = cold_chain_env["client"]
    admin_auth = cold_chain_env["admin_auth"]
    farmer_auth = cold_chain_env["farmer_auth"]

    # 1. Xem danh sách cấu hình
    res = client.get("/cold-chain/thresholds")
    assert res.status_code == 200
    thresholds = res.json()
    assert len(thresholds) >= 3

    rau = next(t for t in thresholds if t["product_type"] == "Rau lá")
    thit = next(t for t in thresholds if t["product_type"] == "Thịt đông lạnh")

    # Xác nhận Rau lá và Thịt đông lạnh không dùng chung một con số
    assert (rau["temp_min"], rau["temp_max"], rau["delay_minutes"]) == (2.0, 8.0, 15)
    assert (thit["temp_min"], thit["temp_max"], thit["delay_minutes"]) == (-22.0, -18.0, 5)

    # 2. Farmer cố tình thêm ngưỡng -> 403 Forbidden
    res_forbidden = client.post(
        "/cold-chain/thresholds",
        auth=farmer_auth,
        json={
            "product_type": "Thuỷ sản tươi sống",
            "temp_min": 0.0,
            "temp_max": 4.0,
            "delay_minutes": 10,
        },
    )
    assert res_forbidden.status_code == 403

    # 3. Admin tạo thành công
    res_create = client.post(
        "/cold-chain/thresholds",
        auth=admin_auth,
        json={
            "product_type": "Thuỷ sản tươi sống",
            "temp_min": 0.0,
            "temp_max": 4.0,
            "delay_minutes": 10,
            "description": "Cá hồi, tôm tươi ướp đá",
        },
    )
    assert res_create.status_code == 201
    created_data = res_create.json()
    assert created_data["product_type"] == "Thuỷ sản tươi sống"
    assert created_data["temp_min"] == 0.0
    assert created_data["temp_max"] == 4.0
    assert created_data["delay_minutes"] == 10

    # 4. Kiểm tra validate: temp_max <= temp_min bị từ chối
    res_invalid = client.post(
        "/cold-chain/thresholds",
        auth=admin_auth,
        json={
            "product_type": "Hàng lỗi nhiệt độ",
            "temp_min": 15.0,
            "temp_max": 10.0,
            "delay_minutes": 10,
        },
    )
    assert res_invalid.status_code == 422


def test_multi_product_shipment_applies_strictest_threshold(cold_chain_env):
    """Test 2: Chuyến chở nhiều sản phẩm áp ngưỡng chặt nhất (max(min), min(max), min(delay))."""
    client: TestClient = cold_chain_env["client"]

    # Kịch bản A: Chở chung Rau lá [2.0°C, 8.0°C, delay=15m] và Trái cây ôn đới [4.0°C, 10.0°C, delay=20m]
    # - Ngưỡng dưới chặt nhất = max(2.0, 4.0) = 4.0°C
    # - Ngưỡng trên chặt nhất = min(8.0, 10.0) = 8.0°C
    # - Độ trễ chặt nhất = min(15, 20) = 15 phút
    res_a = client.post(
        "/cold-chain/effective-threshold",
        json={"product_types": ["Rau lá", "Trái cây ôn đới"]},
    )
    assert res_a.status_code == 200
    data_a = res_a.json()
    assert data_a["effective_temp_min"] == 4.0
    assert data_a["effective_temp_max"] == 8.0
    assert data_a["effective_delay_minutes"] == 15
    assert data_a["is_compatible"] is True
    assert data_a["compatibility_warning"] is None

    # Kịch bản B: Chở chung Rau lá [2.0°C, 8.0°C, delay=15m] và Thịt đông lạnh [-22.0°C, -18.0°C, delay=5m]
    # - Ngưỡng dưới = max(2.0, -22.0) = 2.0°C
    # - Ngưỡng trên = min(8.0, -18.0) = -18.0°C
    # Vì 2.0°C > -18.0°C (dải nhiệt rỗng) -> hệ thống cảnh báo xung đột bảo quản
    res_b = client.post(
        "/cold-chain/effective-threshold",
        json={"product_types": ["Rau lá", "Thịt đông lạnh"]},
    )
    assert res_b.status_code == 200
    data_b = res_b.json()
    assert data_b["effective_temp_min"] == 2.0
    assert data_b["effective_temp_max"] == -18.0
    assert data_b["effective_delay_minutes"] == 5
    assert data_b["is_compatible"] is False
    assert "CẢNH BÁO XUNG ĐỘT" in data_b["compatibility_warning"]


def test_updating_threshold_config_does_not_recalculate_recorded_violations(cold_chain_env):
    """Test 3: Đổi cấu hình không tính lại vi phạm đã ghi (Snapshot vi phạm bất biến)."""
    client: TestClient = cold_chain_env["client"]
    admin_auth = cold_chain_env["admin_auth"]

    # 1. Ghi nhận một telemetry vi phạm của chuyến hàng chở Rau lá (ngưỡng hiện hành: 2.0°C - 8.0°C, delay=15m)
    # Nhiệt độ đo được: 9.5°C kéo dài 25 phút (vượt ngưỡng 8.0°C và vượt delay 15 phút)
    res_telemetry = client.post(
        "/cold-chain/check-telemetry",
        auth=admin_auth,
        json={
            "shipment_code": "SHIP-VN-001",
            "product_types": ["Rau lá"],
            "recorded_temperature": 9.5,
            "duration_minutes": 25.0,
            "location": "Trạm thu phí Long Phước",
        },
    )
    assert res_telemetry.status_code == 200
    check_result = res_telemetry.json()
    assert check_result["is_violated"] is True
    assert check_result["applied_temp_min"] == 2.0
    assert check_result["applied_temp_max"] == 8.0
    assert check_result["applied_delay_minutes"] == 15
    violation_id = check_result["violation_id"]
    assert violation_id is not None

    # Kiểm tra trong danh sách vi phạm
    res_violations_1 = client.get("/cold-chain/violations?shipment_code=SHIP-VN-001")
    assert res_violations_1.status_code == 200
    viols_1 = res_violations_1.json()
    assert len(viols_1) == 1
    v_record = viols_1[0]
    assert v_record["applied_temp_max"] == 8.0
    assert v_record["applied_delay_minutes"] == 15
    assert "9.5°C" in v_record["violation_reason"]

    # 2. Quản trị hệ thống thay đổi cấu hình ngưỡng của "Rau lá":
    # Nới lỏng ngưỡng trên lên 12.0°C và tăng độ trễ lên 30 phút
    # Lấy ID của threshold "Rau lá"
    res_list = client.get("/cold-chain/thresholds")
    rau_item = next(t for t in res_list.json() if t["product_type"] == "Rau lá")

    res_update = client.put(
        f"/cold-chain/thresholds/{rau_item['id']}",
        auth=admin_auth,
        json={
            "temp_max": 12.0,
            "delay_minutes": 30,
        },
    )
    assert res_update.status_code == 200
    updated_rau = res_update.json()
    assert updated_rau["temp_max"] == 12.0
    assert updated_rau["delay_minutes"] == 30

    # 3. Khẳng định: Bản ghi vi phạm đã ghi nhận trước đó KHÔNG bị tính lại hoặc xoá mất
    # Vẫn giữ nguyên applied_temp_max = 8.0, applied_delay_minutes = 15
    res_violations_2 = client.get("/cold-chain/violations?shipment_code=SHIP-VN-001")
    assert res_violations_2.status_code == 200
    viols_2 = res_violations_2.json()
    assert len(viols_2) == 1
    preserved_violation = viols_2[0]
    assert preserved_violation["id"] == violation_id
    assert preserved_violation["applied_temp_max"] == 8.0
    assert preserved_violation["applied_delay_minutes"] == 15
    assert preserved_violation["recorded_temperature"] == 9.5
    assert preserved_violation["duration_minutes"] == 25.0

    # 4. Khi có chuyến xe mới chạy với cấu hình mới (9.5°C kéo dài 25 phút),
    # với ngưỡng mới là 12.0°C và độ trễ 30m thì chuyến này sẽ KHÔNG bị vi phạm:
    res_new_check = client.post(
        "/cold-chain/check-telemetry",
        auth=admin_auth,
        json={
            "shipment_code": "SHIP-VN-002",
            "product_types": ["Rau lá"],
            "recorded_temperature": 9.5,
            "duration_minutes": 25.0,
            "location": "Kho phân phối Thủ Đức",
        },
    )
    assert res_new_check.status_code == 200
    new_result = res_new_check.json()
    assert new_result["is_violated"] is False
    assert new_result["applied_temp_max"] == 12.0
    assert new_result["applied_delay_minutes"] == 30
