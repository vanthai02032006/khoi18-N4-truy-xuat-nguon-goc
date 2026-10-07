"""Fixture dùng chung cho bộ test backend.

Ghi chú về cách test các endpoint: pipeline CI chỉ cài ``pytest``/``flake8``
(**không** có ``httpx``), nên bộ test **không** dùng
``fastapi.testclient.TestClient``. Thay vào đó test gọi trực tiếp hàm endpoint
(kèm Session và tài khoản truyền tay) và gọi trực tiếp dependency kiểm quyền -
nhờ vậy vẫn khẳng định đúng mã lỗi ``403``/``400``/``422`` mà không phát sinh
phụ thuộc mới. Phần kiểm chứng end-to-end qua HTTP được thực hiện thủ công bằng
``uvicorn`` + ``curl`` và ghi vào PR.
"""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import ROLE_ADMIN, ROLE_FARMER, Base, Batch, Farm, User
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
def engine(db_file: Path) -> Iterator[Engine]:
    """Engine SQLite đã tạo bảng theo metadata của toàn bộ model."""
    engine = build_engine(db_file)
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    """Session SQLAlchemy của test; luôn đóng sau khi test xong."""
    with Session(engine) as session:
        yield session


@pytest.fixture()
def sender(session: Session) -> User:
    """Tài khoản **bên giao** (đóng vai chủ vùng trồng tạo phiếu bàn giao)."""
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
    """Tài khoản **bên nhận** - người duy nhất được xác nhận/từ chối phiếu."""
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
    """Tài khoản không liên quan tới phiếu (đây là ``admin``) - phải nhận 403."""
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
    """Lô nông sản có sẵn "tổ chức đang giữ" để kiểm tra việc đổi chủ sở hữu."""
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
