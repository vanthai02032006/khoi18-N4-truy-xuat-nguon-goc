"""Bộ kiểm thử đơn vị & kiểm thử chấp nhận (DoD / Acceptance Criteria) cho Task T-58 (SCRUM-74).

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
1. Trang chi tiết tổng hợp 3 tab: Tổng quan, Dòng thời gian (T-32), Nguồn gốc (T-50) hoạt động mượt mà.
2. Nút thao tác tự động ẩn khi lô đang chờ bàn giao (PENDING_HANDOVER); phân quyền thao tác chính xác.
3. Ràng buộc kỹ thuật: Quyền thực hiện luôn được kiểm tra độc lập tại máy chủ (Server-side validation):
   - Inspector bị chặn 403 Forbidden đối với các thao tác Bàn giao, Tách, Gộp.
   - Khi lô đang chờ bàn giao (PENDING_HANDOVER), máy chủ từ chối 400 Bad Request đối với mọi thao tác.
"""

from datetime import date, datetime, timezone
from pathlib import Path
import sys

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Đảm bảo import được package `app`
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.models import (
    BATCH_STATUS_ACTIVE,
    BATCH_STATUS_HANDED_OVER,
    BATCH_STATUS_PENDING_HANDOVER,
    Base,
    Batch,
    BatchEvent,
    BatchLineage,
    Farm,
    ROLE_ADMIN,
    ROLE_FARMER,
    ROLE_INSPECTOR,
    User,
)
from app.routers.batches import (
    get_batch_full_detail,
    handover_batch,
    merge_batches,
    split_batch,
)
from app.schemas import (
    BatchHandoverRequest,
    BatchMergeRequest,
    BatchSplitChildItem,
    BatchSplitRequest,
)
from app.security import hash_password, require_farmer


