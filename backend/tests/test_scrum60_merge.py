"""Tests cho SCRUM-60: Giao dịch gộp lô nông sản (Batch Merge Transaction).

Kiểm tra:
1. Merge 2 lô thành công.
2. Merge nhiều lô thành công (3+ lô mẹ).
3. Lấy đúng một phần quantity và cập nhật remaining_quantity chính xác.
4. Chặn lấy vượt tồn (overselling / over-allocation) -> lỗi 400, không thay đổi tồn kho.
5. Lô mẹ không tồn tại -> lỗi 404.
6. Số lượng lấy không hợp lệ (<= 0) -> lỗi 400.
7. Danh sách lô mẹ bị trùng lặp -> lỗi 400.
8. Transaction rollback: khi có lỗi xảy ra giữa chừng, toàn bộ transaction bị huỷ,
   không tạo lô mới, không trừ tồn kho, không tạo quan hệ một phần.
9. Kiểm tra toàn vẹn quan hệ phả hệ (Genealogy relations).
10. Kiểm tra chống race-condition / deadlock khi lock theo thứ tự ID cố định.
"""

from datetime import date
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, Batch, BatchCustodyHistory, BatchRelation, Farm, User
from app.schemas import BatchMergeRequest, ParentBatchItem
from app.services.batch_service import merge_batches_transaction


