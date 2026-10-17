"""Kiểm thử lớp kiểm tra (validation) đầu vào cho endpoint tạo lô nông sản (SCRUM-36 / T-20).

Mục tiêu kiểm thử & Tiêu chí nghiệm thu (DoD / AC):
1. Từ chối ngày thu hoạch trong tương lai (422 Unprocessable Entity, trường 'harvest_date').
2. Từ chối khối lượng không dương (<= 0) (422 Unprocessable Entity, trường 'quantity').
3. Từ chối tạo lô trên thửa đất của tổ chức khác (403 Forbidden).
4. Dữ liệu hợp lệ được tạo thành công bình thường (201 Created).
"""

from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Farm, User, ROLE_FARMER, ROLE_ADMIN
from app.security import hash_password
from app.tenant import set_tenant_org


@pytest.fixture
def test_setup():
    """Thiết lập database SQLite in-memory và TestClient dùng dependency override."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Thêm user farmer và admin, cùng với thửa đất mẫu
    with TestingSessionLocal() as db:
        farmer_user = User(
            username="farmer_test",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        admin_user = User(
            username="admin_test",
            password=hash_password("123456"),
            role=ROLE_ADMIN,
        )
        db.add_all([farmer_user, admin_user])
        db.commit()

        # Thửa đất #1 thuộc tổ chức HTX Nong San A
        farm_org_a = Farm(
            name="Vùng Trồng Xoài Thửa A",
            location="Đồng Tháp",
            area=5.0,
            owner="HTX Nong San A",
        )
        # Thửa đất #2 thuộc tổ chức HTX Nong San B
        farm_org_b = Farm(
            name="Vùng Trồng Sầu Riêng Thửa B",
            location="Bến Tre",
            area=3.5,
            owner="HTX Nong San B",
        )
        farm_org_c = Farm(
            name="Vùng Trồng Chi Nhánh Thửa C",
            location="Đồng Tháp",
            area=1.5,
            owner="HTX Nong San A - Chi Nhanh",
        )
        db.add_all([farm_org_a, farm_org_b, farm_org_c])
        db.commit()
        farm_a_id = farm_org_a.id
        farm_b_id = farm_org_b.id
        farm_c_id = farm_org_c.id

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "client": client,
        "farm_a_id": farm_a_id,
        "farm_b_id": farm_b_id,
        "farm_c_id": farm_c_id,
    }

    app.dependency_overrides.clear()


def test_reject_future_harvest_date(test_setup):
    """Ca 1: Từ chối ngày thu hoạch trong tương lai (kèm mã lỗi 422 và tên trường 'harvest_date')."""
    client = test_setup["client"]
    farm_id = test_setup["farm_a_id"]

    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    payload = {
        "farm_id": farm_id,
        "product_name": "Xoài Cát Chu",
        "quantity": 100.0,
        "harvest_date": tomorrow,
    }

    # Đăng nhập bằng tài khoản farmer
    response = client.post(
        "/batches",
        json=payload,
        auth=("farmer_test", "123456"),
        headers={"X-Organization-Id": "HTX Nong San A"},
    )

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    # Kiểm tra rõ ràng trường lỗi là harvest_date kèm thông báo tiếng Việt
    error_fields = [tuple(err["loc"]) for err in data["detail"]]
    assert any("harvest_date" in loc for loc in error_fields)
    assert any("tương lai" in err.get("msg", "") for err in data["detail"])


def test_reject_non_positive_quantity(test_setup):
    """Ca 2: Từ chối khối lượng không dương (<= 0) (kèm mã lỗi 422 và tên trường 'quantity')."""
    client = test_setup["client"]
    farm_id = test_setup["farm_a_id"]

    today_str = date.today().isoformat()

    # Thử với quantity = 0
    payload_zero = {
        "farm_id": farm_id,
        "product_name": "Xoài Cát Chu",
        "quantity": 0.0,
        "harvest_date": today_str,
    }
    response_zero = client.post(
        "/batches",
        json=payload_zero,
        auth=("farmer_test", "123456"),
        headers={"X-Organization-Id": "HTX Nong San A"},
    )
    assert response_zero.status_code == 422
    data_zero = response_zero.json()
    error_fields_zero = [tuple(err["loc"]) for err in data_zero["detail"]]
    assert any("quantity" in loc for loc in error_fields_zero)
    assert any("lớn hơn 0" in err.get("msg", "") for err in data_zero["detail"])

    # Thử với quantity âm (< 0)
    payload_neg = {
        "farm_id": farm_id,
        "product_name": "Xoài Cát Chu",
        "quantity": -50.0,
        "harvest_date": today_str,
    }
    response_neg = client.post(
        "/batches",
        json=payload_neg,
        auth=("farmer_test", "123456"),
        headers={"X-Organization-Id": "HTX Nong San A"},
    )
    assert response_neg.status_code == 422
    data_neg = response_neg.json()
    error_fields_neg = [tuple(err["loc"]) for err in data_neg["detail"]]
    assert any("quantity" in loc for loc in error_fields_neg)
    assert any("lớn hơn 0" in err.get("msg", "") for err in data_neg["detail"])


def test_reject_other_organization_farm(test_setup):
    """Ca 3: Từ chối tạo lô trên thửa đất của tổ chức khác (mã lỗi 403 Forbidden)."""
    client = test_setup["client"]
    # Thửa đất B thuộc "HTX Nông Sản B", nhưng người gọi đại diện "HTX Nông Sản A"
    farm_b_id = test_setup["farm_b_id"]

    today_str = date.today().isoformat()
    payload = {
        "farm_id": farm_b_id,
        "product_name": "Sầu Riêng Ri6",
        "quantity": 150.0,
        "harvest_date": today_str,
    }

    response = client.post(
        "/batches",
        json=payload,
        auth=("farmer_test", "123456"),
        headers={"X-Organization-Id": "HTX Nong San A"},
    )

    assert response.status_code == 403
    data = response.json()
    assert "detail" in data
    assert "tổ chức khác" in data["detail"].lower() or "quyền" in data["detail"].lower()


def test_valid_batch_creation_success(test_setup):
    """Ca hợp lệ: Dữ liệu hợp lệ được tạo thành công bình thường (201 Created)."""
    client = test_setup["client"]
    farm_a_id = test_setup["farm_a_id"]

    today_str = date.today().isoformat()
    payload = {
        "farm_id": farm_a_id,
        "product_name": "Xoài Cát Chu Đạt Chuẩn VietGAP",
        "quantity": 250.5,
        "harvest_date": today_str,
    }

    response = client.post(
        "/batches",
        json=payload,
        auth=("farmer_test", "123456"),
        headers={"X-Organization-Id": "HTX Nong San A"},
    )

    assert response.status_code == 201
    created_batch = response.json()
    assert created_batch["farm_id"] == farm_a_id
    assert created_batch["product_name"] == "Xoài Cát Chu Đạt Chuẩn VietGAP"
    assert created_batch["quantity"] == 250.5
    assert created_batch["harvest_date"] == today_str
    assert "id" in created_batch


def test_reject_other_tenant_id_header(test_setup):
    """SCRUM-95: Từ chối tạo lô khi X-Tenant-Id không khớp với tenant của thửa đất (403 Forbidden)."""
    client = test_setup["client"]
    farm_b_id = test_setup["farm_b_id"]

    today_str = date.today().isoformat()
    payload = {
        "farm_id": farm_b_id,
        "product_name": "Sầu Riêng Ri6",
        "quantity": 100.0,
        "harvest_date": today_str,
    }

    response = client.post(
        "/batches",
        json=payload,
        auth=("farmer_test", "123456"),
        headers={"X-Tenant-Id": "HTX Nong San A"},
    )

    assert response.status_code == 403
    data = response.json()
    assert "detail" in data
    assert "tổ chức khác" in data["detail"].lower() or "quyền" in data["detail"].lower()


def test_reject_farm_whose_owner_only_contains_tenant_name(test_setup):
    """Tên tenant là substring của owner khác vẫn phải bị từ chối."""
    client = test_setup["client"]
    payload = {
        "farm_id": test_setup["farm_c_id"],
        "product_name": "Xoài Cát Chu",
        "quantity": 100.0,
        "harvest_date": date.today().isoformat(),
    }

    response = client.post(
        "/batches",
        json=payload,
        auth=("farmer_test", "123456"),
        headers={"X-Organization-Id": "HTX Nong San A"},
    )

    assert response.status_code == 403


def test_duplicate_idempotency_key_creates_only_one_batch(test_setup):
    """Retry POST cùng Idempotency-Key trả lại batch trước đó, không insert thêm."""
    client = test_setup["client"]
    payload = {
        "farm_id": test_setup["farm_a_id"],
        "product_name": "Xoài Cát Chu",
        "quantity": 100.0,
        "harvest_date": date.today().isoformat(),
    }
    headers = {"X-Organization-Id": "HTX Nong San A", "Idempotency-Key": "harvest-submit-001"}

    first = client.post(
        "/batches", json=payload, auth=("farmer_test", "123456"), headers=headers
    )
    duplicate = client.post(
        "/batches", json=payload, auth=("farmer_test", "123456"), headers=headers
    )

    assert first.status_code == 201
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == first.json()["id"]
    assert len(client.get("/batches").json()) == 1


def test_idempotency_key_cannot_be_reused_for_different_payload(test_setup):
    client = test_setup["client"]
    payload = {
        "farm_id": test_setup["farm_a_id"],
        "product_name": "Xoài Cát Chu",
        "quantity": 100.0,
        "harvest_date": date.today().isoformat(),
    }
    headers = {"X-Organization-Id": "HTX Nong San A", "Idempotency-Key": "harvest-submit-002"}

    first = client.post(
        "/batches", json=payload, auth=("farmer_test", "123456"), headers=headers
    )
    changed_payload = {**payload, "quantity": 200.0}
    second = client.post(
        "/batches", json=changed_payload, auth=("farmer_test", "123456"), headers=headers
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert "Idempotency-Key" in second.json()["detail"]
