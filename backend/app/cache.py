"""Bộ nhớ đệm (Cache) cho kết quả truy vết phả hệ lô nông sản (T-49).

Ràng buộc kỹ thuật:
    Kết quả truy vết cache theo lô trong 60 giây vì lịch sử phả hệ không thay đổi.
"""

import threading
import time
from typing import Any


class TraceCache:
    """Cache lưu kết quả truy vết nguồn gốc (lineage/trace) với TTL = 60s."""

    def __init__(self, ttl_seconds: int = 60) -> None:
        self.ttl_seconds = ttl_seconds
        self._store: dict[int, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, batch_id: int) -> tuple[Any, int] | None:
        """Lấy dữ liệu cache của một lô nông sản nếu còn hạn.

        Args:
            batch_id: ID lô nông sản.

        Returns:
            Tuple (data, remaining_seconds) nếu cache hợp lệ, hoặc None nếu hết hạn/chưa có.
        """
        now = time.time()
        with self._lock:
            entry = self._store.get(batch_id)
            if entry is None:
                return None

            created_at, data = entry
            elapsed = now - created_at
            if elapsed < self.ttl_seconds:
                remaining = int(self.ttl_seconds - elapsed)
                return data, max(1, remaining)

            # Đã hết hạn -> xoá khỏi cache
            self._store.pop(batch_id, None)
            return None

    def set(self, batch_id: int, data: Any) -> None:
        """Lưu kết quả truy vết vào cache.

        Args:
            batch_id: ID lô nông sản.
            data: Dữ liệu kết quả truy vết.
        """
        with self._lock:
            self._store[batch_id] = (time.time(), data)

    def invalidate(self, batch_id: int | None = None) -> None:
        """Xoá cache của một lô (hoặc toàn bộ nếu batch_id là None).

        Args:
            batch_id: ID lô cần xoá cache, hoặc None để làm mới toàn bộ.
        """
        with self._lock:
            if batch_id is not None:
                self._store.pop(batch_id, None)
            else:
                self._store.clear()

    def get_remaining(self, batch_id: int) -> int:
        """Lấy thời gian còn lại (giây) của cache cho lô này."""
        result = self.get(batch_id)
        return result[1] if result is not None else 0


# Instance dùng chung cho toàn bộ ứng dụng
trace_cache = TraceCache(ttl_seconds=60)