@pytest.fixture
def db_session():
    """Tạo session SQLite in-memory cho kiểm thử độc lập."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    admin = User(username="admin_user", password=hash_password("123456"), role=ROLE_ADMIN)
    farmer = User(username="farmer_user", password=hash_password("123456"), role=ROLE_FARMER)
    inspector = User(username="inspector_user", password=hash_password("123456"), role=ROLE_INSPECTOR)
    farm = Farm(name="Vườn Xoài Cát Chu", location="Đồng Tháp", area=5.0, owner="HTX Mỹ Xương")
    session.add_all([admin, farmer, inspector, farm])
    session.commit()

    b1 = Batch(farm_id=farm.id, product_name="Xoài Cát Chu Loại 1", quantity=1000.0, harvest_date=date(2026, 1, 15), code="7X9KM2RP", status=BATCH_STATUS_ACTIVE)
    b2 = Batch(farm_id=farm.id, product_name="Xoài Cát Chu Loại 2", quantity=800.0, harvest_date=date(2026, 1, 16), code="8Y2LP3MN", status=BATCH_STATUS_ACTIVE)
    session.add_all([b1, b2])
    session.commit()

    yield {
        "db": session,
        "admin": admin,
        "farmer": farmer,
        "inspector": inspector,
        "farm": farm,
        "b1": b1,
        "b2": b2,
    }
    session.close()


class TestBatchTabsAndActionsDoD:
    """Các bài kiểm thử tiêu chí nghiệm thu DoD của T-58 (SCRUM-74)."""

    def test_3_tabs_detail_endpoint_smooth_and_complete(self, db_session):
        """DoD 1: Cả 3 tab hoạt động mượt mà và tổng hợp dữ liệu đầy đủ."""
        db = db_session["db"]
        b1 = db_session["b1"]
        farm = db_session["farm"]

        # Thêm sự kiện mẫu cho Tab 2 (Timeline T-32)
        ev = BatchEvent(
            batch_id=b1.id,
            event_type="HARVEST",
            payload='{"note": "Thu hoạch buổi sáng"}',
            actor="farmer_user",
            organization="HTX Mỹ Xương",
            timestamp=datetime.now(timezone.utc).isoformat(),
            hash="a" * 64,
            previous_hash="0" * 64,
        )
        db.add(ev)
        db.commit()

        # Gọi endpoint GET /{id}/detail
        detail = get_batch_full_detail(batch_id=b1.id, db=db)

        # Tab 1: Tổng quan
        assert detail.batch.id == b1.id
        assert detail.batch.code == "7X9KM2RP"
        assert detail.batch.status == BATCH_STATUS_ACTIVE
        assert detail.farm_name == farm.name
        assert detail.farm_location == farm.location

        # Tab 2: Dòng thời gian (T-32)
        assert len(detail.timeline.events) >= 1
        assert detail.timeline.events[0].event_type == "HARVEST"

        # Tab 3: Nguồn gốc (T-50)
        assert detail.genealogy.target_batch_id == b1.id
        assert detail.ancestors.target_batch == b1.code
        assert detail.ancestors.root_batches == [b1.code]

    def test_role_based_permissions_on_batch_actions(self, db_session):
        """DoD 2: Phân quyền thao tác chính xác (Inspector bị chặn 403, Farmer & Admin được phép)."""
        db = db_session["db"]
        farmer = db_session["farmer"]
        admin = db_session["admin"]
        inspector = db_session["inspector"]
        b1 = db_session["b1"]

        # Farmer và Admin được phép qua dependency require_farmer
        assert require_farmer(farmer).username == farmer.username
        assert require_farmer(admin).username == admin.username

        # Inspector bị chặn 403 Forbidden
        with pytest.raises(HTTPException) as exc_info:
            require_farmer(inspector)
        assert exc_info.value.status_code == 403

        # Inspector gọi API bàn giao trực tiếp bị chặn 403
        req_handover = BatchHandoverRequest(target_organization="Công Ty Xuất Khẩu")
        with pytest.raises(HTTPException) as exc_info:
            handover_batch(batch_id=b1.id, data=req_handover, current_user=require_farmer(inspector), db=db)
        assert exc_info.value.status_code == 403

    def test_handover_action_changes_status_to_pending(self, db_session):
        """Thao tác Bàn giao: Chuyển trạng thái sang PENDING_HANDOVER và ghi nhận mắt xích sự kiện."""
        db = db_session["db"]
        farmer = db_session["farmer"]
        b1 = db_session["b1"]

        assert b1.status == BATCH_STATUS_ACTIVE

        req_handover = BatchHandoverRequest(
            target_organization="Công Ty Thu Mua Đồng Tháp",
            note="Bàn giao xe lạnh 01",
        )
        updated = handover_batch(batch_id=b1.id, data=req_handover, current_user=farmer, db=db)

        # Kiểm tra trạng thái đã chuyển sang PENDING_HANDOVER
        assert updated.status == BATCH_STATUS_PENDING_HANDOVER
        assert b1.status == BATCH_STATUS_PENDING_HANDOVER

        # Kiểm tra sự kiện HANDOVER đã được ghi vào chuỗi bất biến
        last_ev = db.query(BatchEvent).filter_by(batch_id=b1.id, event_type="HANDOVER").first()
        assert last_ev is not None
        assert last_ev.actor == farmer.username
        assert "Công Ty Thu Mua Đồng Tháp" in last_ev.organization

    def test_server_side_validation_rejects_actions_when_pending_handover(self, db_session):
        """Lưu ý kỹ thuật: Ẩn nút ở UI và máy chủ độc lập từ chối 400 khi lô đang chờ bàn giao."""
        db = db_session["db"]
        farmer = db_session["farmer"]
        b1 = db_session["b1"]

        # Đặt trạng thái lô là PENDING_HANDOVER
        b1.status = BATCH_STATUS_PENDING_HANDOVER
        db.commit()

        # 1. Thao tác Bàn giao lại bị từ chối 400
        req_handover = BatchHandoverRequest(target_organization="Đối tác B")
        with pytest.raises(HTTPException) as exc_info:
            handover_batch(batch_id=b1.id, data=req_handover, current_user=farmer, db=db)
        assert exc_info.value.status_code == 400
        assert "chờ bàn giao" in exc_info.value.detail.lower()

        # 2. Thao tác Tách lô bị từ chối 400
        req_split = BatchSplitRequest(children=[
            BatchSplitChildItem(product_name="Con 1", quantity=400.0),
            BatchSplitChildItem(product_name="Con 2", quantity=600.0),
        ])
        with pytest.raises(HTTPException) as exc_info:
            split_batch(batch_id=b1.id, data=req_split, current_user=farmer, db=db)
        assert exc_info.value.status_code == 400
        assert "chờ bàn giao" in exc_info.value.detail.lower()

        # 3. Thao tác Gộp lô có lô cha đang chờ bàn giao bị từ chối 400
        req_merge = BatchMergeRequest(
            parent_batch_ids=[b1.id, db_session["b2"].id],
            product_name="Lô gộp",
        )
        with pytest.raises(HTTPException) as exc_info:
            merge_batches(batch_id=db_session["b2"].id, data=req_merge, current_user=farmer, db=db)
        assert exc_info.value.status_code == 400
        assert "chờ bàn giao" in exc_info.value.detail.lower()

    def test_split_action_creates_child_batches_and_lineage(self, db_session):
        """Thao tác Tách lô (SPLIT) hoạt động chính xác khi lô ở trạng thái hợp lệ."""
        db = db_session["db"]
        farmer = db_session["farmer"]
        b2 = db_session["b2"]  # quantity = 800.0, status = ACTIVE

        req_split = BatchSplitRequest(children=[
            BatchSplitChildItem(product_name="Xoài Tuyển Chọn A", quantity=300.0),
            BatchSplitChildItem(product_name="Xoài Tiêu Chuẩn B", quantity=500.0),
        ])
        children = split_batch(batch_id=b2.id, data=req_split, current_user=farmer, db=db)

        assert len(children) == 2
        assert children[0].quantity == 300.0
        assert children[1].quantity == 500.0
        assert children[0].status == BATCH_STATUS_ACTIVE
        assert len(children[0].code) == 8

        # Kiểm tra bảng quan hệ batch_lineage đã lưu quan hệ SPLIT
        lineages = db.query(BatchLineage).filter_by(parent_batch_id=b2.id).all()
        assert len(lineages) == 2
        assert all(l.relation_type == "SPLIT" for l in lineages)

    def test_merge_action_creates_merged_lineage_and_events(self, db_session):
        """Thao tác Gộp lô (MERGE) hoạt động chính xác khi các lô ở trạng thái hợp lệ."""
        db = db_session["db"]
        farmer = db_session["farmer"]
        b1 = db_session["b1"]
        b2 = db_session["b2"]
        b1.status = BATCH_STATUS_ACTIVE
        b2.status = BATCH_STATUS_ACTIVE
        db.commit()

        req_merge = BatchMergeRequest(
            parent_batch_ids=[b1.id, b2.id],
            product_name="Xoài Đóng Thùng Xuất Khẩu",
            transferred_quantities=[500.0, 400.0],
        )
        merged = merge_batches(batch_id=0, data=req_merge, current_user=farmer, db=db)

        assert merged.id is not None
        assert merged.product_name == "Xoài Đóng Thùng Xuất Khẩu"
        assert merged.quantity == 900.0

        # Kiểm tra quan hệ MERGE trong batch_lineage
        lineages = db.query(BatchLineage).filter_by(child_batch_id=merged.id).all()
        assert len(lineages) == 2
        assert set(l.parent_batch_id for l in lineages) == {b1.id, b2.id}
        assert all(l.relation_type == "MERGE" for l in lineages)
