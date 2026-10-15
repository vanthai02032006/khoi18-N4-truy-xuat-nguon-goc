"""Kiểm thử bảo mật đăng nhập & chống Brute-Force (Story 2).

Kịch bản kiểm thử:
1. Đăng nhập đúng: trả về username, role, và organization của tài khoản.
2. Anti-User Enumeration: Sai user hoặc sai password đều trả về 401 chung, không lộ tài khoản.
3. Anti-Brute Force: Nhập sai 5 lần liên tiếp -> lần thứ 6 bị khóa 15 phút (HTTP 429) kể cả khi nhập đúng.
4. Tự động mở khóa sau khi hết hạn 15 phút và reset số lần sai khi đăng nhập đúng.
"""

from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import User, ROLE_FARMER
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
    # Tạo tài khoản thử nghiệm
    user = User(
        username="coop_member",
        password=hash_password("Secret123"),
        role=ROLE_FARMER,
        organization="HTX Nông Nghiệp Mỹ Xương",
        failed_login_attempts=0,
        locked_until=None,
    )
    db.add(user)
    db.commit()

    yield db
    db.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_login_success_with_organization(client):
    """Giả sử thông tin đúng, Khi đăng nhập, Thì tạo phiên và trả về tổ chức của tôi."""
    res = client.post("/auth/login", json={"username": "coop_member", "password": "Secret123"})
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == "coop_member"
    assert data["role"] == "farmer"
    assert data["organization"] == "HTX Nông Nghiệp Mỹ Xương"


def test_anti_user_enumeration(client):
    """Giả sử sai mật khẩu hoặc sai username, Khi đăng nhập, Thì thông báo lỗi chung không tiết lộ."""
    # Sai password
    res1 = client.post("/auth/login", json={"username": "coop_member", "password": "WrongPassword"})
    assert res1.status_code == 401
    assert res1.json()["detail"] == "Sai tên đăng nhập hoặc mật khẩu."

    # User không tồn tại
    res2 = client.post("/auth/login", json={"username": "non_existent_user", "password": "AnyPassword"})
    assert res2.status_code == 401
    assert res2.json()["detail"] == "Sai tên đăng nhập hoặc mật khẩu."


def test_brute_force_lockout_after_5_failures(client, db_session):
    """Giả sử nhập sai 5 lần liên tiếp, Khi thử lần thứ 6, Thì tài khoản bị khoá 15 phút kể cả khi nhập đúng."""
    # Nhập sai 5 lần
    for i in range(5):
        res = client.post("/auth/login", json={"username": "coop_member", "password": f"Wrong_{i}"})
        assert res.status_code == 401

    # Kiểm tra database: tài khoản đã bị set locked_until
    user = db_session.query(User).filter_by(username="coop_member").first()
    assert user.failed_login_attempts == 5
    assert user.locked_until is not None

    # Lần thứ 6: Dù nhập ĐÚNG mật khẩu vẫn bị chặn với HTTP 429 và thông báo khóa
    res6 = client.post("/auth/login", json={"username": "coop_member", "password": "Secret123"})
    assert res6.status_code == 429
    assert "tạm khóa" in res6.json()["detail"]


def test_auto_unlock_after_15_minutes(client, db_session):
    """Tài khoản tự động mở khóa khi đã qua 15 phút và đăng nhập đúng."""
    # Giả lập tài khoản bị khóa trong quá khứ (cách đây 16 phút)
    past_time = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    user = db_session.query(User).filter_by(username="coop_member").first()
    user.failed_login_attempts = 5
    user.locked_until = past_time
    db_session.commit()

    # Thử đăng nhập lại với mật khẩu đúng -> Thành công
    res = client.post("/auth/login", json={"username": "coop_member", "password": "Secret123"})
    assert res.status_code == 200

    # Bộ đếm được reset về 0
    db_session.refresh(user)
    assert user.failed_login_attempts == 0
    assert user.locked_until is None
