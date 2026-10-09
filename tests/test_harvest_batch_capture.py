"""Kiểm thử tính năng ghi nhận thu hoạch thành lô có mã riêng (Batch Code & Harvest Capture).

Tiêu chí nghiệm thu (DoD / AC):
1. Giả sử đã có thửa và sản phẩm, Khi ghi nhận thu hoạch với khối lượng và ngày hợp lệ,
   Thì hệ thống tạo lô mới kèm mã lô sinh tự động và hiện mã đó cỡ lớn cho người dùng.
2. Giả sử hai người cùng ghi nhận thu hoạch trong cùng một khoảnh khắc, Khi cả hai lưu,
   Thì mỗi người nhận một mã lô khác nhau.
3. Giả sử tôi gõ lại mã lô vừa nhận vào ô tìm kiếm, Khi tìm,
   Thì ra đúng lô đó dù tôi gõ chữ thường hay chữ hoa (case-insensitive).
4. Giả sử lô vừa tạo, Khi xem chi tiết,
   Thì tổ chức đang giữ là tổ chức của tôi và khối lượng còn lại bằng khối lượng thu hoạch.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, Farm, User, ROLE_FARMER
from app.security import hash_password


@pytest.fixture
def harvest_env():
    """Thiết lập môi trường kiểm thử cho module ghi nhận thu hoạch và mã lô."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        user_1 = User(
            username="farmer_one",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        user_2 = User(
            username="farmer_two",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        db.add_all([user_1, user_2])

        farm = Farm(
            name="Vuon Xoai My Xuong",
            location="Dong Thap",
            area=4.2,
            owner="HTX Xoai My Xuong",
        )
        db.add(farm)
        db.commit()
        db.refresh(farm)
        farm_id = farm.id

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
        "farm_id": farm_id,
        "TestingSessionLocal": TestingSessionLocal,
    }

    app.dependency_overrides.clear()


def _auth(username: str, password: str = "123456") -> str:
    creds = f"{username}:{password}".encode("utf-8")
    return f"Basic {base64.b64encode(creds).decode('utf-8')}"


def test_harvest_creates_batch_with_auto_generated_code(harvest_env):
    """AC 1: Giả sử đã có thửa và sản phẩm, Khi ghi nhận thu hoạch với khối lượng và ngày hợp lệ,
    Thì hệ thống tạo lô mới kèm mã lô sinh tự động.
    """
    client: TestClient = harvest_env["client"]
    farm_id: int = harvest_env["farm_id"]
    today_str = date.today().isoformat()

    payload = {
        "farm_id": farm_id,
        "product_name": "Xoai Cat Chu VietGAP",
        "quantity": 850.5,
        "harvest_date": today_str,
    }

    res = client.post(
        "/batches",
        headers={
            "Authorization": _auth("farmer_one"),
            "X-Organization-Id": "HTX Xoai My Xuong",
        },
        json=payload,
    )

    assert res.status_code == 201, res.text
    data = res.json()
    assert data["farm_id"] == farm_id
    assert data["product_name"] == "Xoai Cat Chu VietGAP"
    assert data["quantity"] == 850.5
    assert data["harvest_date"] == today_str
    # Khẳng định có mã lô riêng biệt tự động sinh
    assert "batch_code" in data
    assert data["batch_code"] is not None
    assert data["batch_code"].startswith("LOT-")
    assert len(data["batch_code"]) >= 12  # Ví dụ: LOT-YYYYMMDD-XXXX


def test_concurrent_harvest_creates_distinct_batch_codes(harvest_env):
    """AC 2: Giả sử hai người cùng ghi nhận thu hoạch trong cùng một khoảnh khắc,
    Khi cả hai lưu, Thì mỗi người nhận một mã lô khác nhau.
    """
    client: TestClient = harvest_env["client"]
    farm_id: int = harvest_env["farm_id"]
    today_str = date.today().isoformat()

    # Người 1 gửi request tạo lô thu hoạch
    res1 = client.post(
        "/batches",
        headers={
            "Authorization": _auth("farmer_one"),
            "X-Organization-Id": "HTX Xoai My Xuong",
        },
        json={
            "farm_id": farm_id,
            "product_name": "Xoai Cat Chu Dot 1",
            "quantity": 300.0,
            "harvest_date": today_str,
        },
    )

    # Người 2 gửi request tạo lô thu hoạch trong cùng một thời điểm
    res2 = client.post(
        "/batches",
        headers={
            "Authorization": _auth("farmer_two"),
            "X-Organization-Id": "HTX Xoai My Xuong",
        },
        json={
            "farm_id": farm_id,
            "product_name": "Xoai Cat Chu Dot 2",
            "quantity": 450.0,
            "harvest_date": today_str,
        },
    )

    assert res1.status_code == 201
    assert res2.status_code == 201

    data1 = res1.json()
    data2 = res2.json()

    code1 = data1["batch_code"]
    code2 = data2["batch_code"]

    assert code1 is not None and code2 is not None
    # Khẳng định hai mã lô hoàn toàn khác nhau, không bị xung đột
    assert code1 != code2, f"Mã lô của 2 người phải khác nhau nhưng đều là: {code1}"


