"""Router đăng nhập (``POST /auth/login``) - Sprint 4.

Đặc điểm theo yêu cầu của sprint: **không JWT**, không token, không refresh
token. Endpoint chỉ kiểm tra ``username``/``password`` với bảng ``users`` rồi
trả về ``username`` + ``role`` để frontend hiển thị và ẩn/hiện chức năng.

Việc xác thực cho **các request sau** dùng HTTP Basic
(``Authorization: Basic base64(username:password)``) - xem dependency
``get_current_user`` / ``require_admin`` / ``require_farmer``
trong ``app/security.py``. Swagger UI tự hiện nút **Authorize** nhờ đó.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import LoginRequest, LoginResponse
from app.security import authenticate_user

router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
)


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Đăng nhập",
    description=(
        "Kiểm tra `username`/`password` với bảng `users`.\n\n"
        "**Thành công:** trả về `username` và `role` "
        "(`admin` hoặc `farmer`). **Không sinh token** vì dự án không dùng JWT - "
        "client gửi lại thông tin đăng nhập qua header `Authorization` (HTTP Basic) "
        "ở những request cần quyền."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Sai tên đăng nhập hoặc mật khẩu.",
        },
    },
)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    """Đăng nhập bằng tài khoản trong bảng ``users``.

    Args:
        payload: ``username`` + ``password`` đã được Pydantic validate.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        LoginResponse: ``{"username": ..., "role": ...}`` (HTTP 200).

    Raises:
        HTTPException: **401** nếu sai tên đăng nhập hoặc mật khẩu.
    """
    user = authenticate_user(db, payload.username, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai tên đăng nhập hoặc mật khẩu.",
        )

    return LoginResponse(
        username=user.username,
        role=user.role,
        organization_id=getattr(user, "organization_id", None),
    )
