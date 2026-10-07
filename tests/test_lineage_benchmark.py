"""Kiểm thử thuật toán phả hệ BFS đối chiếu với tệp đáp án đếm tay (SCRUM-69 / T-53, SCRUM-67, SCRUM-66 / T-50, T-52).

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
1. Nạp bộ dữ liệu chuẩn ở T-52 vào môi trường SQLite độc lập.
2. Chạy truy ngược cho từng lô và so sánh khớp 100% với đáp án đếm tay ở T-53.
3. Chèn một dòng quan hệ tạo chu trình bằng câu lệnh SQL trực tiếp, khẳng định hàm báo lỗi chính xác (LineageCycleError).
"""

import json
import os
import sqlite3
import pytest

from app.lineage import find_ancestors_bfs, LineageCycleError


@pytest.fixture
def benchmark_data():
    """Tải bộ dữ liệu chuẩn (T-52 & T-53) từ tệp JSON."""
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


def test_lineage_benchmark_against_ground_truth(benchmark_data):
    """AC 1: Mọi lô trong bộ mẫu đối chiếu trực tiếp khớp 100% với đáp án đếm tay."""
    relations = benchmark_data["graph_structure"]["relations"]
    ground_truth = benchmark_data["ground_truth_ancestors"]

    for batch_code, expected_ancestors in ground_truth.items():
        computed_ancestors = find_ancestors_bfs(batch_code, relations)
        assert set(computed_ancestors) == set(expected_ancestors), (
            f"Lô {batch_code}: Kết quả tính ({computed_ancestors}) "
            f"không khớp đáp án đếm tay ({expected_ancestors})"
        )


def test_lineage_from_database_matches_100_percent(sqlite_isolated_lineage_db, benchmark_data):
    """AC 1 (Môi trường độc lập): Đọc quan hệ từ SQLite, truy ngược từng lô, khớp 100% với T-53."""
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
