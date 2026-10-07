"""Kiểm thử thuật toán phả hệ BFS đối chiếu với tệp đáp án đếm tay (SCRUM-69 / T-53 & SCRUM-67).

Tệp đáp án đếm tay được lưu tại `backend/data/lineage_benchmark.json`.
"""

import json
import os
import pytest

from app.lineage import find_ancestors_bfs, LineageCycleError


def test_lineage_benchmark_against_ground_truth():
    """Kiểm tra toàn bộ các lô mẫu trong tệp JSON phải khớp 100% với đáp án đếm tay."""
    benchmark_path = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "lineage_benchmark.json")
    with open(benchmark_path, "r", encoding="utf-8") as f:
        benchmark_data = json.load(f)

    relations = benchmark_data["graph_structure"]["relations"]
    ground_truth = benchmark_data["ground_truth_ancestors"]

    for batch_code, expected_ancestors in ground_truth.items():
        computed_ancestors = find_ancestors_bfs(batch_code, relations)
        # So sánh tập hợp tổ tiên
        assert set(computed_ancestors) == set(expected_ancestors), (
            f"Lô {batch_code}: Kết quả tính ({computed_ancestors}) "
            f"không khớp đáp án đếm tay ({expected_ancestors})"
        )

    print("Toàn bộ 11 lô trong bộ benchmark khớp 100% với đáp án đếm tay!")


def test_lineage_cycle_detection():
    """Kiểm thử phát hiện chu trình cố ý (A -> B -> A)."""
    cyclic_relations = [
        {"parent": "BATCH-A", "child": "BATCH-B"},
        {"parent": "BATCH-B", "child": "BATCH-A"},
    ]
    with pytest.raises(LineageCycleError):
        find_ancestors_bfs("BATCH-A", cyclic_relations)
