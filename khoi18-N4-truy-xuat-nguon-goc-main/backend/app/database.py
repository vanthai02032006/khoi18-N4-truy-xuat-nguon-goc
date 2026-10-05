"""Cấu hình kết nối cơ sở dữ liệu SQLite bằng SQLAlchemy.

File này chịu trách nhiệm duy nhất một việc: tạo `engine`, `SessionLocal`,
`Base` và các hàm tiện ích dùng chung cho tầng truy cập dữ liệu.

Sprint 1: SQLite (file local, không cần cài server) để chạy demo nhanh.
Sprint sau: muốn đổi sang PostgreSQL/MySQL thì sửa `DATABASE_URL`, cài driver
tương ứng (`psycopg`, `pymysql`...), bỏ `connect_args` chỉ dành riêng cho
SQLite ở dưới - phần ORM (models/schemas/routers) không phải sửa.

Sprint 4: thêm `seed_default_users()` - tạo sẵn 2 tài khoản demo
(`admin`/`farmer`, mật khẩu `123456`) mỗi khi khởi động nếu chưa có.
"""

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ---------------------------------------------------------------- Cấu hình ---
# Thư mục `backend/` (cha của thư mục `app/`) - nơi đặt file database .db
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# Đường dẫn database: Ưu tiên lấy từ biến môi trường DATABASE_URL (Docker / Production PostgreSQL)
# Nếu không có, mặc định dùng SQLite local file ttcs.db
ENV_DATABASE_URL = os.getenv("DATABASE_URL")
if ENV_DATABASE_URL:
    DATABASE_URL: str = ENV_DATABASE_URL
    # Tương thích driver postgresql
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_FILE: Path = BASE_DIR / "ttcs.db"
    DATABASE_URL: str = f"sqlite:///{DATABASE_FILE.as_posix()}"

# -------------------------------------------------------------- SQLAlchemy ---
# `check_same_thread=False` chỉ dùng cho SQLite
is_sqlite = DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False,  # đổi thành True nếu muốn xem câu SQL sinh ra khi debug
)

# Mỗi request sẽ mở một Session riêng, không tự commit/autoflush (an toàn hơn).
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    """Base class cho toàn bộ ORM models.

    Mọi model trong ``app/models.py`` đều kế thừa class này để SQLAlchemy
    biết cách ánh xạ (map) class Python -> bảng trong database.
    """


