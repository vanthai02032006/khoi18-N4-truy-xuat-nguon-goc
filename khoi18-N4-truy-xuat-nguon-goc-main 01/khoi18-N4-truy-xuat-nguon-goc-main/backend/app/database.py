"""Cấu hình kết nối cơ sở dữ liệu SQLite bằng SQLAlchemy.

File này chịu trách nhiệm duy nhất một việc: tạo `engine`, `SessionLocal`,
`Base` và các hàm tiện ích dùng chung cho tầng truy cập dữ liệu.

Sprint 1: SQLite (file local, không cần cài server) để chạy demo nhanh.
Sprint 4: thêm `seed_default_users()` - tạo sẵn 2 tài khoản demo
(`admin`/`farmer`, mật khẩu `123456`) mỗi khi khởi động nếu chưa có.
Sprint 6: hỗ trợ đầy đủ các bảng Handover và Event kèm khởi tạo dữ liệu mẫu.
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

# Mỗi request sẽ mở một Session riêng, không tự commit/autoflush.
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    """Base class cho toàn bộ ORM models.

    Mọi model trong ``app/models.py`` đều kế thừa class này để SQLAlchemy
    biết cách ánh xạ (map) class Python -> bảng trong database.
    """


# ------------------------------------------------------------------- Helper ---
def get_db() -> Generator[Session, None, None]:
    """Dependency cung cấp Session cho mỗi request và tự đóng khi xong."""
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
    """
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
                continue  # tài khoản đã có -> giữ nguyên

            db.add(
                User(
                    username=item["username"],
                    password=hash_password(item["password"]),
                    role=item["role"],
                )
            )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
    finally:
        db.close()


