"""Kiểm thử tính năng tách nhập nhiều dòng lô con (SCRUM-56 / T-40).

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
1. Tách thành công tạo nhiều lô con theo các dòng khối lượng gửi lên.
2. Trừ chính xác khối lượng của lô mẹ, không để số dư âm.
3. Từ chối yêu cầu (400 Bad Request) nếu tổng khối lượng con vượt quá số dư còn lại của lô mẹ.
4. Ghi nhận sự kiện SPLIT vào chuỗi sự kiện bất biến (hash-chain).
5. Trả về đầy đủ danh sách mã lô con (child_batches).
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
from app.models import Batch, Farm, User, ROLE_FARMER
from app.security import hash_password


@pytest.fixture
def split_test_env():
    """Thiết lập môi trường kiểm thử cho API tách lô nhiều dòng."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        user = User(
            username="farmer_split_test",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        db.add(user)

        farm = Farm(
            name="Vùng Trồng Sầu Riêng Bến Tre",
            location="Bến Tre",
            area=3.5,
            owner="HTX Sầu Riêng",
        )
        db.add(farm)
        db.commit()

        # Tạo lô mẹ có khối lượng 100.0 kg
        parent_batch = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6",
            quantity=100.0,
            harvest_date=date(2026, 6, 1),
        )
        db.add(parent_batch)
        db.commit()

        batch_id = parent_batch.id

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "client": client,
        "batch_id": batch_id,
        "session_factory": TestingSessionLocal,
    }

    app.dependency_overrides.clear()


def test_split_batch_success_with_multiple_items(split_test_env):
    """AC 1: Tách thành công nhiều dòng lô con (30kg, 25kg, 15kg = tổng 70kg <= 100kg).

    - Lô mẹ còn lại 30kg.
    - Sinh ra 3 lô con với mã cụ thể.
    """
    client = split_test_env["client"]
    batch_id = split_test_env["batch_id"]

    payload = {
        "items": [
            {"quantity": 30.0, "note": "Kiện 1 - Xuất khẩu Nhật"},
            {"quantity": 25.0, "note": "Kiện 2 - Siêu thị loại 1"},
            {"quantity": 15.0, "note": "Kiện 3 - Bán lẻ nội địa"},
        ]
    }

    res = client.post(
        f"/batches/{batch_id}/split",
        json=payload,
        auth=("farmer_split_test", "123456"),
    )
    assert res.status_code == 201, res.text
    data = res.json()

    assert data["parent_batch_id"] == batch_id
    assert data["parent_remaining_quantity"] == 30.0
    assert len(data["child_batches"]) == 3

    child_quantities = [c["quantity"] for c in data["child_batches"]]
    assert child_quantities == [30.0, 25.0, 15.0]

    # Kiểm tra mã lô con là số nguyên hợp lệ > 0
    child_ids = [c["id"] for c in data["child_batches"]]
    assert len(set(child_ids)) == 3
    for cid in child_ids:
        assert cid > batch_id

    # Kiểm tra số dư lô mẹ trong DB
    with split_test_env["session_factory"]() as db:
        parent = db.get(Batch, batch_id)
        assert parent.quantity == 30.0


