"""Module sinh mã định danh / mã truy xuất nguồn gốc nông sản (T-18 / SCRUM-34).

Chức năng:
1. Sinh chuỗi ngẫu nhiên bảo mật (CSPRNG) sử dụng module `secrets`.
2. Sử dụng bảng ký tự an toàn từ cấu hình (loại bỏ '0', 'O', '1', 'I', 'l').
3. Cơ chế tự động sinh lại (retry) khi phát hiện trùng mã (vi phạm ràng buộc UNIQUE).
"""

from collections.abc import Callable
import logging
import secrets
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import (
    CODE_ALPHABET,
    CODE_LENGTH,
    FORBIDDEN_CHARACTERS,
    MAX_CODE_RETRIES,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CodeCollisionError(RuntimeError):
    """Ngoại lệ ném ra khi không thể sinh được mã duy nhất sau số lần thử tối đa."""


def generate_code(length: int = CODE_LENGTH, alphabet: str = CODE_ALPHABET) -> str:
    """Sinh chuỗi mã ngẫu nhiên an toàn có độ dài xác định.

    Sử dụng nguồn ngẫu nhiên bảo mật (CSPRNG) từ module `secrets` của Python,
    đáp ứng tiêu chuẩn an toàn thông tin chống đoán mò.

    Args:
        length: Độ dài của mã cần sinh (mặc định từ cấu hình: 8).
        alphabet: Bảng ký tự cho phép (mặc định đã loại bỏ '0', 'O', '1', 'I', 'l').

    Returns:
        str: Chuỗi mã gồm các ký tự ngẫu nhiên.

    Raises:
        ValueError: Nếu độ dài <= 0 hoặc bảng ký tự rỗng.
    """
    if length <= 0:
        raise ValueError(f"Độ dài mã phải lớn hơn 0 (nhận được: {length}).")
    if not alphabet:
        raise ValueError("Bảng ký tự cho phép không được rỗng.")

    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_unique_code(
    is_exists_fn: Callable[[str], bool],
    max_retries: int = MAX_CODE_RETRIES,
    length: int = CODE_LENGTH,
    alphabet: str = CODE_ALPHABET,
) -> str:
    """Sinh mã duy nhất với cơ chế tự động thử lại nếu phát hiện trùng mã.

    Args:
        is_exists_fn: Hàm callback nhận mã vừa sinh và trả về True nếu mã đã tồn tại.
        max_retries: Số lần thử tối đa trước khi báo lỗi (mặc định 10).
        length: Độ dài mã (mặc định 8).
        alphabet: Bảng ký tự cho phép.

    Returns:
        str: Mã duy nhất chưa từng xuất hiện.

    Raises:
        CodeCollisionError: Khi thử lại quá `max_retries` lần mà vẫn bị trùng.
    """
    for attempt in range(1, max_retries + 1):
        code = generate_code(length=length, alphabet=alphabet)
        if not is_exists_fn(code):
            return code
        logger.warning(
            "Phát hiện trùng mã '%s' ở lần thử thứ %d/%d. Đang tiến hành sinh lại...",
            code,
            attempt,
            max_retries,
        )

    raise CodeCollisionError(
        f"Không thể sinh mã duy nhất sau {max_retries} lần thử do trùng lặp liên tiếp."
    )


def execute_with_unique_retry(
    save_fn: Callable[[str], T],
    db: Session | None = None,
    max_retries: int = MAX_CODE_RETRIES,
    length: int = CODE_LENGTH,
    alphabet: str = CODE_ALPHABET,
) -> tuple[str, T]:
    """Thực thi thao tác lưu bản ghi với cơ chế bắt lỗi Unique Constraint của DB để sinh lại mã.

    Khi lưu vào cơ sở dữ liệu có ràng buộc UNIQUE (IntegrityError), hàm sẽ tự động:
    1. Rollback transaction (nếu có đối tượng `db`).
    2. Sinh lại mã mới.
    3. Thử lưu lại cho tới khi thành công hoặc chạm ngưỡng `max_retries`.

    Args:
        save_fn: Hàm nhận mã sinh ra và thực hiện lưu vào database, trả về bản ghi kết quả.
        db: SQLAlchemy Session (tùy chọn) để thực hiện rollback khi bắt được IntegrityError.
        max_retries: Số lần thử tối đa khi xảy ra vi phạm ràng buộc unique.
        length: Độ dài mã.
        alphabet: Bảng ký tự.

    Returns:
        tuple[str, T]: Tuple chứa (mã thành công, kết quả trả về từ save_fn).

    Raises:
        CodeCollisionError: Nếu vi phạm trùng mã liên tiếp vượt quá `max_retries`.
        IntegrityError: Nếu lỗi toàn vẹn không liên quan đến trùng mã (hoặc khi hết lượt thử).
    """
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        code = generate_code(length=length, alphabet=alphabet)
        try:
            result = save_fn(code)
            return code, result
        except IntegrityError as exc:
            last_error = exc
            logger.warning(
                "Ràng buộc UNIQUE bắt được trùng mã '%s' tại lần thử %d/%d. Đang rollback và sinh lại...",
                code,
                attempt,
                max_retries,
            )
            if db is not None:
                db.rollback()

    raise CodeCollisionError(
        f"Ràng buộc unique liên tục bị vi phạm sau {max_retries} lần thử sinh mã."
    ) from last_error


__all__ = [
    "CodeCollisionError",
    "FORBIDDEN_CHARACTERS",
    "execute_with_unique_retry",
    "generate_code",
    "generate_unique_code",
]
