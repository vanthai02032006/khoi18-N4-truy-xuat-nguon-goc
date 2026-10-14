"""S-32 / SCRUM-99: nhập danh mục sản phẩm và thửa từ CSV - tất cả hoặc không."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import ROLE_ADMIN, ROLE_FARMER, Farm, Product, User
from app.security import hash_password

ADMIN = ("quan_tri", "123456")
FARMER = ("nong_ho", "123456")
CSV = {"Content-Type": "text/csv"}


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Session() as db:
        db.add_all([
            User(username=ADMIN[0], password=hash_password(ADMIN[1]), role=ROLE_ADMIN),
            User(username=FARMER[0], password=hash_password(FARMER[1]), role=ROLE_FARMER),
            Product(name="Xoài Cát Chu", unit="kg"),
        ])
        db.commit()

    def override():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override
    yield {"client": TestClient(app), "Session": Session}
    app.dependency_overrides.clear()


def _count(env, model) -> int:
    with env["Session"]() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_valid_file_creates_every_row(env):
    body = "﻿name,unit,description\nThanh Long,kg,Ruột đỏ\nBưởi Năm Roi,piece,\n"
    res = env["client"].post("/imports/products", content=body.encode(), headers=CSV, auth=ADMIN)
    assert res.status_code == 201, res.text
    assert res.json()["created"] == 2
    assert _count(env, Product) == 3


def test_one_bad_row_creates_nothing_and_reports_row_and_column(env):
    body = "name,unit\nThanh Long,kg\nSầu Riêng,thung\nxoài cát chu,kg\nThanh Long,kg\n"
    res = env["client"].post("/imports/products", content=body.encode(), headers=CSV, auth=ADMIN)
    assert res.status_code == 422
    errors = res.json()["detail"]["errors"]
    assert {"row": 3, "column": "unit"} in [{k: e[k] for k in ("row", "column")} for e in errors]
    assert any(e["row"] == 4 and e["column"] == "name" for e in errors)  # trùng danh mục
    assert any(e["row"] == 5 and "Trùng với dòng 2" in e["message"] for e in errors)
    assert _count(env, Product) == 1  # không dòng nào được tạo


def test_farm_import_validates_area_and_is_all_or_nothing(env):
    bad = "name,location,area,owner\nThửa 1,Cao Lãnh,2.5,HTX A\nThửa 2,Cao Lãnh,-1,HTX A\nThửa 3,,abc,HTX A\n"
    res = env["client"].post("/imports/farms", content=bad.encode(), headers=CSV, auth=ADMIN)
    assert res.status_code == 422
    cells = {(e["row"], e["column"]) for e in res.json()["detail"]["errors"]}
    assert cells == {(3, "area"), (4, "location"), (4, "area")}
    assert _count(env, Farm) == 0
    good = "name,location,area,owner\nThửa 1,Cao Lãnh,\"2,5\",HTX A\n"
    assert env["client"].post("/imports/farms", content=good.encode(), headers=CSV, auth=ADMIN).json()["created"] == 1


def test_only_admin_and_size_limit(env):
    assert env["client"].post("/imports/products", content=b"name,unit\nA,kg\n", headers=CSV, auth=FARMER).status_code == 403
    big = b"name,unit\n" + b"x" * 1_100_000
    assert env["client"].post("/imports/products", content=big, headers=CSV, auth=ADMIN).status_code == 413