def test_reject_split_when_total_exceeds_remaining(split_test_env):
    """AC 2: Tổng nhập vượt quá phần còn lại của lô mẹ -> bị từ chối 400 Bad Request."""
    client = split_test_env["client"]
    batch_id = split_test_env["batch_id"]

    # Lô mẹ 100kg, yêu cầu tách tổng 120kg (60 + 60)
    payload = {
        "items": [
            {"quantity": 60.0, "note": "Kiện A"},
            {"quantity": 60.0, "note": "Kiện B"},
        ]
    }

    res = client.post(
        f"/batches/{batch_id}/split",
        json=payload,
        auth=("farmer_split_test", "123456"),
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "vượt quá" in detail.lower()

    # Khối lượng lô mẹ không bị trừ
    with split_test_env["session_factory"]() as db:
        parent = db.get(Batch, batch_id)
        assert parent.quantity == 100.0


def test_reject_invalid_items_quantity(split_test_env):
    """Từ chối khối lượng không hợp lệ (<= 0)."""
    client = split_test_env["client"]
    batch_id = split_test_env["batch_id"]

    payload = {
        "items": [
            {"quantity": 0.0, "note": "Kiện lỗi 0kg"},
        ]
    }
    res = client.post(
        f"/batches/{batch_id}/split",
        json=payload,
        auth=("farmer_split_test", "123456"),
    )
    assert res.status_code == 422


def test_ac1_split_100kg_into_30_30_40_leaves_0kg_with_parent_id(split_test_env):
    """AC 1: Giả sử tôi đang giữ một lô còn 100 kg,
    Khi tách thành ba lô con 30, 30, 40 kg,
    Thì ba lô con được tạo với mã riêng, mỗi lô ghi rõ lô mẹ, và lô mẹ còn lại 0 kg.
    """
    client = split_test_env["client"]
    batch_id = split_test_env["batch_id"]

    payload = {
        "items": [
            {"quantity": 30.0, "note": "Lô con 1"},
            {"quantity": 30.0, "note": "Lô con 2"},
            {"quantity": 40.0, "note": "Lô con 3"},
        ]
    }

    res = client.post(
        f"/batches/{batch_id}/split",
        json=payload,
        auth=("farmer_split_test", "123456"),
    )
    assert res.status_code == 201, res.text
    data = res.json()

    # 1. Lô mẹ còn lại đúng 0 kg
    assert data["parent_remaining_quantity"] == 0.0
    assert data["parent_batch_id"] == batch_id

    # 2. Ba lô con được tạo
    children = data["child_batches"]
    assert len(children) == 3

    # 3. Ba lô con có mã riêng (batch_code khác nhau và không rỗng)
    codes = [c["batch_code"] for c in children]
    assert len(set(codes)) == 3
    for code in codes:
        assert code and code.startswith("LOT-")

    # 4. Mỗi lô ghi rõ lô mẹ (parent_id)
    for c in children:
        assert c["parent_id"] == batch_id

    # 5. Kiểm tra đối chiếu trong cơ sở dữ liệu
    with split_test_env["session_factory"]() as db:
        parent = db.get(Batch, batch_id)
        assert parent.quantity == 0.0

        db_children = db.query(Batch).filter(Batch.parent_id == batch_id).all()
        assert len(db_children) == 3
        assert sorted([c.quantity for c in db_children]) == [30.0, 30.0, 40.0]
        for child in db_children:
            assert child.parent_id == batch_id


def test_ac2_split_100kg_into_30_and_30_leaves_40kg_and_can_split_again(split_test_env):
    """AC 2: Giả sử tôi tách 100 kg thành 30 và 30,
    Khi lưu, Thì lô mẹ còn lại 40 kg và vẫn tách tiếp được.
    """
    client = split_test_env["client"]
    batch_id = split_test_env["batch_id"]

    # Đợt 1: Tách 30kg và 30kg
    payload_1 = {
        "items": [
            {"quantity": 30.0, "note": "Đợt 1 - Phần A"},
            {"quantity": 30.0, "note": "Đợt 1 - Phần B"},
        ]
    }
    res_1 = client.post(
        f"/batches/{batch_id}/split",
        json=payload_1,
        auth=("farmer_split_test", "123456"),
    )
    assert res_1.status_code == 201, res_1.text
    data_1 = res_1.json()

    # Lô mẹ còn lại đúng 40 kg
    assert data_1["parent_remaining_quantity"] == 40.0
    with split_test_env["session_factory"]() as db:
        parent = db.get(Batch, batch_id)
        assert parent.quantity == 40.0

    # Đợt 2: Vẫn tiếp tục tách tiếp được từ lô mẹ đó (ví dụ tách tiếp 20kg và 20kg)
    payload_2 = {
        "items": [
            {"quantity": 20.0, "note": "Đợt 2 - Phần C"},
            {"quantity": 20.0, "note": "Đợt 2 - Phần D"},
        ]
    }
    res_2 = client.post(
        f"/batches/{batch_id}/split",
        json=payload_2,
        auth=("farmer_split_test", "123456"),
    )
    assert res_2.status_code == 201, res_2.text
    data_2 = res_2.json()

    # Lô mẹ sau đợt 2 còn lại đúng 0 kg
    assert data_2["parent_remaining_quantity"] == 0.0
    with split_test_env["session_factory"]() as db:
        parent = db.get(Batch, batch_id)
        assert parent.quantity == 0.0
        # Tổng cộng đã tạo 4 lô con từ lô mẹ này
        all_children = db.query(Batch).filter(Batch.parent_id == batch_id).all()
        assert len(all_children) == 4


def test_ac3_split_failure_at_second_child_rolls_back_completely(split_test_env):
    """AC 3: Giả sử tạo lô con thứ hai thất bại giữa chừng,
    Khi giao dịch bị huỷ, Thì không có lô con nào được tạo và lô mẹ giữ nguyên khối lượng.
    """
    client = split_test_env["client"]
    batch_id = split_test_env["batch_id"]

    # Thử qua API với simulate_error_at_index = 2
    payload = {
        "items": [
            {"quantity": 30.0, "note": "Lô con 1 - thành công"},
            {"quantity": 30.0, "note": "Lô con 2 - gặp sự cố"},
            {"quantity": 40.0, "note": "Lô con 3 - chưa tới"},
        ],
        "simulate_error_at_index": 2,
    }

    res = client.post(
        f"/batches/{batch_id}/split",
        json=payload,
        auth=("farmer_split_test", "123456"),
    )
    assert res.status_code == 500
    assert "hủy" in res.json()["detail"].lower() or "lỗi" in res.json()["detail"].lower()

    # Xác nhận tính toàn vẹn: Không có lô con nào được tạo, lô mẹ giữ nguyên 100 kg
    with split_test_env["session_factory"]() as db:
        parent = db.get(Batch, batch_id)
        assert parent.quantity == 100.0

        children_count = db.query(Batch).filter(Batch.parent_id == batch_id).count()
        assert children_count == 0

    # Thử trực tiếp qua service split_batch (app.batch_split)
    from app.batch_split import split_batch as service_split_batch
    with split_test_env["session_factory"]() as db:
        with pytest.raises(RuntimeError):
            service_split_batch(
                db=db,
                parent_batch_id=batch_id,
                child_quantities=[30.0, 30.0, 40.0],
                error_at_child_index=2,
            )

        # Sau khi service ném lỗi: lô mẹ vẫn 100kg, không lô con nào được tạo
        parent_after = db.get(Batch, batch_id)
        assert parent_after.quantity == 100.0
        assert db.query(Batch).filter(Batch.parent_id == batch_id).count() == 0


def test_ac4_split_batch_not_held_by_org_returns_403(split_test_env):
    """AC 4: Giả sử lô không do tổ chức tôi giữ,
    Khi tôi gọi API tách, Thì trả về 403.
    """
    client = split_test_env["client"]
    batch_id = split_test_env["batch_id"]

    # Đặt lô mẹ thuộc về "Cong Ty Xuat Khau VinEco"
    with split_test_env["session_factory"]() as db:
        parent = db.get(Batch, batch_id)
        parent.current_holder_org = "Cong Ty Xuat Khau VinEco"
        db.commit()

    payload = {
        "items": [
            {"quantity": 30.0, "note": "Thử tách lô của tổ chức khác"},
        ]
    }

    # 1. Gọi với context tổ chức khác ("HTX Sau Rieng Ben Tre") -> 403 Forbidden
    res_diff_org = client.post(
        f"/batches/{batch_id}/split",
        json=payload,
        auth=("farmer_split_test", "123456"),
        headers={"X-Organization-Id": "HTX Sau Rieng Ben Tre"},
    )
    assert res_diff_org.status_code == 403
    assert "nắm giữ" in res_diff_org.json()["detail"].lower()

    # 2. Gọi không truyền header (mặc định tenant HTX Nông Nghiệp Số 4 != VinEco) -> 403 Forbidden
    res_default_org = client.post(
        f"/batches/{batch_id}/split",
        json=payload,
        auth=("farmer_split_test", "123456"),
    )
    assert res_default_org.status_code == 403

    # 3. Gọi với đúng tổ chức đang giữ ("Cong Ty Xuat Khau VinEco") -> 201 Thành công
    res_same_org = client.post(
        f"/batches/{batch_id}/split",
        json=payload,
        auth=("farmer_split_test", "123456"),
        headers={"X-Organization-Id": "Cong Ty Xuat Khau VinEco"},
    )
    assert res_same_org.status_code == 201
    assert res_same_org.json()["parent_remaining_quantity"] == 70.0
