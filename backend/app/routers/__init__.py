"""Nhóm các router (endpoint) của API theo từng nghiệp vụ.

Quy ước: mỗi file trong package này là một `APIRouter` cho một nhóm chức năng
(`health.py`, `farms.py`, `batches.py`, `auth.py`, `users.py`, sau này là
`cold_chain.py`...). Tất cả router được đăng ký tập trung tại `app/main.py`.
"""

from app.routers import auth, batches, cold_chain, events, farms, health, orders, users

__all__ = ["auth", "batches", "cold_chain", "events", "farms", "health", "orders", "users"]
