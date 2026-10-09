"""Kiểm thử AC của SCRUM-49 (S-14): danh sách lô tổ chức đang giữ.

Khẳng định:
- Chỉ trả về lô mà tổ chức của request đang giữ (cách ly theo tổ chức).
- Sắp xếp mới nhất trước; mặc định 20 lô mỗi trang.
- Phân trang con trỏ: trang sau không trùng và không sót lô.
- Lô đã bàn giao sang tổ chức khác biến mất khỏi danh sách của bên giao.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, Farm

ORG_A = "HTX Bac Giang"
ORG_B = "HTX Luc Ngan"


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
        farm = Farm(name="Vùng Trồng", location="Bắc Giang", area=2.0, owner="Nông dân")
        db.add(farm)
        db.commit()
        # 25 lô của ORG_A, 3 lô của ORG_B
        for i in range(25):
            db.add(Batch(
                farm_id=farm.id, product_name="Vải Thiều", quantity=10.0 + i,
                harvest_date=date(2026, 6, 1), current_holder_org=ORG_A,
            ))
        for i in range(3):
            db.add(Batch(
                farm_id=farm.id, product_name="Nhãn", quantity=5.0,
                harvest_date=date(2026, 6, 1), current_holder_org=ORG_B,
            ))
        db.commit()

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _headers(org: str) -> dict:
    return {"X-Organization-Id": org}


def test_only_batches_held_by_requesting_org(client):
    resp = client.get("/batches", headers=_headers(ORG_B))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert all(b["current_holder_org"] == ORG_B for b in body)


def test_default_page_size_is_20_and_newest_first(client):
    resp = client.get("/batches", headers=_headers(ORG_A))
    body = resp.json()
    assert len(body) == 20
    ids = [b["id"] for b in body]
    assert ids == sorted(ids, reverse=True)


def test_cursor_pages_do_not_overlap_or_miss(client):
    seen = []
    cursor = None
    for _ in range(5):
        params = {"limit": 7}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/batches", params=params, headers=_headers(ORG_A)).json()
        if not page:
            break
        seen.extend(b["id"] for b in page)
        cursor = page[-1]["id"]
    assert len(seen) == 25
    assert len(set(seen)) == 25


def test_product_filter_and_search_still_work(client):
    resp = client.get("/batches", params={"product": "Vải"}, headers=_headers(ORG_A))
    assert all("Vải" in b["product_name"] for b in resp.json())
    resp = client.get("/batches", params={"product": "Nhãn"}, headers=_headers(ORG_A))
    assert resp.json() == []


def test_handed_over_batch_disappears_from_sender(client):
    first = client.get("/batches", headers=_headers(ORG_A)).json()[0]
    # Giả lập bàn giao: đổi tổ chức đang giữ sang ORG_B
    from app.database import get_db as _g  # noqa: F401 - giữ import rõ ràng
    db_gen = app.dependency_overrides[get_db]()
    db = next(db_gen)
    batch = db.get(Batch, first["id"])
    batch.current_holder_org = ORG_B
    db.commit()
    db.close()
    ids = [b["id"] for b in client.get("/batches", headers=_headers(ORG_A)).json()]
    assert first["id"] not in ids
