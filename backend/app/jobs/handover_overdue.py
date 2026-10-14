"""Job đánh dấu bàn giao chờ quá hạn (S-24 / T-56 / SCRUM-72, SCRUM-85).

Ý tưởng job quét định kỳ của Lê Hoàng Quân (nhánh ``feature/quan-5-tasks``), viết
lại cho đúng model ``Handover`` hiện tại và đúng AC của S-24:

- Phiếu ``pending`` tạo trước mốc ngưỡng (mặc định 48 giờ) được gắn ``is_overdue``.
- **Chỉ đánh dấu, không đổi trạng thái, không tự huỷ** - bên nhận vẫn xác nhận
  được và sự kiện ghi rõ "xác nhận muộn".
- Ngưỡng là tham số cấu hình: biến môi trường ``HANDOVER_OVERDUE_HOURS``.
- Mỗi lần chạy ghi log số bản ghi đã xử lý.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HANDOVER_STATUS_PENDING, Handover

logger = logging.getLogger("agritrace.jobs.handover_overdue")

DEFAULT_THRESHOLD_HOURS = 48


def overdue_threshold_hours() -> int:
    try:
        return max(1, int(os.environ.get("HANDOVER_OVERDUE_HOURS", DEFAULT_THRESHOLD_HOURS)))
    except ValueError:
        return DEFAULT_THRESHOLD_HOURS


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_handover_overdue(handover: Handover, now: datetime | None = None) -> bool:
    """Phiếu đang chờ và đã quá ngưỡng (kể cả khi job chưa kịp chạy)."""
    if handover.status != HANDOVER_STATUS_PENDING:
        return bool(getattr(handover, "is_overdue", False))
    if getattr(handover, "is_overdue", False):
        return True
    created = handover.created_at
    if created is None:
        return False
    if created.tzinfo is not None:
        created = created.astimezone(timezone.utc).replace(tzinfo=None)
    now = now or _utcnow_naive()
    return created <= now - timedelta(hours=overdue_threshold_hours())


def scan_and_flag_overdue_handovers(
    db: Session,
    threshold_hours: int | None = None,
    now: datetime | None = None,
) -> int:
    """Gắn cờ quá hạn cho phiếu chờ quá ngưỡng. Trả về số phiếu vừa được gắn cờ."""
    hours = threshold_hours or overdue_threshold_hours()
    now = now or _utcnow_naive()
    cutoff = now - timedelta(hours=hours)
    rows = list(
        db.scalars(
            select(Handover).where(
                Handover.status == HANDOVER_STATUS_PENDING,
                Handover.is_overdue.is_(False),
                Handover.created_at <= cutoff,
            )
        ).all()
    )
    for h in rows:
        h.is_overdue = True
    db.commit()
    logger.info("Quét bàn giao quá hạn (%sh): đã gắn cờ %s phiếu.", hours, len(rows))
    return len(rows)


async def run_overdue_scheduler(session_factory, interval_seconds: int = 3600) -> None:
    """Chạy job mỗi giờ trong suốt vòng đời ứng dụng; lỗi một lần không dừng job."""
    while True:
        try:
            with session_factory() as db:
                scan_and_flag_overdue_handovers(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - chỉ log
            logger.exception("Job quét bàn giao quá hạn gặp lỗi.")
        await asyncio.sleep(interval_seconds)


__all__ = [
    "is_handover_overdue",
    "overdue_threshold_hours",
    "run_overdue_scheduler",
    "scan_and_flag_overdue_handovers",
]
