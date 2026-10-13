"""CLI script thực thi quét phiếu bàn giao quá hạn 48 giờ (Task 5 / SCRUM-51).

Sử dụng:
    python scripts/scan_overdue_handovers.py [--threshold-hours 48]
"""

import argparse
import sys
from pathlib import Path

# Cấu hình UTF-8 cho stdout trên Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.database import SessionLocal, init_db
from app.jobs.handover_scanner import scan_and_flag_overdue_handovers


def main():
    parser = argparse.ArgumentParser(
        description="Quét bảng handovers và gắn cờ quá hạn cho các bản ghi PENDING quá 48h."
    )
    parser.add_argument(
        "--threshold-hours",
        type=int,
        default=48,
        help="Ngưỡng giờ quá hạn (mặc định: 48)",
    )
    args = parser.parse_args()

    init_db()

    print("=" * 70)
    print("AGRI-TRACE: QUÉT & GẮN CỜ PHIẾU BÀN GIAO QUÁ HẠN (TASK 5 / SCRUM-51)")
    print(f"Ngưỡng thời gian quá hạn: {args.threshold_hours} giờ")
    print("=" * 70)

    with SessionLocal() as db:
        count = scan_and_flag_overdue_handovers(db, threshold_hours=args.threshold_hours)

    print("-" * 70)
    print(f"KẾT QUẢ: Đã xử lý và gắn cờ thành công {count} bản ghi bàn giao quá hạn.")
    print("=" * 70)


if __name__ == "__main__":
    main()
