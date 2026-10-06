"""Xác thực (authentication) và phân quyền (authorization) cơ bản - Sprint 4.

Nguyên tắc của sprint này: **đơn giản, chạy được demo, không JWT**.
- Không sinh token, không lưu session ở server, không refresh token.
- Client gửi kèm thông tin đăng nhập ở mỗi request theo chuẩn **HTTP Basic**:
  ``Authorization: Basic base64(username:password)``.
- ``POST /auth/login`` (xem ``app/routers/auth.py``) dùng cùng hàm kiểm tra
  bên dưới, chỉ để frontend biết ``username`` + ``role`` ngay sau khi đăng nhập.

Hai dependency phân quyền dùng cho các router::

    from app.security import require_farmer

    @router.post("/farms")
    def create_farm(user: User = Depends(require_farmer), ...):
        ...

Vì dùng ``HTTPBasic`` của FastAPI nên Swagger UI tự hiện nút **Authorize** và
có thể test quyền ngay trên ``/docs``.

Lưu ý bảo mật: chọn HTTP Basic + SHA-256 là để demo nhanh, **không dùng cho
production** (Basic gửi mật khẩu ở mọi request nên bắt buộc phải có HTTPS;
SHA-256 không salt nên không chống được brute-force - production nên dùng
``bcrypt``/``argon2`` qua ``passlib`` và chuyển sang JWT/OAuth2).
"""
from __future__ import annotations

import hashlib
from hmac import compare_digest

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ROLE_ADMIN, ROLE_FARMER, User

# ``auto_error=False`` để mình tự trả lỗi 401 với thông điệp tiếng Việt,
# thay vì để FastAPI trả "Not authenticated" (mặc định của HTTPBasic).
basic_scheme = HTTPBasic(auto_error=False)

# ------------------------------------------------------- Mật khẩu (hash) ---
def hash_password(raw_password: str) -> str:
    """Băm mật khẩu bằng SHA-256, trả về chuỗi hex 64 ký tự.

    Dùng ``hashlib`` của Python standard library nên **không cần cài thêm
    thư viện**. Database chỉ lưu giá trị đã băm, không lưu mật khẩu thô.

    Args:
        raw_password: Mật khẩu người dùng nhập (dạng thô).

    Returns:
        str: Mật khẩu đã băm, dạng hex.

    Note:
        Chỉ dùng cho demo: SHA-256 ở đây **không có salt** và tính rất nhanh
        nên không chống được brute-force/rainbow table.
    """
    return hashlib.sha256(raw_password.encode("utf-8")).hexdigest()


def verify_password(raw_password: str, hashed_password: str) -> bool:
    """So sánh mật khẩu người dùng nhập với mật khẩu đã băm trong database.

    Không so sánh bằng ``==`` mà dùng ``hmac.compare_digest`` (so sánh theo
    thời gian hằng) để tránh timing attack.
    """
    return compare_digest(hash_password(raw_password), hashed_password)


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    """Tra bảng ``users`` và trả về tài khoản nếu thông tin đăng nhập đúng.

    Args:
        db: Session SQLAlchemy hiện tại.
        username: Tên đăng nhập.
        password: Mật khẩu dạng thô (hàm tự băm để so sánh).

    Returns:
        User | None: Tài khoản nếu hợp lệ, ``None`` nếu sai username **hoặc**
        sai mật khẩu (cố tình không phân biệt để tránh dò tài khoản).
    """
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(password, user.password):
        return None
    return user


# ----------------------------------------------------------- Dependencies ---
def get_current_user(
    credentials: HTTPBasicCredentials | None = Depends(basic_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Dependency: lấy tài khoản đang gọi API từ header ``Authorization``.

    Raises:
        HTTPException: **401** nếu thiếu header ``Authorization``, hoặc
            username/mật khẩu không đúng. Header ``WWW-Authenticate: Basic``
            giúp Swagger UI/browser biết cần đăng nhập.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Chưa đăng nhập hoặc thông tin đăng nhập không đúng.",
        headers={"WWW-Authenticate": "Basic"},
    )

    if credentials is None:  # client không gửi header Authorization
        raise unauthorized

    user = authenticate_user(db, credentials.username, credentials.password)
    if user is None:  # username không tồn tại hoặc sai mật khẩu
        raise unauthorized

    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency: **chỉ** tài khoản role ``admin`` được phép gọi API.

    Dùng cho các chức năng quản trị hệ thống - ví dụ ``GET /users``.

    Args:
        current_user: Tài khoản đã xác thực do ``get_current_user`` cấp.

    Returns:
        User: Tài khoản admin (router có thể dùng để log/audit).

    Raises:
        HTTPException: **401** nếu chưa đăng nhập; **403** nếu đã đăng nhập
            nhưng vai trò không phải admin.
    """
    if current_user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ tài khoản admin được phép thực hiện chức năng này.",
        )
    return current_user


def require_farmer(current_user: User = Depends(get_current_user)) -> User:
    """Dependency: cho phép **farmer và admin** (nhóm chức năng nông sản).

    Quy ước phân quyền của sprint này:

    - ``farmer`` (nông dân): quản lý nông sản - vùng trồng + lô nông sản.
    - ``admin``: có toàn quyền nên cũng đi qua được dependency này.

    Args:
        current_user: Tài khoản đã xác thực do ``get_current_user`` cấp.

    Returns:
        User: Tài khoản đang gọi API.

    Raises:
        HTTPException: **401** nếu chưa đăng nhập; **403** nếu vai trò không
            nằm trong danh sách được phép.
    """
    if current_user.role not in (ROLE_FARMER, ROLE_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Chỉ tài khoản nông dân (farmer) hoặc admin "
                "được phép thực hiện chức năng này."
            ),
        )
    return current_user


__all__ = [
    "authenticate_user",
    "basic_scheme",
    "get_current_user",
    "hash_password",
    "require_admin",
    "require_farmer",
    "verify_password",
]

