"""Bộ kiểm thử đơn vị & kiểm thử chấp nhận (DoD / Acceptance Criteria) cho Task T-37 (SCRUM-53).

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
1. Migration tiến (upgrade) và lùi (downgrade) an toàn.
2. Tạo chỉ mục trên cả cột cha (parent_batch_id) lẫn con (child_batch_id) để phục vụ truy ngược và truy xuôi tối ưu.
3. Ràng buộc UNIQUE trên cặp [lô cha, lô con] để tránh ghi trùng quan hệ.
4. Một lô con của phép gộp (MERGE) có nhiều dòng cha.
5. Phép tách (SPLIT): Một lô cha có nhiều dòng con.
6. API truy ngược (Backward trace) và truy xuôi (Forward trace) hoạt động chính xác.
"""

from datetime import date
from pathlib import Path
import sys

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

# Đảm bảo import được package `app` và `migrations` từ thư mục `backend/`
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.models import (
    Base,
    Batch,
    BatchLineage,
    Farm,
    RELATION_MERGE,
    RELATION_SPLIT,
    ROLE_ADMIN,
    ROLE_FARMER,
    User,
)
from app.routers.lineage import (
    get_batch_genealogy,
    list_batch_lineages,
    record_batch_lineage,
    trace_backward,
    trace_forward,
)
from app.schemas import BatchLineageCreate
from migrations.scrum_53_batch_lineage import downgrade, upgrade


@pytest.fixture
def engine():
    """Tạo engine SQLite in-memory cho mỗi bài test."""
    test_engine = create_engine("sqlite:///:memory:")
    # Tạo các bảng cơ sở (farms, batches, users...)
    Base.metadata.create_all(test_engine)
    return test_engine


@pytest.fixture
def db_session(engine):
    """Session kết nối cơ sở dữ liệu test."""
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def sample_batches(db_session):
    """Tạo các lô hàng mẫu để kiểm thử phả hệ."""
    farm = Farm(name="Vườn mẫu", location="Đồng Tháp", area=3.0, owner="HTX")
    db_session.add(farm)
    db_session.commit()

    b1 = Batch(farm_id=farm.id, product_name="Xoài Cát Chu Lô 1", quantity=1000.0, harvest_date=date(2026, 1, 1), code="7X9KM2RP")
    b2 = Batch(farm_id=farm.id, product_name="Xoài Cát Chu Lô 2", quantity=800.0, harvest_date=date(2026, 1, 2), code="8Y2LP3MN")
    b3 = Batch(farm_id=farm.id, product_name="Xoài Sấy Hộp Thành Phẩm", quantity=1500.0, harvest_date=date(2026, 1, 3), code="9Z3MR4PQ")
    b4 = Batch(farm_id=farm.id, product_name="Xoài Loại 1 Phân Tách A", quantity=500.0, harvest_date=date(2026, 1, 4), code="4A5BT6UV")
    b5 = Batch(farm_id=farm.id, product_name="Xoài Loại 2 Phân Tách B", quantity=500.0, harvest_date=date(2026, 1, 4), code="5B6CU7WX")

    db_session.add_all([b1, b2, b3, b4, b5])
    db_session.commit()
    return {"b1": b1, "b2": b2, "b3": b3, "b4": b4, "b5": b5}


