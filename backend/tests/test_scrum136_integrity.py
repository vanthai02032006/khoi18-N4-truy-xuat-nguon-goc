"""Unit test & Integration test cho SCRUM-136: Job S-12 kiểm tra tính toàn vẹn dữ liệu."""

from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Batch, BatchRelation, Farm, IntegrityCheckLog, User, ROLE_ADMIN, ROLE_FARMER
from app.security import hash_password
from app.services.integrity_service import run_s12_integrity_check


@pytest.fixture
def integrity_test_env(tmp_path):
    """Môi trường kiểm thử cô lập cho S-12."""
    db_file = tmp_path / "integrity_test.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    db = TestingSession()
    admin = User(username="admin_s12", password=hash_password("123456"), role=ROLE_ADMIN, organization_id="ORG_ADMIN")
    farmer = User(username="farmer_s12", password=hash_password("123456"), role=ROLE_FARMER, organization_id="ORG_FARMER")
    farm1 = Farm(name="Vùng Trồng Chuẩn", location="Cần Thơ", area=5.0, owner="HTX 1")
    db.add_all([admin, farmer, farm1])
    db.commit()

    b1 = Batch(farm_id=farm1.id, product_name="Xoài Cát Chu", quantity=1000.0, remaining_quantity=1000.0, harvest_date=date(2026, 9, 1))
    db.add(b1)
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
        "farm1": farm1,
        "batch1": b1,
        "client": client,
        "admin_auth": ("admin_s12", "123456"),
        "farmer_auth": ("farmer_s12", "123456"),
    }

    app.dependency_overrides.clear()
    db.close()


def test_integrity_check_clean_database_passes(integrity_test_env):
    """Cơ sở dữ liệu chuẩn hợp lệ phải đạt trạng thái PASSED với 0 lỗi."""
    db = integrity_test_env["db"]
    log_entry, violations = run_s12_integrity_check(db, triggered_by="TEST_CLEAN")

    assert log_entry.status == "PASSED"
    assert log_entry.error_count == 0
    assert len(violations) == 0


def test_integrity_check_detects_quantity_anomalies(integrity_test_env):
    """Phát hiện bất thường số lượng: remaining âm hoặc vượt quá ban đầu."""
    db = integrity_test_env["db"]
    farm1 = integrity_test_env["farm1"]

    # Thêm batch có remaining > quantity
    b_anomaly1 = Batch(farm_id=farm1.id, product_name="Bơ", quantity=500.0, remaining_quantity=600.0, harvest_date=date(2026, 9, 2))
    # Thêm batch có remaining < 0
    b_anomaly2 = Batch(farm_id=farm1.id, product_name="Mít", quantity=500.0, remaining_quantity=-50.0, harvest_date=date(2026, 9, 2))
    db.add_all([b_anomaly1, b_anomaly2])
    db.commit()

    log_entry, violations = run_s12_integrity_check(db, triggered_by="TEST_QUANTITY")
    assert log_entry.status == "FAILED"
    assert log_entry.error_count >= 2
    cats = [v["category"] for v in violations]
    assert "QUANTITY_ANOMALY" in cats


def test_integrity_check_detects_genealogy_cycle(integrity_test_env):
    """Phát hiện chu trình phả hệ vòng tròn (Cycle Detection)."""
    db = integrity_test_env["db"]
    farm1 = integrity_test_env["farm1"]

    b_a = Batch(farm_id=farm1.id, product_name="A", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1))
    b_b = Batch(farm_id=farm1.id, product_name="B", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1))
    b_c = Batch(farm_id=farm1.id, product_name="C", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1))
    db.add_all([b_a, b_b, b_c])
    db.commit()

    # Tạo quan hệ vòng tròn: A -> B -> C -> A
    rel1 = BatchRelation(parent_batch_id=b_a.id, child_batch_id=b_b.id, used_quantity=10.0)
    rel2 = BatchRelation(parent_batch_id=b_b.id, child_batch_id=b_c.id, used_quantity=10.0)
    rel3 = BatchRelation(parent_batch_id=b_c.id, child_batch_id=b_a.id, used_quantity=10.0)
    db.add_all([rel1, rel2, rel3])
    db.commit()

    log_entry, violations = run_s12_integrity_check(db, triggered_by="TEST_CYCLE")
    assert log_entry.status == "FAILED"
    cats = [v["category"] for v in violations]
    assert "CYCLE_DETECTED" in cats


def test_integrity_check_does_not_modify_or_delete_data(integrity_test_env):
    """Yêu cầu bắt buộc: Kiểm tra toàn vẹn KHÔNG ĐƯỢC tự động xoá hoặc sửa dữ liệu bị lỗi."""
    db = integrity_test_env["db"]
    farm1 = integrity_test_env["farm1"]

    b_err = Batch(farm_id=farm1.id, product_name="Sầu riêng lỗi", quantity=100.0, remaining_quantity=-20.0, harvest_date=date(2026, 9, 5))
    db.add(b_err)
    db.commit()
    err_id = b_err.id

    run_s12_integrity_check(db, triggered_by="TEST_NO_MUTATION")

    # Bản ghi lỗi vẫn còn nguyên vẹn trong DB
    re_queried = db.get(Batch, err_id)
    assert re_queried is not None
    assert re_queried.remaining_quantity == -20.0


def test_integrity_api_run_and_latest(integrity_test_env):
    """Kiểm tra API /integrity-check/run và /integrity-check/latest."""
    client = integrity_test_env["client"]
    admin_auth = integrity_test_env["admin_auth"]
    farmer_auth = integrity_test_env["farmer_auth"]

    # Farmer gọi /run -> 403
    resp_f = client.post("/integrity-check/run", auth=farmer_auth)
    assert resp_f.status_code == 403

    # Admin gọi /run -> 200
    resp_a = client.post("/integrity-check/run", auth=admin_auth)
    assert resp_a.status_code == 200
    data = resp_a.json()
    assert "result" in data
    assert data["result"]["status"] == "PASSED"

    # Farmer gọi /latest -> 200 (xem được kết quả)
    resp_l = client.get("/integrity-check/latest", auth=farmer_auth)
    assert resp_l.status_code == 200
    assert resp_l.json()["status"] == "PASSED"
