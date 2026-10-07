"""Kiểm thử và đo lường hiệu năng truy vấn sự kiện nối bảng organizations (SCRUM-44 / T-28).

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
1. Một truy vấn nối bảng sự kiện với organizations trong một lượt, không truy vấn con (No subquery / No N+1).
2. Lọc theo quyền xem qua hàm phân quyền tổ chức ở T-12 (Tenant scoping / Data isolation).
3. Dùng chỉ mục ở T-23: trả về đúng thứ tự thời gian (chronological order).
4. Đo thời gian với 200 sự kiện: thời gian thực thi dưới 200ms.
5. Log DB / event listener ghi nhận chỉ 1 câu lệnh SQL duy nhất khi lấy danh sách sự kiện.
6. Hỗ trợ phân trang khi lô có trên 500 sự kiện (limit, offset).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import time
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, BatchEvent, Farm, Organization, User, ROLE_FARMER
from app.security import compute_event_hash, hash_password
from app.tenant import set_tenant_org


@pytest.fixture
def benchmark_env():
    """Thiết lập môi trường test SQLite in-memory với 200 sự kiện và đo lường SQL."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    sql_statements = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        sql_statements.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)

    with TestingSessionLocal() as db:
        # 1. Tạo tổ chức
        org_a = Organization(
            code="ORG-A",
            name="HTX Nông Sản Đồng Tháp A",
            description="Hợp tác xã nông sản chuyên xoài",
        )
        org_b = Organization(
            code="ORG-B",
            name="Doanh Nghiệp Chế Biến B",
            description="Doanh nghiệp sấy nông sản",
        )
        db.add_all([org_a, org_b])
        db.commit()

        # 2. Tạo User & Farm & Batch
        user = User(
            username="farmer_benchmark",
            password=hash_password("123456"),
            role=ROLE_FARMER,
        )
        db.add(user)

        farm = Farm(
            name="Vùng Trồng Xoài Thử Nghiệm",
            location="Đồng Tháp",
            area=5.0,
            owner="HTX Nông Sản Đồng Tháp A",
        )
        db.add(farm)
        db.commit()

        batch = Batch(
            farm_id=farm.id,
            product_name="Xoài Cát Chu",
            quantity=1000.0,
            harvest_date=date(2026, 5, 20),
        )
        db.add(batch)
        db.commit()

        # 3. Tạo 200 sự kiện có liên kết chuỗi mã băm (Cryptographic hash chain)
        prev_hash = "0" * 64
        events = []
        for i in range(200):
            ts = f"2026-05-20T{8 + (i // 60):02d}:{i % 60:02d}:00Z"
            payload = f'{{"step": {i}, "temperature": 25.{i % 10}}}'
            actor = "farmer_benchmark"
            org_code = "ORG-A"
            h = compute_event_hash(
                event_type="MONITORING",
                payload=payload,
                actor=actor,
                organization=org_code,
                timestamp=ts,
                previous_hash=prev_hash,
            )
            ev = BatchEvent(
                batch_id=batch.id,
                org_id=org_a.id,
                event_type="MONITORING",
                payload=payload,
                actor=actor,
                organization=org_code,
                timestamp=ts,
                hash=h,
                previous_hash=prev_hash,
            )
            events.append(ev)
            prev_hash = h

        db.add_all(events)
        db.commit()

        batch_id = batch.id

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    def override_current_user():
        u = User(username="farmer_benchmark", role=ROLE_FARMER)
        u.id = 1
        return u

    from app.security import get_current_user
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_current_user
    client = TestClient(app)

    yield {
        "client": client,
        "batch_id": batch_id,
        "sql_statements": sql_statements,
        "org_a": org_a,
        "org_b": org_b,
        "db_session_factory": TestingSessionLocal,
    }

    app.dependency_overrides.clear()
    event.remove(engine, "before_cursor_execute", before_cursor_execute)


