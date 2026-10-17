"""Kiểm thử tính năng danh mục sản phẩm dùng chung (SCRUM-93).

Tiêu chí nghiệm thu BDD (DoD / AC):
1. Giả sử tôi là quản trị (admin), khi thêm sản phẩm với tên và đơn vị tính,
   thì sản phẩm xuất hiện trong danh sách chọn của mọi tổ chức.
2. Giả sử tôi thêm sản phẩm trùng tên (kể cả khác chữ hoa/thường),
   khi lưu thì bị chặn (HTTP 409) kèm thông báo rõ ràng bằng tiếng Việt.
3. Giả sử tôi là vùng trồng (farmer / người dùng thường), khi mở danh mục
   thì chỉ xem được (GET 200), không sửa/tạo được (POST/PUT/DELETE trả HTTP 403).
"""

from __future__ import annotations

import base64
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Product, User, ROLE_ADMIN, ROLE_FARMER
from app.security import hash_password


def _basic_auth(username: str, password: str = "123456") -> str:
    creds = f"{username}:{password}".encode("utf-8")
    return f"Basic {base64.b64encode(creds).decode('utf-8')}"


@pytest.fixture
def products_env():
    """Thiết lập database SQLite in-memory và TestClient với admin và farmer."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        admin_user = User(
            username="admin_user",
            password=hash_password("123456"),
            role=ROLE_ADMIN,
        )
        farmer_org_a = User(
            username="farmer_org_a",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        farmer_org_b = User(
            username="farmer_org_b",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        db.add_all([admin_user, farmer_org_a, farmer_org_b])

        # Tạo sẵn 1 sản phẩm mẫu
        sample_prod = Product(
            name="Xoài Cát Chu Cao Lãnh",
            unit="kg",
            description="Xoài đạt chuẩn VietGAP",
        )
        db.add(sample_prod)
        db.commit()

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
    }

    app.dependency_overrides.clear()


def test_admin_create_product_appears_for_all_organizations(products_env):
    """BDD 1: Quản trị thêm sản phẩm với tên và đơn vị tính, sản phẩm xuất hiện trong danh sách của mọi tổ chức."""
    client: TestClient = products_env["client"]

    # 1. Admin tạo sản phẩm mới
    new_product_payload = {
        "name": "Sầu Riêng Ri6 Chợ Lách",
        "unit": "kg",
        "description": "Sầu riêng thơm ngon cơm vàng",
    }
    create_res = client.post(
        "/products",
        json=new_product_payload,
        headers={"Authorization": _basic_auth("admin_user")},
    )
    assert create_res.status_code == 201
    created_data = create_res.json()
    assert created_data["name"] == "Sầu Riêng Ri6 Chợ Lách"
    assert created_data["unit"] == "kg"
    assert created_data.get("is_shared") is True

    # 2. Tổ chức A (farmer_org_a) đọc danh sách sản phẩm dùng chung
    res_org_a = client.get(
        "/products",
        headers={
            "Authorization": _basic_auth("farmer_org_a"),
            "X-Organization-Id": "HTX Nong San A",
        },
    )
    assert res_org_a.status_code == 200
    names_org_a = [p["name"] for p in res_org_a.json()]
    assert "Sầu Riêng Ri6 Chợ Lách" in names_org_a

    # 3. Tổ chức B (farmer_org_b) đọc danh sách sản phẩm dùng chung
    res_org_b = client.get(
        "/products",
        headers={
            "Authorization": _basic_auth("farmer_org_b"),
            "X-Organization-Id": "HTX Nong San B",
        },
    )
    assert res_org_b.status_code == 200
    names_org_b = [p["name"] for p in res_org_b.json()]
    assert "Sầu Riêng Ri6 Chợ Lách" in names_org_b


def test_reject_duplicate_product_name(products_env):
    """BDD 2: Thêm sản phẩm trùng tên thì bị chặn kèm thông báo rõ ràng tiếng Việt."""
    client: TestClient = products_env["client"]

    # Thử thêm sản phẩm trùng tên chính xác với "Xoài Cát Chu Cao Lãnh"
    duplicate_payload = {
        "name": "Xoài Cát Chu Cao Lãnh",
        "unit": "kg",
        "description": "Trùng tên",
    }
    res_exact = client.post(
        "/products",
        json=duplicate_payload,
        headers={"Authorization": _basic_auth("admin_user")},
    )
    assert res_exact.status_code == 409
    data_exact = res_exact.json()
    assert "detail" in data_exact
    assert "đã tồn tại" in data_exact["detail"].lower()

    # Thử thêm sản phẩm trùng tên khác chữ hoa/thường ("xoài cát chu cao lãnh")
    case_payload = {
        "name": "  xoài cát chu cao lãnh  ",
        "unit": "box",
        "description": "Trùng tên khác hoa thường",
    }
    res_case = client.post(
        "/products",
        json=case_payload,
        headers={"Authorization": _basic_auth("admin_user")},
    )
    assert res_case.status_code == 409
    data_case = res_case.json()
    assert "detail" in data_case
    assert "đã tồn tại" in data_case["detail"].lower()


def test_product_name_case_insensitive_unique_constraint(products_env):
    """Constraint DB chặn tên trùng khác hoa/thường kể cả ngoài API."""
    TestingSessionLocal = products_env["TestingSessionLocal"]
    with TestingSessionLocal() as db:
        db.add(Product(name="xoài cát chu cao lãnh", unit="kg"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_farmer_read_only_and_forbidden_write(products_env):
    """BDD 3: Vùng trồng (farmer) chỉ xem được danh mục (GET 200), không được sửa/thêm/xoá (403 Forbidden)."""
    client: TestClient = products_env["client"]

    # 1. Farmer xem danh mục -> Được phép (HTTP 200)
    get_res = client.get(
        "/products",
        headers={"Authorization": _basic_auth("farmer_org_a")},
    )
    assert get_res.status_code == 200
    assert len(get_res.json()) >= 1

    # 2. Farmer cố thêm sản phẩm -> Bị chặn (HTTP 403)
    post_res = client.post(
        "/products",
        json={"name": "Thanh Long Ruột Đỏ", "unit": "kg"},
        headers={"Authorization": _basic_auth("farmer_org_a")},
    )
    assert post_res.status_code == 403
    assert "admin" in post_res.json().get("detail", "").lower()

    # 3. Farmer cố sửa sản phẩm -> Bị chặn (HTTP 403)
    put_res = client.put(
        "/products/1",
        json={"name": "Xoài Cát Chu Đổi Tên", "unit": "kg"},
        headers={"Authorization": _basic_auth("farmer_org_a")},
    )
    assert put_res.status_code == 403

    # 4. Farmer cố xoá sản phẩm -> Bị chặn (HTTP 403)
    del_res = client.delete(
        "/products/1",
        headers={"Authorization": _basic_auth("farmer_org_a")},
    )
    assert del_res.status_code == 403