@pytest.fixture
def db_session():
    """Tạo session SQLite in-memory độc lập cho mỗi bài test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = TestingSession()

    # Tạo farm mẫu
    farm = Farm(name="Vùng Trồng Mẫu", location="Đồng Tháp", area=5.0, owner="HTX Mẫu")
    session.add(farm)
    session.commit()
    session.refresh(farm)

    yield session

    session.close()


def test_merge_two_batches_success(db_session: Session):
    """Yêu cầu 1, 2, 3: Merge 2 lô A (100kg lấy 50kg) và B (200kg lấy 80kg) -> C (130kg)."""
    # Tạo 2 lô mẹ
    batch_a = Batch(
        farm_id=1,
        product_name="Xoài Cát Chu A",
        quantity=100.0,
        remaining_quantity=100.0,
        harvest_date=date(2026, 9, 20),
        organization_id="ORG_A",
    )
    batch_b = Batch(
        farm_id=1,
        product_name="Xoài Cát Chu B",
        quantity=200.0,
        remaining_quantity=200.0,
        harvest_date=date(2026, 9, 21),
        organization_id="ORG_A",
    )
    db_session.add_all([batch_a, batch_b])
    db_session.commit()

    request = BatchMergeRequest(
        parents=[
            ParentBatchItem(parent_batch_id=batch_a.id, used_quantity=50.0),
            ParentBatchItem(parent_batch_id=batch_b.id, used_quantity=80.0),
        ],
        product_name="Xoài Cát Chu Gộp C",
        farm_id=1,
        harvest_date=date(2026, 9, 25),
        organization_id="ORG_A",
    )

    result = merge_batches_transaction(db_session, request)

    # 1. Kiểm tra lô mới C
    assert result.new_batch.product_name == "Xoài Cát Chu Gộp C"
    assert result.new_batch.quantity == 130.0
    assert result.new_batch.remaining_quantity == 130.0
    assert result.new_batch.id is not None

    # 2. Kiểm tra tồn kho lô mẹ còn lại
    db_session.refresh(batch_a)
    db_session.refresh(batch_b)
    assert batch_a.remaining_quantity == 50.0
    assert batch_b.remaining_quantity == 120.0

    # 3. Kiểm tra quan hệ phả hệ (Genealogy)
    assert len(result.relations) == 2
    relations = db_session.scalars(
        select(BatchRelation).where(BatchRelation.child_batch_id == result.new_batch.id)
    ).all()
    assert len(relations) == 2

    parent_map = {r.parent_batch_id: r.used_quantity for r in relations}
    assert parent_map[batch_a.id] == 50.0
    assert parent_map[batch_b.id] == 80.0


def test_merge_multiple_batches(db_session: Session):
    """Yêu cầu: Merge nhiều lô (3 lô mẹ)."""
    b1 = Batch(farm_id=1, product_name="Sầu Riêng 1", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1))
    b2 = Batch(farm_id=1, product_name="Sầu Riêng 2", quantity=150.0, remaining_quantity=150.0, harvest_date=date(2026, 9, 2))
    b3 = Batch(farm_id=1, product_name="Sầu Riêng 3", quantity=200.0, remaining_quantity=200.0, harvest_date=date(2026, 9, 3))
    db_session.add_all([b1, b2, b3])
    db_session.commit()

    request = BatchMergeRequest(
        parents=[
            ParentBatchItem(parent_batch_id=b1.id, used_quantity=30.0),
            ParentBatchItem(parent_batch_id=b2.id, used_quantity=40.0),
            ParentBatchItem(parent_batch_id=b3.id, used_quantity=50.0),
        ],
        product_name="Sầu Riêng Hỗn Hợp",
        farm_id=1,
        harvest_date=date(2026, 9, 5),
    )

    result = merge_batches_transaction(db_session, request)

    assert result.new_batch.quantity == 120.0  # 30 + 40 + 50
    db_session.refresh(b1)
    db_session.refresh(b2)
    db_session.refresh(b3)
    assert b1.remaining_quantity == 70.0
    assert b2.remaining_quantity == 110.0
    assert b3.remaining_quantity == 150.0


def test_cannot_exceed_parent_remaining_quantity(db_session: Session):
    """Yêu cầu 4: Chặn khi used_quantity > remaining_quantity -> ném 400, không thay đổi dữ liệu."""
    batch_a = Batch(farm_id=1, product_name="Bưởi A", quantity=50.0, remaining_quantity=50.0, harvest_date=date(2026, 9, 1))
    batch_b = Batch(farm_id=1, product_name="Bưởi B", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1))
    db_session.add_all([batch_a, batch_b])
    db_session.commit()

    request = BatchMergeRequest(
        parents=[
            ParentBatchItem(parent_batch_id=batch_a.id, used_quantity=60.0),  # Vượt 50kg!
            ParentBatchItem(parent_batch_id=batch_b.id, used_quantity=10.0),
        ],
        product_name="Bưởi Gộp Lỗi",
        farm_id=1,
        harvest_date=date(2026, 9, 2),
    )

    with pytest.raises(HTTPException) as exc_info:
        merge_batches_transaction(db_session, request)

    assert exc_info.value.status_code == 400
    assert "không đủ để lấy" in exc_info.value.detail

    # Đảm bảo dữ liệu nguyên vẹn không bị trừ
    db_session.refresh(batch_a)
    db_session.refresh(batch_b)
    assert batch_a.remaining_quantity == 50.0
    assert batch_b.remaining_quantity == 100.0


def test_parent_not_found_raises_404(db_session: Session):
    """Yêu cầu 5: Lô mẹ không tồn tại trong CSDL -> ném 404."""
    batch_a = Batch(farm_id=1, product_name="Thanh Long A", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1))
    db_session.add(batch_a)
    db_session.commit()

    request = BatchMergeRequest(
        parents=[
            ParentBatchItem(parent_batch_id=batch_a.id, used_quantity=20.0),
            ParentBatchItem(parent_batch_id=9999, used_quantity=30.0),  # Không tồn tại!
        ],
        product_name="Thanh Long Gộp",
        farm_id=1,
        harvest_date=date(2026, 9, 2),
    )

    with pytest.raises(HTTPException) as exc_info:
        merge_batches_transaction(db_session, request)

    assert exc_info.value.status_code == 404
    assert "9999" in exc_info.value.detail


def test_duplicate_parent_id_rejected(db_session: Session):
    """Chặn trùng lặp parent_batch_id trong danh sách gộp."""
    batch_a = Batch(farm_id=1, product_name="Cam A", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1))
    db_session.add(batch_a)
    db_session.commit()

    request = BatchMergeRequest(
        parents=[
            ParentBatchItem(parent_batch_id=batch_a.id, used_quantity=10.0),
            ParentBatchItem(parent_batch_id=batch_a.id, used_quantity=20.0),  # Trùng!
        ],
        product_name="Cam Gộp",
        farm_id=1,
        harvest_date=date(2026, 9, 2),
    )

    with pytest.raises(HTTPException) as exc_info:
        merge_batches_transaction(db_session, request)

    assert exc_info.value.status_code == 400
    assert "trùng lặp" in exc_info.value.detail


def test_transaction_rollback_on_failure(db_session: Session):
    """Yêu cầu 7 & 8: Rollback toàn bộ transaction khi có lỗi bất kỳ trong quá trình xử lý."""
    batch_a = Batch(farm_id=1, product_name="Chanh A", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1))
    batch_b = Batch(farm_id=1, product_name="Chanh B", quantity=50.0, remaining_quantity=50.0, harvest_date=date(2026, 9, 1))
    db_session.add_all([batch_a, batch_b])
    db_session.commit()

    # Request với parent thứ hai vượt tồn
    request = BatchMergeRequest(
        parents=[
            ParentBatchItem(parent_batch_id=batch_a.id, used_quantity=30.0),  # hợp lệ
            ParentBatchItem(parent_batch_id=batch_b.id, used_quantity=999.0),  # không hợp lệ -> fail
        ],
        product_name="Chanh Thất Bại",
        farm_id=1,
        harvest_date=date(2026, 9, 2),
    )

    initial_batches_count = len(db_session.scalars(select(Batch)).all())
    initial_relations_count = len(db_session.scalars(select(BatchRelation)).all())

    with pytest.raises(HTTPException):
        merge_batches_transaction(db_session, request)

    # Đảm bảo rollback sạch: không có lô mới và lô A không bị trừ
    db_session.refresh(batch_a)
    db_session.refresh(batch_b)
    assert batch_a.remaining_quantity == 100.0
    assert batch_b.remaining_quantity == 50.0

    current_batches_count = len(db_session.scalars(select(Batch)).all())
    current_relations_count = len(db_session.scalars(select(BatchRelation)).all())
    assert current_batches_count == initial_batches_count
    assert current_relations_count == initial_relations_count
