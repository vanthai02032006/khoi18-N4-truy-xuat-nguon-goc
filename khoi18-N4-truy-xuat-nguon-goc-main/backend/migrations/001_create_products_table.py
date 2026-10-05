"""Migration 001: Tạo bảng products (T-14 / SCRUM-30).

Mô tả & Yêu cầu kỹ thuật:
- Tạo bảng `products` KHÔNG có cột `organization_id` (bảng danh mục dùng chung toàn hệ thống, không gắn tenant ID).
- Ràng buộc UNIQUE cho trường tên sản phẩm (`name`) để chống trùng lặp.
- Trường đơn vị tính (`unit`) sử dụng kiểu dữ liệu ENUM chứa các giá trị chuẩn (kg, g, ton, liter, box, bottle, piece, bundle).
- Đảm bảo migration có thể tiến (up/upgrade) và lùi (down/downgrade/rollback) an toàn.
"""

import sys
from pathlib import Path
from datetime import datetime

# Đảm bảo in tiếng Việt không bị lỗi charmap trên Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Đảm bảo import được app package khi chạy script trực tiếp
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    inspect,
)

# Định nghĩa các giá trị Enum chuẩn
PRODUCT_UNITS = ("kg", "g", "ton", "liter", "box", "bottle", "piece", "bundle")


def upgrade(bind):
    """Tiến trình nâng cấp (UP / Upgrade) - Tạo bảng products."""
    inspector = inspect(bind)
    if "products" in inspector.get_table_names():
        print("[MIGRATION 001] Bảng 'products' đã tồn tại. Bỏ qua bước tạo.")
        return

    meta = MetaData()
    products_table = Table(
        "products",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", String(255), nullable=False, unique=True, index=True),
        # Lưu ý: Không có cột organization_id (danh mục dùng chung toàn hệ thống)
        Column(
            "unit",
            Enum(*PRODUCT_UNITS, name="product_unit_enum", native_enum=False),
            nullable=False,
        ),
        Column("description", String(500), nullable=True),
        Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
        Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False),
    )

    products_table.create(bind=bind, checkfirst=True)
    print("[MIGRATION 001 UP] Tạo thành công bảng 'products' không có cột organization_id, name UNIQUE, unit ENUM.")


def downgrade(bind):
    """Tiến trình lùi/hạ cấp an toàn (DOWN / Downgrade / Rollback) - Xoá bảng products."""
    inspector = inspect(bind)
    if "products" not in inspector.get_table_names():
        print("[MIGRATION 001 DOWN] Bảng 'products' không tồn tại. Không cần rollback.")
        return

    meta = MetaData()
    products_table = Table("products", meta, autoload_with=bind)
    products_table.drop(bind=bind, checkfirst=True)
    print("[MIGRATION 001 DOWN] Rollback hoàn tất: Đã xoá an toàn bảng 'products'.")


if __name__ == "__main__":
    import sys
    from app.database import engine

    action = sys.argv[1] if len(sys.argv) > 1 else "up"
    if action == "up":
        upgrade(engine)
    elif action == "down":
        downgrade(engine)
    else:
        print(f"Unknown action: {action}. Use 'up' or 'down'.")
