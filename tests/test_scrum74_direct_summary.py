"""Kiểm thử truy vấn tổng hợp chi tiết lô kèm lô mẹ và lô con trực tiếp (SCRUM-74 / T-55 / T-39 / T-54).

🎯 Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
• Trả về đầy đủ các trường dữ liệu: lô, thửa, sản phẩm, tổ chức đang giữ, danh sách lô mẹ trực tiếp, danh sách lô con trực tiếp, số lượng sự kiện.
• Log truy vấn DB cho thấy tối đa 3 câu lệnh SQL; không tải toàn bộ dòng thời gian ở đây (chỉ đếm số lượng sự kiện).
• Kiểm quyền qua T-54: lô bị hạn chế (is_restricted=True) thì tài khoản không thuộc tổ chức / quyền hạn sẽ nhận 403 Forbidden.
"""

from __future__ import annotations

from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Batch,
    BatchEvent,
    BatchRelation,
    Farm,
    User,
    ROLE_ADMIN,
    ROLE_FARMER,
)
from app.security import hash_password


@pytest.fixture
def scrum74_env():
    """Thiết lập môi trường kiểm thử với SQLite engine và bộ đếm câu lệnh SQL."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Đếm câu lệnh SQL
    sql_queries = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        # Bỏ qua các lệnh BEGIN/COMMIT hoặc PRAGMA của SQLite
        stmt_clean = statement.strip().upper()
        if not stmt_clean.startswith("PRAGMA") and not stmt_clean.startswith("ROLLBACK"):
            sql_queries.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)

    with TestingSessionLocal() as db:
        # Tạo người dùng
        admin_user = User(username="admin_user", password=hash_password("123456"), role=ROLE_ADMIN)
        farmer_user = User(username="farmer_org4", password=hash_password("123456"), role=ROLE_FARMER)
        stranger_user = User(username="stranger_farmer", password=hash_password("123456"), role=ROLE_FARMER)
        db.add_all([admin_user, farmer_user, stranger_user])

        # Tạo vùng trồng
        farm = Farm(
            name="Vùng Trồng Sầu Riêng Ri6 Cái Mơn",
            location="Chợ Lách, Bến Tre",
            area=5.2,
            owner="HTX Nông Nghiệp Số 4",
        )
        db.add(farm)
        db.commit()

        # Tạo lô mẹ 1 & 2
        p1 = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6",
            quantity=80.0,
            harvest_date=date(2026, 6, 10),
            batch_code="LOT-P1",
            current_holder_org="HTX Nông Nghiệp Số 4",
        )
        p2 = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6",
            quantity=120.0,
            harvest_date=date(2026, 6, 11),
            batch_code="LOT-P2",
            current_holder_org="HTX Nông Nghiệp Số 4",
        )
        db.add_all([p1, p2])
        db.commit()

        # Tạo lô hiện tại B (gộp từ P1 và P2)
        b = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6 Thượng Hạng",
            quantity=150.0,
            harvest_date=date(2026, 6, 12),
            batch_code="LOT-MAIN-B",
            current_holder_org="HTX Nông Nghiệp Số 4",
            current_owner="farmer_org4",
            is_restricted=False,
        )
        db.add(b)
        db.commit()

        # Quan hệ mẹ -> B
        rel_p1 = BatchRelation(parent_batch_id=p1.id, child_batch_id=b.id, used_quantity=60.0)
        rel_p2 = BatchRelation(parent_batch_id=p2.id, child_batch_id=b.id, used_quantity=90.0)
        db.add_all([rel_p1, rel_p2])

        # Tạo lô con C1 và C2 từ B
        c1 = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6 Đóng Thùng Loại 1",
            quantity=50.0,
            harvest_date=date(2026, 6, 13),
            batch_code="LOT-CHILD-C1",
            current_holder_org="HTX Nông Nghiệp Số 4",
            parent_id=b.id,
        )
        c2 = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6 Đóng Thùng Loại 2",
            quantity=70.0,
            harvest_date=date(2026, 6, 13),
            batch_code="LOT-CHILD-C2",
            current_holder_org="HTX Nông Nghiệp Số 4",
            parent_id=b.id,
        )
        db.add_all([c1, c2])
        db.commit()

        # Tạo 10 sự kiện vào nhật ký của B để kiểm chứng không tải toàn bộ dòng thời gian
        for i in range(10):
            ev = BatchEvent(
                batch_id=b.id,
                event_type="AUDIT_EVENT",
                payload=f'{{"step": {i}, "note": "Ghi nhận kiểm định đợt {i}"}}',
                actor="farmer_org4",
                organization="HTX Nông Nghiệp Số 4",
                timestamp=f"2026-06-12T10:{i:02d}:00Z",
                hash="f" * 64,
                previous_hash="0" * 64,
            )
            db.add(ev)
        db.commit()

        # Tạo thêm 1 lô bị hạn chế T-54 (is_restricted = True)
        restricted_b = Batch(
            farm_id=farm.id,
            product_name="Sầu Riêng Ri6 Mật Mã",
            quantity=40.0,
            harvest_date=date(2026, 6, 14),
            batch_code="LOT-RESTRICTED-T54",
            current_holder_org="HTX Nông Nghiệp Số 4",
            current_owner="farmer_org4",
            is_restricted=True,
        )
        db.add(restricted_b)
        db.commit()

        main_b_id = b.id
        restricted_b_id = restricted_b.id

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "client": client,
        "main_b_id": main_b_id,
        "restricted_b_id": restricted_b_id,
        "sql_queries": sql_queries,
    }

    app.dependency_overrides.clear()
    event.remove(engine, "before_cursor_execute", before_cursor_execute)


def test_direct_summary_returns_all_fields_and_under_3_sql_queries(scrum74_env):
    """SCRUM-74: Trả về đầy đủ trường dữ liệu và log truy vấn tối đa 3 câu lệnh SQL."""
    client = scrum74_env["client"]
    b_id = scrum74_env["main_b_id"]
    sql_queries = scrum74_env["sql_queries"]
    auth = ("farmer_org4", "123456")

    from app.tenant import set_tenant_org
    set_tenant_org("HTX Nông Nghiệp Số 4")

    sql_queries.clear()

    res = client.get(
        f"/batches/{b_id}/direct-summary",
        auth=auth,
    )
    assert res.status_code == 200, res.text
    data = res.json()

    # 1. Kiểm tra số lượng câu SQL: TỐI ĐA 3 CÂU LỆNH SQL
    # Có thể bao gồm xác thực user (nếu chưa cache), nhưng câu lệnh trong endpoint <= 3
    # Lọc các truy vấn liên quan tới nghiệp vụ batches / farms / events
    batch_queries = [
        q for q in sql_queries
        if any(table in q.lower() for table in ["batches", "farms", "batch_events", "batch_relations"])
    ]
    assert len(batch_queries) <= 3, (
        f"Số lượng câu lệnh SQL ({len(batch_queries)}) vượt quá giới hạn 3 câu lệnh: {batch_queries}"
    )

    # 2. Kiểm tra không tải toàn bộ dòng thời gian: chỉ đếm số lượng sự kiện
    assert data["events_count"] == 10
    assert "events" not in data, "Không được tải toàn bộ mảng events trong API này"

    # 3. Kiểm tra đầy đủ các trường dữ liệu
    assert data["id"] == b_id
    assert data["batch_code"] == "LOT-MAIN-B"
    assert data["product_name"] == "Sầu Riêng Ri6 Thượng Hạng"
    assert data["quantity"] == 150.0
    assert data["harvest_date"] == "2026-06-12"
    assert data["is_restricted"] is False
    assert data["current_holder_org"] == "HTX Nông Nghiệp Số 4"

    # Thửa / Vùng trồng
    farm_info = data["farm"]
    assert farm_info["name"] == "Vùng Trồng Sầu Riêng Ri6 Cái Mơn"
    assert "Chợ Lách" in farm_info["location"]
    assert farm_info["area"] == 5.2
    assert "Thửa" in farm_info["plot_name"]

    # Sản phẩm & tổ chức
    assert data["product"]["name"] == "Sầu Riêng Ri6 Thượng Hạng"
    assert data["holder_org"]["name"] == "HTX Nông Nghiệp Số 4"

    # Quan hệ trực tiếp mẹ (1 tầng, T-39)
    parents = data["direct_parents"]
    assert len(parents) == 2
    parent_codes = [p["batch_code"] for p in parents]
    assert "LOT-P1" in parent_codes
    assert "LOT-P2" in parent_codes

    # Quan hệ trực tiếp con (1 tầng, T-39)
    children = data["direct_children"]
    assert len(children) == 2
    child_codes = [c["batch_code"] for c in children]
    assert "LOT-CHILD-C1" in child_codes
    assert "LOT-CHILD-C2" in child_codes


def test_direct_summary_t54_permission_control(scrum74_env):
    """SCRUM-74: Kiểm quyền qua T-54 (is_restricted)."""
    client = scrum74_env["client"]
    restricted_id = scrum74_env["restricted_b_id"]

    # 1. Người lạ thuộc tổ chức khác cố tình truy cập lô bị hạn chế -> 403 Forbidden
    from app.tenant import set_tenant_org
    set_tenant_org("Doanh Nghiep Nguoi La")
    res_stranger = client.get(
        f"/batches/{restricted_id}/direct-summary",
        auth=("stranger_farmer", "123456"),
    )
    assert res_stranger.status_code == 403
    assert "hạn chế bảo mật" in res_stranger.json()["detail"] or "T-54" in res_stranger.json()["detail"]

    # 2. Chủ sở hữu hợp pháp -> 200 OK
    set_tenant_org("HTX Nông Nghiệp Số 4")
    res_owner = client.get(
        f"/batches/{restricted_id}/direct-summary",
        auth=("farmer_org4", "123456"),
    )
    assert res_owner.status_code == 200

    # 3. Quản trị viên (admin) -> 200 OK
    set_tenant_org("Doanh Nghiep Nguoi La")  # Dù org nào admin vẫn được xem
    res_admin = client.get(
        f"/batches/{restricted_id}/direct-summary",
        auth=("admin_user", "123456"),
    )
    assert res_admin.status_code == 200
