"""Kiểm thử quy trình cảnh báo và xác nhận xử lý lệnh thu hồi nông sản (Story 5).

Kịch bản kiểm thử:
1. Cảnh báo khẩn cấp: Nhà phân phối thấy danh sách lô của tổ chức mình trong diện thu hồi khi gọi active-alerts.
2. Xác nhận đã xử lý: Ghi nhận trạng thái COMPLETED, người thực hiện (actor), thời điểm (timestamp) và ghi chú thực địa.
3. Trạng thái chờ: Khi chưa bấm xử lý, phần trách nhiệm của tổ chức vẫn hiển thị nhãn PENDING.
"""

from datetime import date, datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, Farm, User, Recall, RecallAssignment, ROLE_FARMER
from app.security import hash_password


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

    # Tạo User Nhà phân phối WinMart và User Siêu thị Co.opmart
    distributor_user = User(
        username="winmart_staff",
        password=hash_password("123456"),
        role=ROLE_FARMER,
        organization="Hệ Thống WinMart Miền Bắc",
    )
    other_user = User(
        username="coop_staff",
        password=hash_password("123456"),
        role=ROLE_FARMER,
        organization="Hệ Thống Co.opmart",
    )
    db.add_all([distributor_user, other_user])
    db.commit()

    farm = Farm(name="Vùng Trồng Mẫu", location="Hà Nội", area=3.0, owner="Nông Dân C")
    db.add(farm)
    db.commit()

    batch_wm = Batch(
        farm_id=farm.id,
        product_name="Dưa Lưới Fuji",
        quantity=300.0,
        harvest_date=date(2026, 10, 2),
    )
    batch_coop = Batch(
        farm_id=farm.id,
        product_name="Dưa Lưới Fuji",
        quantity=200.0,
        harvest_date=date(2026, 10, 2),
    )
    db.add_all([batch_wm, batch_coop])
    db.commit()

    # Tạo Lệnh thu hồi khẩn cấp
    recall = Recall(
        code="RECALL-2026-001",
        title="Thu hồi dưa lưới do phát hiện vi khuẩn Salmonella",
        reason="Mẫu kiểm nghiệm tại chợ đầu mối vượt ngưỡng cho phép",
        status="ACTIVE",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    db.add(recall)
    db.commit()

    # Phân công thu hồi cho WinMart và Co.opmart
    assign_wm = RecallAssignment(
        recall_id=recall.id,
        batch_id=batch_wm.id,
        organization="Hệ Thống WinMart Miền Bắc",
        status="PENDING",
    )
    assign_coop = RecallAssignment(
        recall_id=recall.id,
        batch_id=batch_coop.id,
        organization="Hệ Thống Co.opmart",
        status="PENDING",
    )
    db.add_all([assign_wm, assign_coop])
    db.commit()

    yield {
        "db": db,
        "recall_id": recall.id,
        "assign_wm_id": assign_wm.id,
        "assign_coop_id": assign_coop.id,
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


def test_distributor_active_recall_alerts(client, db_session):
    """Giả sử lệnh thu hồi vừa mở, Khi tôi đăng nhập, Thì thấy cảnh báo nổi bật liệt kê lô của tôi trong lệnh."""
    res = client.get(
        "/recalls/active-alerts",
        auth=("winmart_staff", "123456"),
    )
    assert res.status_code == 200
    alerts = res.json()
    assert len(alerts) == 1
    assert alerts[0]["recall_code"] == "RECALL-2026-001"
    assert alerts[0]["product_name"] == "Dưa Lưới Fuji"
    assert alerts[0]["organization"] == "Hệ Thống WinMart Miền Bắc"
    assert alerts[0]["status"] == "PENDING"


def test_resolve_recall_with_timestamp_and_notes(client, db_session):
    """Giả sử tôi đã gom hàng khỏi kệ, Khi xác nhận đã xử lý, Thì lệnh ghi nhận tổ chức tôi đã xong kèm thời điểm và ghi chú."""
    recall_id = db_session["recall_id"]
    assign_id = db_session["assign_wm_id"]

    res = client.post(
        f"/recalls/{recall_id}/assignments/{assign_id}/resolve",
        json={"notes": "Đã niêm phong 300kg dưa lưới trong kho cách ly số 3, chờ tiêu hủy."},
        auth=("winmart_staff", "123456"),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "COMPLETED"
    assert data["resolved_by"] == "winmart_staff"
    assert data["resolved_at"] is not None
    assert "Đã niêm phong 300kg" in data["notes"]


def test_unresolved_distributor_stays_pending(client, db_session):
    """Giả sử tôi chưa xử lý xong, Khi xem, Thì phần của tôi vẫn hiện là đang chờ (PENDING)."""
    recall_id = db_session["recall_id"]

    res = client.get(
        f"/recalls/{recall_id}",
        auth=("coop_staff", "123456"),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == "RECALL-2026-001"

    coop_item = next(a for a in data["assignments"] if a["organization"] == "Hệ Thống Co.opmart")
    assert coop_item["status"] == "PENDING"
    assert coop_item["resolved_by"] is None
