"""Kiểm thử màn hình tổng quan 360 độ lô nông sản (Story 3).

Kịch bản kiểm thử:
1. Xem thông tin đầy đủ một lô: sản phẩm, thửa gốc, khối lượng, tổ chức giữ, trạng thái, allowed_actions.
2. Liên kết phả hệ cha-con trực tiếp: hiển thị danh sách lô mẹ và lô con.
3. Chặn truy cập trái phép: Người dùng khác tổ chức (không phải admin) bị trả về 403 Forbidden.
"""

from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, Farm, User, ROLE_ADMIN, ROLE_FARMER
from app.security import hash_password


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = TestingSessionLocal()

    # Tạo User HTX A và User HTX B
    user_a = User(
        username="farmer_a",
        password=hash_password("123456"),
        role=ROLE_FARMER,
        organization="HTX Nông Nghiệp Mỹ Xương",
    )
    user_b = User(
        username="farmer_b",
        password=hash_password("123456"),
        role=ROLE_FARMER,
        organization="HTX Khác Bắc Giang",
    )
    admin_user = User(
        username="admin",
        password=hash_password("123456"),
        role=ROLE_ADMIN,
        organization="Sở Nông Nghiệp",
    )
    db.add_all([user_a, user_b, admin_user])
    db.commit()

    # Tạo Vùng trồng và Lô mẹ + Lô con
    farm = Farm(
        name="Vùng Trồng Xoài Thửa #1",
        location="Đồng Tháp",
        area=3.5,
        owner="Nguyễn Văn A",
    )
    db.add(farm)
    db.commit()

    parent_batch = Batch(
        farm_id=farm.id,
        product_name="Xoài Cát Chu Mẹ",
        quantity=500.0,
        remaining_quantity=300.0,
        organization="HTX Nông Nghiệp Mỹ Xương",
        status="LƯU_KHO",
        harvest_date=date(2026, 10, 1),
    )
    db.add(parent_batch)
    db.commit()

    child_batch = Batch(
        farm_id=farm.id,
        product_name="Xoài Loại 1 Tách",
        quantity=200.0,
        remaining_quantity=200.0,
        organization="HTX Nông Nghiệp Mỹ Xương",
        status="SƠ_CHẾ",
        parent_batch_id=parent_batch.id,
        harvest_date=date(2026, 10, 1),
    )
    db.add(child_batch)
    db.commit()

    yield {
        "db": db,
        "parent_id": parent_batch.id,
        "child_id": child_batch.id,
    }
    db.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session["db"]
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_batch_overview_full_info_and_role_actions(client, db_session):
    """Giả sử mở một lô bất kỳ, Khi trang tải, Thì thấy đầy đủ thông tin cốt lõi và các nút hợp vai trò."""
    parent_id = db_session["parent_id"]
    res = client.get(
        f"/batches/{parent_id}/overview",
        auth=("farmer_a", "123456"),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["product_name"] == "Xoài Cát Chu Mẹ"
    assert data["farm_name"] == "Vùng Trồng Xoài Thửa #1"
    assert data["initial_quantity"] == 500.0
    assert data["remaining_quantity"] == 300.0
    assert data["organization"] == "HTX Nông Nghiệp Mỹ Xương"
    assert data["status"] == "LƯU_KHO"
    assert "SPLIT" in data["allowed_actions"]
    assert "DELETE" not in data["allowed_actions"]  # Farmer không có quyền DELETE


def test_batch_overview_direct_lineage_links(client, db_session):
    """Giả sử lô có lô mẹ hoặc con trực tiếp, Khi xem, Thì thấy danh sách liên kết."""
    parent_id = db_session["parent_id"]
    child_id = db_session["child_id"]

    # Xem lô mẹ -> thấy con
    res_parent = client.get(f"/batches/{parent_id}/overview", auth=("farmer_a", "123456"))
    assert res_parent.status_code == 200
    data_p = res_parent.json()
    assert len(data_p["child_batches"]) == 1
    assert data_p["child_batches"][0]["id"] == child_id
    assert data_p["child_batches"][0]["product_name"] == "Xoài Loại 1 Tách"

    # Xem lô con -> thấy mẹ
    res_child = client.get(f"/batches/{child_id}/overview", auth=("farmer_a", "123456"))
    assert res_child.status_code == 200
    data_c = res_child.json()
    assert len(data_c["parent_batches"]) == 1
    assert data_c["parent_batches"][0]["id"] == parent_id
    assert data_c["parent_batches"][0]["product_name"] == "Xoài Cát Chu Mẹ"


def test_batch_overview_tenant_isolation_forbidden(client, db_session):
    """Giả sử tôi không có quyền xem lô (khác tổ chức), Khi mở, Thì bị từ chối 403."""
    parent_id = db_session["parent_id"]
    # farmer_b thuộc "HTX Khác Bắc Giang" cố tình mở lô của "HTX Nông Nghiệp Mỹ Xương"
    res = client.get(
        f"/batches/{parent_id}/overview",
        auth=("farmer_b", "123456"),
    )
    assert res.status_code == 403
    assert "không có quyền xem" in res.json()["detail"]


def test_batch_overview_admin_can_access_all_and_has_full_actions(client, db_session):
    """Admin có quyền xem mọi tổ chức và có đủ các quyền quản trị."""
    parent_id = db_session["parent_id"]
    res = client.get(
        f"/batches/{parent_id}/overview",
        auth=("admin", "123456"),
    )
    assert res.status_code == 200
    data = res.json()
    assert "DELETE" in data["allowed_actions"]
    assert "TRANSFER_CUSTODY" in data["allowed_actions"]