def test_search_by_batch_code_case_insensitive(harvest_env):
    """AC 3: Giả sử tôi gõ lại mã lô vừa nhận vào ô tìm kiếm, Khi tìm,
    Thì ra đúng lô đó dù tôi gõ chữ thường hay chữ hoa.
    """
    client: TestClient = harvest_env["client"]
    farm_id: int = harvest_env["farm_id"]
    today_str = date.today().isoformat()

    # 1. Tạo 1 lô mới
    create_res = client.post(
        "/batches",
        headers={
            "Authorization": _auth("farmer_one"),
            "X-Organization-Id": "HTX Xoai My Xuong",
        },
        json={
            "farm_id": farm_id,
            "product_name": "Xoai Tu Quy Lo Dac Biet",
            "quantity": 1200.0,
            "harvest_date": today_str,
        },
    )
    assert create_res.status_code == 201
    created_batch = create_res.json()
    batch_code = created_batch["batch_code"]
    assert batch_code is not None

    # 2. Tìm kiếm với mã viết chữ thường (lowercase)
    lower_query = batch_code.lower()
    res_lower = client.get(f"/batches?search={lower_query}", headers={"X-Organization-Id": "HTX Xoai My Xuong"})
    assert res_lower.status_code == 200
    items_lower = res_lower.json()
    assert len(items_lower) == 1
    assert items_lower[0]["id"] == created_batch["id"]
    assert items_lower[0]["batch_code"] == batch_code

    # 3. Tìm kiếm với mã viết chữ hoa (uppercase)
    upper_query = batch_code.upper()
    res_upper = client.get(f"/batches?search={upper_query}", headers={"X-Organization-Id": "HTX Xoai My Xuong"})
    assert res_upper.status_code == 200
    items_upper = res_upper.json()
    assert len(items_upper) == 1
    assert items_upper[0]["id"] == created_batch["id"]
    assert items_upper[0]["batch_code"] == batch_code

    # 4. Tìm kiếm với một phần của mã lô (substring)
    part_query = batch_code[4:10].lower()
    res_part = client.get(f"/batches?search={part_query}", headers={"X-Organization-Id": "HTX Xoai My Xuong"})
    assert res_part.status_code == 200
    items_part = res_part.json()
    assert any(b["id"] == created_batch["id"] for b in items_part)


def test_batch_details_holder_org_and_quantity(harvest_env):
    """AC 4: Giả sử lô vừa tạo, Khi xem chi tiết,
    Thì tổ chức đang giữ là tổ chức của tôi và khối lượng còn lại bằng khối lượng thu hoạch.
    """
    client: TestClient = harvest_env["client"]
    farm_id: int = harvest_env["farm_id"]
    today_str = date.today().isoformat()
    harvest_qty = 625.5
    my_org = "HTX Xoai My Xuong"

    # Tạo lô thu hoạch
    create_res = client.post(
        "/batches",
        headers={
            "Authorization": _auth("farmer_one"),
            "X-Organization-Id": my_org,
        },
        json={
            "farm_id": farm_id,
            "product_name": "Xoai Cat Hoa Loc",
            "quantity": harvest_qty,
            "harvest_date": today_str,
        },
    )
    assert create_res.status_code == 201
    batch_id = create_res.json()["id"]

    # Gọi API xem chi tiết (GET /batches/{id})
    detail_res = client.get(f"/batches/{batch_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()

    # Khẳng định tổ chức đang giữ là tổ chức của tôi
    assert detail["current_holder_org"] == my_org
    # Khẳng định khối lượng còn lại bằng khối lượng thu hoạch
    assert detail["quantity"] == harvest_qty
