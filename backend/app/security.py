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


def get_current_user_optional(
    credentials: HTTPBasicCredentials | None = Depends(basic_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """Dependency: lấy tài khoản nếu có header Authorization, hoặc None nếu không gửi.

    Nếu client gửi credentials nhưng sai mật khẩu -> 401 Unauthorized.
    Nếu client không gửi credentials -> None.
    """
    if credentials is None:
        return None

    user = authenticate_user(db, credentials.username, credentials.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Thông tin đăng nhập không hợp lệ.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user


def check_batch_view_permission(batch: Any, current_user: User | None) -> None:
    """Kiểm tra quyền xem lô nông sản theo T-54.

    Tiêu chí nghiệm thu:
        Lô không có quyền xem bị trả về mã lỗi 403 Forbidden.

    Quy tắc phân quyền T-54:
        1. Tài khoản vai trò `admin`: có toàn quyền xem mọi lô nông sản.
        2. Nếu lô có cờ bảo mật `is_restricted = True`:
           - Phải đăng nhập và là `admin` hoặc là chủ sở hữu (owner).
           - Nếu chưa đăng nhập hoặc không đủ quyền xem -> ném lỗi 403 Forbidden.
        3. Nếu người dùng đăng nhập có vai trò không hợp lệ -> ném lỗi 403 Forbidden.
    """
    # 1. Admin luôn có quyền xem
    if current_user is not None and current_user.role == ROLE_ADMIN:
        return

    is_restricted = getattr(batch, "is_restricted", False)
    owner = getattr(batch, "owner", None)
    farm = getattr(batch, "farm", None)
    farm_owner = farm.owner if farm else None

    # 2. Nếu lô bị giới hạn quyền xem (is_restricted)
    if is_restricted:
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Lô nông sản #{batch.id} thuộc chế độ bảo mật riêng. Bạn không có quyền xem (403 Forbidden).",
            )

        # Kiểm tra xem user có phải chủ sở hữu của lô hoặc vùng trồng không
        is_owner = False
        if owner and current_user.username.lower() == owner.lower():
            is_owner = True
        elif farm_owner and current_user.username.lower() in farm_owner.lower():
            is_owner = True

        if not is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Tài khoản '{current_user.username}' không có quyền xem lô nông sản #{batch.id} theo chính sách T-54 (403 Forbidden).",
            )

    # 3. Nếu người dùng đăng nhập nhưng có vai trò lạ (không phải admin/farmer)
    if current_user is not None and current_user.role not in (ROLE_ADMIN, ROLE_FARMER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vai trò của bạn không được phép xem lô nông sản này.",
        )


__all__ = [
    "authenticate_user",
    "basic_scheme",
    "check_batch_view_permission",
    "get_current_user",
    "get_current_user_optional",
    "hash_password",
    "require_admin",
    "require_farmer",
    "verify_password",
]


