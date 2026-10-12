"""Kiểm thử chấp nhận nghiệp vụ Gộp lô nông sản (Batch Merge - SCRUM-60 / SCRUM-62 / T-44 / T-46).

Các tiêu chí nghiệm thu (Acceptance Criteria - AC):
1. Giả sử tôi đang giữ ba lô cùng sản phẩm,
   Khi gộp toàn bộ,
   Thì một lô mới được tạo ghi rõ cả ba lô mẹ, và ba lô mẹ còn lại 0 kg.

2. Giả sử tôi gộp một phần của mỗi lô,
   Khi lưu,
   Thì lô mới có khối lượng bằng tổng phần lấy và mỗi lô mẹ trừ đúng phần đó.

3. Giả sử hai lô khác sản phẩm,
   Khi gộp,
   Thì bị chặn kèm lý do.

4. Giả sử một trong các lô không do tổ chức tôi giữ,
   Khi gộp,
   Thì bị chặn.
"""

from __future__ import annotations

from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, BatchRelation, Farm, User, ROLE_FARMER
from app.security import hash_password


@pytest.fixture
def merge_test_env():
    """Thiết lập môi trường kiểm thử cho API gộp lô nông sản."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        user = User(
            username="distributor_user",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        db.add(user)

        farm = Farm(
            name="Vùng Trồng Sầu Riêng Chợ Lách",
            location="Bến Tre",
            area=5.0,
            owner="HTX Nong Nghiep So 4",
        )
        db.add(farm)
        db.commit()

        # Tạo sẵn 3 lô cùng sản phẩm "Sầu Riêng Ri6" do "HTX Nông Nghiệp Số 4" nắm giữ
        b1 = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6",
            quantity=100.0,
            harvest_date=date(2026, 6, 1),
            batch_code="LOT-SR-01",
            current_holder_org="HTX Nông Nghiệp Số 4",
        )
        b2 = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6",
            quantity=150.0,
            harvest_date=date(2026, 6, 2),
            batch_code="LOT-SR-02",
            current_holder_org="HTX Nông Nghiệp Số 4",
        )
        b3 = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6",
            quantity=200.0,
            harvest_date=date(2026, 6, 3),
            batch_code="LOT-SR-03",
            current_holder_org="HTX Nông Nghiệp Số 4",
        )
        # Tạo thêm 1 lô khác sản phẩm: "Xoài Cát Chu"
        b_mango = Batch(
            farm_id=farm.id,
            product_name="Xoài Cát Chu",
            quantity=80.0,
            harvest_date=date(2026, 6, 4),
            batch_code="LOT-MANGO-01",
            current_holder_org="HTX Nông Nghiệp Số 4",
        )
        # Tạo thêm 1 lô thuộc tổ chức khác: "Cong Ty Xuat Khau VinEco"
        b_other_org = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6",
            quantity=100.0,
            harvest_date=date(2026, 6, 5),
            batch_code="LOT-SR-OTHER",
            current_holder_org="Cong Ty Xuat Khau VinEco",
        )

        db.add_all([b1, b2, b3, b_mango, b_other_org])
        db.commit()

        b1_id, b2_id, b3_id = b1.id, b2.id, b3.id
        mango_id = b_mango.id
        other_org_id = b_other_org.id

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "client": client,
        "b1_id": b1_id,
        "b2_id": b2_id,
        "b3_id": b3_id,
        "mango_id": mango_id,
        "other_org_id": other_org_id,
        "session_factory": TestingSessionLocal,
    }

    app.dependency_overrides.clear()


def test_ac1_merge_all_three_batches_leaves_zero_and_records_parents(merge_test_env):
    """Kịch bản 1: Giả sử tôi đang giữ ba lô cùng sản phẩm,
    Khi gộp toàn bộ,
    Thì một lô mới được tạo ghi rõ cả ba lô mẹ, và ba lô mẹ còn lại 0 kg.
    """
    client = merge_test_env["client"]
    b1_id = merge_test_env["b1_id"]
    b2_id = merge_test_env["b2_id"]
    b3_id = merge_test_env["b3_id"]
    auth = ("distributor_user", "123456")

    # Gộp toàn bộ khối lượng: 100kg + 150kg + 200kg = 450kg
    payload = {
        "items": [
            {"batch_id": b1_id, "quantity": 100.0},
            {"batch_id": b2_id, "quantity": 150.0},
            {"batch_id": b3_id, "quantity": 200.0},
        ],
        "product_name": "Sầu Riêng Ri6 Thùng Tổng",
        "note": "Gộp toàn bộ 3 lô nhỏ thành kiện lớn xuất khẩu",
    }

    res = client.post("/batches/merge", json=payload, auth=auth)
    assert res.status_code == 201, res.text
    data = res.json()

    merged = data["merged_batch"]
    merged_id = merged["id"]

    # 1. Khối lượng lô mới đúng bằng tổng cả 3 lô: 450 kg
    assert merged["quantity"] == 450.0
    assert merged["product_name"] == "Sầu Riêng Ri6 Thùng Tổng"

    # 2. Cả ba lô mẹ còn lại 0 kg trong response và DB
    parents_res = data["parent_batches"]
    assert len(parents_res) == 3
    for p in parents_res:
        assert p["quantity"] == 0.0

    with merge_test_env["session_factory"]() as db:
        p1 = db.get(Batch, b1_id)
        p2 = db.get(Batch, b2_id)
        p3 = db.get(Batch, b3_id)
        assert p1.quantity == 0.0
        assert p2.quantity == 0.0
        assert p3.quantity == 0.0

        # 3. Lô mới ghi rõ cả ba lô mẹ qua bảng quan hệ BatchRelation
        relations = db.query(BatchRelation).filter(BatchRelation.child_batch_id == merged_id).all()
        assert len(relations) == 3

        rel_dict = {r.parent_batch_id: float(r.used_quantity) for r in relations}
        assert rel_dict[b1_id] == 100.0
        assert rel_dict[b2_id] == 150.0
        assert rel_dict[b3_id] == 200.0


def test_ac2_merge_partial_quantities_updates_parent_and_new_batch(merge_test_env):
    """Kịch bản 2: Giả sử tôi gộp một phần của mỗi lô,
    Khi lưu,
    Thì lô mới có khối lượng bằng tổng phần lấy và mỗi lô mẹ trừ đúng phần đó.
    """
    client = merge_test_env["client"]
    b1_id = merge_test_env["b1_id"]  # 100 kg
    b2_id = merge_test_env["b2_id"]  # 150 kg
    b3_id = merge_test_env["b3_id"]  # 200 kg
    auth = ("distributor_user", "123456")

    # Lấy một phần: b1 lấy 30kg, b2 lấy 40kg, b3 lấy 50kg
    payload = {
        "items": [
            {"batch_id": b1_id, "quantity": 30.0},
            {"batch_id": b2_id, "quantity": 40.0},
            {"batch_id": b3_id, "quantity": 50.0},
        ],
        "note": "Gộp một phần hàng đợt 1",
    }

    res = client.post("/batches/merge", json=payload, auth=auth)
    assert res.status_code == 201, res.text
    data = res.json()

    merged = data["merged_batch"]
    merged_id = merged["id"]

    # 1. Lô mới có khối lượng bằng đúng tổng phần lấy: 30 + 40 + 50 = 120 kg
    assert merged["quantity"] == 120.0

    # 2. Mỗi lô mẹ trừ đúng phần đó:
    # b1: 100 - 30 = 70 kg
    # b2: 150 - 40 = 110 kg
    # b3: 200 - 50 = 150 kg
    with merge_test_env["session_factory"]() as db:
        p1 = db.get(Batch, b1_id)
        p2 = db.get(Batch, b2_id)
        p3 = db.get(Batch, b3_id)
        assert p1.quantity == 70.0
        assert p2.quantity == 110.0
        assert p3.quantity == 150.0

        # Kiểm tra bảng quan hệ BatchRelation
        relations = db.query(BatchRelation).filter(BatchRelation.child_batch_id == merged_id).all()
        assert len(relations) == 3
        rel_map = {r.parent_batch_id: float(r.used_quantity) for r in relations}
        assert rel_map[b1_id] == 30.0
        assert rel_map[b2_id] == 40.0
        assert rel_map[b3_id] == 50.0


def test_ac3_merge_different_products_rejected_with_reason(merge_test_env):
    """Kịch bản 3: Giả sử hai lô khác sản phẩm,
    Khi gộp,
    Thì bị chặn kèm lý do.
    """
    client = merge_test_env["client"]
    b1_id = merge_test_env["b1_id"]        # Sầu Riêng Ri6
    mango_id = merge_test_env["mango_id"]  # Xoài Cát Chu
    auth = ("distributor_user", "123456")

    payload = {
        "items": [
            {"batch_id": b1_id, "quantity": 20.0},
            {"batch_id": mango_id, "quantity": 20.0},
        ],
        "note": "Cố tình gộp Sầu Riêng với Xoài",
    }

    res = client.post("/batches/merge", json=payload, auth=auth)
    assert res.status_code == 400
    detail = res.json()["detail"].lower()

    # Phải có lý do từ chối rõ ràng trong thông báo
    assert "khác loại sản phẩm" in detail or "khác sản phẩm" in detail
    assert "sầu riêng ri6" in detail
    assert "xoài cát chu" in detail

    # Đảm bảo khối lượng cả 2 lô mẹ không bị trừ
    with merge_test_env["session_factory"]() as db:
        p1 = db.get(Batch, b1_id)
        p_mango = db.get(Batch, mango_id)
        assert p1.quantity == 100.0
        assert p_mango.quantity == 80.0


def test_ac4_merge_batch_not_held_by_org_rejected(merge_test_env):
    """Kịch bản 4: Giả sử một trong các lô không do tổ chức tôi giữ,
    Khi gộp,
    Thì bị chặn.
    """
    client = merge_test_env["client"]
    b1_id = merge_test_env["b1_id"]              # Do HTX Nong Nghiep So 4 giữ
    other_org_id = merge_test_env["other_org_id"]  # Do Cong Ty Xuat Khau VinEco giữ
    auth = ("distributor_user", "123456")

    payload = {
        "items": [
            {"batch_id": b1_id, "quantity": 20.0},
            {"batch_id": other_org_id, "quantity": 20.0},
        ],
        "note": "Gộp lẫn lô của VinEco",
    }

    # 1. Gọi dưới danh nghĩa mặc định của user (HTX Nông Nghiệp Số 4) -> Bị chặn 403 vì other_org_id thuộc VinEco
    res = client.post(
        "/batches/merge",
        json=payload,
        auth=auth,
    )
    assert res.status_code == 403
    detail = res.json()["detail"].lower()
    assert "nắm giữ" in detail or "quyền" in detail

    # 2. Gọi dưới danh nghĩa Cong Ty Xuat Khau VinEco -> Bị chặn 403 vì b1_id lại thuộc HTX Nong Nghiep So 4
    res_vineco = client.post(
        "/batches/merge",
        json=payload,
        auth=auth,
        headers={"X-Organization-Id": "Cong Ty Xuat Khau VinEco"},
    )
    assert res_vineco.status_code == 403

    # Đảm bảo khối lượng các lô không bị ảnh hưởng
    with merge_test_env["session_factory"]() as db:
        p1 = db.get(Batch, b1_id)
        p_other = db.get(Batch, other_org_id)
        assert p1.quantity == 100.0
        assert p_other.quantity == 100.0
