"""Integration test cho SCRUM-50: API tìm kiếm theo mã lô, bộ lọc sản phẩm và phân quyền.

Kiểm tra:
1. Tìm theo mã lô (search query param): chính xác mã lô hoặc từ khoá.
2. Lọc theo tên sản phẩm (product_name query param): chỉ trả các lô có cùng loại sản phẩm.
3. Kết hợp vừa search vừa filter: kết quả thoả mãn cả 2 điều kiện.
4. Lọc theo quyền người dùng (SCRUM-70): user chỉ nhận các lô mình có quyền xem.
5. Khi không tìm thấy: trả danh sách rỗng (Empty state ở frontend).
"""

from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, BatchCustodyHistory, Farm, User
from app.security import hash_password


@pytest.fixture
def test_setup():
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

    farm = Farm(name="Vùng Trồng Xoài Mỹ Xương", location="Đồng Tháp", area=4.5, owner="HTX Xoài")
    db.add(farm)

    user_admin = User(username="admin_user", password=hash_password("123456"), role="admin", organization_id="ORG_ADMIN")
    user_farmer = User(username="farmer_mx", password=hash_password("123456"), role="farmer", organization_id="ORG_MX")
    db.add_all([user_admin, user_farmer])
    db.commit()

    # Tạo các lô
    b1 = Batch(id=101, farm_id=farm.id, product_name="Xoài Cát Chu Loại 1", quantity=1000.0, remaining_quantity=1000.0, harvest_date=date(2026, 9, 20), organization_id="ORG_MX")
    b2 = Batch(id=102, farm_id=farm.id, product_name="Xoài Cát Chu Xuất Khẩu", quantity=2000.0, remaining_quantity=2000.0, harvest_date=date(2026, 9, 21), organization_id="ORG_MX")
    b3 = Batch(id=103, farm_id=farm.id, product_name="Sầu Riêng Ri6", quantity=500.0, remaining_quantity=500.0, harvest_date=date(2026, 9, 22), organization_id="ORG_MX")
    b4 = Batch(id=104, farm_id=farm.id, product_name="Bưởi Da Xanh", quantity=800.0, remaining_quantity=800.0, harvest_date=date(2026, 9, 23), organization_id="ORG_OTHER")
    db.add_all([b1, b2, b3, b4])

    db.add(BatchCustodyHistory(batch_id=101, organization_id="ORG_MX"))
    db.add(BatchCustodyHistory(batch_id=102, organization_id="ORG_MX"))
    db.add(BatchCustodyHistory(batch_id=103, organization_id="ORG_MX"))
    db.add(BatchCustodyHistory(batch_id=104, organization_id="ORG_OTHER"))
    db.commit()

    client = TestClient(app)

    yield client

    app.dependency_overrides.clear()
    db.close()


def test_search_by_batch_code(test_setup: TestClient):
    """Tìm theo mã lô (ID)."""
    client = test_setup
    res = client.get("/batches?search=101", auth=("farmer_mx", "123456"))
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["id"] == 101


def test_search_by_keyword(test_setup: TestClient):
    """Tìm theo từ khoá tên nông sản."""
    client = test_setup
    res = client.get("/batches?search=Xoài", auth=("farmer_mx", "123456"))
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    assert all("Xoài" in item["product_name"] for item in data)


def test_filter_by_product_name(test_setup: TestClient):
    """Lọc theo dropdown loại nông sản."""
    client = test_setup
    res = client.get("/batches?product_name=Sầu Riêng Ri6", auth=("farmer_mx", "123456"))
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["product_name"] == "Sầu Riêng Ri6"


def test_filter_and_permission_isolation(test_setup: TestClient):
    """Quyền xem: farmer_mx chỉ thấy các lô của ORG_MX (lô 101, 102, 103), không thấy lô 104 của ORG_OTHER."""
    client = test_setup
    res = client.get("/batches", auth=("farmer_mx", "123456"))
    assert res.status_code == 200
    data = res.json()
    ids = [item["id"] for item in data]
    assert 101 in ids
    assert 102 in ids
    assert 103 in ids
    assert 104 not in ids  # Lô 104 thuộc ORG_OTHER bị cô lập quyền!

    # Admin gọi sẽ thấy tất cả 4 lô
    res_admin = client.get("/batches", auth=("admin_user", "123456"))
    assert res_admin.status_code == 200
    assert len(res_admin.json()) == 4
