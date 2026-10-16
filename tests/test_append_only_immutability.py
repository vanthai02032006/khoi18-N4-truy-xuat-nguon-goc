"""Test tích hợp: **CSDL từ chối** lệnh sửa/xoá trên bảng chỉ thêm.

Mục tiêu: khẳng định quy ước **bảng chỉ thêm (append-only)** được thực thi ở
tầng cơ sở dữ liệu, không phải bằng kiểm tra trong code nghiệp vụ.

Ba nhóm khẳng định:

1. Đường ghi hợp lệ vẫn chạy: ``INSERT`` và ``SELECT`` **thành công** với tài
   khoản ứng dụng.
2. ``UPDATE`` / ``DELETE`` phát ra từ **tài khoản ứng dụng** bị **từ chối ngay**
   (lớp 1 - quyền tài khoản: PostgreSQL ``REVOKE``; SQLite authorizer).
3. ``UPDATE`` / ``DELETE`` phát ra từ **kết nối toàn quyền** (giả lập tài khoản
   migration/admin) **vẫn bị từ chối** (lớp 2 - trigger ``RAISE(ABORT)``), kể cả
   khi gọi bằng ``sqlite3`` thuần, không qua SQLAlchemy.

Test chạy được trong CI trên SQLite (không cần PostgreSQL) nên luôn xanh ổn
định, và là chốt chặn tự động mỗi khi có ai **vô tình cấp lại quyền** sửa/xoá
cho bảng chỉ thêm.
"""

from __future__ import annotations

import sqlite3

from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.append_only import APPEND_ONLY_TABLES, is_append_only, trigger_names
from app.models import ROLE_ADMIN, Base, Batch, BatchEvent, Farm, User
from app.routers.batches import delete_batch
from app.routers.farms import delete_farm
from conftest import build_engine



def _count_events(engine) -> int:
    """Đếm số bản ghi nhật ký hiện có (đọc qua tài khoản ứng dụng)."""
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(BatchEvent)).scalar_one()


def _batch_exists(engine, batch_id: int) -> bool:
    """``True`` nếu lô vẫn còn trong CSDL (dùng để khẳng định xoá không xảy ra)."""
    with engine.connect() as connection:
        return connection.execute(
            select(func.count()).select_from(Batch).where(Batch.id == batch_id)
        ).scalar_one() > 0


def _event_payload(engine, event_id: int) -> str | None:
    """Đọc ``payload`` của một sự kiện để kiểm tra dữ liệu không bị sửa."""
    with engine.connect() as connection:
        return connection.execute(
            select(BatchEvent.payload).where(BatchEvent.id == event_id)
        ).scalar_one()


# ------------------------------------------- 1. Đường ghi hợp lệ vẫn hoạt động ---
def test_insert_va_select_thanh_cong_voi_tai_khoan_ung_dung(app_engine, seeded_event) -> None:
    """INSERT/SELECT trên bảng chỉ thêm phải chạy bình thường."""
    with Session(app_engine) as session:
        event = BatchEvent(
            batch_id=seeded_event["batch_id"],
            event_type="HANDOVER",
            payload='{"note": "Bàn giao cho đơn vị vận chuyển"}',
            actor="app_runtime_user",
            organization="HTX Nông Nghiệp Số 4",
            timestamp="2026-10-07T00:00:01+00:00",
            hash="c" * 64,
            previous_hash="a" * 64,
        )
        session.add(event)
        session.commit()  # INSERT không bị chặn

        assert event.id is not None
        assert _count_events(app_engine) == 2
        assert "Bàn giao cho đơn vị vận chuyển" in _event_payload(app_engine, event.id)


