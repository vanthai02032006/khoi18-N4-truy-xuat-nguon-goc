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

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ---------------------------------------------------------------- Cấu hình ---
# Thư mục `backend/` (cha của thư mục `app/`) - nơi đặt file database .db
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# Đường dẫn file SQLite: backend/ttcs.db
DATABASE_FILE: Path = BASE_DIR / "ttcs.db"
DATABASE_URL: str = f"sqlite:///{DATABASE_FILE.as_posix()}"

# -------------------------------------------------------------- SQLAlchemy ---
# `check_same_thread=False` là bắt buộc với SQLite khi dùng cùng FastAPI,
# vì mỗi request có thể được xử lý trên một thread khác nhau.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
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
    from decimal import Decimal
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
                    quantity=Decimal("1500.0000"),
                    harvest_date=date(2026, 9, 25),
                    batch_code=f"LOT-{f1:02d}-20260925-01",
                    owner="farmer",
                ),
                Batch(
                    farm_id=f1,
                    product_name="Xoài Cát Chu Xuất Khẩu Sang Nhật",
                    quantity=Decimal("2200.0000"),
                    harvest_date=date(2026, 9, 28),
                    batch_code=f"LOT-{f1:02d}-20260928-02",
                    owner="farmer",
                ),
                Batch(
                    farm_id=f2,
                    product_name="Sầu Riêng Ri6 Cơm Vàng Hạt Lép",
                    quantity=Decimal("3400.0000"),
                    harvest_date=date(2026, 9, 26),
                    batch_code=f"LOT-{f2:02d}-20260926-01",
                    owner="farmer",
                ),
                Batch(
                    farm_id=f2,
                    product_name="Sầu Riêng Ri6 Tuyển Chọn Loại Đặc Biệt",
                    quantity=Decimal("4000.0000"),
                    harvest_date=date(2026, 9, 29),
                    batch_code=f"LOT-{f2:02d}-20260929-02",
                    owner="farmer",
                ),
                Batch(
                    farm_id=f3,
                    product_name="Bưởi Da Xanh Đạt Chuẩn GlobalGAP",
                    quantity=Decimal("2800.0000"),
                    harvest_date=date(2026, 9, 27),
                    batch_code=f"LOT-{f3:02d}-20260927-01",
                    owner="farmer",
                ),
                Batch(
                    farm_id=f4,
                    product_name="Thanh Long Ruột Đỏ Hàng Chọn Xuất Khẩu",
                    quantity=Decimal("4100.0000"),
                    harvest_date=date(2026, 9, 29),
                    batch_code=f"LOT-{f4:02d}-20260929-01",
                    owner="farmer",
                ),
                Batch(
                    farm_id=f5,
                    product_name="Nhãn Lồng Hưng Yên Hương Chi Loại 1",
                    quantity=Decimal("1800.0000"),
                    harvest_date=date(2026, 9, 27),
                    batch_code=f"LOT-{f5:02d}-20260927-01",
                    owner="farmer",
                ),
                Batch(
                    farm_id=f6,
                    product_name="Bơ Booth 7 Đắk Lắk Trái To Đều",
                    quantity=Decimal("3200.0000"),
                    harvest_date=date(2026, 9, 28),
                    batch_code=f"LOT-{f6:02d}-20260928-01",
                    owner="farmer",
                ),
                Batch(
                    farm_id=f7,
                    product_name="Vải Thiều Lục Ngạn Chuẩn VietGAP Đóng Hộp",
                    quantity=Decimal("5000.0000"),
                    harvest_date=date(2026, 9, 26),
                    batch_code=f"LOT-{f7:02d}-20260926-01",
                    owner="farmer",
                ),
                Batch(
                    farm_id=f8,
                    product_name="Chè Ô Long Mộc Châu Búp Non Thu Hái Sớm",
                    quantity=Decimal("850.0000"),
                    harvest_date=date(2026, 9, 30),
                    batch_code=f"LOT-{f8:02d}-20260930-01",
                    owner="farmer",
                ),
            ]
            db.add_all(batches_data)
            db.commit()
    except SQLAlchemyError:
        db.rollback()
    finally:
        db.close()