def seed_sample_agricultural_data() -> None:
    """Tạo dữ liệu mẫu các thửa đất, lô nông sản, bàn giao và nhật ký sự kiện nếu database trống."""
    from datetime import date
    from sqlalchemy import func
    from app.events import (
        EVENT_TYPE_BATCH_CREATED,
        EVENT_TYPE_HANDOVER_ACCEPTED,
        EVENT_TYPE_HANDOVER_PENDING,
        record_event,
    )
    from app.models import Farm, Batch, Handover, HANDOVER_STATUS_PENDING, HANDOVER_STATUS_ACCEPTED

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
                farms_data[6].id,
                farms_data[6].id,
                farms_data[7].id,
            )

            batches_data = [
                Batch(
                    farm_id=f1,
                    product_name="Xoài Cát Chu Loại 1 (VietGAP)",
                    quantity=1500.0,
                    harvest_date=date(2026, 9, 25),
                    current_owner="Hợp tác xã Xoài Mỹ Xương",
                ),
                Batch(
                    farm_id=f1,
                    product_name="Xoài Cát Chu Xuất Khẩu Sang Nhật",
                    quantity=2200.0,
                    harvest_date=date(2026, 9, 28),
                    current_owner="Công ty Xuất nhập khẩu Rau quả Đồng Tháp",
                ),
                Batch(
                    farm_id=f2,
                    product_name="Sầu Riêng Ri6 Cơm Vàng Hạt Lép",
                    quantity=3400.0,
                    harvest_date=date(2026, 9, 26),
                    current_owner="Hộ Nông Dân Nguyễn Văn Nam",
                ),
                Batch(
                    farm_id=f2,
                    product_name="Sầu Riêng Ri6 Tuyển Chọn Loại Đặc Biệt",
                    quantity=4000.0,
                    harvest_date=date(2026, 9, 29),
                    current_owner="Hộ Nông Dân Nguyễn Văn Nam",
                ),
                Batch(
                    farm_id=f3,
                    product_name="Bưởi Da Xanh Đạt Chuẩn GlobalGAP",
                    quantity=2800.0,
                    harvest_date=date(2026, 9, 27),
                    current_owner="HTX Nông Nghiệp Sông Xoài",
                ),
                Batch(
                    farm_id=f4,
                    product_name="Thanh Long Ruột Đỏ Hàng Chọn Xuất Khẩu",
                    quantity=4100.0,
                    harvest_date=date(2026, 9, 29),
                    current_owner="Tổ Hợp Tác Thanh Long Hàm Mỹ",
                ),
                Batch(
                    farm_id=f5,
                    product_name="Nhãn Lồng Hưng Yên Hương Chi Loại 1",
                    quantity=1800.0,
                    harvest_date=date(2026, 9, 27),
                    current_owner="Hợp Tác Xã Nhãn Miền Thiết",
                ),
                Batch(
                    farm_id=f6,
                    product_name="Bơ Booth 7 Đắk Lắk Trái To Đều",
                    quantity=3200.0,
                    harvest_date=date(2026, 9, 28),
                    current_owner="Hộ Canh Tác Lê Hoàng Long",
                ),
                Batch(
                    farm_id=f7,
                    product_name="Vải Thiều Lục Ngạn Chuẩn VietGAP Đóng Hộp",
                    quantity=5000.0,
                    harvest_date=date(2026, 9, 26),
                    current_owner="Hợp Tác Xã Nông Sản Quý Sơn",
                ),
                Batch(
                    farm_id=f8,
                    product_name="Chè Ô Long Mộc Châu Búp Non Thu Hái Sớm",
                    quantity=850.0,
                    harvest_date=date(2026, 9, 30),
                    current_owner="Công Ty CP Nông Sản Sạch Mộc Châu",
                ),
            ]
            db.add_all(batches_data)
            db.commit()

            # Ghi nhận sự kiện khởi tạo cho các lô
            for b in batches_data:
                record_event(
                    db=db,
                    batch_id=b.id,
                    event_type=EVENT_TYPE_BATCH_CREATED,
                    actor_name=b.current_owner,
                    description=f"Thu hoạch và ghi nhận lô nông sản #{b.id} ({b.product_name}) - Khối lượng: {b.quantity} kg.",
                    metadata_info={"product_name": b.product_name, "quantity": b.quantity},
                )
            db.commit()

            # Tạo bàn giao mẫu 1: Bàn giao CHỜ XỬ LÝ (pending) cho Batch 1 (Lô vẫn thuộc bên giao)
            b1 = batches_data[0]
            h1 = Handover(
                batch_id=b1.id,
                sender_name=b1.current_owner,
                receiver_name="Trung tâm Logistics Chuỗi Lạnh Đồng Tháp",
                status=HANDOVER_STATUS_PENDING,
                notes="Bàn giao tại cửa kho phân loại để đưa vào quy trình làm lạnh sơ bộ.",
            )
            db.add(h1)
            record_event(
                db=db,
                batch_id=b1.id,
                event_type=EVENT_TYPE_HANDOVER_PENDING,
                actor_name=b1.current_owner,
                description=(
                    f"Khởi tạo yêu cầu bàn giao lô nông sản #{b1.id} sang [Trung tâm Logistics Chuỗi Lạnh Đồng Tháp]. "
                    f"Trạng thái: Chờ xử lý. Quyền quản lý vẫn thuộc bên giao [{b1.current_owner}]."
                ),
                metadata_info={"status": HANDOVER_STATUS_PENDING, "receiver": "Trung tâm Logistics Chuỗi Lạnh Đồng Tháp"},
            )

            # Tạo bàn giao mẫu 2: Bàn giao ĐÃ NHẬN (accepted) cho Batch 2 (Quyền quản lý đã sang bên nhận)
            b2 = batches_data[1]
            h2 = Handover(
                batch_id=b2.id,
                sender_name="Hợp tác xã Xoài Mỹ Xương",
                receiver_name="Công ty Xuất nhập khẩu Rau quả Đồng Tháp",
                status=HANDOVER_STATUS_ACCEPTED,
                notes="Đã kiểm tra chất lượng và niêm phong container lạnh.",
            )
            db.add(h2)
            record_event(
                db=db,
                batch_id=b2.id,
                event_type=EVENT_TYPE_HANDOVER_ACCEPTED,
                actor_name="Công ty Xuất nhập khẩu Rau quả Đồng Tháp",
                description=(
                    f"Tiếp nhận thành công lô nông sản #{b2.id}. Quyền quản lý lô hàng "
                    f"đã chuyển sang [Công ty Xuất nhập khẩu Rau quả Đồng Tháp]."
                ),
                metadata_info={"status": HANDOVER_STATUS_ACCEPTED, "receiver": "Công ty Xuất nhập khẩu Rau quả Đồng Tháp"},
            )
            db.commit()

    except SQLAlchemyError:
        db.rollback()
    finally:
        db.close()


def init_db() -> None:
    """Tạo toàn bộ bảng trong database dựa trên metadata của các models."""
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    seed_default_users()
    seed_sample_agricultural_data()
