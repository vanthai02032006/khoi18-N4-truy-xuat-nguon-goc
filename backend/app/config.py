"""Cấu hình hệ thống và hằng số sinh mã định danh (T-18 / SCRUM-34).

Chứa các hằng số cấu hình tập trung cho việc sinh mã định danh lô hàng / mã truy xuất nguồn gốc.
"""

from typing import Final

# -----------------------------------------------------------------------------
# Cấu hình sinh mã định danh (T-18 / SCRUM-34)
# -----------------------------------------------------------------------------

# Danh sách các ký tự dễ nhầm lẫn cần loại bỏ: '0', 'O', '1', 'I', 'l'
FORBIDDEN_CHARACTERS: Final[frozenset[str]] = frozenset({"0", "O", "1", "I", "l"})

# Độ dài mặc định của mã
CODE_LENGTH: Final[int] = 8

# Bảng ký tự chuẩn (Alphabet) loại bỏ toàn bộ ký tự dễ nhầm lẫn ('0', 'O', '1', 'I', 'l').
# Không gian gồm 57 ký tự (8 chữ số + 24 chữ hoa + 25 chữ thường):
# 23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz
CODE_ALPHABET: Final[str] = (
    "23456789"
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
    "abcdefghijkmnopqrstuvwxyz"
)

# Số lần thử tối đa khi sinh lại mã nếu gặp trùng lặp
MAX_CODE_RETRIES: Final[int] = 10

# Khai báo gói cấu hình tập trung duy nhất (Single Source of Truth)
CODE_CONFIG: Final[dict[str, object]] = {
    "length": CODE_LENGTH,
    "alphabet": CODE_ALPHABET,
    "forbidden_chars": FORBIDDEN_CHARACTERS,
    "max_retries": MAX_CODE_RETRIES,
}

__all__ = [
    "CODE_ALPHABET",
    "CODE_CONFIG",
    "CODE_LENGTH",
    "FORBIDDEN_CHARACTERS",
    "MAX_CODE_RETRIES",
]