def seed_sample_batch_events() -> None:
    """Tạo chuỗi 10 sự kiện chuẩn nối băm mật mã SHA-256 cho Lô nông sản #1 nếu chưa có."""
    from app.models import Batch, BatchEvent
    from app.event_chain import build_10_events_for_batch
    from sqlalchemy import func

    db: Session = SessionLocal()
    try:
        event_count = db.scalar(select(func.count()).select_from(BatchEvent)) or 0
        if event_count == 0:
            first_batch = db.query(Batch).order_by(Batch.id.asc()).first()
            if first_batch:
                build_10_events_for_batch(db, first_batch.id)
    except SQLAlchemyError:
        db.rollback()
    finally:
        db.close()


def migrate_batches_table() -> None:
    """Tự động bổ sung các cột còn thiếu cho bảng batches (parent_id, is_restricted, batch_code, owner)."""
    from sqlalchemy import text

    with engine.begin() as conn:
        cols_result = conn.execute(text("PRAGMA table_info(batches)")).fetchall()
        cols = [row[1] for row in cols_result]
        if cols:
            if "parent_id" not in cols:
                conn.execute(text("ALTER TABLE batches ADD COLUMN parent_id INTEGER REFERENCES batches(id)"))
            if "is_restricted" not in cols:
                conn.execute(text("ALTER TABLE batches ADD COLUMN is_restricted BOOLEAN NOT NULL DEFAULT 0"))
            if "batch_code" not in cols:
                conn.execute(text("ALTER TABLE batches ADD COLUMN batch_code VARCHAR(100)"))
            if "owner" not in cols:
                conn.execute(text("ALTER TABLE batches ADD COLUMN owner VARCHAR(255)"))

            # Gán mã lô mặc định cho các lô cũ nếu đang NULL
            rows = conn.execute(text("SELECT id, farm_id, harvest_date FROM batches WHERE batch_code IS NULL")).fetchall()
            for r in rows:
                date_clean = str(r[2]).replace("-", "")
                code = f"LOT-{r[1]:02d}-{date_clean}-{r[0]:02d}"
                conn.execute(
                    text("UPDATE batches SET batch_code = :code, owner = 'farmer' WHERE id = :id"),
                    {"code": code, "id": r[0]},
                )

        # Đảm bảo bảng quan hệ phả hệ batch_relations (T-44 / T-46) tồn tại
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS batch_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                child_batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                used_quantity NUMERIC(12, 4) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_parent_child_batch UNIQUE (parent_batch_id, child_batch_id)
            )
        """))


def init_db() -> None:
    """Tạo toàn bộ bảng trong database dựa trên metadata của các models.

    Được gọi một lần khi ứng dụng khởi động (xem ``app/main.py``):

    - ``Base.metadata.create_all()``: bảng chưa có thì tạo, bảng đã có thì
      giữ nguyên (không làm mất dữ liệu đang lưu).
    - ``migrate_batches_table()``: tự động nâng cấp cấu trúc bảng batches nếu thiếu cột.
    - ``seed_default_users()``: tạo 2 tài khoản mặc định cho chức năng đăng nhập
      + phân quyền (Sprint 4).
    - ``seed_sample_agricultural_data()``: nạp dữ liệu mẫu về thửa đất và lô nông sản.
    - ``seed_sample_batch_events()``: nạp 10 sự kiện chuẩn chuỗi lạnh cho lô đầu tiên.
    """
    from app import models  # noqa: F401  (import để đăng ký metadata)

    Base.metadata.create_all(bind=engine)
    migrate_batches_table()
    seed_default_users()
    seed_sample_agricultural_data()
    seed_sample_batch_events()


