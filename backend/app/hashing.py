"""Tiện ích chuẩn hoá (canonicalization) và băm (hashing) dữ liệu sự kiện / lô hàng - SCRUM-40.

Quy tắc chuẩn hoá (Canonicalization):
1. Key sắp xếp theo thứ tự alphabet (áp dụng đệ quy cho mọi nested dictionary).
2. Format số thống nhất: số thực nguyên (như 5.0) chuyển về số nguyên (5),
   số thực có phần thập phân được làm tròn và loại bỏ các số 0 thừa ở đuôi.
3. Format ngày/giờ cố định theo chuẩn ISO 8601 (YYYY-MM-DD hoặc ISO datetime UTC).
4. Chuỗi văn bản loại bỏ khoảng trắng thừa ở đầu và cuối (strip).
5. Xử lý null/None nhất quán (serialize thành null trong JSON).
6. Chuỗi kết quả JSON không chứa khoảng trắng thừa giữa các token (separators=(',', ':')).
7. Sử dụng thuật toán SHA-256 từ thư viện chuẩn `hashlib` của Python.
8. Hỗ trợ liên kết chuỗi băm (hash chain):
   current_hash = SHA256(canonicalCurrentEvent + previousHash).
"""

from __future__ import annotations

import datetime
import hashlib
import json
from decimal import Decimal
from typing import Any


def canonicalize_value(val: Any) -> Any:
    """Chuẩn hoá đệ quy từng giá trị theo quy tắc deterministic."""
    if val is None:
        return None

    # Date / Datetime / Time -> ISO 8601
    if isinstance(val, (datetime.datetime, datetime.date, datetime.time)):
        if isinstance(val, datetime.datetime) and val.tzinfo is not None:
            # Normalize datetime có múi giờ về UTC
            utc_dt = val.astimezone(datetime.timezone.utc)
            return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        return val.isoformat()

    # Chuỗi: cắt bỏ khoảng trắng thừa ở 2 đầu
    if isinstance(val, str):
        return val.strip()

    # Boolean phải kiểm tra trước int vì bool là subclass của int trong Python
    if isinstance(val, bool):
        return val

    # Số thập phân / số thực
    if isinstance(val, (float, Decimal)):
        f_val = float(val)
        if f_val.is_integer():
            return int(f_val)
        # Làm tròn 8 chữ số thập phân để tránh sai số dấu phẩy động
        rounded = round(f_val, 8)
        return int(rounded) if rounded.is_integer() else rounded

    if isinstance(val, int):
        return val

    # Dictionary / Object: sort key theo alphabet và đệ quy giá trị
    if isinstance(val, dict):
        return {
            str(k).strip(): canonicalize_value(v)
            for k, v in sorted(val.items(), key=lambda item: str(item[0]))
        }

    # List / Tuple / Set: giữ thứ tự list/tuple, sort set
    if isinstance(val, (set, frozenset)):
        canonical_items = [canonicalize_value(item) for item in val]
        return sorted(canonical_items, key=lambda x: json.dumps(x, sort_keys=True, default=str))

    if isinstance(val, (list, tuple)):
        return [canonicalize_value(item) for item in val]

    # Pydantic models (v1 và v2)
    if hasattr(val, "model_dump") and callable(val.model_dump):
        return canonicalize_value(val.model_dump())
    if hasattr(val, "dict") and callable(val.dict):
        return canonicalize_value(val.dict())

    # Đối tượng thông thường có __dict__
    if hasattr(val, "__dict__"):
        return canonicalize_value(val.__dict__)

    return str(val).strip()


def canonicalize(data: Any) -> str:
    """Chuyển đổi dữ liệu bất kỳ thành chuỗi JSON chuẩn tắc (canonical string).

    - Các key được sắp xếp alphabet.
    - Không có khoảng trắng thừa sau dấu hai chấm `:` hoặc dấu phẩy `,`.
    - Hỗ trợ ký tự tiếng Việt UTF-8 (ensure_ascii=False).
    """
    normalized = canonicalize_value(data)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_event_hash(data: Any, previous_hash: str | None = None) -> str:
    """Tính mã băm SHA-256 từ dữ liệu đã được chuẩn hoá.

    Hỗ trợ hash chain của sự kiện trước:
    currentHash = SHA256(canonicalCurrentEvent + previousHash)

    Args:
        data: Dữ liệu sự kiện / lô hàng cần băm.
        previous_hash: Mã băm của sự kiện liền trước (tuỳ chọn).

    Returns:
        str: Chuỗi hex 64 ký tự của SHA-256.
    """
    canonical_str = canonicalize(data)
    if previous_hash is not None and str(previous_hash).strip():
        payload_to_hash = f"{canonical_str}{str(previous_hash).strip()}"
    else:
        payload_to_hash = canonical_str

    return hashlib.sha256(payload_to_hash.encode("utf-8")).hexdigest()


def verify_event_hash(data: Any, expected_hash: str, previous_hash: str | None = None) -> bool:
    """Kiểm tra mã băm có khớp với dữ liệu hay không."""
    from hmac import compare_digest

    computed = compute_event_hash(data, previous_hash=previous_hash)
    return compare_digest(computed.lower(), expected_hash.strip().lower())


__all__ = [
    "canonicalize",
    "canonicalize_value",
    "compute_event_hash",
    "verify_event_hash",
]