# ------------------------------------------------------------------- Helper ---
def get_db() -> Generator[Session, None, None]:
    """Dependency cung cấp Session cho mỗi request và tự đóng khi xong.

    Cách dùng trong router::

        from fastapi import Depends
        from sqlalchemy.orm import Session
        from app.database import get_db

        @router.get("/items")
        def list_items(db: Session = Depends(get_db)):
            ...
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_default_users() -> None:
    """Tạo 2 tài khoản mặc định (``admin`` / ``farmer``) nếu chưa tồn tại.

    Tài khoản mặc định (mật khẩu giống nhau để dễ demo):

    ==========  ==========  =====================
    username    password    role
    ==========  ==========  =====================
    ``admin``   ``123456``  ``admin`` (toàn quyền)
    ``farmer``  ``123456``  ``farmer`` (nông dân)
    ==========  ==========  =====================

    Hàm **idempotent** - gọi lại nhiều lần (mỗi lần server khởi động) cũng
    không tạo trùng, và **không ghi đè** tài khoản đã có (kể cả khi người dùng
    đã đổi mật khẩu), nhờ kiểm tra ``username`` trước khi insert.

    Mật khẩu được băm bằng ``hash_password`` (SHA-256) trước khi lưu - database
    không bao giờ chứa mật khẩu dạng thô.
    """
    # Import trong hàm để tránh import vòng: models cần `Base` ở module này,
    # còn security cần `get_db` ở module này.
    from app.models import ROLE_ADMIN, ROLE_FARMER, User
    from app.security import hash_password

    default_users: tuple[dict[str, str], ...] = (
        {"username": "admin", "password": "123456", "role": ROLE_ADMIN},
        {"username": "farmer", "password": "123456", "role": ROLE_FARMER},
    )

    db: Session = SessionLocal()
    try:
        for item in default_users:
            exists = db.scalar(select(User).where(User.username == item["username"]))
            if exists is not None:
                continue  # tài khoản đã có -> giữ nguyên, không ghi đè

            db.add(
                User(
                    username=item["username"],
                    password=hash_password(item["password"]),
                    role=item["role"],
                )
            )
        db.commit()
    except SQLAlchemyError:
        # Không để server chết vì lỗi seed dữ liệu mẫu.
        db.rollback()
    finally:
        db.close()


def seed_sample_agricultural_data() -> None:
    """Tạo dữ liệu mẫu các thửa đất / vùng trồng và lô nông sản thu hoạch nếu database trống.

    Giúp hệ thống có sẵn dữ liệu trực quan sinh động về đất canh tác, cây trồng,
    sản lượng và ngày thu hoạch để phục vụ nghiệm thu và demo ngay khi khởi động.
    """
    from datetime import date
    from sqlalchemy import func
    from app.models import Farm, Batch

    db: Session = SessionLocal()
    try:
        farm_count = db.scalar(select(func.count()).select_from(Farm)) or 0
        if farm_count == 0:
            farms_data = [
                Farm(
                    name="Vùng Trồng Xoài Cát Chu Cao Lãnh (Thửa Đất #1)",
                    location="Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp",
                    area=4.5,
                    owner="Hợp tác xã Xoài Mỹ Xương",
                ),
                Farm(
                    name="Vườn Sầu Riêng Ri6 Chợ Lách (Thửa Đất #2)",
                    location="Xã Vĩnh Thành, Huyện Chợ Lách, Tỉnh Bến Tre",
                    area=6.2,
                    owner="Hộ Nông Dân Nguyễn Văn Nam",
                ),
                Farm(
                    name="Trang Trại Bưởi Da Xanh Sông Xoài (Thửa Đất #3)",
                    location="Xã Sông Xoài, Thị Xã Phú Mỹ, Bà Rịa - Vũng Tàu",
                    area=8.0,
                    owner="HTX Nông Nghiệp Sông Xoài",
                ),
                Farm(
                    name="Vùng Canh Tác Thanh Long Ruột Đỏ (Thửa Đất #4)",
                    location="Xã Hàm Mỹ, Huyện Hàm Thuận Nam, Bình Thuận",
                    area=5.5,
                    owner="Tổ Hợp Tác Thanh Long Hàm Mỹ",
                ),
                Farm(
                    name="Vườn Nhãn Lồng Hưng Yên Hương Chi (Thửa Đất #5)",
                    location="Xã Hồng Nam, Thành Phố Hưng Yên, Tỉnh Hưng Yên",
                    area=3.8,
                    owner="Hợp Tác Xã Nhãn Miền Thiết",
                ),
                Farm(
                    name="Trang Trại Bơ Booth & Sầu Riêng Đắk Lắk (Thửa Đất #6)",
                    location="Xã Ea Ktur, Huyện Cư Kuin, Tỉnh Đắk Lắk",
                    area=12.0,
                    owner="Hộ Canh Tác Lê Hoàng Long",
                ),
                Farm(
                    name="Vùng Trồng Vải Thiều Lục Ngạn (Thửa Đất #7)",
                    location="Xã Quý Sơn, Huyện Lục Ngạn, Tỉnh Bắc Giang",
                    area=7.5,
                    owner="Hợp Tác Xã Nông Sản Quý Sơn",
                ),
                Farm(
                    name="Thửa Đất Canh Tác Chè Ô Long Mộc Châu (Thửa Đất #8)",
                    location="Thị Trấn Nông Trường Mộc Châu, Tỉnh Sơn La",
                    area=15.0,
                    owner="Công Ty CP Nông Sản Sạch Mộc Châu",
                ),
            ]
            db.add_all(farms_data)
            db.commit()

            f1, f2, f3, f4, f5, f6, f7, f8 = (
                farms_data[0].id,
                farms_data[1].id,
                farms_data[2].id,
                farms_data[3].id,
                farms_data[4].id,
                farms_data[5].id,
                farms_data[6].id,
                farms_data[7].id,
            )

            batches_data = [
                Batch(
                    farm_id=f1,
                    product_name="Xoài Cát Chu Loại 1 (VietGAP)",
                    quantity=1500.0,
                    harvest_date=date(2026, 9, 25),
                ),
                Batch(
                    farm_id=f1,
                    product_name="Xoài Cát Chu Xuất Khẩu Sang Nhật",
                    quantity=2200.0,
                    harvest_date=date(2026, 9, 28),
                ),
                Batch(
                    farm_id=f2,
                    product_name="Sầu Riêng Ri6 Cơm Vàng Hạt Lép",
                    quantity=3400.0,
                    harvest_date=date(2026, 9, 26),
                ),
                Batch(
                    farm_id=f2,
                    product_name="Sầu Riêng Ri6 Tuyển Chọn Loại Đặc Biệt",
                    quantity=4000.0,
                    harvest_date=date(2026, 9, 29),
                ),
                Batch(
                    farm_id=f3,
                    product_name="Bưởi Da Xanh Đạt Chuẩn GlobalGAP",
                    quantity=2800.0,
                    harvest_date=date(2026, 9, 27),
                ),
                Batch(
                    farm_id=f4,
                    product_name="Thanh Long Ruột Đỏ Hàng Chọn Xuất Khẩu",
                    quantity=4100.0,
                    harvest_date=date(2026, 9, 29),
                ),
                Batch(
                    farm_id=f5,
                    product_name="Nhãn Lồng Hưng Yên Hương Chi Loại 1",
                    quantity=1800.0,
                    harvest_date=date(2026, 9, 27),
                ),
                Batch(
                    farm_id=f6,
                    product_name="Bơ Booth 7 Đắk Lắk Trái To Đều",
                    quantity=3200.0,
                    harvest_date=date(2026, 9, 28),
                ),
                Batch(
                    farm_id=f7,
                    product_name="Vải Thiều Lục Ngạn Chuẩn VietGAP Đóng Hộp",
                    quantity=5000.0,
                    harvest_date=date(2026, 9, 26),
                ),
                Batch(
                    farm_id=f8,
                    product_name="Chè Ô Long Mộc Châu Búp Non Thu Hái Sớm",
                    quantity=850.0,
                    harvest_date=date(2026, 9, 30),
                ),
            ]
            db.add_all(batches_data)
            db.commit()
    except SQLAlchemyError:
        db.rollback()
    finally:
        db.close()


def seed_sample_products() -> None:
    """Nạp danh mục sản phẩm chuẩn toàn hệ thống (T-14 / SCRUM-30).

    Lưu ý: Bảng danh mục sản phẩm không gắn organization_id.
    """
    from app.models import Product, ProductUnit

    sample_products = [
        {"name": "Xoài Cát Chu", "unit": ProductUnit.KG, "description": "Xoài Cát Chu đặc sản Cao Lãnh đạt chuẩn VietGAP"},
        {"name": "Sầu Riêng Ri6", "unit": ProductUnit.KG, "description": "Sầu Riêng Ri6 cơm vàng hạt lép Chợ Lách"},
        {"name": "Bưởi Da Xanh", "unit": ProductUnit.KG, "description": "Bưởi Da Xanh Sông Xoài chuẩn xuất khẩu GlobalGAP"},
        {"name": "Thanh Long Ruột Đỏ", "unit": ProductUnit.KG, "description": "Thanh Long Ruột Đỏ Hàm Mỹ chất lượng cao"},
        {"name": "Nhãn Lồng Hương Chi", "unit": ProductUnit.KG, "description": "Nhãn Lồng Hưng Yên Hương Chi cùi dày giòn ngọt"},
        {"name": "Bơ Booth 7 Đắk Lắk", "unit": ProductUnit.KG, "description": "Bơ Booth Tây Nguyên sáp dẻo"},
        {"name": "Vải Thiều Lục Ngạn", "unit": ProductUnit.BOX, "description": "Vải Thiều Lục Ngạn đóng hộp tiêu chuẩn"},
        {"name": "Chè Ô Long Mộc Châu", "unit": ProductUnit.BOX, "description": "Chè Ô Long búp non hữu cơ Mộc Châu đóng hộp"},
    ]

    db: Session = SessionLocal()
    try:
        for p in sample_products:
            exists = db.scalar(select(Product).where(Product.name == p["name"]))
            if exists is None:
                db.add(Product(name=p["name"], unit=p["unit"], description=p["description"]))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
    finally:
        db.close()


def init_db() -> None:
    """Tạo toàn bộ bảng trong database dựa trên metadata của các models.

    Được gọi một lần khi ứng dụng khởi động (xem ``app/main.py``):

    - ``Base.metadata.create_all()``: bảng chưa có thì tạo, bảng đã có thì
      giữ nguyên (không làm mất dữ liệu đang lưu).
    - ``seed_default_users()``: tạo 2 tài khoản mặc định cho chức năng đăng nhập
      + phân quyền (Sprint 4).
    - ``seed_sample_agricultural_data()``: nạp dữ liệu mẫu về thửa đất và lô nông sản.
    - ``seed_sample_products()``: nạp dữ liệu danh mục sản phẩm chuẩn toàn hệ thống.
    """
    from sqlalchemy import text
    from app import models  # noqa: F401  (import để đăng ký metadata)

    Base.metadata.create_all(bind=engine)

    # Đảm bảo tương thích schema SQLite local nếu bảng handovers đã tồn tại từ trước
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE handovers ADD COLUMN is_overdue BOOLEAN DEFAULT 0"))
    except Exception:
        pass  # Đã có cột is_overdue

    seed_default_users()
    seed_sample_agricultural_data()
    seed_sample_products()


