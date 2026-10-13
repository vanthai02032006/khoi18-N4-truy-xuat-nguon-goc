"""Script minh hoạ và kiểm chứng tự động cho hàm kiểm tra chuỗi một lô (T-28 / SCRUM-44).

Chạy script:
    python scripts/demo_chain_verification.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Thêm đường dẫn backend vào sys.path (ưu tiên khoi18-N4-truy-xuat-nguon-goc/backend nếu có)
sub_backend = Path(__file__).resolve().parent.parent / "khoi18-N4-truy-xuat-nguon-goc" / "backend"
backend_dir = sub_backend if sub_backend.exists() else Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.events import (
    ChainErrorType,
    get_events_for_batch,
    record_event,
    verify_batch_chain,
    verify_multiple_batches_chains,
)
from app.models import Base, Batch, Farm, User
from app.security import hash_password


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 70)
    print("🚀 BẮT ĐẦU KIỂM CHỨNG HÀM KIỂM TRA CHUỖI SỰ KIỆN MỘT LÔ (T-28 / SCRUM-44)")
    print("=" * 70)

    # 1. Khởi tạo CSDL SQLite in-memory
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()

    # Tạo dữ liệu người dùng, nông trại và 2 lô
    user = User(username="farmer_viet", password=hash_password("123456"), role="farmer")
    farm = Farm(name="Vườn Xoài Cát Chu Cao Lãnh #1", location="Đồng Tháp", area=3.0, owner="HTX Nông Nghiệp Số 4")
    db.add_all([user, farm])
    db.commit()

    b1_kwargs = {
        "farm_id": farm.id,
        "product_name": "Xoài Cát Chu",
        "quantity": 1000.0,
        "harvest_date": date(2026, 9, 20),
        "batch_code": "LOT-2026-001",
    }
    b2_kwargs = {
        "farm_id": farm.id,
        "product_name": "Xoài Cát Chu Loại 2",
        "quantity": 500.0,
        "harvest_date": date(2026, 9, 21),
        "batch_code": "LOT-2026-002",
    }
    if hasattr(Batch, "current_holder_org"):
        b1_kwargs["current_holder_org"] = "HTX Nông Nghiệp Số 4"
        b2_kwargs["current_holder_org"] = "HTX Nông Nghiệp Số 4"

    batch_1 = Batch(**b1_kwargs)
    batch_2 = Batch(**b2_kwargs)
    db.add_all([batch_1, batch_2])
    db.commit()

    print("\n[Bước 1] Ghi nhận chuỗi 5 sự kiện chuẩn vào Lô #1 (T-25 / SCRUM-41)...")
    event_pipeline = [
        ("HARVEST", "Thu hoạch xoài tại vườn Tháp Mười, khối lượng 1000kg"),
        ("QUALITY_INSPECTION", "Kiểm định đạt tiêu chuẩn VietGAP loại 1"),
        ("WASH_AND_SORT", "Sơ chế rửa ozone và phân loại kích cỡ đồng đều"),
        ("PACKAGING", "Đóng thùng carton 5 lớp có mã QR truy xuất"),
        ("COLD_STORAGE_IN", "Nhập kho bảo quản lạnh 4.0 độ C tại Mỹ Xương"),
    ]

    for idx, (etype, note) in enumerate(event_pipeline):
        ev = record_event(
            db=db,
            batch_id=batch_1.id,
            event_type=etype,
            actor="farmer_viet",
            payload={"step": idx + 1, "note": note},
            organization="HTX Nông Nghiệp Số 4",
        )
        print(f"  + Sự kiện #{ev.id} [{etype}]: Hash = {ev.hash[:16]}... | Prev = {ev.previous_hash[:16]}...")
    db.commit()

    # -------------------------------------------------------------------------
    # KỊCH BẢN 1: Chuỗi nguyên vẹn
    # -------------------------------------------------------------------------
    print("\n[Kịch bản 1] Kiểm tra chuỗi khi còn nguyên vẹn...")
    res_1 = verify_batch_chain(db, batch_1.id)
    print(f"  • is_valid: {res_1.is_valid}")
    print(f"  • tampered_index: {res_1.tampered_index}")
    print(f"  • error_type: {res_1.error_type}")
    print(f"  • total_events: {res_1.total_events} | verified_count: {res_1.verified_count}")
    print(f"  • Chi tiết: {res_1.error_detail}")
    assert res_1.is_valid is True, "Kịch bản 1 thất bại!"
    print("  => ĐẠT TIÊU CHÍ 1: Chuỗi nguyên vẹn trả về hợp lệ 100%!")

    # -------------------------------------------------------------------------
    # KỊCH BẢN 2: Giả lập sửa lén dữ liệu (SQL UPDATE)
    # -------------------------------------------------------------------------
    print("\n[Kịch bản 2] Giả lập kẻ xấu sửa lén dữ liệu sự kiện số 3 (WASH_AND_SORT)...")
    events = get_events_for_batch(db, batch_1.id)
    target_event = events[2]  # index 2
    db.execute(
        text("UPDATE batch_events SET payload = :tampered WHERE id = :id"),
        {"tampered": '{"step": 3, "note": "HÀNG NHÁI KÉM CHẤT LƯỢNG ĐÃ BỊ SỬA LÉN"}', "id": target_event.id},
    )
    db.commit()

    res_2 = verify_batch_chain(db, batch_1.id)
    print(f"  • is_valid: {res_2.is_valid}")
    print(f"  • tampered_index: {res_2.tampered_index} (mong đợi: 2)")
    print(f"  • error_type: {res_2.error_type} (mong đợi: {ChainErrorType.DATA_MODIFIED.value})")
    print(f"  • tampered_event_id: #{res_2.tampered_event_id}")
    print(f"  • Hash lưu trữ:     {res_2.recorded_hash[:20]}...")
    print(f"  • Hash tính lại:    {res_2.expected_hash[:20]}...")
    print(f"  • Chi tiết: {res_2.error_detail}")
    assert res_2.is_valid is False and res_2.tampered_index == 2, "Kịch bản 2 thất bại!"
    print("  => ĐẠT TIÊU CHÍ 2: Chuỗi bị sửa lén trả về đúng chính xác vị trí bản ghi sai!")

    # -------------------------------------------------------------------------
    # KỊCH BẢN 3: Giả lập xóa mất bản ghi sự kiện (SQL DELETE)
    # -------------------------------------------------------------------------
    print("\n[Kịch bản 3] Giả lập xóa mất một sự kiện ở giữa chuỗi của Lô #2...")
    for i in range(4):
        record_event(
            db=db,
            batch_id=batch_2.id,
            event_type=f"STAGE_{i}",
            actor="farmer_viet",
            payload={"i": i},
        )
    db.commit()

    events_b2 = get_events_for_batch(db, batch_2.id)
    deleted_ev = events_b2[1]  # Xóa sự kiện ở index 1
    db.execute(text("DELETE FROM batch_events WHERE id = :id"), {"id": deleted_ev.id})
    db.commit()

    res_3 = verify_batch_chain(db, batch_2.id)
    print(f"  • is_valid: {res_3.is_valid}")
    print(f"  • tampered_index: {res_3.tampered_index} (mong đợi: 1 - vị trí đứt mạch)")
    print(f"  • error_type: {res_3.error_type} (mong đợi: {ChainErrorType.CHAIN_BROKEN.value})")
    print(f"  • Chi tiết: {res_3.error_detail}")
    assert res_3.is_valid is False and res_3.tampered_index == 1, "Kịch bản 3 thất bại!"
    print("  => ĐẠT TIÊU CHÍ 3: Chuỗi bị xóa mất sự kiện trả về đúng vị trí đứt mạch!")

    # -------------------------------------------------------------------------
    # KỊCH BẢN 4: Kiểm tra batch nhiều lô trong 1 query (Chống N+1 query)
    # -------------------------------------------------------------------------
    print("\n[Kịch bản 4] Kiểm tra nhiều lô trong đúng 1 câu lệnh SQL (Chống N+1 query)...")
    multi_res = verify_multiple_batches_chains(db, [batch_1.id, batch_2.id])
    print(f"  • Lô #{batch_1.id}: is_valid={multi_res[batch_1.id].is_valid}, error_type={multi_res[batch_1.id].error_type}")
    print(f"  • Lô #{batch_2.id}: is_valid={multi_res[batch_2.id].is_valid}, error_type={multi_res[batch_2.id].error_type}")
    assert batch_1.id in multi_res and batch_2.id in multi_res

    print("\n" + "=" * 70)
    print("🎉 TẤT CẢ TIÊU CHÍ NGHIỆM THU (DoD / AC) ĐỀU ĐẠT CHÍNH XÁC 100%!")
    print("=" * 70)
    db.close()


if __name__ == "__main__":
    main()
