"""S-10 + S-20: tạo lô có sự kiện thu hoạch; sau tách và gộp mọi lô liên quan vẫn
hợp lệ khi kiểm toàn vẹn, và phả hệ đọc được qua API truy ngược/truy xuôi."""

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
from app.models import ROLE_FARMER, Farm, User
from app.security import hash_password

HTX = ("htx_user", "123456")


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    install_append_only_protection(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Session() as db:
        db.add(User(username=HTX[0], password=hash_password(HTX[1]), role=ROLE_FARMER, organization_id="ORG_HTX"))
        db.add_all([Farm(name="Thửa A", location="Cao Lãnh", area=1, owner="HTX"),
                    Farm(name="Thửa B", location="Cái Bè", area=2, owner="HTX")])
        db.commit()

    def override():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _create(c, farm_id, qty):
    r = c.post("/batches", auth=HTX, json={"farm_id": farm_id, "product_name": "Xoài", "quantity": qty,
                                           "harvest_date": str(date.today())})
    assert r.status_code == 201, r.text
    return r.json()


def _events(c, batch_id):
    r = c.get(f"/batches/{batch_id}/events", auth=HTX)
    assert r.status_code == 200, r.text
    return r.json()


def test_full_chain_split_merge_trace(client):
    a = _create(client, 1, 100)
    b = _create(client, 2, 80)
    ev = _events(client, a["id"])
    assert [e["event_type"] for e in ev["events"]] == ["HARVEST"]

    split = client.post(f"/batches/{a['id']}/split", auth=HTX, json={"items": [{"quantity": 30}, {"quantity": 30}]})
    assert split.status_code in (200, 201), split.text
    c1, c2 = [x["id"] for x in split.json()["child_batches"]]

    merged = client.post("/batches/merge", auth=HTX, json={"farm_id": 1, "items": [
        {"batch_id": c1, "quantity": 30}, {"batch_id": b["id"], "quantity": 50}]})
    assert merged.status_code == 201, merged.text
    m = merged.json()["merged_batch"]

    for bid in (a["id"], b["id"], c1, c2, m["id"]):
        assert _events(client, bid)["is_valid"] is True, bid

    back = client.get(f"/batches/{m['id']}/trace-back", auth=HTX).json()
    assert {f["farm_name"] for f in back["origin_farms"]} == {"Thửa A", "Thửa B"}

    fwd = client.get(f"/batches/{a['id']}/trace-forward", auth=HTX).json()
    assert {d["batch_id"] for d in fwd["descendants"]} == {c1, c2, m["id"]}

    # Lô gộp vẫn thuộc tổ chức đã gộp -> xem được trong danh sách đang giữ.
    holding = client.get("/batches?holding=true", auth=HTX).json()
    assert m["id"] in [x["id"] for x in holding]


def test_batch_detail_of_split_parent_does_not_crash(client):
    """Hồi quy: GET /batches/{id} của lô đã tách từng trả 500 (lệch schema children)."""
    a = _create(client, 1, 100)
    split = client.post(f"/batches/{a['id']}/split", auth=HTX, json={"items": [{"quantity": 40}]})
    assert split.status_code in (200, 201), split.text
    child_id = split.json()["child_batches"][0]["id"]
    res = client.get(f"/batches/{a['id']}", auth=HTX)
    assert res.status_code == 200, res.text
    assert [r["child_batch_id"] for r in res.json()["child_relations"]] == [child_id]
    child = client.get(f"/batches/{child_id}", auth=HTX).json()
    assert [r["parent_batch_id"] for r in child["parent_relations"]] == [a["id"]]
