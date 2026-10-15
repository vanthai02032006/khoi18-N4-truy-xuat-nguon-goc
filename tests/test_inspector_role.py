import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Farm,
    ROLE_ADMIN,
    ROLE_FARMER,
    ROLE_INSPECTOR,
    User,
)
from app.security import hash_password


@pytest.fixture
def inspector_test_env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = TestingSessionLocal()
    # Tạo user admin, farmer, inspector
    admin = User(username="admin", password=hash_password("123456"), role=ROLE_ADMIN)
    farmer = User(username="farmer", password=hash_password("123456"), role=ROLE_FARMER)
    inspector = User(username="inspector", password=hash_password("123456"), role=ROLE_INSPECTOR)
    farm = Farm(name="Vườn Thanh Long Chợ Gạo", location="Tiền Giang", area=5.0, owner="HTX Chợ Gạo")
    db.add_all([admin, farmer, inspector, farm])
    db.commit()
    db.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_inspector_can_view_farms(inspector_test_env):
    """Inspector có quyền xem danh sách thửa đất / vùng trồng để phục vụ thanh tra."""
    res = inspector_test_env.get("/farms", auth=("inspector", "123456"))
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    assert data[0]["name"] == "Vườn Thanh Long Chợ Gạo"


def test_inspector_cannot_create_farm(inspector_test_env):
    """Inspector không có quyền tạo vùng trồng (chỉ farmer hoặc admin)."""
    payload = {
        "name": "Trang trại mới",
        "location": "Long An",
        "area": 2.5,
        "owner": "Nông dân A",
    }
    res = inspector_test_env.post("/farms", json=payload, auth=("inspector", "123456"))
    assert res.status_code == 403


def test_inspector_can_create_recall_order(inspector_test_env):
    """Inspector có quyền ban hành lệnh kiểm tra / thu hồi nông sản."""
    payload = {
        "title": "Kiểm định dư lượng thuốc BVTV",
        "reason": "Thanh tra đột xuất",
        "target_organizations": ["HTX Trồng Trọt", "Đơn Vị Phân Phối"],
    }
    res = inspector_test_env.post("/orders", json=payload, auth=("inspector", "123456"))
    assert res.status_code == 201
    order = res.json()
    assert order["order_code"].startswith("ORD-REC-")
    assert order["total_targets"] == 2


def test_farmer_cannot_create_recall_order(inspector_test_env):
    """Farmer không có quyền ban hành lệnh kiểm tra / thu hồi."""
    payload = {
        "title": "Lệnh thu hồi bất hợp pháp",
        "reason": "Thử nghiệm",
        "target_organizations": ["HTX Trồng Trọt"],
    }
    res = inspector_test_env.post("/orders", json=payload, auth=("farmer", "123456"))
    assert res.status_code == 403
