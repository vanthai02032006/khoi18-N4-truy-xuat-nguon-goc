"""Bộ kiểm thử cho tính năng Nhật ký kiểm toán hệ thống (Audit Log Middleware).

Đặc tả User Story:
Là quản trị hệ thống tôi muốn xem lại ai đã thực hiện thao tác nào để làm rõ trách nhiệm
khi có tranh cãi mà không phải đọc chuỗi sự kiện của từng lô.

Tiêu chí nghiệm thu (DoD / Acceptance Criteria):
1. Giả sử một người dùng đăng nhập, tạo lô, tách lô,
   Khi quản trị mở nhật ký,
   Thì thấy ba dòng kèm người, tổ chức, thời điểm, địa chỉ IP và mã đối tượng.
2. Giả sử lần đăng nhập thất bại,
   Khi xem nhật ký,
   Thì có dòng ghi nhận kèm lý do.
3. Ràng buộc: Ghi ở middleware, không rải rác trong từng handler.
4. Phân quyền: Chỉ tài khoản role=admin mới có quyền xem nhật ký kiểm toán.
"""

from __future__ import annotations

import inspect
from datetime import date
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import (
    ROLE_ADMIN,
    ROLE_FARMER,
    AuditLog,
    Farm,
    User,
)
from app.routers import auth as auth_router
from app.routers import batches as batches_router
from app.security import hash_password


@pytest.fixture()
def audit_test_env(tmp_path) -> Iterator[dict]:
    """Khởi tạo môi trường CSDL file tạm riêng cho từng bài test audit log."""
    db_file = tmp_path / "audit_test.db"
    engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)

    # Nạp tài khoản và dữ liệu mẫu
    with session_factory() as session:
        admin_user = User(
            username="admin_auditor",
            password=hash_password("admin123"),
            role=ROLE_ADMIN,
        )
        farmer_user = User(
            username="farmer_viet",
            password=hash_password("farm123"),
            role=ROLE_FARMER,
        )
        sample_farm = Farm(
            name="Vườn Xoài Cát Chu Cao Lãnh #1",
            location="Đồng Tháp",
            area=5.0,
            owner="HTX Nông Nghiệp Số 4",
        )
        session.add_all([admin_user, farmer_user, sample_farm])
        session.commit()
        farm_id = sample_farm.id

    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)
    try:
        yield {
            "client": client,
            "engine": engine,
            "farm_id": farm_id,
            "admin_auth": ("admin_auditor", "admin123"),
            "farmer_auth": ("farmer_viet", "farm123"),
        }
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_audit_log_three_steps_login_create_split(audit_test_env):
    """Tiêu chí 1: Người dùng đăng nhập, tạo lô, tách lô -> Thấy đúng 3 dòng
    kèm người, tổ chức, thời điểm, địa chỉ IP và mã đối tượng.
    """
    client: TestClient = audit_test_env["client"]
    farmer_auth = audit_test_env["farmer_auth"]
    admin_auth = audit_test_env["admin_auth"]
    farm_id = audit_test_env["farm_id"]
    client_ip = "192.168.1.88"
    headers = {"X-Forwarded-For": client_ip}

    # 1. Thao tác 1: Người dùng đăng nhập
    login_res = client.post(
        "/auth/login",
        json={"username": "farmer_viet", "password": "farm123"},
        headers=headers,
    )
    assert login_res.status_code == 200, f"Đăng nhập thất bại: {login_res.text}"

    # 2. Thao tác 2: Người dùng tạo lô nông sản
    create_batch_payload = {
        "farm_id": farm_id,
        "product_name": "Xoài Cát Chu Loại 1",
        "quantity": 1000.0,
        "harvest_date": "2026-09-20",
        "batch_code": "LOT-2026-AUDIT-01",
    }
    create_res = client.post(
        "/batches",
        json=create_batch_payload,
        auth=farmer_auth,
        headers=headers,
    )
    assert create_res.status_code == 201, f"Tạo lô thất bại: {create_res.text}"
    created_batch = create_res.json()
    batch_id = created_batch["id"]
    batch_code = created_batch["batch_code"]
    assert bool(batch_code)

    # 3. Thao tác 3: Người dùng tách lô nông sản
    split_payload = {
        "items": [
            {"quantity": 300.0},
            {"quantity": 200.0},
        ],
        "child_quantities": [300.0, 200.0],
    }
    split_res = client.post(
        f"/batches/{batch_id}/split",
        json=split_payload,
        auth=farmer_auth,
        headers=headers,
    )
    assert split_res.status_code == 201, f"Tách lô thất bại: {split_res.text}"

    # 4. Quản trị viên mở nhật ký kiểm toán (GET /audit-logs)
    audit_res = client.get("/audit-logs", auth=admin_auth)
    assert audit_res.status_code == 200, f"Không thể lấy nhật ký: {audit_res.text}"
    logs = audit_res.json()

    # Khẳng định thấy đúng 3 dòng nhật ký
    assert len(logs) == 3, f"Kỳ vọng 3 dòng nhật ký nhưng nhận {len(logs)}: {logs}"

    # Dòng 1: Đăng nhập
    log_login = logs[0]
    assert log_login["actor"] == "farmer_viet"  # người
    assert log_login["organization"] == "HTX Nông Nghiệp Số 4"  # tổ chức
    assert log_login["timestamp"] is not None and "T" in log_login["timestamp"]  # thời điểm
    assert log_login["ip_address"] == client_ip  # địa chỉ IP
    assert log_login["target_code"] == "farmer_viet"  # mã đối tượng
    assert log_login["action"] == "LOGIN"
    assert log_login["status"] == "SUCCESS"

    # Dòng 2: Tạo lô
    log_create = logs[1]
    assert log_create["actor"] == "farmer_viet"  # người
    assert log_create["organization"] == "HTX Nông Nghiệp Số 4"  # tổ chức
    assert log_create["timestamp"] is not None and "T" in log_create["timestamp"]  # thời điểm
    assert log_create["ip_address"] == client_ip  # địa chỉ IP
    assert log_create["target_code"] == batch_code  # mã đối tượng (mã lô vừa tạo)
    assert log_create["action"] == "CREATE_BATCH"
    assert log_create["status"] == "SUCCESS"

    # Dòng 3: Tách lô
    log_split = logs[2]
    assert log_split["actor"] == "farmer_viet"  # người
    assert log_split["organization"] == "HTX Nông Nghiệp Số 4"  # tổ chức
    assert log_split["timestamp"] is not None and "T" in log_split["timestamp"]  # thời điểm
    assert log_split["ip_address"] == client_ip  # địa chỉ IP
    assert log_split["target_code"] == batch_code  # mã đối tượng (mã lô mẹ bị tách)
    assert log_split["action"] == "SPLIT_BATCH"
    assert log_split["status"] == "SUCCESS"