# --------------------------- 2. Tài khoản ứng dụng không được sửa/xoá nhật ký ---
def test_update_bi_tu_choi_voi_tai_khoan_ung_dung(app_engine, seeded_event) -> None:
    """``UPDATE`` từ tài khoản ứng dụng bị CSDL từ chối, dữ liệu giữ nguyên."""
    event_id = seeded_event["event_id"]

    with pytest.raises(DatabaseError):
        with app_engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE batch_events SET payload = '{\"t\": 25}' WHERE id = ?",
                (event_id,),
            )

    # Dữ liệu không bị thay đổi và CSDL vẫn dùng được cho câu lệnh hợp lệ.
    assert "Nhập kho lạnh 4.5°C" in _event_payload(app_engine, event_id)
    assert _count_events(app_engine) == 1


def test_delete_bi_tu_choi_voi_tai_khoan_ung_dung(app_engine, seeded_event) -> None:
    """``DELETE`` từ tài khoản ứng dụng bị CSDL từ chối, bản ghi còn nguyên."""
    event_id = seeded_event["event_id"]

    with pytest.raises(DatabaseError):
        with app_engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM batch_events WHERE id = ?", (event_id,))

    assert _count_events(app_engine) == 1
    assert "Nhập kho lạnh 4.5°C" in _event_payload(app_engine, event_id)


def test_delete_khong_dieu_kien_bi_tu_choi(app_engine, seeded_event) -> None:
    """``DELETE FROM batch_events`` (không ``WHERE``) cũng bị từ chối."""
    with pytest.raises(DatabaseError):
        with app_engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM batch_events")

    assert _count_events(app_engine) == 1


def test_orm_session_khong_sua_hoac_xoa_duoc_su_kien(app_engine, seeded_event) -> None:
    """Đường ORM (``Query.update`` / ``Session.delete``) cũng bị CSDL từ chối."""
    event_id = seeded_event["event_id"]

    with Session(app_engine) as session:
        with pytest.raises(DatabaseError):
            session.execute(
                update(BatchEvent).where(BatchEvent.id == event_id).values(payload="hacked")
            )
            session.commit()
        session.rollback()

    with Session(app_engine) as session:
        event = session.get(BatchEvent, event_id)
        assert event is not None
        with pytest.raises(DatabaseError):
            session.delete(event)
            session.commit()
        session.rollback()

    assert _count_events(app_engine) == 1


# ------------------- 3. Kết nối toàn quyền vẫn bị trigger chặn (van an toàn) ---
def test_update_bi_tu_choi_tren_ket_noi_toan_quyen(privileged_engine, seeded_event) -> None:
    """Kết nối toàn quyền (không qua authorizer) vẫn bị trigger từ chối UPDATE."""
    event_id = seeded_event["event_id"]

    with pytest.raises(DatabaseError):
        with privileged_engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE batch_events SET payload = 'sửa lén' WHERE id = ?",
                (event_id,),
            )

    assert "Nhập kho lạnh 4.5°C" in _event_payload(privileged_engine, event_id)


def test_delete_bi_tu_choi_tren_ket_noi_toan_quyen(privileged_engine, seeded_event) -> None:
    """Kết nối toàn quyền vẫn bị trigger từ chối DELETE - nhật ký không mất dấu vết."""
    event_id = seeded_event["event_id"]

    with pytest.raises(DatabaseError):
        with privileged_engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM batch_events WHERE id = ?", (event_id,))

    assert _count_events(privileged_engine) == 1


def test_ket_noi_sqlite_thuan_cung_bi_trigger_chan(db_file: Path, app_engine, seeded_event) -> None:
    """Gọi thẳng ``sqlite3`` (không qua SQLAlchemy) vẫn bị CSDL từ chối.

    Khẳng định lớp bảo vệ nằm trong chính file CSDL (trigger), nên không thể lách
    bằng cách đổi thư viện truy cập.
    """
    event_id = seeded_event["event_id"]

    connection = sqlite3.connect(db_file.as_posix())
    try:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE batch_events SET payload = 'sửa lén' WHERE id = ?", (event_id,))
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("DELETE FROM batch_events WHERE id = ?", (event_id,))
    finally:
        connection.close()

    assert "Nhập kho lạnh 4.5°C" in _event_payload(app_engine, event_id)


