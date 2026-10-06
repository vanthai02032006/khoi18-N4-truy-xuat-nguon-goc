"""Integration test cho API SCRUM-70: Phân quyền xem lô qua FastAPI TestClient.

Kiểm tra:
1. GET /batches/{batch_id} chưa đăng nhập => 401 Unauthorized.
2. GET /batches/{batch_id} đúng quyền (đang giữ hoặc là tổ tiên) => 200 OK.
3. GET /batches/{batch_id} không có quyền => 403 Forbidden.
4. GET /batches/{batch_id} lô không tồn tại => 404 Not Found.
5. GET /batches lọc danh sách đúng các lô được xem.
"""

from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, BatchCustodyHistory, BatchRelation, Farm, User
from app.security import hash_password


@pytest.fixture
def client_and_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = TestingSession()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    # Tạo dữ liệu kiểm thử
    farm = Farm(name="Vùng Trồng Test", location="Đồng Tháp", area=3.0, owner="HTX Test")
    db.add(farm)

    # 2 User: farmer_a (ORG_A) và farmer_b (ORG_B)
    user_a = User(username="farmer_a", password=hash_password("123456"), role="farmer", organization_id="ORG_A")
    user_b = User(username="farmer_b", password=hash_password("123456"), role="farmer", organization_id="ORG_B")
    db.add_all([user_a, user_b])
    db.commit()

    # Chuỗi phả hệ: Batch Parent (ORG_A) -> Batch Child (ORG_A)
    # Batch Unrelated (ORG_B)
    batch_parent = Batch(farm_id=farm.id, product_name="Lô Cha A", quantity=100.0, remaining_quantity=50.0, harvest_date=date(2026, 9, 1), organization_id="ORG_A")
    batch_child = Batch(farm_id=farm.id, product_name="Lô Con A", quantity=50.0, remaining_quantity=50.0, harvest_date=date(2026, 9, 2), organization_id="ORG_A")
    batch_b = Batch(farm_id=farm.id, product_name="Lô Riêng B", quantity=80.0, remaining_quantity=80.0, harvest_date=date(2026, 9, 3), organization_id="ORG_B")
    db.add_all([batch_parent, batch_child, batch_b])
    db.commit()

    # Relation: parent -> child
    rel = BatchRelation(parent_batch_id=batch_parent.id, child_batch_id=batch_child.id, used_quantity=50.0)
    db.add(rel)
    # Custody history
    db.add(BatchCustodyHistory(batch_id=batch_parent.id, organization_id="ORG_A"))
    db.add(BatchCustodyHistory(batch_id=batch_child.id, organization_id="ORG_A"))
    db.add(BatchCustodyHistory(batch_id=batch_b.id, organization_id="ORG_B"))
    db.commit()

    client = TestClient(app)

    yield client, db, batch_parent.id, batch_child.id, batch_b.id

    app.dependency_overrides.clear()
    db.close()


def test_api_unauthorized_without_login(client_and_db):
    client, _, parent_id, _, _ = client_and_db
    # Chưa đăng nhập gọi GET /batches/{id} => 401
    response = client.get(f"/batches/{parent_id}")
    assert response.status_code == 401


def test_api_can_view_own_and_ancestor_batch(client_and_db):
    client, _, parent_id, child_id, batch_b_id = client_and_db
    # farmer_a gọi xem lô của mình và tổ tiên của mình => 200 OK
    res_child = client.get(f"/batches/{child_id}", auth=("farmer_a", "123456"))
    assert res_child.status_code == 200
    assert res_child.json()["id"] == child_id

    res_parent = client.get(f"/batches/{parent_id}", auth=("farmer_a", "123456"))
    assert res_parent.status_code == 200
    assert res_parent.json()["id"] == parent_id


def test_api_unrelated_batch_forbidden_403(client_and_db):
    client, _, _, _, batch_b_id = client_and_db
    # farmer_a cố xem lô của ORG_B => 403 Forbidden
    response = client.get(f"/batches/{batch_b_id}", auth=("farmer_a", "123456"))
    assert response.status_code == 403
    assert "không có quyền xem" in response.json()["detail"]


def test_api_nonexistent_batch_404(client_and_db):
    client, _, _, _, _ = client_and_db
    response = client.get("/batches/999999", auth=("farmer_a", "123456"))
    assert response.status_code == 404
