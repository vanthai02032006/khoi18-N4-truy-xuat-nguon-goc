"""Kiểm thử **kế hoạch thu hồi theo lô & cửa hàng** (S-20).

User Story: *"Là nhà phân phối tôi muốn biết chính xác lô nào ở cửa hàng nào và
còn bao nhiêu để thu hồi đúng chỗ thay vì lục cả kho."*

Tiêu chí nghiệm thu (DoD / AC):
1. Giả sử lệnh thu hồi có 20 lô,
   Khi xem,
   Thì mỗi lô kèm tổ chức, địa điểm, khối lượng còn lại và khối lượng đã tiêu thụ.
2. Giả sử một phần đã bán cho người tiêu dùng,
   Khi xem tổng,
   Thì thấy rõ bao nhiêu còn thu hồi được và bao nhiêu đã ra thị trường.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    BATCH_STATUS_CONSUMED,
    Batch,
    BatchEvent,
    Farm,
    Location,
    ROLE_FARMER,
    ROLE_INSPECTOR,
    User,
)
from app.security import hash_password

INSPECTOR_AUTH = ("inspector_plan", "123456")
FARMER_AUTH = ("farmer_plan", "123456")

#: Sản phẩm chung của 20 lô trong lệnh thu hồi.
PRODUCT = "Xoài Cát Chu"

#: Tổ chức đang giữ lô - mỗi cửa hàng một tổ chức trong chuỗi.
#: Tên đặt ASCII vì giá trị header ``X-Organization-Id`` chỉ nhận ký tự ASCII.
ORG_HOLDER_A = "HTX Xoai My Xuong"
ORG_HOLDER_B = "Nha Phan Pho Mi Tay"
ORG_HOLDER_C = "Chuoi Sieu Thi Xanh"

STORE_A = "Cửa hàng Mỹ Xương"
STORE_B = "Kho lạnh Cao Lãnh"
STORE_C = "Cửa hàng Sa Đéc"

RECALL_TARGETS = ["Cơ Quan Kiểm Tra Chất Lượng", ORG_HOLDER_A, ORG_HOLDER_B, ORG_HOLDER_C]


@pytest.fixture
def recall_plan_env():
    """Môi trường kiểm thử: 3 cửa hàng + 20 lô của cùng một sản phẩm.

    Phân bổ: 8 lô ở cửa hàng A, 6 lô ở kho B, 6 lô ở cửa hàng C. Trong đó lô đầu
    tiên của mỗi cửa hàng đã bán một phần (hoặc bán hết) cho người tiêu dùng để
    kiểm tra việc tách bạch "còn thu hồi được" và "đã ra thị trường".
    """
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
                User(username=INSPECTOR_AUTH[0], password=hash_password(INSPECTOR_AUTH[1]), role=ROLE_INSPECTOR),
                User(username=FARMER_AUTH[0], password=hash_password(FARMER_AUTH[1]), role=ROLE_FARMER),
            ]
        )
        farm = Farm(
            name="Vùng trồng xoài Cao Lãnh",
            location="Xã Mỹ Xương, Huyện Cao Lãnh, Đồng Tháp",
            area=4.5,
            owner=ORG_HOLDER_A,
        )
        db.add(farm)
        db.flush()

        stores = [
            Location(organization=ORG_HOLDER_A, name=STORE_A, address="Ấp Mỹ An, Xã Mỹ Xương, Đồng Tháp"),
            Location(organization=ORG_HOLDER_B, name=STORE_B, address="Số 12 Nguyễn Huệ, TP Cao Lãnh, Đồng Tháp"),
            Location(organization=ORG_HOLDER_C, name=STORE_C, address="Số 5 Trần Phú, TP Sa Đéc, Đồng Tháp"),
        ]
        db.add_all(stores)
        db.flush()
        store_a, store_b, store_c = stores

        grouped_batches: dict[str, list[int]] = {STORE_A: [], STORE_B: [], STORE_C: []}
        layout = [
            (STORE_A, store_a.id, ORG_HOLDER_A, 8),
            (STORE_B, store_b.id, ORG_HOLDER_B, 6),
            (STORE_C, store_c.id, ORG_HOLDER_C, 6),
        ]
        for store_name, location_id, holder, count in layout:
            for _ in range(count):
                batch = Batch(
                    farm_id=farm.id,
                    product_name=PRODUCT,
                    quantity=100.0,
                    harvest_date=date(2026, 10, 1),
                    current_holder_org=holder,
                    current_location_id=location_id,
                )
                db.add(batch)
                db.flush()
                grouped_batches[store_name].append(batch.id)

        db.commit()

        def get_batch(batch_id: int) -> Batch:
            return db.get(Batch, batch_id)

        # Lô ở cửa hàng A bán một phần 40/100 kg cho người tiêu dùng.
        partial_batch = get_batch(grouped_batches[STORE_A][0])
        partial_batch.quantity = 60.0
        partial_batch.consumed_quantity = 40.0
        # Lô ở kho B đã bán hết - toàn bộ đã ra thị trường.
        sold_out_batch = get_batch(grouped_batches[STORE_B][0])
        sold_out_batch.quantity = 0.0
        sold_out_batch.consumed_quantity = 100.0
        sold_out_batch.status = BATCH_STATUS_CONSUMED
        # Lô ngoài phạm vi lệnh - không được xuất hiện trong kế hoạch thu hồi.
        outsider = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6",
            quantity=500.0,
            harvest_date=date(2026, 10, 2),
            current_holder_org=ORG_HOLDER_A,
            current_location_id=store_a.id,
        )
        db.add(outsider)
        db.commit()

        payload = {
            "batch_ids": [bid for ids in grouped_batches.values() for bid in ids],
            "by_store": grouped_batches,
            "partial_batch_id": partial_batch.id,
            "sold_out_batch_id": sold_out_batch.id,
            "outsider_batch_id": outsider.id,
            "store_ids": {STORE_A: store_a.id, STORE_B: store_b.id, STORE_C: store_c.id},
            "farm_id": farm.id,
        }

    def override_get_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    yield {"client": TestClient(app), "session_factory": session_factory, **payload}
    app.dependency_overrides.clear()
    engine.dispose()


def _create_recall_order(client: TestClient, *, batch_ids: list[int] | None = None, product_name: str | None = None) -> dict:
    """Ban hành một lệnh thu hồi và trả về body (đã kiểm tra 201)."""
    body: dict = {
        "title": "Thu hồi khẩn cấp lô xoài nhiễm khuẩn",
        "reason": "Phát hiện dư lượng thuốc BVTV vượt ngưỡng ở lô xoài Cao Lãnh",
        "target_organizations": RECALL_TARGETS,
    }
    if batch_ids is not None:
        body["batch_ids"] = batch_ids
    if product_name is not None:
        body["product_name"] = product_name

    response = client.post("/orders", json=body, auth=INSPECTOR_AUTH)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# AC 1: Lệnh thu hồi có 20 lô -> mỗi lô kèm tổ chức, địa điểm, còn lại, đã tiêu thụ.
# ---------------------------------------------------------------------------
def test_ac1_plan_lists_all_twenty_batches_with_org_location_and_quantities(recall_plan_env):
    """Giả sử lệnh thu hồi có 20 lô, khi xem thì mỗi lô ghi rõ chỗ đang giữ và khối lượng."""
    client = recall_plan_env["client"]
    order = _create_recall_order(client, batch_ids=recall_plan_env["batch_ids"])
    assert order["batch_count"] == 20
    assert len(order["batch_ids"]) == 20

    response = client.get(f"/orders/{order['id']}/recovery-plan", auth=INSPECTOR_AUTH)
    assert response.status_code == 200, response.text
    plan = response.json()

    assert plan["total_batches"] == 20
    assert len(plan["batches"]) == 20

    for row in plan["batches"]:
        assert row["organization"], "mỗi lô phải kèm tổ chức đang giữ"
        assert row["location_name"], "mỗi lô phải kèm địa điểm hiện tại"
        assert row["remaining_quantity"] >= 0
        assert row["consumed_quantity"] >= 0
        assert row["remaining_quantity"] + row["consumed_quantity"] == pytest.approx(row["total_quantity"])

    rows_by_batch = {row["batch_id"]: row for row in plan["batches"]}
    store_a_batch_id = recall_plan_env["by_store"][STORE_A][1]
    store_b_batch_id = recall_plan_env["by_store"][STORE_B][1]
    assert rows_by_batch[store_a_batch_id]["location_name"] == STORE_A
    assert rows_by_batch[store_a_batch_id]["organization"] == ORG_HOLDER_A
    assert rows_by_batch[store_b_batch_id]["location_name"] == STORE_B
    assert rows_by_batch[store_b_batch_id]["organization"] == ORG_HOLDER_B

    # Lô không thuộc lệnh không xuất hiện trong kế hoạch thu hồi.
    assert recall_plan_env["outsider_batch_id"] not in rows_by_batch


def test_ac1_plan_groups_batches_by_store_for_targeted_recall(recall_plan_env):
    """Mỗi cửa hàng một nhóm: biết chính xác phải tới đâu lấy lại bao nhiêu kg."""
    client = recall_plan_env["client"]
    order = _create_recall_order(client, batch_ids=recall_plan_env["batch_ids"])

    plan = client.get(f"/orders/{order['id']}/recovery-plan", auth=INSPECTOR_AUTH).json()
    assert plan["total_locations"] == 3
    groups = {group["location_name"]: group for group in plan["locations"]}
    assert set(groups) == {STORE_A, STORE_B, STORE_C}
    assert groups[STORE_A]["batch_count"] == 8
    assert groups[STORE_B]["batch_count"] == 6
    assert groups[STORE_C]["batch_count"] == 6
    assert groups[STORE_A]["location_id"] == recall_plan_env["store_ids"][STORE_A]
    assert groups[STORE_B]["location_address"].startswith("Số 12")

    # Tổng theo cửa hàng khớp tổng của lệnh.
    assert sum(g["remaining_quantity"] for g in plan["locations"]) == pytest.approx(plan["recallable_quantity"])
    assert sum(g["consumed_quantity"] for g in plan["locations"]) == pytest.approx(plan["consumed_quantity"])


# ---------------------------------------------------------------------------
# AC 2: Một phần đã bán cho người tiêu dùng -> tổng tách rõ còn thu hồi được / đã ra thị trường.
# ---------------------------------------------------------------------------
def test_ac2_totals_split_recallable_and_market_quantity(recall_plan_env):
    """Giả sử một phần đã bán, khi xem tổng thì thấy rõ còn thu hồi được bao nhiêu, đã ra thị trường bao nhiêu."""
    client = recall_plan_env["client"]
    sold_out_batch_id = recall_plan_env["sold_out_batch_id"]

    # Ghi nhận bán một phần 25 kg của một lô tại cửa hàng C qua API nghiệp vụ.
    store_c_batch_id = recall_plan_env["by_store"][STORE_C][1]
    sold = client.post(
        f"/batches/{store_c_batch_id}/consume",
        json={"quantity": 25, "note": "Bán lẻ tại quầy"},
        auth=FARMER_AUTH,
        headers={"X-Organization-Id": ORG_HOLDER_C},
    )
    assert sold.status_code == 200, sold.text
    assert sold.json()["quantity"] == 75.0
    assert sold.json()["consumed_quantity"] == 25.0
    assert sold.json()["remaining_quantity"] == 75.0
    assert sold.json()["total_quantity"] == 100.0
    assert sold.json()["status"] == "available"

    order = _create_recall_order(client, batch_ids=recall_plan_env["batch_ids"])
    plan = client.get(f"/orders/{order['id']}/recovery-plan", auth=INSPECTOR_AUTH).json()

    assert plan["total_quantity"] == pytest.approx(2000.0)
    # 20 lô x 100 kg, đã bán: 40 (A) + 100 (B) + 25 (C).
    assert plan["consumed_quantity"] == pytest.approx(165.0)
    assert plan["recallable_quantity"] == pytest.approx(1835.0)
    assert plan["recallable_quantity"] + plan["consumed_quantity"] == pytest.approx(plan["total_quantity"])
    assert plan["recallable_ratio"] == "1835/2000"
    assert plan["recallable_batches"] == 19
    assert plan["consumed_batches"] == 1

    rows_by_batch = {row["batch_id"]: row for row in plan["batches"]}
    assert rows_by_batch[store_c_batch_id]["remaining_quantity"] == 75.0
    assert rows_by_batch[store_c_batch_id]["consumed_quantity"] == 25.0
    assert rows_by_batch[store_c_batch_id]["is_recallable"] is True

    # Lô đã bán hết: không còn gì để thu hồi, toàn bộ đã ra thị trường.
    sold_out_row = rows_by_batch[sold_out_batch_id]
    assert sold_out_row["remaining_quantity"] == 0.0
    assert sold_out_row["consumed_quantity"] == 100.0
    assert sold_out_row["is_recallable"] is False
    assert sold_out_row["status"] == BATCH_STATUS_CONSUMED


def test_ac2_partial_sale_is_recorded_in_hash_chain(recall_plan_env):
    """Mỗi lần bán (một phần hay toàn bộ) đều vào chuỗi băm bất biến của lô."""
    client = recall_plan_env["client"]
    batch_id = recall_plan_env["by_store"][STORE_A][2]

    client.post(
        f"/batches/{batch_id}/consume",
        json={"quantity": 30, "note": "Bán sỉ cho tạp hoá"},
        auth=FARMER_AUTH,
        headers={"X-Organization-Id": ORG_HOLDER_A},
    )
    client.post(
        f"/batches/{batch_id}/consume",
        auth=FARMER_AUTH,
        headers={"X-Organization-Id": ORG_HOLDER_A},
    )

    with recall_plan_env["session_factory"]() as db:
        batch = db.get(Batch, batch_id)
        assert batch.quantity == 0
        assert batch.consumed_quantity == 100.0
        assert batch.status == BATCH_STATUS_CONSUMED

        events = list(
            db.scalars(
                select(BatchEvent).where(BatchEvent.batch_id == batch_id).order_by(BatchEvent.id)
            ).all()
        )
        assert [event.event_type for event in events] == ["BATCH_CONSUMED", "BATCH_CONSUMED"]

        first = json.loads(events[0].payload)
        assert first["sold_quantity"] == 30
        assert first["remaining_quantity"] == 70.0
        assert first["is_sold_out"] is False

        second = json.loads(events[1].payload)
        assert second["sold_quantity"] == 70.0
        assert second["remaining_quantity"] == 0.0
        assert second["is_sold_out"] is True
        assert events[1].previous_hash == events[0].hash


def test_recovery_plan_for_single_batch_order_from_incident(recall_plan_env):
    """Lệnh mở từ sự cố một lô (S-19) vẫn xem được kế hoạch thu hồi với đúng lô đó."""
    client = recall_plan_env["client"]
    batch_id = recall_plan_env["by_store"][STORE_A][3]

    incident = client.post(
        f"/batches/{batch_id}/incident",
        json={"reason": "Phát hiện dư lượng thuốc BVTV vượt ngưỡng"},
        auth=INSPECTOR_AUTH,
    )
    assert incident.status_code == 201, incident.text
    order_id = incident.json()["id"]

    plan = client.get(f"/orders/{order_id}/recovery-plan", auth=FARMER_AUTH).json()
    assert plan["total_batches"] == 1
    assert plan["batches"][0]["batch_id"] == batch_id
    assert plan["batches"][0]["location_name"] == STORE_A
    assert plan["batches"][0]["organization"] == ORG_HOLDER_A
    assert plan["recallable_quantity"] == 100.0
    assert plan["consumed_quantity"] == 0.0


def test_order_scope_by_product_name_collects_every_batch(recall_plan_env):
    """Nêu tên sản phẩm thay vì liệt kê từng lô: hệ thống gom mọi lô cùng sản phẩm."""
    client = recall_plan_env["client"]
    order = _create_recall_order(client, product_name=PRODUCT)
    assert order["batch_count"] == 20

    plan = client.get(f"/orders/{order['id']}/recovery-plan", auth=INSPECTOR_AUTH).json()
    assert plan["total_batches"] == 20
    assert all(row["product_name"] == PRODUCT for row in plan["batches"])


def test_order_scope_rejects_unknown_batch_and_product(recall_plan_env):
    """Chỉ định lô không tồn tại hoặc sản phẩm không có lô nào -> 400 kèm lý do rõ ràng."""
    client = recall_plan_env["client"]

    missing_batch = client.post(
        "/orders",
        json={
            "title": "Thu hồi lô không tồn tại",
            "reason": "Kiểm tra dữ liệu",
            "target_organizations": RECALL_TARGETS,
            "batch_ids": [999_999],
        },
        auth=INSPECTOR_AUTH,
    )
    assert missing_batch.status_code == 400
    assert "999999" in missing_batch.json()["detail"]

    missing_product = client.post(
        "/orders",
        json={
            "title": "Thu hồi sản phẩm không có lô",
            "reason": "Kiểm tra dữ liệu",
            "target_organizations": RECALL_TARGETS,
            "product_name": "Nhãn Lồng Hưng Yên",
        },
        auth=INSPECTOR_AUTH,
    )
    assert missing_product.status_code == 400
    assert "Nhãn Lồng Hưng Yên" in missing_product.json()["detail"]


def test_partial_consume_blocks_overselling_and_repeat_on_sold_out(recall_plan_env):
    """Bán quá khối lượng còn lại hoặc bán lại lô đã hết -> 400, không đổi dữ liệu."""
    client = recall_plan_env["client"]
    batch_id = recall_plan_env["by_store"][STORE_C][2]

    oversell = client.post(
        f"/batches/{batch_id}/consume",
        json={"quantity": 150},
        auth=FARMER_AUTH,
        headers={"X-Organization-Id": ORG_HOLDER_C},
    )
    assert oversell.status_code == 400
    assert "vượt quá khối lượng còn lại" in oversell.json()["detail"]

    sold_out = client.post(
        f"/batches/{recall_plan_env['sold_out_batch_id']}/consume",
        auth=FARMER_AUTH,
        headers={"X-Organization-Id": ORG_HOLDER_B},
    )
    assert sold_out.status_code == 400

    with recall_plan_env["session_factory"]() as db:
        assert db.get(Batch, batch_id).quantity == 100.0
        assert db.get(Batch, batch_id).consumed_quantity == 0.0


def test_recovery_plan_requires_login_and_existing_order(recall_plan_env):
    """Kế hoạch thu hồi là hồ sơ nội bộ: chưa đăng nhập 401, lệnh không tồn tại 404."""
    client = recall_plan_env["client"]
    order = _create_recall_order(client, batch_ids=recall_plan_env["batch_ids"])

    anonymous = client.get(f"/orders/{order['id']}/recovery-plan")
    assert anonymous.status_code == 401

    unknown = client.get("/orders/999999/recovery-plan", auth=INSPECTOR_AUTH)
    assert unknown.status_code == 404

    unknown_order = client.get("/orders/999999", auth=INSPECTOR_AUTH)
    assert unknown_order.status_code == 404


def test_order_events_are_written_to_every_batch_in_scope(recall_plan_env):
    """Lệnh nhiều lô: sự kiện RECALL_ISSUED (và RECALL_COMPLETED) vào chuỗi băm của **từng lô**."""
    client = recall_plan_env["client"]
    batch_ids = recall_plan_env["by_store"][STORE_A][:3]
    order = _create_recall_order(client, batch_ids=batch_ids)

    for batch_id in batch_ids:
        timeline = client.get(f"/batches/{batch_id}/events", auth=INSPECTOR_AUTH).json()
        assert timeline["is_valid"] is True
        issued = [ev for ev in timeline["events"] if ev["event_type"] == "RECALL_ISSUED"]
        assert len(issued) == 1
        assert json.loads(issued[0]["payload"])["order_code"] == order["order_code"]

    # Bên cuối cùng xác nhận -> lệnh hoàn tất và mọi lô đều ghi RECALL_COMPLETED.
    for index, org in enumerate(RECALL_TARGETS):
        response = client.post(
            f"/orders/{order['id']}/confirm",
            json={"org_name": org, "note": "Đã xử lý xong"},
            auth=FARMER_AUTH,
        )
        assert response.status_code == 200, response.text
        is_last = index == len(RECALL_TARGETS) - 1
        assert response.json()["is_order_completed"] is is_last

    for batch_id in batch_ids:
        timeline = client.get(f"/batches/{batch_id}/events", auth=INSPECTOR_AUTH).json()
        assert timeline["is_valid"] is True
        completed = [ev for ev in timeline["events"] if ev["event_type"] == "RECALL_COMPLETED"]
        assert len(completed) == 1
        assert json.loads(completed[0]["payload"])["order_id"] == order["id"]