class TestBatchLineageDoD:
    """Các bài kiểm thử tiêu chí nghiệm thu DoD của SCRUM-53."""

    def test_migration_upgrade_and_downgrade_safe(self, engine):
        """DoD: Migration tiến (upgrade) và lùi (downgrade) được an toàn."""
        # 1. Chạy downgrade xoá bảng trước (nếu có từ Base.metadata.create_all)
        downgrade(engine)

        inspector = inspect(engine)
        assert "batch_lineage" not in inspector.get_table_names()

        # 2. Chạy upgrade()
        upgrade(engine)
        inspector = inspect(engine)
        assert "batch_lineage" in inspector.get_table_names()

        # Kiểm tra chỉ mục đã được tạo sau upgrade
        indexes = inspector.get_indexes("batch_lineage")
        index_names = [idx["name"] for idx in indexes]
        assert "ix_batch_lineage_parent_batch_id" in index_names
        assert "ix_batch_lineage_child_batch_id" in index_names

        # 3. Chạy downgrade() lùi an toàn
        downgrade(engine)
        inspector = inspect(engine)
        assert "batch_lineage" not in inspector.get_table_names()

        # 4. Tái chạy upgrade() kiểm tra tính idempotent
        upgrade(engine)
        inspector = inspect(engine)
        assert "batch_lineage" in inspector.get_table_names()

    def test_indexes_on_parent_and_child_columns(self, engine):
        """DoD: Tạo chỉ mục trên cả cột cha lẫn con để phục vụ truy ngược và truy xuôi tối ưu."""
        inspector = inspect(engine)
        indexes = inspector.get_indexes("batch_lineage")

        parent_idx = next((idx for idx in indexes if idx["name"] == "ix_batch_lineage_parent_batch_id"), None)
        child_idx = next((idx for idx in indexes if idx["name"] == "ix_batch_lineage_child_batch_id"), None)

        assert parent_idx is not None, "Thiếu chỉ mục trên cột cha"
        assert parent_idx["column_names"] == ["parent_batch_id"]

        assert child_idx is not None, "Thiếu chỉ mục trên cột con"
        assert child_idx["column_names"] == ["child_batch_id"]

    def test_unique_constraint_on_parent_and_child_pair(self, db_session, sample_batches):
        """Ràng buộc kỹ thuật: Ràng buộc UNIQUE trên cặp [lô cha, lô con] để tránh ghi trùng quan hệ."""
        b1 = sample_batches["b1"]
        b3 = sample_batches["b3"]

        # Lần 1: Thêm quan hệ thành công
        lin1 = BatchLineage(
            parent_batch_id=b1.id,
            child_batch_id=b3.id,
            transferred_quantity=500.0,
            relation_type=RELATION_MERGE,
            created_at="2026-10-07T00:00:00Z",
        )
        db_session.add(lin1)
        db_session.commit()

        # Lần 2: Cố tình ghi trùng cặp [b1.id, b3.id] -> phải bị UniqueConstraint chặn
        lin_duplicate = BatchLineage(
            parent_batch_id=b1.id,
            child_batch_id=b3.id,
            transferred_quantity=200.0,
            relation_type=RELATION_MERGE,
            created_at="2026-10-07T01:00:00Z",
        )
        db_session.add(lin_duplicate)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_merge_operation_multiple_parent_rows(self, db_session, sample_batches):
        """Mục tiêu: Một lô con của phép gộp có nhiều dòng cha."""
        b1 = sample_batches["b1"]
        b2 = sample_batches["b2"]
        b3 = sample_batches["b3"]

        # Phép gộp b3 từ cả b1 (800kg) và b2 (700kg) -> b3 có 2 dòng cha khác nhau
        lin_parent1 = BatchLineage(
            parent_batch_id=b1.id,
            child_batch_id=b3.id,
            transferred_quantity=800.0,
            relation_type=RELATION_MERGE,
            created_at="2026-10-07T02:00:00Z",
        )
        lin_parent2 = BatchLineage(
            parent_batch_id=b2.id,
            child_batch_id=b3.id,
            transferred_quantity=700.0,
            relation_type=RELATION_MERGE,
            created_at="2026-10-07T02:05:00Z",
        )
        db_session.add_all([lin_parent1, lin_parent2])
        db_session.commit()

        # Xác minh: Lô b3 có đúng 2 dòng cha
        parents_of_b3 = db_session.query(BatchLineage).filter_by(child_batch_id=b3.id).all()
        assert len(parents_of_b3) == 2
        parent_ids = {p.parent_batch_id for p in parents_of_b3}
        assert parent_ids == {b1.id, b2.id}
        assert all(p.relation_type == RELATION_MERGE for p in parents_of_b3)

    def test_split_operation_multiple_children(self, db_session, sample_batches):
        """Mục tiêu: Phép tách (SPLIT) - Một lô cha tách thành nhiều lô con."""
        b1 = sample_batches["b1"]
        b4 = sample_batches["b4"]
        b5 = sample_batches["b5"]

        # Tách b1 thành b4 (500kg) và b5 (500kg)
        lin_child1 = BatchLineage(
            parent_batch_id=b1.id,
            child_batch_id=b4.id,
            transferred_quantity=500.0,
            relation_type=RELATION_SPLIT,
            created_at="2026-10-07T03:00:00Z",
        )
        lin_child2 = BatchLineage(
            parent_batch_id=b1.id,
            child_batch_id=b5.id,
            transferred_quantity=500.0,
            relation_type=RELATION_SPLIT,
            created_at="2026-10-07T03:05:00Z",
        )
        db_session.add_all([lin_child1, lin_child2])
        db_session.commit()

        children_of_b1 = db_session.query(BatchLineage).filter_by(parent_batch_id=b1.id).all()
        assert len(children_of_b1) == 2
        child_ids = {c.child_batch_id for c in children_of_b1}
        assert child_ids == {b4.id, b5.id}

    def test_lineage_api_endpoints_trace(self, db_session, sample_batches):
        """Kiểm tra API ghi nhận quan hệ và truy ngược / truy xuôi."""
        farmer = User(username="farmer_test", password="xxx", role=ROLE_FARMER)
        b1 = sample_batches["b1"]
        b2 = sample_batches["b2"]
        b3 = sample_batches["b3"]

        # 1. Gọi record_batch_lineage API
        req_merge1 = BatchLineageCreate(
            parent_batch_id=b1.id,
            child_batch_id=b3.id,
            transferred_quantity=600.0,
            relation_type=RELATION_MERGE,
        )
        resp1 = record_batch_lineage(data=req_merge1, current_user=farmer, db=db_session)
        assert resp1.id is not None
        assert resp1.parent_batch_id == b1.id
        assert resp1.child_batch_id == b3.id

        # 2. Ghi tiếp cha thứ 2 vào cùng lô con b3
        req_merge2 = BatchLineageCreate(
            parent_batch_id=b2.id,
            child_batch_id=b3.id,
            transferred_quantity=400.0,
            relation_type=RELATION_MERGE,
        )
        resp2 = record_batch_lineage(data=req_merge2, current_user=farmer, db=db_session)
        assert resp2.id is not None

        # 3. Ghi trùng -> trả 409 Conflict
        with pytest.raises(HTTPException) as exc_info:
            record_batch_lineage(data=req_merge1, current_user=farmer, db=db_session)
        assert exc_info.value.status_code == 409

        # 4. Truy ngược (Backward trace) lô con b3 -> nhận được 2 cha
        backward_nodes = trace_backward(batch_id=b3.id, db=db_session)
        assert len(backward_nodes) == 2
        assert {n.batch_id for n in backward_nodes} == {b1.id, b2.id}

        # 5. Truy xuôi (Forward trace) lô cha b1 -> nhận được 1 con (b3)
        forward_nodes = trace_forward(batch_id=b1.id, db=db_session)
        assert len(forward_nodes) == 1
        assert forward_nodes[0].batch_id == b3.id

        # 6. Tổng hợp toàn bộ phả hệ
        genealogy = get_batch_genealogy(batch_id=b3.id, db=db_session)
        assert genealogy.target_batch_id == b3.id
        assert len(genealogy.parents) == 2
        assert len(genealogy.children) == 0
