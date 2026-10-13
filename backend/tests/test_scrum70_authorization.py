"""Unit và Integration test cho SCRUM-70: Quy tắc phân quyền xem lô theo phả hệ.

Kiểm tra:
1. CASE A: User/Org đang giữ batch => PASS
2. CASE B: User/Org giữ batch con, batch được request là tổ tiên (ancestor) => PASS
3. CASE C: Batch hoàn toàn không liên quan => DENY (False / 403)
4. Phả hệ nhiều cấp (A -> B -> C -> D), Org giữ D có quyền xem cả A, B, C, D.
5. Batch root (A).
6. Batch không tồn tại => DENY (False).
7. Org không tồn tại => DENY (False).
8. Bảo vệ chống vòng lặp vô tận (genealogy cycle protection) nếu xuất hiện chu trình.
9. Từng giữ batch (custody history) => PASS.
"""

from datetime import date
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, Batch, BatchCustodyHistory, BatchRelation, Farm, User
from app.services.authorization_service import can_user_view_batch, can_view


@pytest.fixture
def db_session():
    """Tạo session SQLite in-memory độc lập cho mỗi bài test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = TestingSession()

    farm = Farm(name="Vùng Trồng Sen Tháp Mười", location="Đồng Tháp", area=10.0, owner="HTX Sen")
    session.add(farm)
    session.commit()

    yield session

    session.close()


def test_case_a_user_holds_batch_pass(db_session: Session):
    """CASE A: Organization hiện tại đang giữ batch đó => PASS."""
    batch_1 = Batch(
        farm_id=1,
        product_name="Xoài Cát Chu #1",
        quantity=500.0,
        remaining_quantity=500.0,
        harvest_date=date(2026, 9, 20),
        organization_id="ORG_A",
    )
    db_session.add(batch_1)
    db_session.commit()

    # Org A đang giữ batch_1 -> PASS
    assert can_view(db_session, "ORG_A", batch_1.id) is True


def test_case_b_child_holder_views_ancestor_pass(db_session: Session):
    """CASE B: Organization giữ batch con, batch được request là tổ tiên (ancestor) => PASS."""
    # Tạo chuỗi phả hệ: A -> B -> C -> D
    batch_a = Batch(farm_id=1, product_name="Lô Gốc A", quantity=1000.0, remaining_quantity=500.0, harvest_date=date(2026, 9, 1), organization_id="ORG_ROOT")
    batch_b = Batch(farm_id=1, product_name="Lô Trung Gian B", quantity=500.0, remaining_quantity=300.0, harvest_date=date(2026, 9, 5), organization_id="ORG_MID")
    batch_c = Batch(farm_id=1, product_name="Lô Chế Biến C", quantity=300.0, remaining_quantity=200.0, harvest_date=date(2026, 9, 10), organization_id="ORG_MID")
    batch_d = Batch(farm_id=1, product_name="Lô Đóng Gói D", quantity=200.0, remaining_quantity=200.0, harvest_date=date(2026, 9, 15), organization_id="ORG_FINAL")

    db_session.add_all([batch_a, batch_b, batch_c, batch_d])
    db_session.commit()

    # Thiết lập quan hệ phả hệ: A -> B -> C -> D
    rel_ab = BatchRelation(parent_batch_id=batch_a.id, child_batch_id=batch_b.id, used_quantity=500.0)
    rel_bc = BatchRelation(parent_batch_id=batch_b.id, child_batch_id=batch_c.id, used_quantity=300.0)
    rel_cd = BatchRelation(parent_batch_id=batch_c.id, child_batch_id=batch_d.id, used_quantity=200.0)
    db_session.add_all([rel_ab, rel_bc, rel_cd])
    db_session.commit()

    # ORG_FINAL đang giữ D: phải được xem D, C, B, và root A
    assert can_view(db_session, "ORG_FINAL", batch_d.id) is True  # CASE 1 (trực tiếp)
    assert can_view(db_session, "ORG_FINAL", batch_c.id) is True  # CASE 3 (parent)
    assert can_view(db_session, "ORG_FINAL", batch_b.id) is True  # CASE 3 (grandparent)
    assert can_view(db_session, "ORG_FINAL", batch_a.id) is True  # CASE 3 (great-grandparent / root)


def test_case_c_unrelated_batch_deny(db_session: Session):
    """CASE C: Batch hoàn toàn không liên quan => DENY."""
    batch_d = Batch(farm_id=1, product_name="Lô D", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 15), organization_id="ORG_FINAL")
    batch_unrelated = Batch(farm_id=1, product_name="Lô Độc Lập X", quantity=200.0, remaining_quantity=200.0, harvest_date=date(2026, 9, 15), organization_id="ORG_OTHER")
    db_session.add_all([batch_d, batch_unrelated])
    db_session.commit()

    # ORG_FINAL đang giữ D, không có quan hệ phả hệ gì với batch_unrelated
    assert can_view(db_session, "ORG_FINAL", batch_unrelated.id) is False


def test_custody_history_previously_held_pass(db_session: Session):
    """CASE 2: Organization từng giữ batch đó trong quá khứ => PASS."""
    batch_transferred = Batch(
        farm_id=1,
        product_name="Lô Đã Chuyển Giao",
        quantity=300.0,
        remaining_quantity=300.0,
        harvest_date=date(2026, 9, 1),
        organization_id="ORG_NEW_HOLDER",  # hiện tại thuộc org mới
    )
    db_session.add(batch_transferred)
    db_session.commit()

    # Ghi nhận lịch sử: ORG_OLD_HOLDER từng giữ lô này
    history = BatchCustodyHistory(batch_id=batch_transferred.id, organization_id="ORG_OLD_HOLDER")
    db_session.add(history)
    db_session.commit()

    # Cả org mới (đang giữ) và org cũ (từng giữ) đều có quyền xem
    assert can_view(db_session, "ORG_NEW_HOLDER", batch_transferred.id) is True
    assert can_view(db_session, "ORG_OLD_HOLDER", batch_transferred.id) is True


def test_nonexistent_batch_and_org(db_session: Session):
    """Kiểm tra batch hoặc org không tồn tại => DENY (False)."""
    assert can_view(db_session, "ORG_ANY", 99999) is False
    assert can_view(db_session, "", 1) is False
    assert can_view(db_session, None, 1) is False


def test_cycle_protection_prevents_infinite_loop(db_session: Session):
    """Bảo vệ chống vòng lặp vô tận khi đồ thị phả hệ có cycle: A -> B -> C -> A."""
    b_a = Batch(farm_id=1, product_name="Cycle A", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1), organization_id="ORG_CYCLE")
    b_b = Batch(farm_id=1, product_name="Cycle B", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 2), organization_id="ORG_CYCLE")
    b_c = Batch(farm_id=1, product_name="Cycle C", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 3), organization_id="ORG_CYCLE")
    db_session.add_all([b_a, b_b, b_c])
    db_session.commit()

    # Tạo quan hệ vòng tròn
    db_session.add_all([
        BatchRelation(parent_batch_id=b_a.id, child_batch_id=b_b.id, used_quantity=10.0),
        BatchRelation(parent_batch_id=b_b.id, child_batch_id=b_c.id, used_quantity=10.0),
        BatchRelation(parent_batch_id=b_c.id, child_batch_id=b_a.id, used_quantity=10.0),
    ])
    db_session.commit()

    # Hàm không được treo hay vượt đệ quy tối đa (recursion error)
    result = can_view(db_session, "ORG_CYCLE", b_a.id)
    assert result is True


def test_can_user_view_batch_admin_and_farmer(db_session: Session):
    """Kiểm tra helper can_user_view_batch với User model."""
    admin_user = User(username="admin_test", password="hash", role="admin", organization_id="ORG_ADMIN")
    farmer_user_1 = User(username="farmer_1", password="hash", role="farmer", organization_id="ORG_1")
    farmer_user_2 = User(username="farmer_2", password="hash", role="farmer", organization_id="ORG_2")
    db_session.add_all([admin_user, farmer_user_1, farmer_user_2])

    batch_1 = Batch(farm_id=1, product_name="Lô 1", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1), organization_id="ORG_1")
    db_session.add(batch_1)
    db_session.commit()

    # Admin xem được tất cả
    assert can_user_view_batch(db_session, admin_user, batch_1.id) is True

    # Farmer 1 thuộc ORG_1 => xem được
    assert can_user_view_batch(db_session, farmer_user_1, batch_1.id) is True

    # Farmer 2 thuộc ORG_2 => bị từ chối
    assert can_user_view_batch(db_session, farmer_user_2, batch_1.id) is False