def test_single_query_join_and_performance_under_200ms(benchmark_env):
    """Tiêu chí nghiệm thu:

    1. Trả về đúng 200 sự kiện, nối với organizations lấy được tên tổ chức đầy đủ.
    2. Thứ tự đúng chuẩn thời gian (chronological order).
    3. Thời gian thực thi dưới 200ms.
    4. Log DB ghi nhận chỉ 1 câu lệnh SQL duy nhất khi thực thi endpoint.
    5. Không có subquery / N+1 query.
    """
    client = benchmark_env["client"]
    batch_id = benchmark_env["batch_id"]
    sql_statements = benchmark_env["sql_statements"]

    set_tenant_org("ORG-A")

    # Xoá danh sách SQL đã chạy khi setup dữ liệu
    sql_statements.clear()

    # Đo thời gian thực thi
    start_time = time.perf_counter()
    response = client.get(
        f"/batches/{batch_id}/events",
        auth=("farmer_benchmark", "123456"),
        headers={"X-Organization-Id": "ORG-A"},
    )
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    assert response.status_code == 200, response.text
    data = response.json()

    # 1. Kiểm tra số lượng sự kiện và tính toàn vẹn
    assert data["batch_id"] == batch_id
    assert data["is_valid"] is True
    assert data["tampered_index"] is None
    assert len(data["events"]) == 200

    # 2. Kiểm tra tên tổ chức được lấy qua JOIN
    for ev in data["events"]:
        assert ev["organization_name"] == "HTX Nông Sản Đồng Tháp A"
        assert ev["organization"] == "ORG-A"

    # 3. Kiểm tra đúng thứ tự thời gian tăng dần
    timestamps = [ev["timestamp"] for ev in data["events"]]
    assert timestamps == sorted(timestamps)

    # 4. Kiểm tra thời gian thực thi dưới 200ms (DoD: < 200ms)
    print(f"\n[BENCHMARK] Execution time for 200 events: {elapsed_ms:.2f}ms (Threshold < 200ms)")
    assert elapsed_ms < 200.0, f"Execution time {elapsed_ms:.2f}ms exceeded 200ms threshold!"

    # 5. Kiểm tra log DB: Chỉ duy nhất 1 câu lệnh SQL được thực thi
    print(f"[DB AUDIT] Number of SQL statements executed: {len(sql_statements)}")
    for i, s in enumerate(sql_statements, 1):
        print(f"SQL #{i}: {s}")

    assert len(sql_statements) == 1, f"Yêu cầu chỉ 1 câu lệnh SQL duy nhất, nhưng phát hiện {len(sql_statements)} câu!"
    sql_lower = sql_statements[0].lower()

    # Khẳng định có JOIN (hoặc LEFT OUTER JOIN)
    assert "join" in sql_lower
    assert "organizations" in sql_lower
    # Khẳng định không có subquery
    assert "select" in sql_lower
    assert sql_lower.count("select") == 1, "Phát hiện truy vấn con (subquery) bên trong câu SQL!"


def test_tenant_scoping_filter_in_join_query(benchmark_env):
    """Kiểm tra điều kiện lọc theo quyền xem (T-12) vẫn hoạt động chính xác với câu lệnh JOIN."""
    client = benchmark_env["client"]
    batch_id = benchmark_env["batch_id"]

    # Đăng nhập ngữ cảnh của ORG-B (không có sự kiện nào của ORG-B trong lô này)
    set_tenant_org("ORG-B")
    response = client.get(
        f"/batches/{batch_id}/events",
        auth=("farmer_benchmark", "123456"),
        headers={"X-Organization-Id": "ORG-B"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["events"]) == 0


def test_pagination_support_for_large_events(benchmark_env):
    """Lưu ý kỹ thuật: Hỗ trợ phân trang khi lô có nhiều sự kiện (limit & offset)."""
    client = benchmark_env["client"]
    batch_id = benchmark_env["batch_id"]

    set_tenant_org("ORG-A")

    # Lấy trang 1: limit=50, offset=0
    res_page_1 = client.get(
        f"/batches/{batch_id}/events?limit=50&offset=0",
        auth=("farmer_benchmark", "123456"),
        headers={"X-Organization-Id": "ORG-A"},
    )
    assert res_page_1.status_code == 200
    data_1 = res_page_1.json()
    assert len(data_1["events"]) == 50
    assert data_1["events"][0]["payload"] == '{"step": 0, "temperature": 25.0}'
    assert data_1["events"][49]["payload"] == '{"step": 49, "temperature": 25.9}'

    # Lấy trang 2: limit=50, offset=50
    res_page_2 = client.get(
        f"/batches/{batch_id}/events?limit=50&offset=50",
        auth=("farmer_benchmark", "123456"),
        headers={"X-Organization-Id": "ORG-A"},
    )
    assert res_page_2.status_code == 200
    data_2 = res_page_2.json()
    assert len(data_2["events"]) == 50
    assert data_2["events"][0]["payload"] == '{"step": 50, "temperature": 25.0}'
    assert data_2["events"][49]["payload"] == '{"step": 99, "temperature": 25.9}'
