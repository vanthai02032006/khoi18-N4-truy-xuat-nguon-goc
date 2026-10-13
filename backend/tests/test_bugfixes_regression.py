"""Bộ kiểm thử hồi quy (Regression Tests) cho các lỗi đã được phát hiện và khắc phục.

1. Bảo toàn sản lượng ban đầu (harvest quantity) của lô mẹ sau khi gộp lô (SCRUM-60).
2. Bảo toàn organization_id khi cập nhật lô nông sản (PUT /batches/{id}) không truyền org_id.
3. Chặn cập nhật chéo dữ liệu lô giữa các tổ chức khác nhau (Tenant Isolation 403 Forbidden).
4. Phản ánh chính xác thuộc tính is_active của thành viên trong API invitations/members.
5. Giải phóng hoàn toàn ngăn xếp đệ quy rec_stack trong thuật toán phát hiện chu trình DFS (S-12).
6. Tìm kiếm mã lô hỗ trợ tiền tố '#' hoặc 'BATCH-'.
7. Kiểm tra kết nối CSDL tự động kích hoạt PRAGMA foreign_keys=ON.
"""

from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Batch,
    BatchCustodyHistory,
    BatchRelation,
    Farm,
    User,
    ROLE_ADMIN,
    ROLE_FARMER,
)
from app.schemas import BatchMergeRequest, BatchUpdate, ParentBatchItem
from app.security import hash_password
from app.services.batch_service import merge_batches_transaction
from app.services.integrity_service import run_s12_integrity_check


