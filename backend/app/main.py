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
- Sprint 6: danh mục **sản phẩm dùng chung** cho mọi tổ chức (``/products``) -
  thêm/sửa chỉ dành cho ``admin``, ``farmer`` chỉ xem danh mục.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.database import init_db
from app.routers import auth, batches, events, farms, health, products, users

# ------------------------------------------------------------------ Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Vòng đời ứng dụng: chạy khi server khởi động và khi server tắt.

    Khi khởi động: tạo bảng còn thiếu trong SQLite và tạo 2 tài khoản mặc định
    (``admin``/``farmer``) nếu chưa có - xem ``app/database.py``.
    """
    init_db()
    yield


# ----------------------------------------------------------- Khởi tạo FastAPI ---
app = FastAPI(
    title="TTCS K18C4 - Truy xuất nguồn gốc và giám sát chuỗi lạnh nông sản",
    description=(
        "Backend API cho hệ thống truy xuất nguồn gốc và giám sát chuỗi lạnh "
        "nông sản.\n\n"
        "- **Sprint 1**: khung dự án + `GET /health`.\n"
        "- **Sprint 2**: module Farm (`POST /farms`, `GET /farms`, "
        "`PUT /farms/{farm_id}`, `DELETE /farms/{farm_id}`).\n"
        "- **Sprint 3**: module Batch - lô nông sản (`POST /batches`, "
        "`GET /batches`, `GET /batches/{batch_id}`, `PUT /batches/{batch_id}`, "
        "`DELETE /batches/{batch_id}`).\n"
        "- **Sprint 4**: đăng nhập + phân quyền cơ bản (`POST /auth/login`, "
        "`GET /users`). **Không dùng JWT**: các API cần quyền dùng HTTP Basic - "
        "bấm nút **Authorize** ở trên rồi nhập `admin` / `123456` (hoặc "
        "`farmer` / `123456`) để test.\n"
        "- **Sprint 5**: hoàn thiện CRUD (`PUT`/`DELETE` cho `/farms` và "
        "`/batches`) + dashboard thống kê trên giao diện. Nhóm `DELETE` yêu cầu "
        "role `admin` (farmer nhận `403`), các API còn lại cho cả `farmer`.\n"
        "- **Sprint 6**: danh mục **sản phẩm dùng chung** cho mọi tổ chức "
        "(`GET /products`, `POST /products`, `PUT /products/{product_id}`). "
        "Mọi vai trò đọc được danh mục; **chỉ `admin`** được thêm/sửa - farmer "
        "gọi `POST`/`PUT` nhận `403 Forbidden`."
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
app.include_router(events.router)
app.include_router(products.router)
app.include_router(users.router)
