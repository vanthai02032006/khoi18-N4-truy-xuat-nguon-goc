"""Fixture dùng chung cho bộ test backend.

Mỗi test dùng file SQLite tạm (tmp_path) nên không chạm vào dữ liệu thật
backend/ttcs.db và tự dọn sau khi chạy.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.append_only import install_append_only_protection
from app.models import ROLE_ADMIN, ROLE_FARMER, Base, Batch, BatchEvent, Farm, User
from app.security import hash_password


def build_engine(db_file: Path) -> Engine:
    """Tạo engine SQLite trỏ tới một file CSDL cụ thể."""
    return create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )


@pytest.fixture()
def db_file(tmp_path: Path) -> Path:
    """Đường dẫn file CSDL tạm, riêng cho từng test."""
    return tmp_path / "ttcs_test.db"


@pytest.fixture()
def app_engine(db_file: Path) -> Iterator[Engine]:
    """Engine của tài khoản ứng dụng: đã bật cả 2 lớp bảo vệ bảng chỉ thêm."""
    engine = build_engine(db_file)
    Base.metadata.create_all(bind=engine)
    install_append_only_protection(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def privileged_engine(app_engine: Engine, db_file: Path) -> Iterator[Engine]:
    """Engine toàn quyền - giả lập tài khoản migration/admin của CSDL."""
    engine = build_engine(db_file)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def seeded_event(app_engine: Engine) -> dict[str, int]:
    """Tạo sẵn 1 vùng trồng + 1 lô + 1 sự kiện nhật ký, trả về id của chúng."""
    with Session(app_engine) as session:
        farm = Farm(
            name="Vùng trồng kiểm thử bất biến",
            location="Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp",
            area=2.5,
            owner="Hợp tác xã kiểm thử",
        )
        session.add(farm)
        session.flush()

        batch = Batch(
            farm_id=farm.id,
            product_name="Xoài Cát Chu (kiểm thử)",
            quantity=120.0,
            harvest_date=date(2026, 9, 25),
        )
        session.add(batch)
        session.flush()

        event = BatchEvent(
            batch_id=batch.id,
            event_type="COLD_STORAGE",
            payload='{"note": "Nhập kho lạnh 4.5°C", "temperature": 4.5}',
            actor="app_runtime_user",
            organization="HTX Nông Nghiệp Số 4",
            timestamp="2026-10-07T00:00:00+00:00",
            hash="a" * 64,
            previous_hash="0" * 64,
        )
        session.add(event)
        session.commit()

        return {"farm_id": farm.id, "batch_id": batch.id, "event_id": event.id}


@pytest.fixture()
def engine(db_file: Path) -> Iterator[Engine]:
    """Engine SQLite đã tạo bảng theo metadata của toàn bộ model."""
    eng = build_engine(db_file)
    Base.metadata.create_all(bind=eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    """Session SQLAlchemy của test; luôn đóng sau khi test xong."""
    with Session(engine) as session:
        yield session


@pytest.fixture()
def sender(session: Session) -> User:
    """Tài khoản bên giao."""
    user = User(
        username="htx_xoai",
        password=hash_password("123456"),
        role=ROLE_FARMER,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def receiver(session: Session) -> User:
    """Tài khoản bên nhận - người duy nhất được xác nhận/từ chối phiếu."""
    user = User(
        username="cty_mekong",
        password=hash_password("123456"),
        role=ROLE_FARMER,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def stranger(session: Session) -> User:
    """Tài khoản không liên quan tới phiếu - nhận 403."""
    user = User(
        username="admin_xa",
        password=hash_password("123456"),
        role=ROLE_ADMIN,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def batch(session: Session, sender: User) -> Batch:
    """Lô nông sản có sẵn để kiểm tra việc đổi chủ sở hữu."""
    farm = Farm(
        name="Vùng trồng xoài Cao Lãnh",
        location="Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp",
        area=4.5,
        owner="Hợp tác xã Xoài Mỹ Xương",
    )
    session.add(farm)
    session.flush()

    batch = Batch(
        farm_id=farm.id,
        product_name="Xoài Cát Chu",
        quantity=1500.0,
        harvest_date=date(2026, 9, 25),
        current_owner="Hợp tác xã Xoài Mỹ Xương",
    )
    session.add(batch)
    session.commit()
    return batch
