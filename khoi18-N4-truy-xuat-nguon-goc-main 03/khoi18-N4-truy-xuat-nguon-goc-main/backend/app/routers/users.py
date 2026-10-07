"""Router quản trị tài khoản (``GET /users``) - Sprint 4, **chỉ admin**.

Router nhỏ này có một mục đích rõ ràng: chứng minh dependency
``require_admin()`` trong ``app/security.py`` hoạt động đúng.

- Gọi bằng tài khoản ``admin``  → ``200 OK`` + danh sách tài khoản.
- Gọi bằng tài khoản ``farmer`` → ``403 Forbidden``.
- Không gửi header ``Authorization`` → ``401 Unauthorized``.

Response **không chứa mật khẩu** (kể cả mật khẩu đã băm) - xem ``UserResponse``.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import UserResponse
from app.security import require_admin

router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


@router.get(
    "",
    response_model=list[UserResponse],
    status_code=status.HTTP_200_OK,
    summary="Lấy danh sách tài khoản (chỉ admin)",
    description=(
        "Trả về toàn bộ tài khoản trong bảng `users` (không kèm mật khẩu), "
        "sắp xếp theo `id` tăng dần.\n\n"
        "**Phân quyền:** chỉ `role = admin` được gọi (dùng `require_admin`). "
        "Farmer gọi sẽ nhận `403 Forbidden`."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Chưa đăng nhập (thiếu header `Authorization`).",
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "Đã đăng nhập nhưng không phải admin.",
        },
    },
)
def list_users(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[User]:
    """Lấy danh sách tài khoản (chỉ admin).

    Args:
        current_user: Tài khoản admin đã được ``require_admin`` kiểm tra quyền.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        list[User]: Danh sách tài khoản, sắp xếp theo ``id`` (HTTP 200).

    Raises:
        HTTPException: 401 nếu chưa đăng nhập; 403 nếu không phải admin
            (được raise tự động bên trong dependency ``require_admin``).
    """
    # `current_user` không dùng trong thân hàm nhưng bắt buộc phải khai báo để
    # FastAPI chạy dependency kiểm tra quyền trước khi vào endpoint.
    _ = current_user
    return list(db.scalars(select(User).order_by(User.id)).all())