@pytest.fixture
def regression_db_env():
    """Tạo DB in-memory riêng biệt cho regression tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = TestingSession()

    farm = Farm(name="Vùng Trồng Hồi Quy", location="Đồng Tháp", area=4.0, owner="HTX")
    db.add(farm)

    admin = User(username="admin_reg", password=hash_password("123456"), role=ROLE_ADMIN, organization_id="ORG_ADMIN")
    farmer_a = User(username="farmer_a_reg", password=hash_password("123456"), role=ROLE_FARMER, organization_id="ORG_A")
    farmer_b = User(username="farmer_b_reg", password=hash_password("123456"), role=ROLE_FARMER, organization_id="ORG_B")
    db.add_all([admin, farmer_a, farmer_b])
    db.commit()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "db": db,
        "farm": farm,
        "admin": admin,
        "farmer_a": farmer_a,
        "farmer_b": farmer_b,
        "client": client,
    }

    app.dependency_overrides.clear()
    db.close()


def test_regression_parent_harvest_quantity_preserved_after_merge(regression_db_env):
    """Lỗi 1: Sau khi gộp lô, parent.quantity (sản lượng gốc) PHẢI ĐƯỢC BẢO TOÀN, chỉ trừ remaining_quantity."""
    db: Session = regression_db_env["db"]
    farm: Farm = regression_db_env["farm"]

    batch_m1 = Batch(
        farm_id=farm.id,
        product_name="Xoài Cát Mẹ 1",
        quantity=500.0,
        remaining_quantity=500.0,
        harvest_date=date(2026, 9, 10),
        organization_id="ORG_A",
    )
    batch_m2 = Batch(
        farm_id=farm.id,
        product_name="Xoài Cát Mẹ 2",
        quantity=300.0,
        remaining_quantity=300.0,
        harvest_date=date(2026, 9, 11),
        organization_id="ORG_A",
    )
    db.add_all([batch_m1, batch_m2])
    db.commit()

    req = BatchMergeRequest(
        parents=[
            ParentBatchItem(parent_batch_id=batch_m1.id, used_quantity=200.0),
            ParentBatchItem(parent_batch_id=batch_m2.id, used_quantity=150.0),
        ],
        product_name="Xoài Gộp Con",
        farm_id=farm.id,
        harvest_date=date(2026, 9, 20),
        organization_id="ORG_A",
    )
    res = merge_batches_transaction(db, req)

    db.refresh(batch_m1)
    db.refresh(batch_m2)

    # KHẲNG ĐỊNH: quantity ban đầu không bị ghi đè!
    assert batch_m1.quantity == 500.0
    assert batch_m1.remaining_quantity == 300.0

    assert batch_m2.quantity == 300.0
    assert batch_m2.remaining_quantity == 150.0

    # Lô con có tổng khối lượng chính xác
    assert res.new_batch.quantity == 350.0

    # Chạy Job S-12 kiểm tra: KHÔNG ĐƯỢC báo lỗi GENEALOGY_OVERUSE sai
    log_entry, violations = run_s12_integrity_check(db, triggered_by="REGRESSION_TEST")
    categories = [v["category"] for v in violations]
    assert "GENEALOGY_OVERUSE" not in categories


def test_regression_update_batch_preserves_organization_id(regression_db_env):
    """Lỗi 2: Cập nhật lô nông sản (PUT) không truyền organization_id thì KHÔNG ĐƯỢC xoá org_id thành None."""
    client: TestClient = regression_db_env["client"]
    db: Session = regression_db_env["db"]
    farm: Farm = regression_db_env["farm"]

    batch = Batch(
        farm_id=farm.id,
        product_name="Thanh Long Ruột Đỏ",
        quantity=100.0,
        remaining_quantity=100.0,
        harvest_date=date(2026, 9, 15),
        organization_id="ORG_A",
    )
    db.add(batch)
    db.commit()
    db.add(BatchCustodyHistory(batch_id=batch.id, organization_id="ORG_A"))
    db.commit()

    # Client gửi PUT không có organization_id
    put_data = {
        "farm_id": farm.id,
        "product_name": "Thanh Long Ruột Đỏ (Đã Đổi Tên)",
        "quantity": 120.0,
        "harvest_date": "2026-09-16",
    }
    resp = client.put(f"/batches/{batch.id}", json=put_data, auth=("farmer_a_reg", "123456"))
    assert resp.status_code == 200

    db.refresh(batch)
    # KHẲNG ĐỊNH: organization_id vẫn giữ nguyên ORG_A
    assert batch.organization_id == "ORG_A"
    assert batch.product_name == "Thanh Long Ruột Đỏ (Đã Đổi Tên)"


def test_regression_update_batch_tenant_isolation(regression_db_env):
    """Lỗi 3: Farmer của ORG_B không được phép sửa lô của ORG_A (403 Forbidden)."""
    client: TestClient = regression_db_env["client"]
    db: Session = regression_db_env["db"]
    farm: Farm = regression_db_env["farm"]

    batch_a = Batch(
        farm_id=farm.id,
        product_name="Xoài của ORG_A",
        quantity=100.0,
        remaining_quantity=100.0,
        harvest_date=date(2026, 9, 15),
        organization_id="ORG_A",
    )
    db.add(batch_a)
    db.commit()

    put_data = {
        "farm_id": farm.id,
        "product_name": "Hack Tên Lô",
        "quantity": 50.0,
        "harvest_date": "2026-09-16",
    }
    # farmer_b_reg (thuộc ORG_B) cố tình sửa lô của ORG_A -> 403 Forbidden
    resp = client.put(f"/batches/{batch_a.id}", json=put_data, auth=("farmer_b_reg", "123456"))
    assert resp.status_code == 403
    assert "tổ chức khác" in resp.json()["detail"]


def test_regression_invitation_members_active_status_serialized(regression_db_env):
    """Lỗi 4: Danh sách thành viên và đổi trạng thái trả về đúng is_active (kể cả khi False)."""
    client: TestClient = regression_db_env["client"]
    farmer_a: User = regression_db_env["farmer_a"]

    # Deactivate farmer_a
    status_payload = {"user_id": farmer_a.id, "active": False, "reason": "Tạm dừng tài khoản"}
    resp = client.post("/invitations/members/status", json=status_payload, auth=("admin_reg", "123456"))
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Lấy danh sách thành viên
    resp_members = client.get("/invitations/members", auth=("admin_reg", "123456"))
    assert resp_members.status_code == 200
    members = resp_members.json()
    f_member = next(m for m in members if m["id"] == farmer_a.id)
    assert f_member["is_active"] is False


def test_regression_cycle_detection_rec_stack_cleanup(regression_db_env):
    """Lỗi 5: rec_stack được giải phóng sạch sẽ khi phát hiện chu trình, không gây false positive ở nhánh khác."""
    db: Session = regression_db_env["db"]
    farm: Farm = regression_db_env["farm"]

    # Nhánh chu trình: 10 -> 11 -> 10
    b10 = Batch(id=10, farm_id=farm.id, product_name="B10", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1))
    b11 = Batch(id=11, farm_id=farm.id, product_name="B11", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1))
    # Nhánh độc lập không chu trình: 20 -> 21
    b20 = Batch(id=20, farm_id=farm.id, product_name="B20", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1))
    b21 = Batch(id=21, farm_id=farm.id, product_name="B21", quantity=100.0, remaining_quantity=100.0, harvest_date=date(2026, 9, 1))
    db.add_all([b10, b11, b20, b21])
    db.commit()

    rel1 = BatchRelation(parent_batch_id=10, child_batch_id=11, used_quantity=10.0)
    rel2 = BatchRelation(parent_batch_id=11, child_batch_id=10, used_quantity=10.0)
    rel3 = BatchRelation(parent_batch_id=20, child_batch_id=21, used_quantity=10.0)
    db.add_all([rel1, rel2, rel3])
    db.commit()

    _, violations = run_s12_integrity_check(db, triggered_by="TEST_CYCLE_LEAK")
    cycle_violations = [v for v in violations if v["category"] == "CYCLE_DETECTED"]
    # Chỉ có 1 vi phạm chu trình (cho nhánh 10-11), KHÔNG ĐƯỢC báo lỗi nhánh 20-21
    assert len(cycle_violations) == 1
    assert "20" not in cycle_violations[0]["record_id"]


def test_regression_batch_search_with_hash_prefix(regression_db_env):
    """Lỗi 6: Tìm kiếm hỗ trợ '#1' hoặc 'BATCH-1' tương thích với UI hiển thị."""
    client: TestClient = regression_db_env["client"]
    db: Session = regression_db_env["db"]
    farm: Farm = regression_db_env["farm"]

    b = Batch(id=777, farm_id=farm.id, product_name="Dưa Lưới Huỳnh Long", quantity=300.0, remaining_quantity=300.0, harvest_date=date(2026, 9, 1), organization_id="ORG_A")
    db.add(b)
    db.commit()

    # Tìm với '#777'
    res1 = client.get("/batches?search=%23777", auth=("farmer_a_reg", "123456"))
    assert res1.status_code == 200
    assert len(res1.json()) == 1
    assert res1.json()[0]["id"] == 777

    # Tìm với 'batch-777'
    res2 = client.get("/batches?search=batch-777", auth=("farmer_a_reg", "123456"))
    assert res2.status_code == 200
    assert len(res2.json()) == 1
    assert res2.json()[0]["id"] == 777
