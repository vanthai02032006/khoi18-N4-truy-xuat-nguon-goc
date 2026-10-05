"""Script kiểm thử nghiệm thu T-29 (SCRUM-45): Cơ chế chống sửa lén bản ghi chuỗi sự kiện lô nông sản.

Kịch bản:
- Dựng lô nông sản có 10 sự kiện liên tiếp theo chuỗi cung ứng lạnh chuẩn.
- Lần lượt chạy 3 ca:
  1. Ca 1: Sửa lén 1 bản ghi bằng SQL trực tiếp -> Kiểm tra phát hiện sửa lén (DATA_MODIFIED).
  2. Ca 2: Xoá 1 bản ghi bằng SQL trực tiếp -> Kiểm tra phát hiện đứt chuỗi / xoá bản ghi (RECORD_DELETED_OR_CHAIN_BROKEN).
  3. Ca 3: Giữ nguyên vẹn 10 sự kiện -> Kiểm tra xác nhận toàn vẹn 100% (VERIFIED).
- Tự động dọn dẹp sạch sẽ sau mỗi ca, đảm bảo chạy lại được nhiều lần mà không cần can thiệp thủ công.
"""

from __future__ import annotations

import sys
from datetime import date
from typing import Generator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine, init_db
from app.event_chain import (
    GENESIS_HASH,
    TamperType,
    build_10_events_for_batch,
    record_batch_event,
    verify_batch_events_integrity,
)
from app.models import Batch, BatchEvent, Farm


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """Tạo session database cho kiểm thử pytest."""
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_test_db() -> Session:
    """Tạo session database cho kiểm thử."""
    return SessionLocal()


