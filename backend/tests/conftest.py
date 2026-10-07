"""Fixture dùng chung cho bộ test backend.

Mỗi test dùng **file SQLite tạm** (``tmp_path``) nên không chạm vào dữ liệu thật
``backend/ttcs.db`` và tự dọn sau khi chạy - nhờ vậy test chạy được trong CI mà
không cần dịch vụ PostgreSQL.
"""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.append_only import install_append_only_protection
from app.models import Base, Batch, BatchEvent, Farm


def build_engine(db_file: Path) -> Engine:
    """Tạo engine SQLite trỏ tới một file CSDL cụ thể.

    ``check_same_thread=False`` để nhiều thread (giống FastAPI) dùng chung engine.
    """
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
    """Engine của **tài khoản ứng dụng**: đã bật cả 2 lớp bảo vệ bảng chỉ thêm.

    Gồm authorizer từ chối UPDATE/DELETE (lớp 1) và trigger ``RAISE(ABORT)``
    (lớp 2) - giống cấu hình của app khi khởi động qua ``init_db()``.
    """
    engine = build_engine(db_file)
    Base.metadata.create_all(bind=engine)
    install_append_only_protection(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def privileged_engine(app_engine: Engine, db_file: Path) -> Iterator[Engine]:
    """Engine **toàn quyền** - giả lập tài khoản migration/admin của CSDL.

    Cố tình **không** gắn authorizer nên chỉ còn trigger bảo vệ. Fixture này
    dùng để chứng minh lớp bảo vệ 2 chặn được cả những kết nối có toàn quyền.
    """
    engine = build_engine(db_file)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def seeded_event(app_engine: Engine) -> dict[str, int]:
    """Tạo sẵn 1 vùng trồng + 1 lô + 1 sự kiện nhật ký, trả về id của chúng.

    Toàn bộ dữ liệu được ghi qua **tài khoản ứng dụng** để khẳng định đường ghi
    hợp lệ (SELECT/INSERT) vẫn hoạt động bình thường.
    """
    with Session(app_engine) as session:
        farm = Farm(
            name="Vùng trồng kiểm thử bất biến",
            location="Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp",
            area=2.5,
            owner="Hợp tác xã kiểm thử",
        )
        session.add(farm)
        session.flush()  # lấy farm.id trước khi tạo lô

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
