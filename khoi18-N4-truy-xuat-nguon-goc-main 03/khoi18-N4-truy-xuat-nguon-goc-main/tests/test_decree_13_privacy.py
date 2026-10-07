"""Kiểm thử quyền xóa dữ liệu cá nhân theo Nghị định 13/2023/NĐ-CP (Story 7).

Kịch bản kiểm thử:
1. PII Erasure: Sau khi xóa, không truy được họ tên, địa chỉ của nông hộ từ API farm, batch overview và public trace.
2. Hash Chain Integrity: Chuỗi băm mật mã SHA-256 của các lô liên quan vẫn nguyên vẹn 100% (is_valid == True, tampered_index is None).
3. Business Continuity: Hồ sơ truy xuất của lô nông sản vẫn tiếp tục hiển thị bình thường với danh tính ẩn danh.
"""

from datetime import date, datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, BatchEvent, Farm, User, ROLE_FARMER
from app.security import compute_event_hash, hash_password


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = TestingSessionLocal()

    # User nông dân
    user = User(
        username="farmer_nguyen",
        password=hash_password("123456"),
        role=ROLE_FARMER,
        organization="HTX Nông Nghiệp Số 4",
    )
    db.add(user)
    db.commit()

    # Tạo Vùng trồng chứa PII ban đầu
    farm = Farm(
        name="Vùng Trồng Xoài Thửa #5",
        location="Thôn 3, Xã Mỹ Xương, Huyện Cao Lãnh, Đồng Tháp",
        area=1.8,
        owner="Nguyễn Văn Định (0912345678)",
    )
    db.add(farm)
    db.commit()

    # Tạo Lô nông sản
    batch = Batch(
        farm_id=farm.id,
        product_name="Xoài Cát Chu VietGAP",
        quantity=800.0,
        remaining_quantity=800.0,
        organization="HTX Nông Nghiệp Số 4",
        status="LƯU_KHO",
        harvest_date=date(2026, 10, 1),
    )
    db.add(batch)
    db.commit()

    # Ghi nhận chuỗi sự kiện hash-chain cho lô
    now_iso = datetime.now(timezone.utc).isoformat()
    ev1_payload = '{"action": "Thu hoach 800kg xoai"}'
    ev1_hash = compute_event_hash(
        event_type="HARVEST",
        payload=ev1_payload,
        actor="farmer_nguyen",
        organization="HTX Nông Nghiệp Số 4",
        timestamp=now_iso,
        previous_hash="0" * 64,
    )
    ev1 = BatchEvent(
        batch_id=batch.id,
        event_type="HARVEST",
        payload=ev1_payload,
        actor="farmer_nguyen",
        organization="HTX Nông Nghiệp Số 4",
        timestamp=now_iso,
        hash=ev1_hash,
        previous_hash="0" * 64,
    )
    db.add(ev1)
    db.commit()

    yield {
        "db": db,
        "farm_id": farm.id,
        "batch_id": batch.id,
    }
    db.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session["db"]
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_pii_erasure_and_hash_chain_preservation(client, db_session):
    """Giả sử nông hộ yêu cầu xóa PII:
    - Họ tên, SĐT, địa chỉ bị xóa khỏi mọi màn hình/API
    - Chuỗi hash vẫn nguyên vẹn 100%
    """
    farm_id = db_session["farm_id"]
    batch_id = db_session["batch_id"]

    # 1. Thực hiện quyền xóa PII theo NĐ 13
    res_anon = client.post(
        f"/farms/{farm_id}/anonymize",
        auth=("farmer_nguyen", "123456"),
    )
    assert res_anon.status_code == 200
    farm_data = res_anon.json()
    assert "Nguyễn Văn Định" not in farm_data["owner"]
    assert "0912345678" not in farm_data["owner"]
    assert "ẩn danh" in farm_data["owner"].lower()
    assert "Thôn 3" not in farm_data["location"]

    # 2. Kiểm tra API chi tiết vùng trồng: PII biến mất
    res_farm = client.get(f"/farms/{farm_id}")
    assert res_farm.status_code == 200
    assert "Nguyễn Văn Định" not in res_farm.json()["owner"]

    # 3. Kiểm tra API Batch Overview 360: hiển thị nông hộ ẩn danh
    res_overview = client.get(
        f"/batches/{batch_id}/overview",
        auth=("farmer_nguyen", "123456"),
    )
    assert res_overview.status_code == 200
    assert "Nguyễn Văn Định" not in res_overview.json()["farm_owner"]
    assert "ẩn danh" in res_overview.json()["farm_owner"].lower()

    # 4. Kiểm tra cổng tra cứu công khai cho người tiêu dùng: vẫn xem được bình thường
    res_public = client.get(f"/public/trace/{batch_id}")
    assert res_public.status_code == 200
    assert res_public.json()["product_name"] == "Xoài Cát Chu VietGAP"
    assert "Nguyễn Văn Định" not in res_public.json()["farm_owner"]

    # 5. KHẲNG ĐỊNH QUAN TRỌNG: Chuỗi băm sự kiện lịch sử vẫn nguyên vẹn, không bị đứt gãy
    res_events = client.get(
        f"/batches/{batch_id}/events",
        auth=("farmer_nguyen", "123456"),
    )
    assert res_events.status_code == 200
    timeline = res_events.json()
    assert timeline["is_valid"] is True
    assert timeline["tampered_index"] is None
    assert len(timeline["events"]) == 1
