"""Unit test & Integration test cho SCRUM-126: Tổng hợp nhiệt độ chuỗi lạnh & bảo lưu số đo vi phạm."""

from datetime import date, datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import (
    Batch,
    Farm,
    HourlyTemperatureSummary,
    TemperatureReading,
    TemperatureThresholdConfig,
    User,
    ROLE_ADMIN,
    ROLE_FARMER,
)
from app.security import hash_password
from app.services.cold_chain_service import (
    aggregate_old_temperatures_job,
    record_temperature,
)


@pytest.fixture
def cold_chain_test_env(tmp_path):
    """Môi trường kiểm thử cô lập cho module chuỗi lạnh."""
    db_file = tmp_path / "cold_chain_test.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    db = TestingSession()
    admin = User(username="admin_cc", password=hash_password("123456"), role=ROLE_ADMIN, organization_id="ORG_ADMIN")
    farmer = User(username="farmer_cc", password=hash_password("123456"), role=ROLE_FARMER, organization_id="ORG_FARMER")
    farm1 = Farm(name="Vùng Trồng Xoài", location="Đồng Tháp", area=3.0, owner="HTX")
    db.add_all([admin, farmer, farm1])
    db.commit()

    # Thêm cấu hình ngưỡng cho "Xoài Cát Chu": 10.0°C - 13.0°C
    thresh_xoai = TemperatureThresholdConfig(product_name="Xoài Cát Chu", min_temp=10.0, max_temp=13.0)
    thresh_def = TemperatureThresholdConfig(product_name="DEFAULT", min_temp=2.0, max_temp=8.0)
    db.add_all([thresh_xoai, thresh_def])
    db.commit()

    batch1 = Batch(farm_id=farm1.id, product_name="Xoài Cát Chu", quantity=500.0, remaining_quantity=500.0, harvest_date=date(2026, 8, 1))
    db.add(batch1)
    db.commit()

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "db": db,
        "batch1": batch1,
        "client": client,
        "admin_auth": ("admin_cc", "123456"),
        "farmer_auth": ("farmer_cc", "123456"),
    }

    app.dependency_overrides.clear()
    db.close()


def test_temperature_recording_detects_violations_by_specific_threshold(cold_chain_test_env):
    """Kiểm tra nhận diện vi phạm theo ngưỡng từng loại nông sản (Xoài Cát Chu 10-13°C)."""
    db = cold_chain_test_env["db"]
    batch1 = cold_chain_test_env["batch1"]

    # 1. Đo an toàn (11.5°C nằm trong khoảng 10-13°C)
    r_safe = record_temperature(db, sensor_id="SEN_01", temperature=11.5, batch_id=batch1.id)
    assert r_safe.is_violation is False
    assert r_safe.violation_reason is None

    # 2. Đo vi phạm dưới ngưỡng (8.0°C < 10.0°C)
    r_low = record_temperature(db, sensor_id="SEN_01", temperature=8.0, batch_id=batch1.id)
    assert r_low.is_violation is True
    assert "Dưới ngưỡng tối thiểu 10.0°C" in r_low.violation_reason

    # 3. Đo vi phạm trên ngưỡng (15.0°C > 13.0°C)
    r_high = record_temperature(db, sensor_id="SEN_01", temperature=15.0, batch_id=batch1.id)
    assert r_high.is_violation is True
    assert "Vượt ngưỡng tối đa 13.0°C" in r_high.violation_reason