def test_audit_log_failed_login_records_reason(audit_test_env):
    """Tiêu chí 2: Đăng nhập thất bại -> Có dòng ghi nhận kèm lý do."""
    client: TestClient = audit_test_env["client"]
    admin_auth = audit_test_env["admin_auth"]
    client_ip = "203.0.113.42"

    # Cố tình đăng nhập với mật khẩu sai
    res = client.post(
        "/auth/login",
        json={"username": "farmer_viet", "password": "wrong_password_123"},
        headers={"X-Forwarded-For": client_ip},
    )
    assert res.status_code == 401

    # Quản trị viên xem nhật ký
    audit_res = client.get("/audit-logs", auth=admin_auth)
    assert audit_res.status_code == 200
    logs = audit_res.json()

    assert len(logs) == 1
    failed_log = logs[0]
    assert failed_log["actor"] == "farmer_viet"
    assert failed_log["action"] == "LOGIN"
    assert failed_log["status"] == "FAILED"
    assert failed_log["ip_address"] == client_ip
    assert failed_log["target_code"] == "farmer_viet"
    # Kèm lý do thất bại rõ ràng
    assert failed_log["reason"] is not None
    assert "Sai tên đăng nhập hoặc mật khẩu" in failed_log["reason"]


def test_audit_log_unknown_user_failed_login(audit_test_env):
    """Đăng nhập thất bại với tài khoản hoàn toàn không tồn tại -> Ghi nhận kèm lý do."""
    client: TestClient = audit_test_env["client"]
    admin_auth = audit_test_env["admin_auth"]

    res = client.post(
        "/auth/login",
        json={"username": "intruder_user", "password": "anypassword"},
    )
    assert res.status_code == 401

    logs = client.get("/audit-logs", auth=admin_auth).json()
    assert len(logs) == 1
    assert logs[0]["actor"] == "intruder_user"
    assert logs[0]["status"] == "FAILED"
    assert logs[0]["reason"] is not None


def test_audit_log_only_admin_can_access(audit_test_env):
    """Phân quyền: Chỉ admin được xem nhật ký kiểm toán; farmer bị 403, unauthenticated bị 401."""
    client: TestClient = audit_test_env["client"]
    farmer_auth = audit_test_env["farmer_auth"]
    admin_auth = audit_test_env["admin_auth"]

    # 1. Unauthenticated -> 401
    res_anon = client.get("/audit-logs")
    assert res_anon.status_code == 401

    # 2. Farmer -> 403 Forbidden
    res_farmer = client.get("/audit-logs", auth=farmer_auth)
    assert res_farmer.status_code == 403

    # 3. Admin -> 200 OK
    res_admin = client.get("/audit-logs", auth=admin_auth)
    assert res_admin.status_code == 200

    # 4. Kiểm tra endpoint alias /admin/audit-logs
    res_alias = client.get("/admin/audit-logs", auth=admin_auth)
    assert res_alias.status_code == 200


def test_audit_log_recorded_by_middleware_not_in_handlers():
    """Ràng buộc kỹ thuật: Ghi ở middleware, không rải rác trong từng handler."""
    auth_source = inspect.getsource(auth_router)
    batches_source = inspect.getsource(batches_router)

    # Khẳng định không có bất kỳ lệnh gọi ghi AuditLog nào bên trong handlers
    assert "AuditLog" not in auth_source, "Không được rải AuditLog bên trong routers/auth.py!"
    assert "AuditLog" not in batches_source, "Không được rải AuditLog bên trong routers/batches.py!"
    assert "save_audit_log" not in auth_source, "Không được gọi save_audit_log bên trong routers/auth.py!"
    assert "save_audit_log" not in batches_source, "Không được gọi save_audit_log bên trong routers/batches.py!"