# ------------------------------------------------------------- Chốt chặn quy ước ---
def test_moi_bang_chi_them_deu_duoc_bao_ve_du_hai_lop(app_engine) -> None:
    """Mỗi bảng trong quy ước phải tồn tại và có đủ 2 trigger bảo vệ bất biến.

    Test này ngăn việc **vô tình cấp lại quyền sai**: thêm bảng vào danh sách
    nhưng quên bảng trong metadata, hoặc ai đó xoá trigger bảo vệ.
    """
    assert APPEND_ONLY_TABLES, "Quy ước bảng chỉ thêm phải khai báo ít nhất 1 bảng"

    with app_engine.connect() as connection:
        trigger_rows = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND name LIKE 'trg_prevent_%'"
            )
        ).scalars().all()

    existing_triggers = set(trigger_rows)

    for table in APPEND_ONLY_TABLES:
        assert is_append_only(table)
        assert table in Base.metadata.tables, f"Bảng {table} chưa khai báo trong models"
        for trigger in trigger_names(table):
            assert trigger in existing_triggers, f"Thiếu trigger bảo vệ {trigger}"


def test_thong_diep_tu_choi_neu_ro_quy_uoc(app_engine, seeded_event) -> None:
    """Thông điệp lỗi khi bị từ chối phải nói rõ bảng là bảng chỉ thêm."""
    with app_engine.connect() as connection:
        with pytest.raises(DatabaseError) as exc_info:
            connection.exec_driver_sql(
                "UPDATE batch_events SET payload = ?",
                ("sửa lén",),
            )

    assert "batch_events" in str(exc_info.value)


# ------------------------------------------- 4. Không ảnh hưởng bảng nghiệp vụ ---
def test_cac_bang_khac_van_sua_duoc(app_engine, seeded_event) -> None:
    """Quy ước bảng chỉ thêm không chặn nhầm việc **sửa** bảng nghiệp vụ khác."""
    with Session(app_engine) as session:
        farm = session.get(Farm, seeded_event["farm_id"])
        assert farm is not None
        farm.owner = "Hợp tác xã đổi tên"
        session.commit()  # UPDATE farms vẫn được phép
        assert session.get(Farm, seeded_event["farm_id"]).owner == "Hợp tác xã đổi tên"

    with app_engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE batches SET quantity = ? WHERE id = ?",
            (200.0, seeded_event["batch_id"]),
        )

    with Session(app_engine) as session:
        assert session.get(Batch, seeded_event["batch_id"]).quantity == 200.0


def test_xoa_lo_da_co_su_kien_bi_csdl_chan(app_engine, seeded_event) -> None:
    """Xoá thẳng lô đã có sự kiện bị **CSDL** chặn (lớp bảo vệ thứ hai).

    Không còn `cascade` trên quan hệ, SQLAlchemy sẽ cố gỡ khoá ngoại của sự kiện
    (``UPDATE batch_events SET batch_id = NULL``) - và lệnh UPDATE đó bị quy ước
    bảng chỉ thêm từ chối. Đây là lý do tầng API phải chặn sớm bằng **409**
    (xem các test ở mục 5) thay vì để lộ lỗi 500.
    """
    with Session(app_engine) as session:
        batch = session.get(Batch, seeded_event["batch_id"])
        assert batch is not None
        session.delete(batch)
        with pytest.raises(DatabaseError):
            session.commit()
        session.rollback()

    # Lô và sự kiện đều còn nguyên (không xoá dở dang).
    assert _batch_exists(app_engine, seeded_event["batch_id"])
    assert _count_events(app_engine) == 1