def create_test_batch(db: Session, label: str) -> Batch:
    """Tạo một lô nông sản độc lập phục vụ kiểm thử."""
    # Tìm hoặc tạo farm demo
    farm = db.query(Farm).first()
    if not farm:
        farm = Farm(
            name="Vùng trồng Thử nghiệm T-29",
            location="Đồng Tháp",
            area=5.0,
            owner="HTX Nông Nghiệp Số",
        )
        db.add(farm)
        db.commit()
        db.refresh(farm)

    batch = Batch(
        farm_id=farm.id,
        product_name=f"Xoài Cát Chu Test T-29 ({label})",
        quantity=1500.0,
        harvest_date=date(2026, 9, 25),
        batch_code=f"LOT-TEST-T29-{label}",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


def cleanup_test_batch(db: Session, batch_id: int) -> None:
    """Dọn dẹp sạch sẽ dữ liệu lô và sự kiện thử nghiệm để tái sử dụng."""
    try:
        db.query(BatchEvent).filter(BatchEvent.batch_id == batch_id).delete()
        db.query(Batch).filter(Batch.id == batch_id).delete()
        db.commit()
    except Exception:
        db.rollback()


def test_case_1_sql_data_tampered(db: Session) -> None:
    """Ca 1: Sửa lén 1 bản ghi bằng SQL trực tiếp -> Khẳng định phát hiện sửa lén."""
    print("\n" + "=" * 70)
    print("--- [CA 1] SỬA LÉN 1 BẢN GHI BẰNG SQL TRỰC TIẾP ---")
    print("=" * 70)

    batch = create_test_batch(db, "CASE1_TAMPER")
    try:
        # 1. Dựng 10 sự kiện chuẩn
        events = build_10_events_for_batch(db, batch.id)
        assert len(events) == 10, f"Mong đợi 10 sự kiện, nhận được {len(events)}"
        print(f"[OK] Đã dựng thành công 10 sự kiện nối chuỗi băm mật mã cho Lô #{batch.id}.")

        # 2. Chọn bản ghi thứ 6 (sự kiện TEMPERATURE_LOG) để sửa lén bằng SQL
        target_event = events[5]  # Index 5 là sự kiện thứ 6 (sequence = 6)
        target_id = target_event.id
        original_data = target_event.data
        original_hash = target_event.hash

        print(f"Bản ghi mục tiêu #{target_id} (thứ tự sequence {target_event.sequence}):")
        print(f"  • Loại sự kiện: {target_event.event_type}")
        print(f"  • Dữ liệu gốc: {original_data}")
        print(f"  • Hash gốc: {original_hash}")

        # Giả lập kẻ tấn công/admin xấu chạy câu lệnh SQL UPDATE trực tiếp vào database
        # Sửa nhiệt độ từ 3.8°C thành 15.0°C (vi phạm chuỗi lạnh nghiêm trọng) mà không cập nhật hash
        tampered_data = '{"stage": "Giám sát chuỗi lạnh", "sensor_id": "SN-TEMP-04", "temp_c": 15.0, "status": "TAMPERED_BY_SQL"}'
        sql_statement = text("UPDATE batch_events SET data = :new_data WHERE id = :event_id")
        db.execute(sql_statement, {"new_data": tampered_data, "event_id": target_id})
        db.commit()
        print(f"[CẢNH BÁO GIẢ LẬP] Đã can thiệp SQL trực tiếp: sửa data của sự kiện #{target_id}.")

        # 3. Chạy hàm kiểm tra tính toàn vẹn
        report = verify_batch_events_integrity(db, batch.id)

        print("\nKẾT QUẢ KIỂM TRA TÍNH TOÀN VẸN:")
        print(f"  • is_valid: {report.is_valid}")
        print(f"  • status: {report.status}")
        print(f"  • tamper_type: {report.tamper_type}")
        print(f"  • tampered_event_id: {report.tampered_event_id}")
        print(f"  • tampered_sequence: {report.tampered_sequence}")
        print(f"  • recorded_hash: {report.recorded_hash}")
        print(f"  • expected_hash: {report.expected_hash}")
        print(f"  • Chi tiết: {report.detail}")

        # 4. Khẳng định nghiệm thu (Assertions)
        assert report.is_valid is False, "Lỗi: Hệ thống không phát hiện ra hành vi sửa lén dữ liệu!"
        assert report.status == "TAMPERED", f"Mong đợi status 'TAMPERED', nhận được {report.status}"
        assert report.tamper_type == TamperType.DATA_MODIFIED.value, (
            f"Mong đợi tamper_type '{TamperType.DATA_MODIFIED.value}', nhận được {report.tamper_type}"
        )
        assert report.tampered_event_id == target_id, (
            f"Mong đợi ID bị sửa là {target_id}, nhận được {report.tampered_event_id}"
        )
        assert report.tampered_sequence == 6, (
            f"Mong đợi sequence bị sửa là 6, nhận được {report.tampered_sequence}"
        )
        assert report.recorded_hash != report.expected_hash, (
            "Mã băm lưu trữ và mã băm tính toán lại phải khác nhau khi dữ liệu bị sửa lén!"
        )

        print("\n[BẰNG CHỨNG NGHIỆM THU CA 1]")
        print(">> Cơ chế chống sửa lén T-29 đã bắt được chính xác 100% hành vi sửa lén SQL:")
        print(f"   - Bản ghi bị sửa: #{report.tampered_event_id} (thứ tự {report.tampered_sequence})")
        print(f"   - Hash lưu trữ:     {report.recorded_hash}")
        print(f"   - Hash nội dung mới: {report.expected_hash}")
        print("=> [PASS CA 1 THÀNH CÔNG RỰC RỠ]")

    finally:
        cleanup_test_batch(db, batch.id)
        print(f"[CLEANUP] Đã tự động dọn dẹp sạch sẽ Lô #{batch.id}.")


def test_case_2_sql_record_deleted(db: Session) -> None:
    """Ca 2: Xoá 1 bản ghi bằng SQL trực tiếp -> Khẳng định phát hiện đứt gãy chuỗi."""
    print("\n" + "=" * 70)
    print("--- [CA 2] XOÁ 1 BẢN GHI BẰNG SQL TRỰC TIẾP ---")
    print("=" * 70)

    batch = create_test_batch(db, "CASE2_DELETE")
    try:
        # 1. Dựng 10 sự kiện chuẩn
        events = build_10_events_for_batch(db, batch.id)
        assert len(events) == 10, f"Mong đợi 10 sự kiện, nhận được {len(events)}"
        print(f"[OK] Đã dựng thành công 10 sự kiện nối chuỗi băm mật mã cho Lô #{batch.id}.")

        # 2. Chọn bản ghi thứ 5 (sự kiện COLD_STORAGE_IN) để xoá bằng SQL
        deleted_event = events[4]  # Index 4 là sự kiện thứ 5 (sequence = 5)
        deleted_id = deleted_event.id
        deleted_sequence = deleted_event.sequence
        deleted_hash = deleted_event.hash

        subsequent_event = events[5]  # Bản ghi thứ 6 liền sau
        subsequent_id = subsequent_event.id

        print(f"Bản ghi bị xoá thủ tiêu dấu vết #{deleted_id} (thứ tự sequence {deleted_sequence}):")
        print(f"  • Loại sự kiện: {deleted_event.event_type}")
        print(f"  • Hash của bản ghi bị xoá: {deleted_hash}")
        print(f"  • Bản ghi liền sau #{subsequent_id} đang trỏ prev_hash về hash trên.")

        # Thực thi câu lệnh SQL DELETE trực tiếp để xoá bản ghi số 5
        sql_statement = text("DELETE FROM batch_events WHERE id = :event_id")
        db.execute(sql_statement, {"event_id": deleted_id})
        db.commit()
        print(f"[CẢNH BÁO GIẢ LẬP] Đã xoá bản ghi #{deleted_id} trực tiếp qua SQL.")

        # 3. Chạy hàm kiểm tra tính toàn vẹn
        report = verify_batch_events_integrity(db, batch.id)

        print("\nKẾT QUẢ KIỂM TRA TÍNH TOÀN VẸN:")
        print(f"  • is_valid: {report.is_valid}")
        print(f"  • status: {report.status}")
        print(f"  • tamper_type: {report.tamper_type}")
        print(f"  • tampered_event_id: {report.tampered_event_id}")
        print(f"  • tampered_sequence: {report.tampered_sequence}")
        print(f"  • recorded_prev_hash: {report.recorded_prev_hash}")
        print(f"  • expected_prev_hash: {report.expected_prev_hash}")
        print(f"  • Chi tiết: {report.detail}")

        # 4. Khẳng định nghiệm thu (Assertions)
        assert report.is_valid is False, "Lỗi: Hệ thống không phát hiện ra sự đứt gãy khi bị xoá bản ghi!"
        assert report.status == "TAMPERED", f"Mong đợi status 'TAMPERED', nhận được {report.status}"
        assert report.tamper_type == TamperType.RECORD_DELETED_OR_CHAIN_BROKEN.value, (
            f"Mong đợi tamper_type '{TamperType.RECORD_DELETED_OR_CHAIN_BROKEN.value}', nhận được {report.tamper_type}"
        )
        assert report.tampered_event_id == subsequent_id, (
            f"Mong đợi phát hiện đứt gãy tại bản ghi #{subsequent_id}, nhận được #{report.tampered_event_id}"
        )

        print("\n[BẰNG CHỨNG NGHIỆM THU CA 2]")
        print(">> Cơ chế chống sửa lén T-29 đã phát hiện đứt gãy chuỗi băm 100% khi có bản ghi bị xoá:")
        print(f"   - Điểm đứt gãy tại bản ghi: #{report.tampered_event_id} (thứ tự {report.tampered_sequence})")
        print(f"   - prev_hash ghi nhận: {report.recorded_prev_hash}")
        print(f"   - prev_hash mong đợi: {report.expected_prev_hash}")
        print("=> [PASS CA 2 THÀNH CÔNG RỰC RỠ]")

    finally:
        cleanup_test_batch(db, batch.id)
        print(f"[CLEANUP] Đã tự động dọn dẹp sạch sẽ Lô #{batch.id}.")


def test_case_3_intact_valid_chain(db: Session) -> None:
    """Ca 3: Giữ nguyên vẹn 10 sự kiện -> Khẳng định toàn vẹn 100%."""
    print("\n" + "=" * 70)
    print("--- [CA 3] GIỮ NGUYÊN VẸN 10 SỰ KIỆN LIÊN TIẾP ---")
    print("=" * 70)

    batch = create_test_batch(db, "CASE3_INTACT")
    try:
        # 1. Dựng 10 sự kiện chuẩn
        events = build_10_events_for_batch(db, batch.id)
        assert len(events) == 10, f"Mong đợi 10 sự kiện, nhận được {len(events)}"
        print(f"[OK] Đã dựng thành công 10 sự kiện nối chuỗi băm mật mã cho Lô #{batch.id}.")

        for ev in events:
            print(f"  • Sự kiện #{ev.id:2d} (seq {ev.sequence:2d}): [{ev.event_type:<20}] prev={ev.prev_hash[:8]}... hash={ev.hash[:8]}...")

        # 2. Không sửa, không xoá bất kỳ bản ghi nào

        # 3. Chạy hàm kiểm tra tính toàn vẹn
        report = verify_batch_events_integrity(db, batch.id)

        print("\nKẾT QUẢ KIỂM TRA TÍNH TOÀN VẸN:")
        print(f"  • is_valid: {report.is_valid}")
        print(f"  • status: {report.status}")
        print(f"  • total_events: {report.total_events}")
        print(f"  • verified_count: {report.verified_count}")
        print(f"  • tamper_type: {report.tamper_type}")
        print(f"  • Chi tiết: {report.detail}")

        # 4. Khẳng định nghiệm thu (Assertions)
        assert report.is_valid is True, "Lỗi: Chuỗi nguyên vẹn nhưng bị báo không hợp lệ!"
        assert report.status == "VERIFIED", f"Mong đợi status 'VERIFIED', nhận được {report.status}"
        assert report.tamper_type is None, f"Mong đợi tamper_type là None, nhận được {report.tamper_type}"
        assert report.total_events == 10, f"Mong đợi 10 sự kiện, nhận được {report.total_events}"
        assert report.verified_count == 10, f"Mong đợi 10 sự kiện được xác thực, nhận được {report.verified_count}"
        assert report.tampered_event_id is None, "Không được có tampered_event_id trong chuỗi nguyên vẹn!"

        print("\n[BẰNG CHỨNG NGHIỆM THU CA 3]")
        print(">> Toàn bộ 10/10 bản ghi trong chuỗi đều khớp mã băm SHA-256 từ Genesis đến cuối.")
        print(f">> {report.detail}")
        print("=> [PASS CA 3 THÀNH CÔNG RỰC RỠ]")

    finally:
        cleanup_test_batch(db, batch.id)
        print(f"[CLEANUP] Đã tự động dọn dẹp sạch sẽ Lô #{batch.id}.")


def main() -> None:
    """Hàm chạy toàn bộ kịch bản kiểm thử T-29."""
    print("=" * 70)
    print("BẮT ĐẦU CHẠY KỊCH BẢN NGHIỆM THU T-29 (SCRUM-45): CHỐNG SỬA LÉN DỮ LIỆU")
    print("=" * 70)

    # Nâng cấp và đồng bộ schema
    init_db()
    db = get_test_db()

    try:
        # Ca 1: Sửa lén 1 bản ghi bằng SQL
        test_case_1_sql_data_tampered(db)

        # Ca 2: Xoá 1 bản ghi bằng SQL
        test_case_2_sql_record_deleted(db)

        # Ca 3: Giữ nguyên vẹn
        test_case_3_intact_valid_chain(db)

        print("\n" + "=" * 70)
        print("🎉 TẤT CẢ 3 CA KIỂM THỬ ĐỀU CHO KẾT QUẢ CHÍNH XÁC 100%!")
        print("🎉 KỊCH BẢN HOÀN TOÀN TỰ ĐỘNG DỌN DẸP, CHẠY LẠI ĐƯỢC NHIỀU LẦN ĐỘC LẬP!")
        print("=" * 70)
    finally:
        db.close()


if __name__ == "__main__":
    main()
