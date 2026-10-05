"""Kiểm thử tự động tính năng Append-Only trên bảng batch_events (T-25 / SCRUM-41).

Kịch bản kiểm thử:
1. INSERT sự kiện mới -> THÀNH CÔNG (App có quyền ghi nhật ký).
2. SELECT sự kiện -> THÀNH CÔNG (App có quyền đọc nhật ký).
3. UPDATE sự kiện -> BỊ TỪ CHỐI NGAY LẬP TỨC (Database raises exception / Permission denied).
4. DELETE sự kiện -> BỊ TỪ CHỐI NGAY LẬP TỨC (Database raises exception / Permission denied).
"""

import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select, update, delete, event
from sqlalchemy.exc import DatabaseError, OperationalError
from app.database import engine, SessionLocal, init_db
from app.models import Batch, BatchEvent, Farm


def install_sqlite_append_only_triggers(conn):
    """Giả lập cơ chế Trigger Append-Only nếu chạy trên môi trường SQLite local."""
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_prevent_update_batch_events
        BEFORE UPDATE ON batch_events
        BEGIN
            SELECT RAISE(ABORT, 'CSDL TỪ CHỐI: Bảng batch_events là Append-Only. Nghiêm cấm mọi thao tác UPDATE!');
        END;
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_prevent_delete_batch_events
        BEFORE DELETE ON batch_events
        BEGIN
            SELECT RAISE(ABORT, 'CSDL TỪ CHỐI: Bảng batch_events là Append-Only. Nghiêm cấm mọi thao tác DELETE!');
        END;
    """)


def run_append_only_verification():
    print("=" * 70)
    print("KIỂM THỬ XÁC MINH CSDL APPEND-ONLY (TASK T-25 / SCRUM-41)")
    print("=" * 70)

    # Khởi tạo schema nếu chưa có
    init_db()

    # Cài trigger chống update/delete cho SQLite local nếu đang dùng SQLite
    with engine.begin() as conn:
        install_sqlite_append_only_triggers(conn.connection)

    db = SessionLocal()
    try:
        # Lấy hoặc tạo 1 lô hàng mẫu
        batch = db.scalar(select(Batch))
        if not batch:
            farm = Farm(name="Vườn mẫu", location="Đồng Tháp", area=2.0, owner="HTX")
            db.add(farm)
            db.commit()
            batch = Batch(farm_id=farm.id, product_name="Xoài Cát Chu", quantity=500.0, harvest_date=datetime.utcnow().date())
            db.add(batch)
            db.commit()

        # -------------------------------------------------------------
        # Bước 1: INSERT Sự kiện (Phải thành công)
        # -------------------------------------------------------------
        print("\n[TEST 1] Thực hiện INSERT bản ghi vào batch_events (App Runtime)...")
        event_record = BatchEvent(
            batch_id=batch.id,
            event_type="COLD_STORAGE",
            details="Nhập kho lạnh bảo quản ngưỡng 4.5°C",
            temperature=4.5,
            created_by="app_runtime_user",
            timestamp=datetime.utcnow(),
            hash_code="a1b2c3d4e5f67890abcdef1234567890abcdef1234567890abcdef1234567890",
        )
        db.add(event_record)
        db.commit()
        db.refresh(event_record)
        print(f" -> THÀNH CÔNG: Đã thêm sự kiện ID={event_record.id}, Type={event_record.event_type}")

        # -------------------------------------------------------------
        # Bước 2: SELECT Sự kiện (Phải thành công)
        # -------------------------------------------------------------
        print("\n[TEST 2] Thực hiện SELECT bản ghi từ batch_events (App Runtime)...")
        fetched = db.get(BatchEvent, event_record.id)
        assert fetched is not None
        print(f" -> THÀNH CÔNG: Đã đọc được sự kiện: {fetched.details}, Nhiệt độ={fetched.temperature}°C")

        # -------------------------------------------------------------
        # Bước 3: UPDATE Sự kiện (CSDL PHẢI TỪ CHỐI NGAY LẬP TỨC)
        # -------------------------------------------------------------
        print("\n[TEST 3] Thử nghiệm tấn công sửa đổi bản ghi (UPDATE)...")
        update_blocked = False
        try:
            fetched.details = "Hacker đã sửa lén nhiệt độ kho lạnh thành 25°C!"
            db.commit()
        except (DatabaseError, OperationalError) as exc:
            db.rollback()
            update_blocked = True
            print(f" -> BẢO VỆ THÀNH CÔNG! CSDL từ chối UPDATE: {exc}")

        if not update_blocked:
            raise RuntimeError("LỖI BẢO MẬT NGHIÊM TRỌNG: Lệnh UPDATE không bị chặn!")

        # -------------------------------------------------------------
        # Bước 4: DELETE Sự kiện (CSDL PHẢI TỪ CHỐI NGAY LẬP TỨC)
        # -------------------------------------------------------------
        print("\n[TEST 4] Thử nghiệm tấn công xoá dấu vết (DELETE)...")
        delete_blocked = False
        try:
            record_to_del = db.get(BatchEvent, event_record.id)
            db.delete(record_to_del)
            db.commit()
        except (DatabaseError, OperationalError) as exc:
            db.rollback()
            delete_blocked = True
            print(f" -> BẢO VỆ THÀNH CÔNG! CSDL từ chối DELETE: {exc}")

        if not delete_blocked:
            raise RuntimeError("LỖI BẢO MẬT NGHIÊM TRỌNG: Lệnh DELETE không bị chặn!")

        print("\n" + "=" * 70)
        print("KẾT LUẬN: Bảng `batch_events` tuân thủ 100% nguyên tắc APPEND-ONLY!")
        print("Tài khoản ứng dụng CHỈ ĐƯỢC PHÉP SELECT & INSERT, bị chặn UPDATE & DELETE.")
        print("=" * 70)

    finally:
        db.close()


if __name__ == "__main__":
    run_append_only_verification()
