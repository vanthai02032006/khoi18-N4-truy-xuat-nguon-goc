"""Kiểm thử S-21: lô đang bị thu hồi thì **không di chuyển được nữa**.

User Story: *"Là cán bộ kiểm tra tôi muốn lô đang bị thu hồi không di chuyển được
nữa để vùng ảnh hưởng không lan rộng trong lúc xử lý."*

Tiêu chí nghiệm thu (DoD / AC):
1. Giả sử lô nằm trong lệnh thu hồi đang mở, khi ai đó bàn giao, tách hoặc gộp nó,
   thì bị chặn kèm mã lệnh thu hồi.
2. Giả sử lệnh đã hoàn tất, khi thao tác, thì vẫn bị chặn vì lô đã được đánh dấu có sự cố.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    HANDOVER_STATUS_PENDING,
    Batch,
    Farm,
    Location,
    ROLE_FARMER,
    ROLE_INSPECTOR,
    User,
)
from app.security import hash_password

#: Tổ chức đang giữ lô (ASCII vì giá trị header ``X-Organization-Id`` chỉ nhận ASCII).
HOLDER_ORG = "HTX Xoai My Xuong"
#: Tổ chức nhận bàn giao trong các ca kiểm thử.
OTHER_ORG = "Nha Phan Pho Mi Tay"

INSPECTOR_AUTH = ("inspector_lock", "123456")
FARMER_AUTH = ("farmer_lock", "123456")

STORE_A = "Cửa hàng Mỹ Xương"
STORE_B = "Kho lạnh Cao Lãnh"


@pytest.fixture
def lock_env():
    """Môi trường kiểm thử: 2 địa điểm + 3 lô xoài của cùng một tổ chức.

    - ``main``: lô bị đánh dấu sự cố / đưa vào lệnh thu hồi.
    - ``partner``: lô tự do dùng làm bạn gộp.
    - ``free``: lô tự do dùng để đối chiếu "vẫn di chuyển được bình thường".
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
                User(
                    username=INSPECTOR_AUTH[0],
                    password=hash_password(INSPECTOR_AUTH[1]),
                    role=ROLE_INSPECTOR,
                ),
                User(
                    username=FARMER_AUTH[0],
                    password=hash_password(FARMER_AUTH[1]),
                    role=ROLE_FARMER,
                ),
            ]
        )
        farm = Farm(
            name="Vùng trồng xoài bị thu hồi",
            location="Xã Mỹ Xương, Huyện Cao Lãnh, Đồng Tháp",
            area=3.0,
            owner=HOLDER_ORG,
        )
        db.add(farm)
        db.flush()

        store_a = Location(organization=HOLDER_ORG, name=STORE_A, address="Ấp Mỹ An, Đồng Tháp")
        store_b = Location(organization=HOLDER_ORG, name=STORE_B, address="Số 12 Nguyễn Huệ, Đồng Tháp")
        db.add_all([store_a, store_b])
        db.flush()
        store_ids = {"a": store_a.id, "b": store_b.id}

        batch_ids: dict[str, int] = {}
        for key, code, quantity in (
            ("main", "LOT-LOCK-MAIN", 300.0),
            ("partner", "LOT-LOCK-PARTNER", 200.0),
            ("free", "LOT-LOCK-FREE", 150.0),
        ):
            batch = Batch(
                farm_id=farm.id,
                product_name="Xoài Cát Chu",
                quantity=quantity,
                harvest_date=date(2026, 10, 1),
                batch_code=code,
                current_holder_org=HOLDER_ORG,
                current_location_id=store_ids["a"],
            )
            db.add(batch)
            db.flush()
            batch_ids[key] = batch.id

        db.commit()

    def override_get_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    yield {
        "client": TestClient(app),
        "session_factory": session_factory,
        "batch_ids": batch_ids,
        "store_a_id": store_ids["a"],
        "store_b_id": store_ids["b"],
    }

    app.dependency_overrides.clear()
    engine.dispose()


