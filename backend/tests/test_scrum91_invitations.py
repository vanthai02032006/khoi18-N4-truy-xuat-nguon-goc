"""Unit test & Integration test cho SCRUM-91: Mời thành viên, phân quyền và cách ly tổ chức."""

from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import (
    OrganizationAuditLog,
    OrganizationInvitation,
    User,
    ROLE_ADMIN,
    ROLE_FARMER,
)
from app.security import hash_password
from app.services.invitation_service import (
    accept_invitation,
    create_invitation,
    revoke_invitation,
    set_user_active_status,
)


@pytest.fixture
def invitation_test_env(tmp_path):
    """Môi trường kiểm thử cô lập cho module lời mời và thành viên tổ chức."""
    db_file = tmp_path / "invitation_test.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    db = TestingSession()
    # Admin hệ thống
    sys_admin = User(username="sysadmin", password=hash_password("123456"), role=ROLE_ADMIN, organization_id="ORG_SYSTEM", email="admin@system.vn")
    # Farmer thường
    farmer1 = User(username="farmer_regular", password=hash_password("123456"), role=ROLE_FARMER, organization_id="ORG_COOP_A", email="farmer@coop.vn")
    db.add_all([sys_admin, farmer1])
    db.commit()

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "db": db,
        "sys_admin": sys_admin,
        "farmer1": farmer1,
        "client": client,
        "admin_auth": ("sysadmin", "123456"),
        "farmer_auth": ("farmer_regular", "123456"),
    }

    app.dependency_overrides.clear()
    db.close()


def test_create_and_accept_invitation_flow(invitation_test_env):
    """Quy trình tạo lời mời, token bảo mật, và chấp nhận lời mời để tạo tài khoản."""
    db = invitation_test_env["db"]
    sys_admin = invitation_test_env["sys_admin"]

    inv, raw_token, invite_link = create_invitation(
        db,
        current_user=sys_admin,
        email="new_member@coop.vn",
        role=ROLE_FARMER,
        target_org_id="ORG_COOP_A",
        expires_in_hours=48,
    )

    assert inv.id is not None
    assert inv.email == "new_member@coop.vn"
    assert inv.token_hash != raw_token  # Token được lưu dưới dạng băm
    assert inv.is_used is False
    assert inv.is_revoked is False
    assert raw_token in invite_link

    # Chấp nhận lời mời
    new_user = accept_invitation(
        db,
        raw_token=raw_token,
        username="new_farmer_01",
        password="secretpassword123",
    )

    assert new_user.username == "new_farmer_01"
    assert new_user.role == ROLE_FARMER
    assert new_user.organization_id == "ORG_COOP_A"
    assert new_user.is_active is True

    # Token chỉ dùng một lần (is_used = True)
    db.refresh(inv)
    assert inv.is_used is True

    # Dùng lại token phải bị từ chối
    with pytest.raises(Exception):
        accept_invitation(db, raw_token=raw_token, username="another_user", password="123")


def test_revoked_or_expired_invitation_cannot_be_accepted(invitation_test_env):
    """Lời mời đã bị thu hồi hoặc hết hạn không thể được chấp nhận."""
    db = invitation_test_env["db"]
    sys_admin = invitation_test_env["sys_admin"]

    # 1. Thu hồi lời mời
    inv, token, _ = create_invitation(db, current_user=sys_admin, email="revoked@coop.vn", target_org_id="ORG_A")
    revoke_invitation(db, invitation_id=inv.id, current_user=sys_admin)

    with pytest.raises(Exception) as exc_info:
        accept_invitation(db, raw_token=token, username="user_revoked", password="123")
    assert "thu hồi" in str(exc_info.value.detail)

    # 2. Lời mời hết hạn
    inv_exp, token_exp, _ = create_invitation(db, current_user=sys_admin, email="expired@coop.vn", target_org_id="ORG_A")
    inv_exp.expires_at = datetime.now() - timedelta(hours=1)
    db.commit()

    with pytest.raises(Exception) as exc_info2:
        accept_invitation(db, raw_token=token_exp, username="user_exp", password="123")
    assert "hết thời hạn" in str(exc_info2.value.detail)


def test_tenant_isolation_and_privilege_escalation_prevention(invitation_test_env):
    """Kiểm tra ngăn chặn mời chéo tổ chức và chống tự nâng quyền admin."""
    db = invitation_test_env["db"]
    farmer1 = invitation_test_env["farmer1"]

    # Farmer cố tình mời vào tổ chức khác ORG_COOP_A -> Phải bị từ chối 403
    with pytest.raises(Exception) as exc_cross:
        create_invitation(
            db,
            current_user=farmer1,
            email="victim@other.vn",
            target_org_id="ORG_ANOTHER",
        )
    assert "tổ chức khác" in str(exc_cross.value.detail)

    # Farmer cố tình gán role='admin' -> Phải bị từ chối 403
    with pytest.raises(Exception) as exc_role:
        create_invitation(
            db,
            current_user=farmer1,
            email="hacker@coop.vn",
            role=ROLE_ADMIN,
        )
    assert "quản trị viên hệ thống" in str(exc_role.value.detail)


def test_deactivate_member_disallows_login(invitation_test_env):
    """Vô hiệu hoá thành viên (is_active = False) khiến người đó không thể đăng nhập."""
    db = invitation_test_env["db"]
    sys_admin = invitation_test_env["sys_admin"]
    farmer1 = invitation_test_env["farmer1"]
    client = invitation_test_env["client"]

    # Đang hoạt động -> gọi API /farms thành công
    resp_ok = client.get("/farms", auth=("farmer_regular", "123456"))
    assert resp_ok.status_code == 200

    # Vô hiệu hoá tài khoản
    set_user_active_status(db, user_id=farmer1.id, active=False, current_user=sys_admin, reason="Nghỉ việc")
    assert farmer1.is_active is False

    # Đăng nhập lại -> Phải nhận 401 Unauthorized vì tài khoản bị vô hiệu hoá
    resp_inactive = client.get("/farms", auth=("farmer_regular", "123456"))
    assert resp_inactive.status_code == 401


def test_audit_log_records_sensitive_operations(invitation_test_env):
    """Kiểm tra mọi thao tác mời, thu hồi, kích hoạt/vô hiệu hoá đều ghi nhật ký kiểm toán."""
    db = invitation_test_env["db"]
    sys_admin = invitation_test_env["sys_admin"]

    inv, token, _ = create_invitation(db, current_user=sys_admin, email="audit_check@test.vn", target_org_id="ORG_TEST")
    revoke_invitation(db, invitation_id=inv.id, current_user=sys_admin)

    logs = list(db.query(OrganizationAuditLog).filter(OrganizationAuditLog.organization_id == "ORG_TEST").all())
    actions = [l.action for l in logs]
    assert "INVITE_SENT" in actions
    assert "INVITE_REVOKED" in actions
