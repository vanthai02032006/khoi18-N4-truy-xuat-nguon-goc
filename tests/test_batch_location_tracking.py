"""Kiểm thử tính năng ghi nhận **lô đang nằm ở cửa hàng nào** (địa điểm theo tổ chức).

User Story (S-18): *"Là nhà phân phối tôi muốn ghi nhận lô đang nằm ở cửa hàng nào
trong hệ thống của tôi để thu hồi chỉ đúng cửa hàng thay vì lục cả chuỗi."*

Tiêu chí nghiệm thu (DoD / AC):
1. Giả sử tổ chức tôi có nhiều địa điểm,
   Khi khai báo địa điểm với tên và địa chỉ,
   Thì địa điểm thuộc tổ chức tôi.
2. Giả sử tôi chuyển một lô về một địa điểm,
   Khi lưu,
   Thì lô ghi rõ địa điểm hiện tại và sự kiện được ghi vào chuỗi.
3. Giả sử chia một lô cho nhiều cửa hàng,
   Khi lưu,
   Thì đó là phép tách ở S-17 với mỗi lô con gắn một địa điểm.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.events import DEFAULT_ORGANIZATION
from app.main import app
from app.models import Batch, Farm, ROLE_FARMER, User
from app.security import hash_password

#: Tổ chức khác tham gia chuỗi cung ứng - dùng để kiểm tra cách ly dữ liệu.
#: Tên đặt ASCII vì giá trị header ``X-Organization-Id`` chỉ nhận ký tự ASCII.
OTHER_ORGANIZATION = "Cong Ty Sieu Thi C"

FARMER_MINE = ("farmer_distributor", "123456")
FARMER_OTHER = ("farmer_retailer_c", "123456")


@pytest.fixture
def location_env():
    """Môi trường kiểm thử: 1 vùng trồng + 1 lô (1000 kg) của tổ chức mặc định."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as db:
        db.add_all(
            [
                User(
                    username=FARMER_MINE[0],
                    password=hash_password(FARMER_MINE[1]),
                    role=ROLE_FARMER,
                ),
                User(
                    username=FARMER_OTHER[0],
                    password=hash_password(FARMER_OTHER[1]),
                    role=ROLE_FARMER,
                ),
            ]
        )
        farm = Farm(
            name="Vùng trồng xoài Cao Lãnh",
            location="Xã Mỹ Xương, Huyện Cao Lãnh, Đồng Tháp",
            area=4.5,
            owner=DEFAULT_ORGANIZATION,
        )
        db.add(farm)
        db.flush()
        batch = Batch(
            farm_id=farm.id,
            product_name="Xoài Cát Chu",
            quantity=1000.0,
            harvest_date=date(2026, 10, 1),
            current_holder_org=DEFAULT_ORGANIZATION,
        )
        db.add(batch)
        db.commit()
        batch_id = batch.id

    def override_get_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    yield {"client": TestClient(app), "batch_id": batch_id}
    app.dependency_overrides.clear()
    engine.dispose()


