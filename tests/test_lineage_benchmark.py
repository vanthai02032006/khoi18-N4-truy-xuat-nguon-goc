"""Kiểm thử thuật toán phả hệ BFS đối chiếu với tệp đáp án đếm tay (SCRUM-69 / T-53, T-51, SCRUM-67, SCRUM-66 / T-50, T-52).

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
1. Nạp bộ dữ liệu chuẩn ở T-52 vào môi trường SQLite độc lập (T-51).
2. Tệp JSON ghi tên hai người đếm độc lập và ngày đối chiếu ở đầu tệp.
3. Hai bản đếm tay độc lập khớp nhau hoàn toàn 100% cho cả truy ngược (tổ tiên) và truy xuôi (hậu duệ).
4. Chạy truy ngược và truy xuôi cho từng lô và so sánh khớp 100% với đáp án đếm tay.
5. Chèn một dòng quan hệ tạo chu trình bằng câu lệnh SQL trực tiếp, khẳng định hàm báo lỗi chính xác (LineageCycleError).
"""

import json
import os
import sqlite3
import pytest

from app.lineage import find_ancestors_bfs, find_descendants_bfs, LineageCycleError


@pytest.fixture
def benchmark_data():
    """Tải bộ dữ liệu chuẩn (T-52, T-51 & T-53) từ tệp JSON."""
    benchmark_path = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "lineage_benchmark.json")
    with open(benchmark_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sqlite_isolated_lineage_db(benchmark_data):
    """Khởi tạo môi trường cơ sở dữ liệu SQLite in-memory độc lập nạp bộ quan hệ ở T-52."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()

    # Tạo bảng quan hệ phả hệ lô hàng
    cur.execute(
        """
        CREATE TABLE batch_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_batch TEXT NOT NULL,
            child_batch TEXT NOT NULL,
            type TEXT NOT NULL,
            quantity REAL NOT NULL
        )
        """
    )

    # Nạp dữ liệu mẫu T-52 vào database bằng SQL INSERT
    relations = benchmark_data["graph_structure"]["relations"]
    for rel in relations:
        cur.execute(
            "INSERT INTO batch_relations (parent_batch, child_batch, type, quantity) VALUES (?, ?, ?, ?)",
            (rel["parent"], rel["child"], rel["type"], rel["quantity"]),
        )
    conn.commit()

    yield conn

    conn.close()


def test_benchmark_metadata_contains_two_counters_and_date(benchmark_data):
    """SCRUM-69 DoD: Tệp JSON nạp được trong test; ghi tên hai người đếm và ngày vào đầu tệp."""
    metadata = benchmark_data.get("metadata", {})
    assert "counters" in metadata, "Phải có danh sách người đếm trong metadata"
    counters = metadata["counters"]
    assert len(counters) >= 2, "Phải có ít nhất 2 người đếm độc lập"

    names = [c["name"] for c in counters]
    assert "Nguyễn Thanh Sơn" in names[0] or "Nguyễn Thanh Sơn" in names[1]
    assert "Lê Hoàng Quân" in names[0] or "Lê Hoàng Quân" in names[1]

    for c in counters:
        assert "date" in c and c["date"], f"Người đếm {c['name']} phải có ngày kiểm đếm"
    assert metadata.get("verified_date"), "Phải có ngày đối chiếu ở đầu tệp"


def test_two_independent_hand_counts_match_100_percent(benchmark_data):
    """SCRUM-69 DoD: Hai bản đếm tay độc lập khớp nhau hoàn toàn 100%."""
    c1 = benchmark_data.get("counter_1_manual_count", {})
    c2 = benchmark_data.get("counter_2_manual_count", {})

    assert c1 and c2, "Phải có đầy đủ 2 bản đếm tay độc lập"
    assert c1.get("counted_by") != c2.get("counted_by"), "Hai người đếm phải là 2 người độc lập khác nhau"

    # 1. So khớp tổ tiên (truy ngược)
    c1_anc = c1["ancestors"]
    c2_anc = c2["ancestors"]
    assert set(c1_anc.keys()) == set(c2_anc.keys())
    for batch_code in c1_anc:
        assert set(c1_anc[batch_code]) == set(c2_anc[batch_code]), (
            f"Lô {batch_code}: Tổ tiên giữa 2 người đếm không khớp ({c1_anc[batch_code]} vs {c2_anc[batch_code]})"
        )

    # 2. So khớp hậu duệ (truy xuôi)
    c1_desc = c1["descendants"]
    c2_desc = c2["descendants"]
    assert set(c1_desc.keys()) == set(c2_desc.keys())
    for batch_code in c1_desc:
        assert set(c1_desc[batch_code]) == set(c2_desc[batch_code]), (
            f"Lô {batch_code}: Hậu duệ giữa 2 người đếm không khớp ({c1_desc[batch_code]} vs {c2_desc[batch_code]})"
        )

    # 3. So khớp với ground truth chuẩn
    assert benchmark_data["metadata"].get("two_independent_counts_match") is True


def test_lineage_benchmark_against_ground_truth(benchmark_data):
    """AC 1 (Truy ngược): Mọi lô trong bộ mẫu đối chiếu trực tiếp khớp 100% với đáp án đếm tay."""
    relations = benchmark_data["graph_structure"]["relations"]
    ground_truth = benchmark_data["ground_truth_ancestors"]

    for batch_code, expected_ancestors in ground_truth.items():
        computed_ancestors = find_ancestors_bfs(batch_code, relations)
        assert set(computed_ancestors) == set(expected_ancestors), (
            f"Lô {batch_code}: Kết quả tính ({computed_ancestors}) "
            f"không khớp đáp án đếm tay ({expected_ancestors})"
        )


def test_lineage_descendants_against_ground_truth(benchmark_data):
    """AC 1 (Truy xuôi - SCRUM-69): Danh sách hậu duệ từng lô đối chiếu trực tiếp khớp 100% đáp án đếm tay."""
    relations = benchmark_data["graph_structure"]["relations"]
    ground_truth_descendants = benchmark_data["ground_truth_descendants"]

    assert len(ground_truth_descendants) == 11, "Phải có đủ 11 lô mẫu trong danh sách hậu duệ"

    for batch_code, expected_descendants in ground_truth_descendants.items():
        computed_descendants = find_descendants_bfs(batch_code, relations)
        assert set(computed_descendants) == set(expected_descendants), (
            f"Lô {batch_code}: Kết quả truy xuôi tính toán ({computed_descendants}) "
            f"không khớp đáp án đếm tay ({expected_descendants})"
        )


def test_lineage_from_database_matches_100_percent(sqlite_isolated_lineage_db, benchmark_data):
    """AC 1 (Môi trường độc lập - Truy ngược): Đọc quan hệ từ SQLite, truy ngược từng lô, khớp 100% với T-53."""
    cur = sqlite_isolated_lineage_db.cursor()
    rows = cur.execute("SELECT parent_batch, child_batch, type, quantity FROM batch_relations").fetchall()
    db_relations = [
        {"parent": r[0], "child": r[1], "type": r[2], "quantity": r[3]}
        for r in rows
    ]

    ground_truth = benchmark_data["ground_truth_ancestors"]
    assert len(ground_truth) == 11, "Bộ benchmark phải có đầy đủ 11 lô mẫu"

    for batch_code, expected_ancestors in ground_truth.items():
        computed_ancestors = find_ancestors_bfs(batch_code, db_relations)
        assert set(computed_ancestors) == set(expected_ancestors), (
            f"Lô {batch_code}: Kết quả truy vấn từ DB ({computed_ancestors}) "
            f"không khớp đáp án chuẩn T-53 ({expected_ancestors})"
        )


def test_lineage_descendants_from_database_matches_100_percent(sqlite_isolated_lineage_db, benchmark_data):
    """AC 1 (Môi trường độc lập - Truy xuôi - SCRUM-69): Đọc quan hệ từ SQLite, truy xuôi từng lô, khớp 100%."""
    cur = sqlite_isolated_lineage_db.cursor()
    rows = cur.execute("SELECT parent_batch, child_batch, type, quantity FROM batch_relations").fetchall()
    db_relations = [
        {"parent": r[0], "child": r[1], "type": r[2], "quantity": r[3]}
        for r in rows
    ]

    ground_truth_descendants = benchmark_data["ground_truth_descendants"]
    for batch_code, expected_descendants in ground_truth_descendants.items():
        computed_descendants = find_descendants_bfs(batch_code, db_relations)
        assert set(computed_descendants) == set(expected_descendants), (
            f"Lô {batch_code}: Kết quả truy xuôi từ DB ({computed_descendants}) "
            f"không khớp đáp án chuẩn T-53 ({expected_descendants})"
        )


def test_intentional_cycle_insertion_via_sql_raises_error(sqlite_isolated_lineage_db):
    """AC 2: Chèn một dòng quan hệ tạo chu trình bằng SQL rồi khẳng định hàm báo lỗi chính xác."""
    cur = sqlite_isolated_lineage_db.cursor()

    # Chuỗi quan hệ ban đầu trong DB:
    # B-ROOT-01 -> B-SPLIT-1A -> B-MERGE-G1
    # Bây giờ cố ý chèn một dòng SQL: B-MERGE-G1 là cha của B-ROOT-01
    # Tạo thành chu trình: B-ROOT-01 -> B-SPLIT-1A -> B-MERGE-G1 -> B-ROOT-01
    cur.execute(
        "INSERT INTO batch_relations (parent_batch, child_batch, type, quantity) VALUES (?, ?, ?, ?)",
        ("B-MERGE-G1", "B-ROOT-01", "SPLIT", 50.0),
    )
    sqlite_isolated_lineage_db.commit()

    # Đọc lại toàn bộ quan hệ từ DB sau khi chèn dòng chu trình
    rows = cur.execute("SELECT parent_batch, child_batch FROM batch_relations").fetchall()
    db_relations = [{"parent": r[0], "child": r[1]} for r in rows]

    # Khẳng định: khi truy ngược từ B-MERGE-G1 hoặc B-SPLIT-1A hoặc B-ROOT-01, hàm phải ném LineageCycleError
    with pytest.raises(LineageCycleError) as exc_info:
        find_ancestors_bfs("B-MERGE-G1", db_relations)

    assert "chu trình" in str(exc_info.value).lower(), f"Thông báo lỗi không đúng kỳ vọng: {exc_info.value}"

    with pytest.raises(LineageCycleError):
        find_ancestors_bfs("B-ROOT-01", db_relations)

    with pytest.raises(LineageCycleError):
        find_ancestors_bfs("B-SPLIT-1A", db_relations)


def test_lineage_simple_cycle_detection():
    """Kiểm thử phát hiện chu trình 2 nút đơn giản (A -> B -> A)."""
    cyclic_relations = [
        {"parent": "BATCH-A", "child": "BATCH-B"},
        {"parent": "BATCH-B", "child": "BATCH-A"},
    ]
    with pytest.raises(LineageCycleError):
        find_ancestors_bfs("BATCH-A", cyclic_relations)
