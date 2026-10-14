"""Kiểm thử AC của SCRUM-49 / T-33 (S-14): danh sách lô tổ chức đang giữ.

Khẳng định:
- Bắt buộc đăng nhập; chỉ trả về lô tổ chức của tài khoản đang/từng giữ (S-05).
- Sắp xếp mới nhất trước; mặc định 20 lô mỗi trang.
- Phân trang con trỏ: trang sau không trùng và không sót lô.
- Bộ lọc sản phẩm và tìm theo mã vẫn hoạt động.
- Lô đã bàn giao xong sang tổ chức khác không còn trong danh sách của bên giao.
"""

from __future__ import annotations

import base64
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import ROLE_FARMER, Batch, BatchCustodyHistory, Farm, User
from app.security import hash_password

ORG_A = "ORG_BAC_GIANG"
ORG_B = "ORG_LUC_NGAN"


def _auth(username: str) -> dict:
    token = base64.b64encode(f"{username}:123456".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with SessionLocal() as db:
        db.add_all([
            User(username="user_a", password=hash_password("123456"), role=ROLE_FARMER, organization_id=ORG_A),
            User(username="user_b", password=hash_password("123456"), role=ROLE_FARMER, organization_id=ORG_B),
        ])
        farm = Farm(name="Vùng Trồng", location="Bắc Giang", area=2.0, owner="Nông dân")
        db.add(farm)
        db.commit()
        # 25 lô của ORG_A, 3 lô của ORG_B
        for i in range(25):
            b = Batch(
                farm_id=farm.id, product_name="Vải Thiều", quantity=10.0 + i,
                harvest_date=date(2026, 6, 1), organization_id=ORG_A, batch_code=f"VT{i:04d}",
            )
            db.add(b)
            db.flush()
            db.add(BatchCustodyHistory(batch_id=b.id, organization_id=ORG_A))
        for i in range(3):
            b = Batch(
                farm_id=farm.id, product_name="Nhãn", quantity=5.0,
                harvest_date=date(2026, 6, 1), organization_id=ORG_B, batch_code=f"NH{i:04d}",
            )
            db.add(b)
            db.flush()
            db.add(BatchCustodyHistory(batch_id=b.id, organization_id=ORG_B))
        db.commit()

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_requires_login(client):
    assert client.get("/batches").status_code == 401


def test_only_batches_held_by_requesting_org(client):
    resp = client.get("/batches", headers=_auth("user_b"))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert {b["product_name"] for b in data} == {"Nhãn"}


def test_default_page_size_is_20_and_newest_first(client):
    data = client.get("/batches", headers=_auth("user_a")).json()
    assert len(data) == 20
    ids = [b["id"] for b in data]
    assert ids == sorted(ids, reverse=True)


def test_cursor_pages_do_not_overlap_or_miss(client):
    first = client.get("/batches", headers=_auth("user_a")).json()
    cursor = first[-1]["id"]
    second = client.get(f"/batches?cursor={cursor}", headers=_auth("user_a")).json()
    ids = [b["id"] for b in first] + [b["id"] for b in second]
    assert len(ids) == 25
    assert len(set(ids)) == 25


def test_product_filter_and_search_still_work(client):
    assert client.get("/batches?product=Nhãn", headers=_auth("user_a")).json() == []
    found = client.get("/batches?search=vt0003", headers=_auth("user_a")).json()
    assert [b["batch_code"] for b in found] == ["VT0003"]


def test_handed_over_batch_disappears_from_sender(client):
    target = client.get("/batches?search=vt0000", headers=_auth("user_a")).json()[0]
    receiver_id = client.get("/batches", headers=_auth("user_b")).status_code  # đăng nhập được
    assert receiver_id == 200

    res = client.post(
        "/handovers",
        headers=_auth("user_a"),
        json={"batch_id": target["id"], "receiver_id": 2, "receiver_name": "HTX Lục Ngạn"},
    )
    assert res.status_code == 201, res.text
    handover_id = res.json()["id"]
    acc = client.post(f"/handovers/{handover_id}/accept", headers=_auth("user_b"), json={})
    assert acc.status_code == 200, acc.text

    holding_a = client.get("/batches?holding=true&limit=100", headers=_auth("user_a")).json()
    assert target["id"] not in [b["id"] for b in holding_a]
    holding_b = client.get("/batches?holding=true", headers=_auth("user_b")).json()
    assert target["id"] in [b["id"] for b in holding_b]