def _create_location(client: TestClient, name: str, address: str) -> dict:
    """Khai báo một địa điểm cho tổ chức mặc định và trả về body (đã kiểm tra 201)."""
    response = client.post(
        "/locations",
        json={"name": name, "address": address},
        auth=FARMER_MINE,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# AC 1: Khai báo địa điểm -> địa điểm thuộc tổ chức tôi (và chỉ tôi thấy).
# ---------------------------------------------------------------------------
def test_ac1_declared_location_belongs_to_my_organization(location_env):
    """Giả sử tổ chức tôi có nhiều địa điểm, khi khai báo thì địa điểm thuộc tổ chức tôi."""
    client = location_env["client"]

    store = _create_location(
        client,
        "Cửa hàng Mỹ Xương",
        "Ấp Mỹ An, Xã Mỹ Xương, Huyện Cao Lãnh, Đồng Tháp",
    )
    warehouse = _create_location(
        client,
        "Kho lạnh Cao Lãnh",
        "Số 12, Đường Nguyễn Huệ, Phường 2, TP Cao Lãnh, Đồng Tháp",
    )

    assert store["organization"] == DEFAULT_ORGANIZATION
    assert warehouse["organization"] == DEFAULT_ORGANIZATION
    assert store["name"] == "Cửa hàng Mỹ Xương"
    assert store["address"] == "Ấp Mỹ An, Xã Mỹ Xương, Huyện Cao Lãnh, Đồng Tháp"

    # Danh sách địa điểm của tôi: đủ 2 địa điểm vừa khai báo, đều thuộc tổ chức tôi.
    mine = client.get("/locations", auth=FARMER_MINE)
    assert mine.status_code == 200, mine.text
    assert {item["organization"] for item in mine.json()} == {DEFAULT_ORGANIZATION}
    assert {item["name"] for item in mine.json()} == {"Cửa hàng Mỹ Xương", "Kho lạnh Cao Lãnh"}

    # Tổ chức khác khai báo địa điểm cùng tên -> không xung đột (mỗi tổ chức một không gian tên).
    other = client.post(
        "/locations",
        json={"name": "Cửa hàng Mỹ Xương", "address": "1 Lê Lợi, Quận 1, TP HCM"},
        auth=FARMER_OTHER,
        headers={"X-Organization-Id": OTHER_ORGANIZATION},
    )
    assert other.status_code == 201, other.text
    assert other.json()["organization"] == OTHER_ORGANIZATION

    # Trong CÙNG một tổ chức thì tên địa điểm không được trùng -> 409.
    duplicated = client.post(
        "/locations",
        json={"name": "Cửa hàng Mỹ Xương", "address": "Địa chỉ khác"},
        auth=FARMER_MINE,
    )
    assert duplicated.status_code == 409

    # Cách ly: tổ chức khác gọi danh sách địa điểm chỉ thấy địa điểm của mình.
    theirs = client.get(
        "/locations",
        auth=FARMER_OTHER,
        headers={"X-Organization-Id": OTHER_ORGANIZATION},
    )
    assert [item["name"] for item in theirs.json()] == ["Cửa hàng Mỹ Xương"]
    assert {item["organization"] for item in theirs.json()} == {OTHER_ORGANIZATION}

    # Cách ly: tổ chức khác không tra cứu được lô tại địa điểm của tôi -> 403.
    forbidden = client.get(
        f"/locations/{store['id']}/batches",
        auth=FARMER_OTHER,
        headers={"X-Organization-Id": OTHER_ORGANIZATION},
    )
    assert forbidden.status_code == 403


# ---------------------------------------------------------------------------
# AC 2: Chuyển lô về địa điểm -> lô ghi rõ địa điểm hiện tại + sự kiện vào chuỗi.
# ---------------------------------------------------------------------------
def test_ac2_moving_batch_records_current_location_and_event(location_env):
    """Giả sử tôi chuyển một lô về một địa điểm, khi lưu thì lô ghi rõ địa điểm hiện tại và sự kiện được ghi vào chuỗi."""
    client = location_env["client"]
    batch_id = location_env["batch_id"]

    store = _create_location(client, "Cửa hàng Mỹ Xương", "Ấp Mỹ An, Cao Lãnh, Đồng Tháp")
    warehouse = _create_location(client, "Kho lạnh Cao Lãnh", "Số 12 Nguyễn Huệ, TP Cao Lãnh")

    moved = client.post(
        f"/batches/{batch_id}/location",
        json={"location_id": store["id"]},
        auth=FARMER_MINE,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["current_location_id"] == store["id"]
    assert moved.json()["current_location_name"] == "Cửa hàng Mỹ Xương"
    assert moved.json()["current_location_address"] == "Ấp Mỹ An, Cao Lãnh, Đồng Tháp"

    # Lô ghi rõ địa điểm hiện tại ở cả chi tiết và danh sách.
    detail = client.get(f"/batches/{batch_id}", auth=FARMER_MINE)
    assert detail.json()["current_location_id"] == store["id"]
    listing = client.get("/batches", auth=FARMER_MINE)
    assert any(row["current_location_name"] == "Cửa hàng Mỹ Xương" for row in listing.json())

    # Sự kiện LOCATION_CHANGED được ghi vào chuỗi băm bất biến của lô.
    timeline = client.get(f"/batches/{batch_id}/events", auth=FARMER_MINE)
    assert timeline.status_code == 200, timeline.text
    assert timeline.json()["is_valid"] is True
    events = [ev for ev in timeline.json()["events"] if ev["event_type"] == "LOCATION_CHANGED"]
    assert len(events) == 1
    assert events[0]["previous_hash"] == "0" * 64
    first_payload = json.loads(events[0]["payload"])
    assert first_payload["to_location_id"] == store["id"]
    assert first_payload["to_location_name"] == "Cửa hàng Mỹ Xương"
    assert first_payload["from_location_id"] is None

    # Chuyển tiếp sang địa điểm thứ hai -> sự kiện nối tiếp đúng chuỗi hash.
    again = client.post(
        f"/batches/{batch_id}/location",
        json={"location_id": warehouse["id"]},
        auth=FARMER_MINE,
    )
    assert again.status_code == 200, again.text
    assert again.json()["current_location_name"] == "Kho lạnh Cao Lãnh"

    timeline_after = client.get(f"/batches/{batch_id}/events", auth=FARMER_MINE).json()
    assert timeline_after["is_valid"] is True
    moves = [ev for ev in timeline_after["events"] if ev["event_type"] == "LOCATION_CHANGED"]
    assert [json.loads(ev["payload"])["to_location_name"] for ev in moves] == [
        "Cửa hàng Mỹ Xương",
        "Kho lạnh Cao Lãnh",
    ]
    assert json.loads(moves[1]["payload"])["from_location_id"] == store["id"]
    assert moves[1]["previous_hash"] == moves[0]["hash"]


def test_ac2_moving_batch_is_blocked_for_foreign_location_or_foreign_batch(location_env):
    """Địa điểm/lô của tổ chức khác thì bị chặn, địa điểm không tồn tại trả 404."""
    client = location_env["client"]
    batch_id = location_env["batch_id"]

    missing = client.post(
        f"/batches/{batch_id}/location",
        json={"location_id": 999_999},
        auth=FARMER_MINE,
    )
    assert missing.status_code == 404

    foreign_location = client.post(
        "/locations",
        json={"name": "Siêu thị C - Quận 1", "address": "1 Lê Lợi, Quận 1, TP HCM"},
        auth=FARMER_OTHER,
        headers={"X-Organization-Id": OTHER_ORGANIZATION},
    )
    assert foreign_location.status_code == 201, foreign_location.text

    # Địa điểm của tổ chức khác -> 403 (không gắn lô sang địa điểm không thuộc mình).
    cross_org_location = client.post(
        f"/batches/{batch_id}/location",
        json={"location_id": foreign_location.json()["id"]},
        auth=FARMER_MINE,
    )
    assert cross_org_location.status_code == 403

    # Lô đang do tổ chức khác nắm giữ -> không được di chuyển -> 403.
    cross_org_batch = client.post(
        f"/batches/{batch_id}/location",
        json={"location_id": 1},
        auth=FARMER_OTHER,
        headers={"X-Organization-Id": OTHER_ORGANIZATION},
    )
    assert cross_org_batch.status_code == 403


def test_ac2_consumed_batch_cannot_be_moved(location_env):
    """Lô đã bán hết thì không chuyển địa điểm được (400)."""
    client = location_env["client"]
    batch_id = location_env["batch_id"]
    store = _create_location(client, "Cửa hàng Mỹ Xương", "Ấp Mỹ An, Cao Lãnh, Đồng Tháp")

    consumed = client.post(f"/batches/{batch_id}/consume", auth=FARMER_MINE)
    assert consumed.status_code == 200, consumed.text

    moved = client.post(
        f"/batches/{batch_id}/location",
        json={"location_id": store["id"]},
        auth=FARMER_MINE,
    )
    assert moved.status_code == 400


# ---------------------------------------------------------------------------
# AC 3: Chia một lô cho nhiều cửa hàng -> phép tách ở S-17, mỗi lô con một địa điểm.
# ---------------------------------------------------------------------------
def test_ac3_split_across_stores_attaches_location_to_each_child(location_env):
    """Giả sử chia một lô cho nhiều cửa hàng, khi lưu thì mỗi lô con gắn một địa điểm."""
    client = location_env["client"]
    batch_id = location_env["batch_id"]

    store_a = _create_location(client, "Cửa hàng Mỹ Xương", "Ấp Mỹ An, Cao Lãnh, Đồng Tháp")
    store_b = _create_location(client, "Cửa hàng Sa Đéc", "Số 5 Trần Hưng Đạo, TP Sa Đéc")

    split = client.post(
        f"/batches/{batch_id}/split",
        json={
            "items": [
                {"quantity": 400, "note": "Kiện A", "location_id": store_a["id"]},
                {"quantity": 250, "note": "Kiện B", "location_id": store_b["id"]},
                {"quantity": 350, "note": "Kiện C"},
            ]
        },
        auth=FARMER_MINE,
    )
    assert split.status_code == 201, split.text

    children = split.json()["child_batches"]
    assert [child["current_location_id"] for child in children] == [
        store_a["id"],
        store_b["id"],
        None,
    ]
    assert [child["current_location_name"] for child in children] == [
        "Cửa hàng Mỹ Xương",
        "Cửa hàng Sa Đéc",
        None,
    ]

    # Sự kiện SPLIT của lô mẹ (phép tách ở S-17) cũng ghi địa điểm của từng lô con.
    timeline = client.get(f"/batches/{batch_id}/events", auth=FARMER_MINE).json()
    assert timeline["is_valid"] is True
    split_event = next(ev for ev in timeline["events"] if ev["event_type"] == "SPLIT")
    split_payload = json.loads(split_event["payload"])
    assert [item["location_id"] for item in split_payload["children"]] == [
        store_a["id"],
        store_b["id"],
        None,
    ]
    assert [item["location_name"] for item in split_payload["children"]] == [
        "Cửa hàng Mỹ Xương",
        "Cửa hàng Sa Đéc",
        None,
    ]

    # Thu hồi đúng cửa hàng: tra cứu tại mỗi địa điểm ra đúng lô con đang nằm đó.
    at_store_a = client.get(f"/locations/{store_a['id']}/batches", auth=FARMER_MINE)
    assert at_store_a.status_code == 200, at_store_a.text
    assert at_store_a.json()["location"]["name"] == "Cửa hàng Mỹ Xương"
    assert at_store_a.json()["total_batches"] == 1
    assert at_store_a.json()["total_quantity"] == 400
    assert [row["batch_code"] for row in at_store_a.json()["batches"]] == [
        children[0]["batch_code"]
    ]

    at_store_b = client.get(f"/locations/{store_b['id']}/batches", auth=FARMER_MINE).json()
    assert [row["batch_code"] for row in at_store_b["batches"]] == [children[1]["batch_code"]]
    assert at_store_b["total_quantity"] == 250


def test_ac3_split_child_without_location_keeps_parent_location(location_env):
    """Dòng lô con không gửi `location_id` thì giữ địa điểm hiện tại của lô mẹ."""
    client = location_env["client"]
    batch_id = location_env["batch_id"]

    store = _create_location(client, "Cửa hàng Mỹ Xương", "Ấp Mỹ An, Cao Lãnh, Đồng Tháp")
    moved = client.post(
        f"/batches/{batch_id}/location",
        json={"location_id": store["id"]},
        auth=FARMER_MINE,
    )
    assert moved.status_code == 200, moved.text

    split = client.post(
        f"/batches/{batch_id}/split",
        json={"items": [{"quantity": 120, "note": "Kiện để lại cửa hàng"}]},
        auth=FARMER_MINE,
    )
    assert split.status_code == 201, split.text
    assert split.json()["child_batches"][0]["current_location_id"] == store["id"]


def test_ac3_split_rejects_location_of_another_organization(location_env):
    """Không được tách lô sang địa điểm thuộc tổ chức khác -> 403."""
    client = location_env["client"]
    batch_id = location_env["batch_id"]

    foreign_location = client.post(
        "/locations",
        json={"name": "Siêu thị C - Quận 1", "address": "1 Lê Lợi, Quận 1, TP HCM"},
        auth=FARMER_OTHER,
        headers={"X-Organization-Id": OTHER_ORGANIZATION},
    )
    assert foreign_location.status_code == 201, foreign_location.text

    split = client.post(
        f"/batches/{batch_id}/split",
        json={
            "items": [
                {"quantity": 10, "location_id": foreign_location.json()["id"]},
            ]
        },
        auth=FARMER_MINE,
    )
    assert split.status_code == 403
