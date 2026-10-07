"""Migration T-37 (SCRUM-53): Bảng quan hệ phả hệ lô hàng (Batch Lineage).

Mục tiêu & Đặc tả:
- Bảng quan hệ: lô cha (parent_batch_id), lô con (child_batch_id),
  khối lượng chuyển từ cha sang con (transferred_quantity), loại quan hệ (SPLIT hoặc MERGE).
- Một lô con của phép gộp có nhiều dòng cha.
- Ràng buộc UNIQUE trên cặp [lô cha, lô con] để tránh ghi trùng quan hệ.
- Tạo chỉ mục trên cả cột cha lẫn con để phục vụ truy ngược và truy xuôi tối ưu.
- Cung cấp hàm upgrade() tiến an toàn và downgrade() lùi an toàn (DoD / AC).
"""

import logging
from sqlalchemy import text
from sqlalchemy.engine import Engine, Connection

logger = logging.getLogger(__name__)

TABLE_NAME = "batch_lineage"
INDEX_PARENT = "ix_batch_lineage_parent_batch_id"
INDEX_CHILD = "ix_batch_lineage_child_batch_id"
CONSTRAINT_UNIQUE = "uq_batch_lineage_parent_child"


def upgrade(target: Engine | Connection) -> None:
    """Tiến hành migration (Upgrade) tạo bảng quan hệ phả hệ và các chỉ mục.

    Tạo bảng `batch_lineage` cùng ràng buộc UNIQUE và 2 chỉ mục trên parent/child.
    An toàn và idempotent (chạy nhiều lần không gây lỗi).
    """
    ddl_statements = [
        # 1. Tạo bảng batch_lineage với khoá ngoại và ràng buộc UNIQUE
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_batch_id INTEGER NOT NULL,
            child_batch_id INTEGER NOT NULL,
            transferred_quantity REAL NOT NULL CHECK (transferred_quantity > 0),
            relation_type VARCHAR(20) NOT NULL CHECK (relation_type IN ('SPLIT', 'MERGE')),
            created_at VARCHAR(50) NOT NULL,
            CONSTRAINT fk_batch_lineage_parent FOREIGN KEY (parent_batch_id) REFERENCES batches(id) ON DELETE CASCADE,
            CONSTRAINT fk_batch_lineage_child FOREIGN KEY (child_batch_id) REFERENCES batches(id) ON DELETE CASCADE,
            CONSTRAINT {CONSTRAINT_UNIQUE} UNIQUE (parent_batch_id, child_batch_id)
        );
        """,
        # 2. Tạo chỉ mục trên cột lô cha (parent_batch_id) để tối ưu truy xuôi (Forward trace)
        f"""
        CREATE INDEX IF NOT EXISTS {INDEX_PARENT} ON {TABLE_NAME} (parent_batch_id);
        """,
        # 3. Tạo chỉ mục trên cột lô con (child_batch_id) để tối ưu truy ngược (Backward trace)
        f"""
        CREATE INDEX IF NOT EXISTS {INDEX_CHILD} ON {TABLE_NAME} (child_batch_id);
        """,
    ]

    if isinstance(target, Engine):
        with target.begin() as conn:
            for statement in ddl_statements:
                conn.execute(text(statement.strip()))
    else:
        for statement in ddl_statements:
            target.execute(text(statement.strip()))

    logger.info("Upgrade thành công migration T-37 (SCRUM-53): Đã tạo bảng %s và các chỉ mục.", TABLE_NAME)


def downgrade(target: Engine | Connection) -> None:
    """Quay lùi migration (Downgrade) an toàn.

    Xoá các chỉ mục và xoá bảng `batch_lineage` an toàn mà không ảnh hưởng tới bảng khác.
    """
    ddl_statements = [
        f"DROP INDEX IF EXISTS {INDEX_PARENT};",
        f"DROP INDEX IF EXISTS {INDEX_CHILD};",
        f"DROP TABLE IF EXISTS {TABLE_NAME};",
    ]

    if isinstance(target, Engine):
        with target.begin() as conn:
            for statement in ddl_statements:
                conn.execute(text(statement.strip()))
    else:
        for statement in ddl_statements:
            target.execute(text(statement.strip()))

    logger.info("Downgrade thành công migration T-37 (SCRUM-53): Đã xoá bảng %s và các chỉ mục.", TABLE_NAME)
