"""S-24 / T-56, T-57 (SCRUM-72, SCRUM-73, SCRUM-85): bàn giao chờ quá 48 giờ.

AC:
- Bàn giao chờ quá 48 giờ -> job gắn nhãn quá hạn, hiện ở danh sách của cả hai bên.
- Bên nhận xác nhận sau khi quá hạn -> vẫn thành công, sự kiện ghi rõ xác nhận muộn.
- Quá hạn chỉ đánh dấu, không tự huỷ; ngưỡng 48 giờ là tham số cấu hình.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.jobs.handover_overdue import scan_and_flag_overdue_handovers
from app.main import app
from app.models import ROLE_FARMER, Batch, BatchEvent, Farm, Handover, User
from app.security import hash_password

SENDER = ("vung_trong", "123456")
RECEIVER = ("htx_so_che", "123456")


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with SessionLocal() as db:
        db.add_all([
            User(username=SENDER[0], password=hash_password(SENDER[1]), role=ROLE_FARMER, organization_id="ORG_VUNG_TRONG"),
            User(username=RECEIVER[0], password=hash_password(RECEIVER[1]), role=ROLE_FARMER, organization_id="ORG_HTX"),
        ])
        farm = Farm(name="Thửa A", location="Cao Lãnh", area=1.0, owner="Vùng trồng")
        db.add(farm)
        db.flush()
        batch = Batch(farm_id=farm.id, product_name="Xoài", quantity=100.0, harvest_date=date(2026, 10, 1),
                      organization_id="ORG_VUNG_TRONG", batch_code="XOAI0001")
        db.add(batch)
        db.commit()
        batch_id = batch.id

    def override():
        with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    res = client.post("/handovers", auth=SENDER,
                      json={"batch_id": batch_id, "receiver_id": 2, "receiver_name": "HTX Sơ Chế"})
    assert res.status_code == 201, res.text
    yield {"client": client, "Session": SessionLocal, "handover_id": res.json()["id"]}
    app.dependency_overrides.clear()


def _age(env, hours: int) -> None:
    with env["Session"]() as db:
        h = db.get(Handover, env["handover_id"])
        h.created_at = datetime.utcnow() - timedelta(hours=hours)
        db.commit()


def test_job_flags_only_pending_older_than_threshold(env):
    _age(env, 47)
    with env["Session"]() as db:
        assert scan_and_flag_overdue_handovers(db) == 0
    _age(env, 49)
    with env["Session"]() as db:
        assert scan_and_flag_overdue_handovers(db) == 1
        assert scan_and_flag_overdue_handovers(db) == 0  # chạy lại không gắn lần hai
        h = db.get(Handover, env["handover_id"])
        assert h.is_overdue is True
        assert h.status == "pending"  # chỉ đánh dấu, không tự huỷ


def test_overdue_label_visible_to_both_sides(env):
    _age(env, 49)
    for who in (SENDER, RECEIVER):
        rows = env["client"].get("/handovers", auth=who).json()
        assert [r["is_overdue"] for r in rows] == [True]


def test_late_confirmation_still_succeeds_and_is_recorded(env):
    _age(env, 72)
    with env["Session"]() as db:
        scan_and_flag_overdue_handovers(db)
    res = env["client"].post(f"/handovers/{env['handover_id']}/accept", auth=RECEIVER, json={})
    assert res.status_code == 200, res.text
    with env["Session"]() as db:
        ev = db.scalars(select(BatchEvent).where(BatchEvent.event_type == "HANDOVER_ACCEPTED")).one()
        assert json.loads(ev.payload)["late"] is True


def test_threshold_is_configurable(env, monkeypatch):
    monkeypatch.setenv("HANDOVER_OVERDUE_HOURS", "12")
    _age(env, 13)
    with env["Session"]() as db:
        assert scan_and_flag_overdue_handovers(db) == 1
