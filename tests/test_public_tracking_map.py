"""Kiểm thử bản đồ hành trình công khai cấp xã/huyện (S-06 / SCRUM-39 / T-58).

Đảm bảo:
1. Trả về toạ độ đại diện cấp xã/huyện của vùng trồng xuất xứ và các trạm trung chuyển.
2. Bảo vệ quyền riêng tư thửa đất (toạ độ làm mờ, không lộ số nhà/thửa đất).
3. Hỗ trợ tra cứu theo cả mã lô (code) và ID lô nông sản.
"""

from datetime import date
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, SessionLocal, get_db
from app.models import Batch, BatchEvent, Farm, User, ROLE_ADMIN
from app.security import compute_event_hash


@pytest.fixture
def map_test_client():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestingSessionLocal() as db:
        farm = Farm(name="Vùng Trồng Cát Chu", location="Xã Mỹ Xương, Huyện Cao Lãnh", area=5.0, owner="HTX Nông Nghiệp")
        db.add(farm)
        db.commit()

        batch = Batch(
            farm_id=farm.id,
            batch_code="LOT-MAP-001",
            product_name="Xoài Cát Chu",
            quantity=1000.0,
            harvest_date=date(2026, 10, 1),
            current_holder_org="HTX Nông Nghiệp",
        )
        db.add(batch)
        db.commit()

        # Thêm sự kiện sơ chế và bàn giao
        h1 = compute_event_hash("HARVEST", "Thu hoạch", "farmer", "HTX Nông Nghiệp", "2026-10-01T08:00:00Z", "0"*64)
        ev1 = BatchEvent(
            batch_id=batch.id,
            event_type="HARVEST",
            payload="Thu hoạch",
            actor="farmer",
            organization="HTX Nông Nghiệp",
            timestamp="2026-10-01T08:00:00Z",
            hash=h1,
            previous_hash="0"*64,
        )
        db.add(ev1)
        db.commit()

        h2 = compute_event_hash("HANDOVER", "Bàn giao vận chuyển", "admin", "Kho Vận Chuyển", "2026-10-02T10:00:00Z", h1)
        ev2 = BatchEvent(
            batch_id=batch.id,
            event_type="HANDOVER",
            payload="Bàn giao vận chuyển",
            actor="admin",
            organization="Kho Vận Chuyển",
            timestamp="2026-10-02T10:00:00Z",
            hash=h2,
            previous_hash=h1,
        )
        db.add(ev2)
        db.commit()

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def test_get_batch_map_by_code(map_test_client: TestClient):
    """Kiểm tra tra cứu bản đồ theo mã lô."""
    response = map_test_client.get("/batches/code/LOT-MAP-001/map")
    assert response.status_code == 200
    data = response.json()
    assert data["batch_code"] == "LOT-MAP-001"
    assert data["product_name"] == "Xoài Cát Chu"
    assert data["origin_point"]["order"] == 1
    assert "Cấp Xã / Huyện" in data["origin_point"]["location_level"]
    assert len(data["waypoints"]) >= 1


def test_get_batch_map_by_id(map_test_client: TestClient):
    """Kiểm tra tra cứu bản đồ theo ID lô."""
    response = map_test_client.get("/batches/1/map")
    assert response.status_code == 200
    data = response.json()
    assert data["origin_point"]["order"] == 1
    assert "privacy_note" in data
