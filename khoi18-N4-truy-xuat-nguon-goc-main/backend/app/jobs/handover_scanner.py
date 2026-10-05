"""Background Job / Scheduler: Quét bảng handovers để gắn cờ quá hạn (Task 5 / SCRUM-51).

Nhiệm vụ:
- Định kỳ chạy mỗi 1 giờ (3600 giây).
- Tìm kiếm các phiếu bàn giao có trạng thái 'PENDING' và thời gian tạo > 48 giờ.
- Gắn cờ `is_overdue = True`, cập nhật `status = 'OVERDUE'`, bổ sung ghi chú cảnh báo.
- Ghi log có cấu trúc (structured logging) đầy đủ thông tin để phục vụ kiểm toán vận hành.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Handover

# Cấu hình logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [HandoverScanner] %(message)s",
)
logger = logging.getLogger("agritrace.jobs.handover_scanner")


def scan_and_flag_overdue_handovers(
    db: Session,
    threshold_hours: int = 48,
) -> int:
    """Quét và gắn cờ các bản ghi bàn giao đang ở trạng thái PENDING quá ngưỡng giờ quy định.

    Args:
        db: SQLAlchemy Session.
        threshold_hours: Ngưỡng thời gian quá hạn tính bằng giờ (mặc định 48h).

    Returns:
        int: Số lượng bản ghi đã được gắn cờ quá hạn.
    """
    now = datetime.utcnow()
    cutoff_time = now - timedelta(hours=threshold_hours)

    logger.info(
        "Bắt đầu quét phiếu bàn giao quá hạn (Ngưỡng: %s giờ, Mốc trước: %s UTC)...",
        threshold_hours,
        cutoff_time.isoformat(),
    )

    # Lấy danh sách các phiếu bàn giao đang PENDING và chưa gắn cờ overdue nhưng đã quá 48h
    stmt = select(Handover).where(
        Handover.status == "PENDING",
        Handover.is_overdue.is_(False),
        Handover.created_at <= cutoff_time,
    )
    overdue_records = list(db.scalars(stmt).all())

    if not overdue_records:
        logger.info("Hoàn tất quét: Không có phiếu bàn giao nào bị quá hạn.")
        return 0

    flagged_count = 0
    for record in overdue_records:
        elapsed_hours = (now - record.created_at).total_seconds() / 3600.0
        
        # Gắn cờ và cập nhật trạng thái
        record.is_overdue = True
        record.status = "OVERDUE"
        
        tag_note = f"[CẢNH BÁO QUÁ HẠN {threshold_hours}H]"
        if record.notes:
            record.notes = f"{tag_note} {record.notes}"
        else:
            record.notes = f"{tag_note} Phiếu bàn giao đã quá 48h chưa được đối tác phản hồi tiếp nhận."

        flagged_count += 1

        logger.warning(
            "[OVERDUE FLAGGED] Phiếu Handover ID=%s (Lô #%s) -> Đối tác: '%s' (ID: %s) "
            "| Tạo lúc: %s UTC | Quá hạn: %.1f giờ | Đã chuyển trạng thái: OVERDUE",
            record.id,
            record.batch_id,
            record.recipient_org_name,
            record.recipient_org_id,
            record.created_at.isoformat(),
            elapsed_hours,
        )

    try:
        db.commit()
        logger.info(
            "Cập nhật CSDL thành công: Đã gắn cờ quá hạn cho %s phiếu bàn giao.",
            flagged_count,
        )
    except Exception as exc:
        db.rollback()
        logger.error("Lỗi khi cập nhật CSDL cho các phiếu quá hạn: %s", exc)
        raise

    return flagged_count


async def start_handover_overdue_scheduler(
    interval_seconds: int = 3600,
    threshold_hours: int = 48,
) -> None:
    """Async background task chạy định kỳ mỗi giờ để quét và gắn cờ phiếu bàn giao quá hạn.

    Args:
        interval_seconds: Tần suất quét tính bằng giây (mặc định: 3600s = 1 giờ).
        threshold_hours: Ngưỡng tính quá hạn (mặc định: 48 giờ).
    """
    logger.info(
        "Khởi động Background Scheduler quét Handover quá hạn "
        "(Chu kỳ: %s giây, Ngưỡng quá hạn: %s giờ).",
        interval_seconds,
        threshold_hours,
    )

    while True:
        try:
            # Mở session riêng biệt cho mỗi chu kỳ quét
            with SessionLocal() as db:
                scan_and_flag_overdue_handovers(db, threshold_hours=threshold_hours)
        except asyncio.CancelledError:
            logger.info("Background Scheduler nhận tín hiệu dừng (Cancelled). Dừng tác vụ.")
            break
        except Exception as exc:
            logger.error("Lỗi không mong muốn trong chu kỳ quét Handover: %s", exc)

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("Background Scheduler dừng khi đang chờ sleep.")
            break
