"""Bộ kiểm thử độc lập cho 4 kịch bản nghiệm thu (Acceptance Scenarios) của Dòng thời gian lô hàng.

Kịch bản 1: Lô qua nhiều bước -> Các sự kiện xếp theo thứ tự thời gian kèm tên tổ chức thực hiện từng bước
Kịch bản 2: Lô vừa được tạo và chỉ có 1 sự kiện -> Thấy đúng 1 dòng, không hiện trang trống
Kịch bản 3: Chuỗi sự kiện có vấn đề toàn vẹn -> Cảnh báo rõ ràng và định vị sai lệch
Kịch bản 4: Lô có 200 sự kiện -> Tải xong và kiểm tra hash dưới 2 giây
"""

import sys
import time
from pathlib import Path
from datetime import datetime, timezone
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Thêm thư mục backend vào sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import Base
from app.models import Farm, Batch, BatchEvent, User
from app.routers.events import get_batch_timeline, record_batch_event
from app.schemas import BatchEventCreate
from app.security import compute_event_hash


def run_timeline_scenarios_tests():
    print("=" * 70)
    print("KIỂM THỬ 4 KỊCH BẢN NGHIỆM THU DÒNG THỜI GIAN SỰ KIỆN LÔ HÀNG")
    print("=" * 70)

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionTest = sessionmaker(bind=engine)
    db = SessionTest()

    # Tạo dữ liệu nền tảng
    farm = Farm(name="Vườn Sầu Riêng Tiền Giang", location="Cai Lậy, Tiền Giang", area=4.5, owner="HTX Nông Nghiệp Cai Lậy")
    db.add(farm)
    user_farmer = User(username="farmer_test", password="pwd", role="farmer")
    db.add(user_farmer)
    db.commit()
    db.refresh(farm)
    db.refresh(user_farmer)

    # -------------------------------------------------------------
    # Kịch bản 1: Giả sử một lô đã qua nhiều bước, Khi mở trang chi tiết,
    # Thì thấy các sự kiện xếp theo thứ tự thời gian kèm tên tổ chức thực hiện từng bước
    # -------------------------------------------------------------
    print("\n[KỊCH BẢN 1] Lô đã qua nhiều bước -> Sắp xếp theo thứ tự thời gian và kèm tên tổ chức...")
    batch_multi = Batch(code="LOTMULTI", farm_id=farm.id, product_name="Sầu Riêng Ri6", quantity=2000.0, harvest_date="2026-03-01", status="ACTIVE")
    db.add(batch_multi)
    db.commit()
    db.refresh(batch_multi)

    steps = [
        ("HARVEST", "Thu hoạch tại vườn lúc sáng sớm", "HTX Nông Nghiệp Cai Lậy"),
        ("CLEANING", "Sơ chế, làm sạch và khử khuẩn", "Nhà Máy Đóng Gói Tiền Giang"),
        ("PACKAGING", "Đóng thùng carton tiêu chuẩn VietGAP", "Công Ty Xuất Nhập Khẩu An Giang"),
        ("TRANSPORT", "Vận chuyển xe lạnh 5°C về kho cảng", "Đơn Vị Logistics Biển Xanh"),
        ("EXPORT", "Kiểm dịch hải quan và xuất khẩu", "Chi Cục Kiểm Dịch Thực Vật Vùng 2"),
    ]

    for ev_type, note, org in steps:
        record_batch_event(
            batch_id=batch_multi.id,
            data=BatchEventCreate(event_type=ev_type, payload=note, organization=org),
            current_user=user_farmer,
            db=db,
        )

    timeline_multi = get_batch_timeline(batch_id=batch_multi.id, current_user=user_farmer, db=db)
    assert timeline_multi.is_valid is True
    assert len(timeline_multi.events) == 5

    # Kiểm tra thứ tự thời gian tăng dần và tên tổ chức
    for i in range(len(timeline_multi.events)):
        ev = timeline_multi.events[i]
        expected_type, expected_note, expected_org = steps[i]
        assert ev.event_type == expected_type
        assert ev.payload == expected_note
        assert ev.organization == expected_org
        assert ev.organization != ""
        print(f"  -> Bước {i+1}: [{ev.event_type}] Tổ chức: '{ev.organization}' - Ghi nhận lúc: {ev.timestamp}")

    # Khẳng định thứ tự thời gian tăng dần
    for i in range(len(timeline_multi.events) - 1):
        assert timeline_multi.events[i].id < timeline_multi.events[i + 1].id
        assert timeline_multi.events[i].timestamp <= timeline_multi.events[i + 1].timestamp
    print("  -> PASSED: Kịch bản 1 hoàn thành - Thứ tự thời gian chuẩn xác, tên tổ chức minh bạch.")

    # -------------------------------------------------------------
    # Kịch bản 2: Giả sử lô vừa được tạo và chỉ có một sự kiện,
    # Khi mở trang, Thì thấy đúng một dòng, không hiện trang trống
    # -------------------------------------------------------------
    print("\n[KỊCH BẢN 2] Lô vừa được tạo -> Thấy đúng một dòng, không hiện trang trống...")
    batch_new = Batch(code="LOTNEW01", farm_id=farm.id, product_name="Xoài Cát Hòa Lộc", quantity=500.0, harvest_date="2026-03-05", status="ACTIVE")
    db.add(batch_new)
    db.commit()
    db.refresh(batch_new)

    timeline_new = get_batch_timeline(batch_id=batch_new.id, current_user=user_farmer, db=db)
    assert timeline_new.is_valid is True
    assert len(timeline_new.events) == 1, f"Lô mới phải có đúng 1 sự kiện khởi tạo, thực tế có {len(timeline_new.events)}"
    first_ev = timeline_new.events[0]
    assert first_ev.event_type == "HARVEST"
    assert first_ev.organization == farm.owner
    assert first_ev.previous_hash == "0" * 64
    print(f"  -> Trả về đúng 1 sự kiện: [{first_ev.event_type}] do tổ chức '{first_ev.organization}' khởi tạo.")
    print("  -> PASSED: Kịch bản 2 hoàn thành - Không bị trang trống, hiển thị đúng 1 dòng.")

    # -------------------------------------------------------------
    # Kịch bản 3: Giả sử chuỗi sự kiện của lô có vấn đề toàn vẹn,
    # Khi mở trang, Thì thấy cảnh báo rõ ràng ở đầu dòng thời gian
    # -------------------------------------------------------------
    print("\n[KỊCH BẢN 3] Chuỗi sự kiện có vấn đề toàn vẹn -> Phát hiện và cảnh báo rõ ràng...")
    batch_tamper = Batch(code="LOTTAMP1", farm_id=farm.id, product_name="Thanh Long Bình Thuận", quantity=1200.0, harvest_date="2026-03-02", status="ACTIVE")
    db.add(batch_tamper)
    db.commit()
    db.refresh(batch_tamper)

    for i in range(3):
        record_batch_event(
            batch_id=batch_tamper.id,
            data=BatchEventCreate(event_type=f"STAGE_{i+1}", payload=f"Dữ liệu gốc {i+1}", organization="Hợp Tác Xã"),
            current_user=user_farmer,
            db=db,
        )

    # Cố ý can thiệp payload của sự kiện mắt xích thứ 2 (index 1) trực tiếp trong CSDL
    tamper_ev = db.query(BatchEvent).filter_by(batch_id=batch_tamper.id, event_type="STAGE_2").first()
    tamper_ev.payload = "DỮ LIỆU ĐÃ BỊ SỬA LÉN TRÁI PHÉP!"
    db.commit()

    timeline_tamper = get_batch_timeline(batch_id=batch_tamper.id, current_user=user_farmer, db=db)
    assert timeline_tamper.is_valid is False
    assert timeline_tamper.tampered_index == 1
    print(f"  -> Kết quả thẩm định: is_valid = {timeline_tamper.is_valid}")
    print(f"  -> Định vị chính xác mắt xích bị can thiệp: index {timeline_tamper.tampered_index} (Mắt xích #{timeline_tamper.tampered_index + 1})")
    print("  -> PASSED: Kịch bản 3 hoàn thành - Cảnh báo đỏ và định vị chính xác vị trí lỗi.")

    # -------------------------------------------------------------
    # Kịch bản 4: Giả sử lô có 200 sự kiện,
    # Khi mở trang, Thì tải xong dưới 2 giây
    # -------------------------------------------------------------
    print("\n[KỊCH BẢN 4] Lô có 200 sự kiện -> Tải xong dưới 2 giây...")
    batch_200 = Batch(code="LOT200EV", farm_id=farm.id, product_name="Gạo ST25 Sóc Trăng", quantity=10000.0, harvest_date="2026-03-01", status="ACTIVE")
    db.add(batch_200)
    db.commit()
    db.refresh(batch_200)

    # Sinh 200 sự kiện có mã hash liên kết hợp lệ
    events_200 = []
    prev = "0" * 64
    now_ts = datetime.now(timezone.utc).isoformat()
    for i in range(200):
        pl = f"Sự kiện kiểm soát chất lượng mẻ #{i+1}"
        h = compute_event_hash("QUALITY_CHECK", pl, "inspector_auto", "Tổ Chức Giám Sát", now_ts, prev)
        ev = BatchEvent(
            batch_id=batch_200.id,
            event_type="QUALITY_CHECK",
            payload=pl,
            actor="inspector_auto",
            organization="Tổ Chức Giám Sát",
            timestamp=now_ts,
            hash=h,
            previous_hash=prev,
        )
        prev = h
        events_200.append(ev)

    db.bulk_save_objects(events_200)
    db.commit()

    # Bắt đầu đo thời gian tải và kiểm tra toàn bộ 200 băm
    start_time = time.perf_counter()
    timeline_200 = get_batch_timeline(batch_id=batch_200.id, current_user=user_farmer, db=db)
    duration = time.perf_counter() - start_time

    assert timeline_200.is_valid is True
    assert len(timeline_200.events) == 200
    assert duration < 2.0, f"Thời gian thực thi {duration:.4f}s vượt quá ngưỡng 2 giây!"

    print(f"  -> Số lượng sự kiện đã xử lý: {len(timeline_200.events)} sự kiện.")
    print(f"  -> Tính toán và xác thực 200 mã SHA-256 hoàn tất trong: {duration * 1000:.2f} ms ({duration:.4f} giây).")
    print(f"  -> Tốc độ: Nhanh hơn tiêu chí nghiệm thu gấp {2.0 / duration:.1f} lần!")
    print("  -> PASSED: Kịch bản 4 hoàn thành xuất sắc (Dưới 2 giây).")

    print("\n" + "=" * 70)
    print("KẾT QUẢ: TOÀN BỘ 4 KỊCH BẢN NGHIỆM THU ĐẠT 100% TIÊU CHÍ (PASSED)!")
    print("=" * 70)


if __name__ == "__main__":
    run_timeline_scenarios_tests()
