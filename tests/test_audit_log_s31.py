"""S-31 / SCRUM-90: nhật ký thao tác - ai làm gì, lúc nào, từ địa chỉ nào."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.append_only import install_append_only_protection
from app.database import Base, get_db
from app.main import app
from app.models import ROLE_ADMIN, ROLE_FARMER, Farm, User
from app.security import hash_password

ADMIN = ("quan_tri", "123456")
FARMER = ("nong_ho", "123456")


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    install_append_only_protection(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Session() as db:
        db.add_all([
            User(username=ADMIN[0], password=hash_password(ADMIN[1]), role=ROLE_ADMIN, organization_id="ORG_ADMIN"),
            User(username=FARMER[0], password=hash_password(FARMER[1]), role=ROLE_FARMER, organization_id="ORG_MY_XUONG"),
        ])
        db.add(Farm(name="Thửa 1", location="Cao Lãnh", area=1.0, owner="Nông hộ"))
        db.commit()

    def override():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override
    yield {"client": TestClient(app), "engine": engine}
    app.dependency_overrides.clear()


def test_login_create_split_are_logged_with_user_org_ip_and_object(env):
    c = env["client"]
    assert c.post("/auth/login", json={"username": FARMER[0], "password": FARMER[1]}).status_code == 200
    created = c.post("/batches", auth=FARMER, json={
        "farm_id": 1, "product_name": "Xoài", "quantity": 100, "harvest_date": str(date.today())})
    assert created.status_code == 201, created.text
    batch = created.json()
    split = c.post(f"/batches/{batch['id']}/split", auth=FARMER, json={"items": [{"quantity": 30}, {"quantity": 20}]})
    assert split.status_code in (200, 201), split.text

    rows = c.get("/audit-logs", auth=ADMIN).json()
    mine = [r for r in rows if r["username"] == FARMER[0]]
    actions = [r["action"] for r in reversed(mine)]
    assert actions == ["Đăng nhập", "Tạo lô", "Tách lô"]
    for r in mine:
        assert r["organization_id"] == "ORG_MY_XUONG"
        assert r["ip_address"]
        assert r["created_at"]
    by_action = {r["action"]: r for r in mine}
    assert by_action["Tạo lô"]["object_id"] == batch["batch_code"]
    assert by_action["Tách lô"]["object_id"] == str(batch["id"])


def test_failed_login_is_logged_with_reason_and_no_password(env):
    c = env["client"]
    assert c.post("/auth/login", json={"username": FARMER[0], "password": "sai-mat-khau"}).status_code == 401
    rows = c.get("/audit-logs?only_failed=true", auth=ADMIN).json()
    assert rows[0]["action"] == "Đăng nhập thất bại"
    assert "Sai" in rows[0]["detail"]
    assert all("sai-mat-khau" not in str(v) for v in rows[0].values())


def test_only_admin_can_read_and_log_is_append_only(env):
    c = env["client"]
    assert c.get("/audit-logs", auth=FARMER).status_code == 403
    c.post("/auth/login", json={"username": FARMER[0], "password": FARMER[1]})
    with pytest.raises(DatabaseError):
        with env["engine"].begin() as conn:
            conn.execute(text("UPDATE audit_logs SET username = 'gia_mao'"))