def _flag_incident(client: TestClient, batch_id: int) -> dict:
    """Đánh dấu lô có sự cố và trả về lệnh thu hồi vừa mở (đã kiểm tra 201)."""
    response = client.post(
        f"/batches/{batch_id}/incident",
        json={"reason": "Phát hiện dư lượng thuốc BVTV vượt ngưỡng cho phép"},
        auth=INSPECTOR_AUTH,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_recall_order(client: TestClient, batch_ids: list[int]) -> dict:
    """Ban hành lệnh thu hồi nhiều lô và trả về body (đã kiểm tra 201)."""
    response = client.post(
        "/orders",
        json={
            "title": "Thu hồi khẩn cấp lô xoài nhiễm khuẩn",
            "reason": "Phát hiện dư lượng thuốc BVTV vượt ngưỡng ở lô xoài Cao Lãnh",
            "target_organizations": ["Cơ Quan Kiểm Tra Chất Lượng", HOLDER_ORG],
            "batch_ids": batch_ids,
        },
        auth=INSPECTOR_AUTH,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _complete_order(client: TestClient, order: dict) -> None:
    """Cho mọi bên liên quan xác nhận để lệnh tự chuyển sang hoàn tất."""
    for org_name in list(order["pending_organizations"]):
        response = client.post(
            f"/orders/{order['id']}/confirm",
            json={"org_name": org_name, "note": "Đã xử lý xong"},
            auth=FARMER_AUTH,
        )
        assert response.status_code == 200, response.text
    assert response.json()["order_status"] == "COMPLETED"


def _movement_attempts(
    client: TestClient,
    batch_id: int,
    *,
    partner_batch_id: int,
    location_id: int,
) -> dict[str, Any]:
    """Gọi cả 4 phép di chuyển trên một lô: bàn giao, tách, gộp, chuyển địa điểm."""
    return {
        "bàn giao": client.post(
            f"/batches/{batch_id}/handover/initiate",
            json={"target_organization": OTHER_ORG},
            auth=FARMER_AUTH,
            headers={"X-Organization-Id": HOLDER_ORG},
        ),
        "tách lô": client.post(
            f"/batches/{batch_id}/split",
            json={"items": [{"quantity": 10.0}]},
            auth=FARMER_AUTH,
            headers={"X-Organization-Id": HOLDER_ORG},
        ),
        "gộp lô": client.post(
            "/batches/merge",
            json={
                "items": [
                    {"batch_id": batch_id, "quantity": 10.0},
                    {"batch_id": partner_batch_id, "quantity": 10.0},
                ]
            },
            auth=FARMER_AUTH,
            headers={"X-Organization-Id": HOLDER_ORG},
        ),
        "chuyển địa điểm": client.post(
            f"/batches/{batch_id}/location",
            json={"location_id": location_id},
            auth=FARMER_AUTH,
            headers={"X-Organization-Id": HOLDER_ORG},
        ),
    }


def _assert_all_blocked_with_order_code(attempts: dict[str, Any], order_code: str) -> None:
    """Mọi phép di chuyển phải trả 409 và nêu đúng mã lệnh thu hồi."""
    for action, response in attempts.items():
        assert response.status_code == 409, f"{action}: {response.text}"
        detail = response.json()["detail"]
        assert isinstance(detail, str), f"{action}: {detail!r}"
        assert order_code in detail, f"{action}: {detail}"


# ---------------------------------------------------------------------------
# AC 1: Lô nằm trong lệnh thu hồi đang mở -> bàn giao / tách / gộp bị chặn kèm mã lệnh.
# ---------------------------------------------------------------------------
def test_ac1_open_order_blocks_handover_split_merge_and_location_move(lock_env):
    """Giả sử lô nằm trong lệnh thu hồi đang mở, khi thao tác thì bị chặn kèm mã lệnh."""
    client = lock_env["client"]
    main_id = lock_env["batch_ids"]["main"]
    partner_id = lock_env["batch_ids"]["partner"]

    order = _flag_incident(client, main_id)
    assert order["status"] == "IN_PROGRESS"

    attempts = _movement_attempts(
        client,
        main_id,
        partner_batch_id=partner_id,
        location_id=lock_env["store_b_id"],
    )
    _assert_all_blocked_with_order_code(attempts, order["order_code"])
    assert "đang nằm trong lệnh thu hồi" in attempts["tách lô"].json()["detail"]


def test_ac1_multi_batch_order_freezes_every_batch_in_scope_only(lock_env):
    """Lệnh nhiều lô (S-20) đóng băng **mọi lô trong phạm vi**, không chạm lô ngoài phạm vi."""
    client = lock_env["client"]
    main_id = lock_env["batch_ids"]["main"]
    partner_id = lock_env["batch_ids"]["partner"]
    free_id = lock_env["batch_ids"]["free"]

    order = _create_recall_order(client, [main_id, partner_id])

    for batch_id in (main_id, partner_id):
        attempts = _movement_attempts(
            client,
            batch_id,
            partner_batch_id=free_id,
            location_id=lock_env["store_b_id"],
        )
        _assert_all_blocked_with_order_code(attempts, order["order_code"])

    # Lô không thuộc phạm vi lệnh vẫn di chuyển và tách được bình thường.
    moved = client.post(
        f"/batches/{free_id}/location",
        json={"location_id": lock_env["store_b_id"]},
        auth=FARMER_AUTH,
        headers={"X-Organization-Id": HOLDER_ORG},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["current_location_id"] == lock_env["store_b_id"]

    split = client.post(
        f"/batches/{free_id}/split",
        json={"items": [{"quantity": 50.0}]},
        auth=FARMER_AUTH,
        headers={"X-Organization-Id": HOLDER_ORG},
    )
    assert split.status_code == 201, split.text
    assert split.json()["parent_remaining_quantity"] == 100.0


def test_ac1_handover_accept_is_blocked_when_recall_opens_after_initiation(lock_env):
    """Bàn giao đã khởi tạo trước khi có lệnh vẫn không thể hoàn tất sau khi lô bị thu hồi."""
    client = lock_env["client"]
    free_id = lock_env["batch_ids"]["free"]

    initiated = client.post(
        f"/batches/{free_id}/handover/initiate",
        json={"target_organization": OTHER_ORG, "note": "Giao cho nhà phân phối"},
        auth=FARMER_AUTH,
        headers={"X-Organization-Id": HOLDER_ORG},
    )
    assert initiated.status_code == 200, initiated.text
    assert initiated.json()["pending_receiver_org"] == OTHER_ORG

    order = _create_recall_order(client, [free_id])

    accepted = client.post(
        f"/batches/{free_id}/handover/accept",
        auth=FARMER_AUTH,
        headers={"X-Organization-Id": OTHER_ORG},
    )
    assert accepted.status_code == 409, accepted.text
    assert order["order_code"] in accepted.json()["detail"]

    # Quyền giữ lô không đổi: bên nhận không tiếp nhận được lô đang bị thu hồi.
    with lock_env["session_factory"]() as db:
        batch = db.get(Batch, free_id)
        assert batch.current_holder_org == HOLDER_ORG
        assert batch.pending_receiver_org == OTHER_ORG


def test_ac1_handover_reject_is_still_allowed_to_clear_pending_state(lock_env):
    """Từ chối bàn giao vẫn được phép: lô không di chuyển mà trạng thái chờ được dọn."""
    client = lock_env["client"]
    free_id = lock_env["batch_ids"]["free"]

    client.post(
        f"/batches/{free_id}/handover/initiate",
        json={"target_organization": OTHER_ORG},
        auth=FARMER_AUTH,
        headers={"X-Organization-Id": HOLDER_ORG},
    )
    _create_recall_order(client, [free_id])

    rejected = client.post(
        f"/batches/{free_id}/handover/reject",
        json={"reason": "Lô đang bị thu hồi, không nhận"},
        auth=FARMER_AUTH,
        headers={"X-Organization-Id": OTHER_ORG},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["pending_receiver_org"] is None
    assert rejected.json()["current_holder_org"] == HOLDER_ORG


# ---------------------------------------------------------------------------
# AC 2: Lệnh đã hoàn tất -> lô vẫn bị chặn vì đã được đánh dấu có sự cố.
# ---------------------------------------------------------------------------
def test_ac2_completed_order_keeps_batch_frozen(lock_env):
    """Lệnh đã hoàn tất, khi thao tác thì vẫn bị chặn kèm mã lệnh (lô đã nhiễm sự cố)."""
    client = lock_env["client"]
    main_id = lock_env["batch_ids"]["main"]
    partner_id = lock_env["batch_ids"]["partner"]

    order = _flag_incident(client, main_id)
    _complete_order(client, order)

    attempts = _movement_attempts(
        client,
        main_id,
        partner_batch_id=partner_id,
        location_id=lock_env["store_b_id"],
    )
    _assert_all_blocked_with_order_code(attempts, order["order_code"])
    assert "đã được đánh dấu có sự cố" in attempts["bàn giao"].json()["detail"]


def test_ac2_batch_response_exposes_incident_marker(lock_env):
    """Tra cứu lô phải thấy dấu sự cố để giao diện chặn thao tác trước khi gọi API."""
    client = lock_env["client"]
    main_id = lock_env["batch_ids"]["main"]
    free_id = lock_env["batch_ids"]["free"]

    order = _flag_incident(client, main_id)

    flagged = client.get(f"/batches/{main_id}").json()
    assert flagged["incident_flagged"] is True
    assert flagged["incident_order_id"] == order["id"]
    assert flagged["incident_order_code"] == order["order_code"]
    assert flagged["incident_flagged_at"]

    untouched = client.get(f"/batches/{free_id}").json()
    assert untouched["incident_flagged"] is False
    assert untouched["incident_order_code"] is None


# ---------------------------------------------------------------------------
# Ca biên: thao tác bị chặn không để lại dấu vết dữ liệu nào.
# ---------------------------------------------------------------------------
def test_blocked_movements_leave_quantities_and_history_untouched(lock_env):
    """Bị chặn thì lô giữ nguyên khối lượng, người giữ, địa điểm và không thêm sự kiện."""
    client = lock_env["client"]
    main_id = lock_env["batch_ids"]["main"]
    partner_id = lock_env["batch_ids"]["partner"]

    order = _flag_incident(client, main_id)
    before = client.get(f"/batches/{main_id}/events", auth=INSPECTOR_AUTH).json()
    event_count_before = len(before["events"])

    _movement_attempts(
        client,
        main_id,
        partner_batch_id=partner_id,
        location_id=lock_env["store_b_id"],
    )

    with lock_env["session_factory"]() as db:
        batch = db.get(Batch, main_id)
        assert batch.quantity == 300.0
        assert batch.current_holder_org == HOLDER_ORG
        assert batch.current_location_id == lock_env["store_a_id"]
        assert batch.pending_receiver_org is None

    after = client.get(f"/batches/{main_id}/events", auth=INSPECTOR_AUTH).json()
    assert len(after["events"]) == event_count_before
    assert after["is_valid"] is True
    assert order["order_code"] == client.get(f"/batches/{main_id}").json()["incident_order_code"]


# ---------------------------------------------------------------------------
# Đường API bàn giao cũ (``POST /handovers``, ``POST /handovers/{id}/accept``)
# cũng phải chịu đúng luật đóng băng - nếu không sẽ lách được qua đây.
# ---------------------------------------------------------------------------
def _create_pending_handover(client: TestClient, batch_id: int, receiver_id: int):
    """Tạo phiếu bàn giao đang chờ qua ``POST /handovers`` (đã kiểm tra 201)."""
    response = client.post(
        "/handovers",
        json={
            "batch_id": batch_id,
            "receiver_id": receiver_id,
            "receiver_name": OTHER_ORG,
            "notes": "Bàn giao lô xoài sang nhà phân phối.",
        },
        auth=INSPECTOR_AUTH,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _farmer_id(session_factory) -> int:
    """ID tài khoản dùng làm bên nhận trong các phiếu bàn giao kiểm thử."""
    with session_factory() as db:
        return db.scalar(select(User.id).where(User.username == FARMER_AUTH[0]))


def test_ac1_legacy_handover_route_is_blocked_while_order_open(lock_env):
    """Lô trong lệnh đang mở thì không tạo được phiếu bàn giao ở đường API cũ."""
    client = lock_env["client"]
    main_id = lock_env["batch_ids"]["main"]
    free_id = lock_env["batch_ids"]["free"]

    order = _flag_incident(client, main_id)

    blocked = client.post(
        "/handovers",
        json={
            "batch_id": main_id,
            "receiver_id": _farmer_id(lock_env["session_factory"]),
            "receiver_name": OTHER_ORG,
        },
        auth=INSPECTOR_AUTH,
    )
    assert blocked.status_code == 409, blocked.text
    assert order["order_code"] in blocked.json()["detail"]
    assert "đang nằm trong lệnh thu hồi" in blocked.json()["detail"]

    # Không có phiếu nào được ghi cho lô đang bị thu hồi.
    assert client.get("/handovers", params={"batch_id": main_id}, auth=INSPECTOR_AUTH).json() == []

    # Lô tự do vẫn bàn giao bình thường: luật chặn không lan sang lô ngoài phạm vi.
    pending = _create_pending_handover(client, free_id, _farmer_id(lock_env["session_factory"]))
    assert pending["status"] == HANDOVER_STATUS_PENDING


def test_ac1_legacy_handover_accept_is_blocked_when_recall_opens_after_initiation(lock_env):
    """Phiếu đã tạo trước khi có lệnh vẫn không tiếp nhận được lô sau khi lô bị thu hồi."""
    client = lock_env["client"]
    free_id = lock_env["batch_ids"]["free"]
    receiver_id = _farmer_id(lock_env["session_factory"])
    handover = _create_pending_handover(client, free_id, receiver_id)

    order = _create_recall_order(client, [free_id])

    accepted = client.post(f"/handovers/{handover['id']}/accept", auth=FARMER_AUTH)
    assert accepted.status_code == 409, accepted.text
    assert order["order_code"] in accepted.json()["detail"]

    # Quyền giữ lô không đổi và phiếu vẫn đang chờ xử lý.
    with lock_env["session_factory"]() as db:
        batch = db.get(Batch, free_id)
        assert batch.current_owner == HOLDER_ORG
    assert client.get(f"/handovers/{handover['id']}", auth=INSPECTOR_AUTH).json()[
        "status"
    ] == HANDOVER_STATUS_PENDING


def test_ac2_legacy_handover_route_stays_blocked_after_order_completed(lock_env):
    """Lệnh đã hoàn tất, lô vẫn mang dấu sự cố nên không bàn giao được ở đường API cũ."""
    client = lock_env["client"]
    main_id = lock_env["batch_ids"]["main"]

    order = _flag_incident(client, main_id)
    _complete_order(client, order)

    blocked = client.post(
        "/handovers",
        json={
            "batch_id": main_id,
            "receiver_id": _farmer_id(lock_env["session_factory"]),
            "receiver_name": OTHER_ORG,
        },
        auth=INSPECTOR_AUTH,
    )
    assert blocked.status_code == 409, blocked.text
    assert order["order_code"] in blocked.json()["detail"]
    assert "đã được đánh dấu có sự cố" in blocked.json()["detail"]
