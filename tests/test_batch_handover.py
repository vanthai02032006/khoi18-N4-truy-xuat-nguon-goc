"""Kiểm thử tính năng bàn giao & xác nhận nhận lô nông sản (Handover Workflow).

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
1. Giả sử có bàn giao chờ tôi, Khi tôi xác nhận, Thì quyền giữ lô chuyển sang tôi và một sự kiện xác nhận được ghi tiếp vào chuỗi.
2. Giả sử tôi từ chối, Khi từ chối kèm lý do, Thì lô vẫn ở chỗ bên giao và lý do được ghi vào chuỗi sự kiện.
3. Giả sử tôi từ chối mà để trống lý do, Khi bấm, Thì bị chặn (HTTP 422 Unprocessable Entity).
4. Giả sử một người khác của tổ chức khác gọi API xác nhận bàn giao không dành cho họ, Khi máy chủ nhận, Thì trả về 403 Forbidden.
"""

from __future__ import annotations

import base64
from datetime import date
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, BatchEvent, Farm, User, ROLE_FARMER
from app.security import hash_password


@pytest.fixture
def handover_env():
    """Thiết lập môi trường kiểm thử với các tổ chức và tài khoản người dùng tương ứng."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        # Tài khoản bên giao (HTX Trồng Trọt)
        user_sender = User(
            username="sender_farmer",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        # Tài khoản bên nhận (HTX Sơ Chế)
        user_receiver = User(
            username="processor_farmer",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        # Tài khoản bên thứ ba không liên quan (Công ty Siêu Thị C)
        user_intruder = User(
            username="intruder_farmer",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        db.add_all([user_sender, user_receiver, user_intruder])

        # Vùng trồng của HTX Trồng Trọt
        farm = Farm(
            name="Vung Trong Xoai Dong Thap",
            location="Dong Thap",
            area=5.0,
            owner="HTX Trong Trot Cao Lanh",
        )
        db.add(farm)
        db.commit()

        # Tạo lô hàng ban đầu của HTX Trong Trot Cao Lanh
        batch = Batch(
            farm_id=farm.id,
            product_name="Xoai Cat Chu",
            quantity=1000.0,
            harvest_date=date(2026, 5, 20),
            current_holder_org="HTX Trong Trot Cao Lanh",
            pending_receiver_org=None,
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)
        batch_id = batch.id

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
        "batch_id": batch_id,
        "TestingSessionLocal": TestingSessionLocal,
    }

    app.dependency_overrides.clear()


def _auth_header(username: str, password: str = "123456") -> str:
    creds = f"{username}:{password}".encode("utf-8")
    return f"Basic {base64.b64encode(creds).decode('utf-8')}"


def test_handover_accept_success(handover_env):
    """Ca 1: Giả sử có bàn giao chờ tôi, Khi tôi xác nhận,
    Thì quyền giữ lô chuyển sang tôi và một sự kiện xác nhận được ghi tiếp vào chuỗi.
    """
    client: TestClient = handover_env["client"]
    batch_id: int = handover_env["batch_id"]
    SessionLocal = handover_env["TestingSessionLocal"]

    sender_auth = _auth_header("sender_farmer")
    receiver_auth = _auth_header("processor_farmer")

    # 1. Bên giao (HTX Trong Trot Cao Lanh) khởi tạo bàn giao sang HTX So Che My Xuong
    init_res = client.post(
        f"/batches/{batch_id}/handover/initiate",
        headers={
            "Authorization": sender_auth,
            "X-Organization-Id": "HTX Trong Trot Cao Lanh",
        },
        json={
            "target_organization": "HTX So Che My Xuong",
            "note": "Bàn giao 1000kg xoài cát Chu để sơ chế và đóng gói",
        },
    )
    assert init_res.status_code == 200, init_res.text
    init_data = init_res.json()
    assert init_data["current_holder_org"] == "HTX Trong Trot Cao Lanh"
    assert init_data["pending_receiver_org"] == "HTX So Che My Xuong"

    # 2. Bên nhận (HTX So Che My Xuong) gọi API xác nhận bàn giao
    accept_res = client.post(
        f"/batches/{batch_id}/handover/accept",
        headers={
            "Authorization": receiver_auth,
            "X-Organization-Id": "HTX So Che My Xuong",
        },
    )
    assert accept_res.status_code == 200, accept_res.text
    accept_data = accept_res.json()

    # Khẳng định quyền giữ lô đã chuyển sang bên nhận và không còn pending
    assert accept_data["status"] == "ACCEPTED"
    assert accept_data["current_holder_org"] == "HTX So Che My Xuong"
    assert accept_data["pending_receiver_org"] is None

    # 3. Kiểm tra trong cơ sở dữ liệu: Quyền giữ lô cập nhật & sự kiện được ghi vào chuỗi
    with SessionLocal() as db:
        batch = db.get(Batch, batch_id)
        assert batch.current_holder_org == "HTX So Che My Xuong"
        assert batch.pending_receiver_org is None

        # Kiểm tra sự kiện HANDOVER_ACCEPTED trong chuỗi sự kiện
        events = list(
            db.scalars(
                select(BatchEvent)
                .where(BatchEvent.batch_id == batch_id)
                .order_by(BatchEvent.id.asc())
            ).all()
        )
        assert len(events) >= 2  # Gồm sự kiện HANDOVER_INITIATED và HANDOVER_ACCEPTED
        last_event = events[-1]
        assert last_event.event_type == "HANDOVER_ACCEPTED"
        assert last_event.organization == "HTX So Che My Xuong"
        assert last_event.actor == "processor_farmer"
        assert last_event.previous_hash == events[-2].hash  # Tính liên kết mã băm của chuỗi

        payload_obj = json.loads(last_event.payload)
        assert payload_obj["action"] == "HANDOVER_ACCEPTED"
        assert payload_obj["from_organization"] == "HTX Trong Trot Cao Lanh"
        assert payload_obj["to_organization"] == "HTX So Che My Xuong"


def test_handover_reject_with_reason(handover_env):
    """Ca 2: Giả sử tôi từ chối, Khi từ chối kèm lý do,
    Thì lô vẫn ở chỗ bên giao và lý do được ghi vào chuỗi sự kiện.
    """
    client: TestClient = handover_env["client"]
    batch_id: int = handover_env["batch_id"]
    SessionLocal = handover_env["TestingSessionLocal"]

    sender_auth = _auth_header("sender_farmer")
    receiver_auth = _auth_header("processor_farmer")

    # 1. Khởi tạo bàn giao
    client.post(
        f"/batches/{batch_id}/handover/initiate",
        headers={
            "Authorization": sender_auth,
            "X-Organization-Id": "HTX Trong Trot Cao Lanh",
        },
        json={
            "target_organization": "HTX So Che My Xuong",
            "note": "Bàn giao lô xoài",
        },
    )

    # 2. Bên nhận từ chối kèm lý do
    rejection_reason = "Xoài bị dập nát quá 20% trong quá trình vận chuyển, không đạt tiêu chuẩn xuất khẩu"
    reject_res = client.post(
        f"/batches/{batch_id}/handover/reject",
        headers={
            "Authorization": receiver_auth,
            "X-Organization-Id": "HTX So Che My Xuong",
        },
        json={
            "reason": rejection_reason,
        },
    )
    assert reject_res.status_code == 200, reject_res.text
    reject_data = reject_res.json()
    assert reject_data["status"] == "REJECTED"
    assert reject_data["current_holder_org"] == "HTX Trong Trot Cao Lanh"
    assert reject_data["pending_receiver_org"] is None

    # 3. Khẳng định lô vẫn ở chỗ bên giao và lý do được ghi vào chuỗi sự kiện
    with SessionLocal() as db:
        batch = db.get(Batch, batch_id)
        assert batch.current_holder_org == "HTX Trong Trot Cao Lanh"
        assert batch.pending_receiver_org is None

        # Kiểm tra sự kiện HANDOVER_REJECTED
        last_event = db.scalars(
            select(BatchEvent)
            .where(BatchEvent.batch_id == batch_id)
            .order_by(BatchEvent.id.desc())
        ).first()
        assert last_event is not None
        assert last_event.event_type == "HANDOVER_REJECTED"
        assert last_event.organization == "HTX So Che My Xuong"

        payload_obj = json.loads(last_event.payload)
        assert payload_obj["action"] == "HANDOVER_REJECTED"
        assert payload_obj["from_organization"] == "HTX Trong Trot Cao Lanh"
        assert payload_obj["rejected_by_organization"] == "HTX So Che My Xuong"
        assert payload_obj["reason"] == rejection_reason


def test_handover_reject_blocked_when_reason_empty(handover_env):
    """Ca 3: Giả sử tôi từ chối mà để trống lý do, Khi bấm, Thì bị chặn."""
    client: TestClient = handover_env["client"]
    batch_id: int = handover_env["batch_id"]
    SessionLocal = handover_env["TestingSessionLocal"]

    sender_auth = _auth_header("sender_farmer")
    receiver_auth = _auth_header("processor_farmer")

    # Khởi tạo bàn giao
    client.post(
        f"/batches/{batch_id}/handover/initiate",
        headers={
            "Authorization": sender_auth,
            "X-Organization-Id": "HTX Trong Trot Cao Lanh",
        },
        json={
            "target_organization": "HTX So Che My Xuong",
        },
    )

    # 1. Gửi lý do là chuỗi rỗng ""
    res_empty = client.post(
        f"/batches/{batch_id}/handover/reject",
        headers={
            "Authorization": receiver_auth,
            "X-Organization-Id": "HTX So Che My Xuong",
        },
        json={"reason": ""},
    )
    assert res_empty.status_code == 422, "Phải chặn khi reason là chuỗi rỗng"

    # 2. Gửi lý do chỉ gồm toàn khoảng trắng "   "
    res_whitespace = client.post(
        f"/batches/{batch_id}/handover/reject",
        headers={
            "Authorization": receiver_auth,
            "X-Organization-Id": "HTX So Che My Xuong",
        },
        json={"reason": "   "},
    )
    assert res_whitespace.status_code == 422, "Phải chặn khi reason chỉ có khoảng trắng"

    # 3. Gửi payload thiếu trường reason
    res_missing = client.post(
        f"/batches/{batch_id}/handover/reject",
        headers={
            "Authorization": receiver_auth,
            "X-Organization-Id": "HTX So Che My Xuong",
        },
        json={},
    )
    assert res_missing.status_code == 422, "Phải chặn khi thiếu trường reason"

    # Khẳng định trong database trạng thái chờ vẫn còn nguyên
    with SessionLocal() as db:
        batch = db.get(Batch, batch_id)
        assert batch.pending_receiver_org == "HTX So Che My Xuong"


def test_handover_unauthorized_organization_forbidden(handover_env):
    """Ca 4: Giả sử một người khác của tổ chức khác gọi API xác nhận bàn giao không dành cho họ,
    Khi máy chủ nhận, Thì trả về 403.
    """
    client: TestClient = handover_env["client"]
    batch_id: int = handover_env["batch_id"]
    SessionLocal = handover_env["TestingSessionLocal"]

    sender_auth = _auth_header("sender_farmer")
    intruder_auth = _auth_header("intruder_farmer")

    # 1. Bên giao khởi tạo bàn giao cho 'HTX So Che My Xuong'
    client.post(
        f"/batches/{batch_id}/handover/initiate",
        headers={
            "Authorization": sender_auth,
            "X-Organization-Id": "HTX Trong Trot Cao Lanh",
        },
        json={
            "target_organization": "HTX So Che My Xuong",
        },
    )

    # 2. Người của bên thứ ba ('Cong Ty Sieu Thi C') cố tình gọi API xác nhận bàn giao
    forbidden_res = client.post(
        f"/batches/{batch_id}/handover/accept",
        headers={
            "Authorization": intruder_auth,
            "X-Organization-Id": "Cong Ty Sieu Thi C",
        },
    )
    assert forbidden_res.status_code == 403, forbidden_res.text
    err_detail = forbidden_res.json()["detail"]
    assert "không phải bên nhận được chỉ định" in err_detail

    # 3. Người của bên thứ ba cũng không thể từ chối thay bên nhận
    reject_forbidden_res = client.post(
        f"/batches/{batch_id}/handover/reject",
        headers={
            "Authorization": intruder_auth,
            "X-Organization-Id": "Cong Ty Sieu Thi C",
        },
        json={"reason": "Từ chối lén"},
    )
    assert reject_forbidden_res.status_code == 403, reject_forbidden_res.text

    # Khẳng định quyền giữ lô và trạng thái chờ không hề bị ảnh hưởng
    with SessionLocal() as db:
        batch = db.get(Batch, batch_id)
        assert batch.current_holder_org == "HTX Trong Trot Cao Lanh"
        assert batch.pending_receiver_org == "HTX So Che My Xuong"
