"""Kiểm thử tự động tính năng Background Job Quét Handover Quá Hạn (Task 5 / SCRUM-51).

Kịch bản kiểm thử:
1. Tạo phiếu #1: PENDING, tạo trước 50h (> 48h) -> PHẢI BỊ GẮN CỜ OVERDUE.
2. Tạo phiếu #2: PENDING, tạo trước 2h (< 48h)  -> GIỮ NGUYÊN PENDING (CHƯA QUÁ HẠN).
3. Tạo phiếu #3: COMPLETED, tạo trước 60h       -> GIỮ NGUYÊN COMPLETED (ĐÃ XONG).
4. Chạy hàm quét `scan_and_flag_overdue_handovers`.
5. Kiểm tra kết quả gắn cờ và thông báo log.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select
from app.database import SessionLocal, init_db
from app.models import Batch, Farm, Handover
from app.jobs.handover_scanner import scan_and_flag_overdue_handovers


def run_test():
    print("=" * 70)
    print("KIỂM THỬ BACKGROUND JOB QUÉT BÀN GIAO QUÁ HẠN 48H (TASK 5)")
    print("=" * 70)

    init_db()
    db = SessionLocal()
    try:
        # Chuẩn bị dữ liệu Farm và Batch mẫu
        farm = db.scalar(select(Farm))
        if not farm:
            farm = Farm(name="Vườn Mẫu Kiểm Thử", location="Đồng Tháp", area=3.0, owner="HTX")
            db.add(farm)
            db.commit()

        batch = db.scalar(select(Batch).where(Batch.farm_id == farm.id))
        if not batch:
            batch = Batch(farm_id=farm.id, product_name="Xoài Thử Nghiệm", quantity=100.0, harvest_date=datetime.utcnow().date())
            db.add(batch)
            db.commit()

        now = datetime.utcnow()

        # Tạo phiếu #1: PENDING, tạo trước 50h (> 48h) -> Phải bị gắn cờ
        h1 = Handover(
            batch_id=batch.id,
            sender_org_id=1,
            recipient_org_id=2,
            recipient_org_name="Công ty Chế biến Nông sản Miền Tây",
            notes="Phiếu giao hàng gửi đi cách đây 50 giờ",
            status="PENDING",
            is_overdue=False,
            created_at=now - timedelta(hours=50),
        )

        # Tạo phiếu #2: PENDING, tạo trước 5h (< 48h) -> Không bị gắn cờ
        h2 = Handover(
            batch_id=batch.id,
            sender_org_id=1,
            recipient_org_id=3,
            recipient_org_name="Siêu thị WinCommerce",
            notes="Phiếu mới tạo 5 giờ",
            status="PENDING",
            is_overdue=False,
            created_at=now - timedelta(hours=5),
        )

        # Tạo phiếu #3: COMPLETED, tạo trước 70h -> Không bị gắn cờ vì đã hoàn thành
        h3 = Handover(
            batch_id=batch.id,
            sender_org_id=1,
            recipient_org_id=4,
            recipient_org_name="Kho lạnh Satra",
            notes="Đã giao nhận hoàn tất",
            status="COMPLETED",
            is_overdue=False,
            created_at=now - timedelta(hours=70),
        )

        db.add_all([h1, h2, h3])
        db.commit()
        db.refresh(h1)
        db.refresh(h2)
        db.refresh(h3)

        print(f"\n[BƯỚC 1] Đã tạo 3 phiếu bàn giao mẫu:")
        print(f" - Phiếu ID={h1.id}: Status={h1.status}, CreatedAt={h1.created_at} (Cách đây 50h)")
        print(f" - Phiếu ID={h2.id}: Status={h2.status}, CreatedAt={h2.created_at} (Cách đây 5h)")
        print(f" - Phiếu ID={h3.id}: Status={h3.status}, CreatedAt={h3.created_at} (Cách đây 70h, COMPLETED)")

        # [BƯỚC 2] Thực thi hàm quét quá hạn 48h
        print("\n[BƯỚC 2] Thực thi Job quét quá hạn (ngưỡng 48 giờ)...")
        flagged_count = scan_and_flag_overdue_handovers(db, threshold_hours=48)
        print(f" -> Số phiếu bị gắn cờ: {flagged_count}")

        # [BƯỚC 3] Kiểm tra xác nhận
        print("\n[BƯỚC 3] Kiểm tra tính chính xác của dữ liệu sau khi quét:")
        db.refresh(h1)
        db.refresh(h2)
        db.refresh(h3)

        assert h1.is_overdue is True, "LỖI: Phiếu #1 (50h) chưa được gắn cờ is_overdue=True!"
        assert h1.status == "OVERDUE", "LỖI: Phiếu #1 chưa chuyển status sang OVERDUE!"
        assert "[CẢNH BÁO QUÁ HẠN 48H]" in (h1.notes or ""), "LỖI: Phiếu #1 chưa có ghi chú cảnh báo quá hạn!"
        print(f" -> Phiếu ID={h1.id} (50h): ĐÃ GẮN CỜ CHÍNH XÁC (Status: {h1.status}, Overdue: {h1.is_overdue})")

        assert h2.is_overdue is False, "LỖI: Phiếu #2 (5h) bị gắn cờ sai!"
        assert h2.status == "PENDING", "LỖI: Phiếu #2 bị đổi trạng thái sai!"
        print(f" -> Phiếu ID={h2.id} (5h): GIỮ NGUYÊN CHÍNH XÁC (Status: {h2.status}, Overdue: {h2.is_overdue})")

        assert h3.is_overdue is False, "LỖI: Phiếu #3 (COMPLETED) bị gắn cờ sai!"
        assert h3.status == "COMPLETED", "LỖI: Phiếu #3 bị đổi trạng thái sai!"
        print(f" -> Phiếu ID={h3.id} (70h COMPLETED): GIỮ NGUYÊN CHÍNH XÁC (Status: {h3.status}, Overdue: {h3.is_overdue})")

        print("\n" + "=" * 70)
        print("TẤT CẢ CÁC KIỂM THỬ TASK 5 ĐỀU ĐẠT CHUẨN 100%!")
        print("=" * 70)

    finally:
        db.close()


if __name__ == "__main__":
    run_test()
