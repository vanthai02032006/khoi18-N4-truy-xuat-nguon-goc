"""Tests for marking batches as sold out and blocking further movement."""

from __future__ import annotations

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
    ROLE_FARMER,
    User,
)
from app.security import hash_password


@pytest.fixture
def consumption_env():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as db:
        user = User(
            username="farmer_consumption_test",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        farm = Farm(
            name="Vùng trồng kiểm thử",
            location="Đồng Tháp",
            area=2.0,
            owner="HTX Nông Nghiệp Số 4",
        )
        db.add_all([user, farm])
        db.flush()
        batch = Batch(
            farm_id=farm.id,
            product_name="Xoài Cát Chu",
            quantity=25.5,
            harvest_date=date(2026, 6, 1),
        )
        db.add(batch)
        db.commit()
        batch_id = batch.id

    def override_get_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    yield {
        "client": TestClient(app),
        "batch_id": batch_id,
        "session_factory": session_factory,
    }
    app.dependency_overrides.clear()
    engine.dispose()


def test_mark_batch_consumed_updates_quantity_status_and_event(consumption_env):
    client = consumption_env["client"]
    batch_id = consumption_env["batch_id"]

    response = client.post(
        f"/batches/{batch_id}/consume",
        auth=("farmer_consumption_test", "123456"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["quantity"] == 0
    assert response.json()["status"] == BATCH_STATUS_CONSUMED

    with consumption_env["session_factory"]() as db:
        batch = db.get(Batch, batch_id)
        events = list(
            db.scalars(
                select(BatchEvent)
                .where(BatchEvent.batch_id == batch_id)
                .order_by(BatchEvent.id)
            ).all()
        )
        assert batch is not None
        assert batch.quantity == 0
        assert batch.status == BATCH_STATUS_CONSUMED
        assert len(events) == 1
        assert events[0].event_type == "BATCH_CONSUMED"
        assert events[0].previous_hash == "0" * 64


def test_consumed_batch_cannot_be_split_or_handed_over(consumption_env):
    client = consumption_env["client"]
    batch_id = consumption_env["batch_id"]
    auth = ("farmer_consumption_test", "123456")

    consumed = client.post(f"/batches/{batch_id}/consume", auth=auth)
    assert consumed.status_code == 200, consumed.text
    repeated = client.post(f"/batches/{batch_id}/consume", auth=auth)
    assert repeated.status_code == 400

    split = client.post(
        f"/batches/{batch_id}/split",
        json={"items": [{"quantity": 1, "note": "không được tách"}]},
        auth=auth,
    )
    assert split.status_code == 400

    batch_handover = client.post(
        f"/batches/{batch_id}/handover/initiate",
        json={"target_organization": "Bên nhận"},
        auth=auth,
    )
    assert batch_handover.status_code == 400

    handover = client.post(
        "/handovers",
        json={"batch_id": batch_id, "receiver_name": "Bên nhận"},
        auth=auth,
    )
    assert handover.status_code == 400

    with consumption_env["session_factory"]() as db:
        assert db.scalar(
            select(BatchEvent.id).where(
                BatchEvent.batch_id == batch_id,
                BatchEvent.event_type == "BATCH_CONSUMED",
            )
        ) is not None
        assert db.get(Batch, batch_id).quantity == 0
