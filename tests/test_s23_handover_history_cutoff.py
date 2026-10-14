"""S-23 / SCRUM-89 AC3: lô đã bàn giao đi khỏi tôi -> vẫn thấy lịch sử tới lúc bàn
giao nhưng không thấy bước sau đó. Bên nhận và cán bộ kiểm tra thấy đủ."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.append_only import install_append_only_protection
from app.database import Base, get_db
from app.main import app
from app.models import ROLE_FARMER, ROLE_INSPECTOR, Farm, User
from app.security import hash_password

A = ("vung_trong_a", "123456")
B = ("htx_b", "123456")
INSPECTOR = ("can_bo", "123456")


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    install_append_only_protection(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Session() as db:
        db.add_all([
            User(username=A[0], password=hash_password(A[1]), role=ROLE_FARMER, organization_id="ORG_A"),
            User(username=B[0], password=hash_password(B[1]), role=ROLE_FARMER, organization_id="ORG_B"),
            User(username=INSPECTOR[0], password=hash_password(INSPECTOR[1]), role=ROLE_INSPECTOR, organization_id="ORG_KT"),
            Farm(name="Thửa A", location="Cao Lãnh", area=1, owner="Vùng trồng A"),
        ])
        db.commit()

    def override():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _types(c, batch_id, who):
    res = c.get(f"/batches/{batch_id}/events", auth=who)
    assert res.status_code == 200, res.text
    data = res.json()
    return [e["event_type"] for e in data["events"]], data


def test_sender_sees_history_only_until_handover(client):
    lot = client.post("/batches", auth=A, json={"farm_id": 1, "product_name": "Xoài", "quantity": 100,
                                                 "harvest_date": str(date.today())}).json()
    h = client.post("/handovers", auth=A, json={"batch_id": lot["id"], "receiver_id": 2, "receiver_name": "HTX B"})
    assert h.status_code == 201, h.text
    assert client.post(f"/handovers/{h.json()['id']}/accept", auth=B, json={}).status_code == 200
    split = client.post(f"/batches/{lot['id']}/split", auth=B, json={"items": [{"quantity": 40}]})
    assert split.status_code in (200, 201), split.text

    a_types, a_data = _types(client, lot["id"], A)
    assert "SPLIT" not in a_types
    assert a_types[-1] in ("OWNER_CHANGED", "HANDOVER_ACCEPTED")
    assert a_data["truncated_after_handover"] is True
    assert a_data["is_valid"] is True  # phần được xem vẫn kiểm toàn vẹn hợp lệ

    for who in (B, INSPECTOR):
        types, data = _types(client, lot["id"], who)
        assert "SPLIT" in types
        assert data["truncated_after_handover"] is False
