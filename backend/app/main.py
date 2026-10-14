"""Điểm khởi tạo (entrypoint) của ứng dụng FastAPI.

Chạy local:
    uvicorn app.main:app --reload

Tài liệu API (Swagger UI):
    http://127.0.0.1:8000/docs

Các sprint:
- Sprint 1: cấu hình FastAPI (metadata, CORS, tài liệu tự động), kết nối SQLite
  qua SQLAlchemy (khởi tạo bảng khi app start), endpoint ``GET /health``.
- Sprint 2/3: module Farm (``/farms``) và Batch (``/batches``).
- Sprint 4: đăng nhập + phân quyền cơ bản (``/auth/login``, ``/users``,
  dependency ``require_admin``/``require_farmer``) - **không dùng JWT**.
- Sprint 5: hoàn thiện CRUD - thêm ``PUT``/``DELETE`` cho ``/farms`` và
  ``/batches`` (xoá chỉ dành cho ``admin``).
"""

from collections.abc import AsyncIterator
import asyncio
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.database import SessionLocal, init_db
from app.jobs.handover_overdue import run_overdue_scheduler
from app.routers import (
    auth,
    backups,
    batches,
    cold_chain,
    events,
    farms,
    handovers,
    health,
    integrity,
    invitations,
    lineage,
    locations,
    orders,
    products,
    users,
)

# ------------------------------------------------------------------ Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Vòng đời ứng dụng: chạy khi server khởi động và khi server tắt.

    Khi khởi động: tạo bảng còn thiếu trong SQLite và tạo 2 tài khoản mặc định
    (``admin``/``farmer``) nếu chưa có - xem ``app/database.py``.
    """
    init_db()
    # S-24: job đánh dấu bàn giao quá hạn chạy mỗi giờ. Tắt bằng AGRITRACE_JOBS=0
    # (bộ test tắt để không chạm vào CSDL thật).
    task = None
    if os.environ.get("AGRITRACE_JOBS", "1") == "1" and "pytest" not in sys.modules:
        task = asyncio.create_task(run_overdue_scheduler(SessionLocal))
    yield
    if task is not None:
        task.cancel()


# ----------------------------------------------------------- Khởi tạo FastAPI ---
app = FastAPI(
    title="AGRI-TRACE - Truy xuất nguồn gốc và giám sát chuỗi lạnh nông sản",
    description=(
        "Backend API cho hệ thống truy xuất nguồn gốc và giám sát chuỗi lạnh "
        "nông sản tiêu chuẩn VietGAP.\n\n"
        "- **SCRUM-138**: Sao lưu dữ liệu SQLite Online Backup & Diễn tập khôi phục DB tạm (`/backups`).\n"
        "- **SCRUM-136**: Job S-12 kiểm tra tính toàn vẹn chuỗi dữ liệu hằng đêm (`/integrity-check`).\n"
        "- **SCRUM-126**: Giám sát nhiệt độ chuỗi lạnh & Tổng hợp số đo theo giờ (`/cold-chain`).\n"
        "- **SCRUM-91**: Mời nhân viên qua email, token một lần, đặt mật khẩu & phân quyền (`/invitations`)."
    ),
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# CORS: cho phép frontend (chạy ở cổng khác) gọi API khi phát triển.
# Sprint sau cần thay "*" bằng danh sách domain cụ thể trước khi deploy production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------- Đăng ký các router ---
# Mỗi module nghiệp vụ là 1 router; thêm module mới = thêm 1 dòng ở đây.
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(farms.router)
app.include_router(batches.router)
app.include_router(lineage.router)
app.include_router(locations.router)
app.include_router(products.router)
app.include_router(handovers.router)
app.include_router(events.router)
app.include_router(orders.router)
app.include_router(cold_chain.router)
app.include_router(users.router)
app.include_router(backups.router)
app.include_router(integrity.router)
app.include_router(invitations.router)