def test_hourly_aggregation_min_max_avg_and_retention_of_violations(cold_chain_test_env):
    """Kiểm tra tổng hợp MIN, MAX, AVG và BẮT BUỘC GIỮ NGUYÊN các số đo vi phạm."""
    db = cold_chain_test_env["db"]
    batch1 = cold_chain_test_env["batch1"]

    # Tạo 4 số đo trong cùng một khung giờ cũ hơn 35 ngày: 2026-08-10 14:00
    base_time = datetime(2026, 8, 10, 14, 0, 0)
    # An toàn: 11.0°C, 12.0°C
    r1 = record_temperature(db, sensor_id="SEN_KHO_01", temperature=11.0, batch_id=batch1.id, recorded_at=base_time + timedelta(minutes=10))
    r2 = record_temperature(db, sensor_id="SEN_KHO_01", temperature=12.0, batch_id=batch1.id, recorded_at=base_time + timedelta(minutes=25))
    # Vi phạm: 6.0°C (lạnh quá), 18.0°C (nóng quá)
    r3 = record_temperature(db, sensor_id="SEN_KHO_01", temperature=6.0, batch_id=batch1.id, recorded_at=base_time + timedelta(minutes=40))
    r4 = record_temperature(db, sensor_id="SEN_KHO_01", temperature=18.0, batch_id=batch1.id, recorded_at=base_time + timedelta(minutes=55))

    r1_id, r2_id, r3_id, r4_id = r1.id, r2.id, r3.id, r4.id

    # Chạy job tổng hợp với cutoff_time là hiện tại (để gom hết các bản ghi cũ)
    cutoff = datetime.now()
    res = aggregate_old_temperatures_job(db, cutoff_time=cutoff)

    assert res["aggregated_buckets"] == 1
    assert res["purged_raw_readings"] == 2  # Chỉ xoá 2 bản ghi an toàn (r1, r2)
    assert res["retained_violation_readings"] == 2  # Giữ lại 2 bản ghi vi phạm (r3, r4)

    # Kiểm tra bảng tổng hợp HourlyTemperatureSummary
    summary = db.query(HourlyTemperatureSummary).first()
    assert summary is not None
    assert summary.sensor_id == "SEN_KHO_01"
    assert summary.min_temp == 6.0
    assert summary.max_temp == 18.0
    # AVG của [11, 12, 6, 18] = 47 / 4 = 11.75
    assert summary.avg_temp == 11.75
    assert summary.sample_count == 4
    assert summary.violation_count == 2

    # Kiểm tra số đo thô: r1, r2 đã bị xoá; r3, r4 VẪN CÒN NGUYÊN VẸN TRONG CSDL!
    assert db.get(TemperatureReading, r1_id) is None
    assert db.get(TemperatureReading, r2_id) is None
    assert db.get(TemperatureReading, r3_id) is not None
    assert db.get(TemperatureReading, r4_id) is not None
    assert db.get(TemperatureReading, r3_id).is_violation is True



def test_idempotent_aggregation_job(cold_chain_test_env):
    """Kiểm tra chạy lại job không tạo dữ liệu trùng trong bảng tổng hợp."""
    db = cold_chain_test_env["db"]
    batch1 = cold_chain_test_env["batch1"]

    t = datetime(2026, 8, 12, 10, 15, 0)
    record_temperature(db, sensor_id="SEN_IDEM", temperature=11.5, batch_id=batch1.id, recorded_at=t)

    cutoff = datetime.now()
    # Chạy lần 1
    res1 = aggregate_old_temperatures_job(db, cutoff_time=cutoff)
    assert res1["aggregated_buckets"] == 1

    # Chạy lần 2 (không có số đo mới)
    res2 = aggregate_old_temperatures_job(db, cutoff_time=cutoff)
    assert res2["aggregated_buckets"] == 0

    # Tổng số bản ghi summary vẫn là 1
    count_summaries = db.query(HourlyTemperatureSummary).filter(HourlyTemperatureSummary.sensor_id == "SEN_IDEM").count()
    assert count_summaries == 1


def test_cold_chain_api_endpoints(cold_chain_test_env):
    """Kiểm tra các API /cold-chain/readings và /cold-chain/summaries."""
    client = cold_chain_test_env["client"]
    farmer_auth = cold_chain_test_env["farmer_auth"]
    admin_auth = cold_chain_test_env["admin_auth"]

    # Gửi số đo mới
    resp_post = client.post(
        "/cold-chain/readings",
        json={"sensor_id": "S_API_01", "temperature": 11.2},
        auth=farmer_auth,
    )
    assert resp_post.status_code == 201

    # Xem thresholds
    resp_t = client.get("/cold-chain/thresholds", auth=farmer_auth)
    assert resp_t.status_code == 200
    assert len(resp_t.json()) >= 2

    # Chạy aggregate qua API (admin)
    resp_agg = client.post("/cold-chain/aggregate?days_threshold=1", auth=admin_auth)
    assert resp_agg.status_code == 200
