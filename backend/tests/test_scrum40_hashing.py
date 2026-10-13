"""Unit test cho SCRUM-40: Chuẩn hoá dữ liệu (canonicalization) và băm (hashing).

Kiểm tra:
1. Cùng dữ liệu, khác thứ tự key => cùng hash.
2. Hash cùng dữ liệu 2 lần => cùng kết quả (deterministic).
3. Thay đổi dù chỉ 1 field / ký tự => hash thay đổi hoàn toàn (avalanche effect).
4. Thay đổi previousHash => hash thay đổi.
5. Dữ liệu ngày/số được canonicalize ổn định (date, datetime, int, float nguyên/thập phân).
6. Khoảng trắng thừa không làm sai lệch canonicalization.
7. Xử lý null/None và nested objects nhất quán.
"""

from datetime import date, datetime, timezone
import pytest

from app.hashing import canonicalize, compute_event_hash, verify_event_hash


def test_same_data_different_key_order_yields_same_hash():
    """Yêu cầu 1: Cùng dữ liệu nhưng khác thứ tự key phải tạo ra cùng canonical string và cùng hash."""
    input_a = {
        "batchId": "B001",
        "temperature": 5,
        "location": "Kho A",
    }
    input_b = {
        "location": "Kho A",
        "temperature": 5,
        "batchId": "B001",
    }
    input_c = {
        "temperature": 5,
        "batchId": "B001",
        "location": "Kho A",
    }

    canonical_a = canonicalize(input_a)
    canonical_b = canonicalize(input_b)
    canonical_c = canonicalize(input_c)

    assert canonical_a == canonical_b == canonical_c
    assert canonical_a == '{"batchId":"B001","location":"Kho A","temperature":5}'

    hash_a = compute_event_hash(input_a)
    hash_b = compute_event_hash(input_b)
    hash_c = compute_event_hash(input_c)

    assert hash_a == hash_b == hash_c
    assert len(hash_a) == 64


def test_hash_twice_deterministic():
    """Yêu cầu 2: Băm cùng dữ liệu hai lần phải cho kết quả giống hệt nhau (hash(sameData) === hash(sameData))."""
    data = {
        "event": "HARVEST",
        "farm_id": 10,
        "quantity": 1250.5,
        "notes": "Thu hoạch xoài Cát Chu",
    }

    hash_1 = compute_event_hash(data)
    hash_2 = compute_event_hash(data)

    assert hash_1 == hash_2
    assert verify_event_hash(data, hash_1) is True


def test_single_character_change_changes_hash():
    """Yêu cầu 3: Thay đổi dù chỉ 1 ký tự trong nội dung thì hash phải thay đổi hoàn toàn."""
    data_1 = {
        "batchId": "BATCH-001",
        "temperature": 4.5,
        "status": "VALID",
    }
    data_2 = {
        "batchId": "BATCH-002",  # khác 1 ký tự
        "temperature": 4.5,
        "status": "VALID",
    }

    hash_1 = compute_event_hash(data_1)
    hash_2 = compute_event_hash(data_2)

    assert hash_1 != hash_2
    assert verify_event_hash(data_1, hash_2) is False


def test_previous_hash_chaining():
    """Yêu cầu 4 & 6: Hỗ trợ hash của sự kiện trước (hash chain) và đổi previousHash thì hash thay đổi."""
    event_data = {
        "batch_id": 1,
        "step": "COOL_STORAGE",
        "temperature": 3.8,
    }
    prev_hash_1 = "0000000000000000000000000000000000000000000000000000000000000000"
    prev_hash_2 = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"

    hash_without_prev = compute_event_hash(event_data)
    hash_with_prev_1 = compute_event_hash(event_data, previous_hash=prev_hash_1)
    hash_with_prev_2 = compute_event_hash(event_data, previous_hash=prev_hash_2)

    assert hash_with_prev_1 != hash_without_prev
    assert hash_with_prev_2 != hash_without_prev
    assert hash_with_prev_1 != hash_with_prev_2

    # Deterministic với cùng previous_hash
    assert compute_event_hash(event_data, previous_hash=prev_hash_1) == hash_with_prev_1


def test_date_and_number_canonicalization():
    """Yêu cầu 5: Dữ liệu ngày tháng và số được canonicalize ổn định."""
    d = date(2026, 9, 25)
    dt = datetime(2026, 9, 25, 14, 30, 0, tzinfo=timezone.utc)

    payload_1 = {
        "date": d,
        "datetime": dt,
        "integer_val": 50,
        "float_whole": 50.0,  # 50.0 nên được chuẩn hoá thành 50
        "float_decimal": 12.5,
    }

    canonical_str = canonicalize(payload_1)
    assert '"date":"2026-09-25"' in canonical_str
    assert '"datetime":"2026-09-25T14:30:00Z"' in canonical_str
    assert '"float_whole":50' in canonical_str
    assert '"integer_val":50' in canonical_str
    assert '"float_decimal":12.5' in canonical_str


def test_whitespace_trimming_does_not_break_canonicalization():
    """Yêu cầu 6: Khoảng trắng thừa ở đầu/cuối chuỗi không làm sai lệch canonicalization."""
    raw_with_spaces = {
        "batchId": "  B001  ",
        "location": " Kho A\t",
        "owner": "HTX Mỹ Xương ",
    }
    clean_data = {
        "batchId": "B001",
        "location": "Kho A",
        "owner": "HTX Mỹ Xương",
    }

    assert canonicalize(raw_with_spaces) == canonicalize(clean_data)
    assert compute_event_hash(raw_with_spaces) == compute_event_hash(clean_data)


def test_nested_objects_and_null_handling():
    """Kiểm tra nested dictionary, list, và giá trị None."""
    nested_data_1 = {
        "batch": {"id": 1, "code": "B1"},
        "tags": ["VietGAP", "Organic"],
        "sensor": None,
    }
    nested_data_2 = {
        "sensor": None,
        "tags": ["VietGAP", "Organic"],
        "batch": {"code": "B1", "id": 1},
    }

    assert canonicalize(nested_data_1) == canonicalize(nested_data_2)
    assert '"sensor":null' in canonicalize(nested_data_1)
    assert compute_event_hash(nested_data_1) == compute_event_hash(nested_data_2)