def test_bang_khong_thuoc_quy_uoc_khong_co_trigger_bao_ve(app_engine) -> None:
    """Chỉ bảng trong quy ước mới có trigger chặn - tránh bảo vệ nhầm."""
    with app_engine.connect() as connection:
        triggers = connection.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        ).scalars().all()

    # Gom tên bảng được bảo vệ từ chính tên trigger đang có trong CSDL.
    protected_tables = {
        table
        for table in APPEND_ONLY_TABLES
        for trigger in trigger_names(table)
        if trigger in triggers
    }

    assert protected_tables == set(APPEND_ONLY_TABLES)
    for table in ("farms", "batches", "users"):
        assert table not in protected_tables
        assert not is_append_only(table)
    assert not is_append_only(None)


def test_doc_du_lieu_sau_khi_bi_tu_choi_van_binh_thuong(db_file: Path, app_engine, seeded_event) -> None:
    """Sau khi thao tác bị từ chối, kết nối mới vẫn đọc được nhật ký bình thường."""
    engine = build_engine(db_file)
    try:
        with engine.connect() as connection:
            event_type = connection.execute(
                select(BatchEvent.event_type).where(BatchEvent.id == seeded_event["event_id"])
            ).scalar_one()
        assert event_type == "COLD_STORAGE"
    finally:
        engine.dispose()


# ------------- 5. Hệ quả: lô / vùng trồng đã có sự kiện thì không xoá được (409) ---
def _make_admin(session: Session) -> User:
    """Tạo tài khoản ``admin`` để truyền vào endpoint xoá (bỏ qua bước xác thực)."""
    admin = User(username="admin_xoa", password="a" * 64, role=ROLE_ADMIN)
    session.add(admin)
    session.commit()
    return admin


def test_khong_xoa_duoc_lo_da_co_su_kien(app_engine, seeded_event) -> None:
    """Xoá lô đã có sự kiện → **409** kèm hướng dẫn, nhật ký còn nguyên.

    Nếu không chặn sớm ở API, lệnh xoá sẽ kéo theo DELETE trên ``batch_events``
    và bị CSDL từ chối (lỗi 500 khó hiểu).
    """
    with Session(app_engine) as session:
        admin = _make_admin(session)

        with pytest.raises(HTTPException) as exc_info:
            delete_batch(batch_id=seeded_event["batch_id"], current_user=admin, db=session)

        assert exc_info.value.status_code == 409
        assert "bảng chỉ thêm" in exc_info.value.detail

    assert _count_events(app_engine) == 1
    assert _batch_exists(app_engine, seeded_event["batch_id"])


def test_khong_xoa_duoc_vung_troong_co_lo_mang_su_kien(app_engine, seeded_event) -> None:
    """Xoá vùng trồng mà lô của nó đã có sự kiện → **409** (tránh xoá dây chuyền)."""
    with Session(app_engine) as session:
        admin = _make_admin(session)

        with pytest.raises(HTTPException) as exc_info:
            delete_farm(farm_id=seeded_event["farm_id"], current_user=admin, db=session)

        assert exc_info.value.status_code == 409
        assert "bảng chỉ thêm" in exc_info.value.detail

    assert _count_events(app_engine) == 1
    assert _batch_exists(app_engine, seeded_event["batch_id"])


def test_van_xoa_duoc_lo_khong_co_su_kien(app_engine, seeded_event) -> None:
    """Lô **chưa có** sự kiện vẫn xoá bình thường - quy ước không chặn nhầm."""
    with Session(app_engine) as session:
        admin = _make_admin(session)
        clean_batch = Batch(
            farm_id=seeded_event["farm_id"],
            product_name="Lô chưa có sự kiện",
            quantity=10.0,
            harvest_date=date(2026, 9, 25),
        )
        session.add(clean_batch)
        session.commit()
        clean_id = clean_batch.id

    with Session(app_engine) as session:
        admin = session.scalars(select(User).where(User.username == "admin_xoa")).one()
        result = delete_batch(batch_id=clean_id, current_user=admin, db=session)
        assert result.deleted_id == clean_id

    assert not _batch_exists(app_engine, clean_id)

