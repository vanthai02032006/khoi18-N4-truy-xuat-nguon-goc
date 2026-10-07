"""Bộ kiểm thử đơn vị & kiểm thử chấp nhận (DoD / Acceptance Criteria) cho Task T-48 (SCRUM-64).

Tiêu chí nghiệm thu (DoD / AC):
1. Khớp hoàn toàn với bộ dữ liệu mẫu benchmark (lineage_benchmark.json).
2. Ca chu trình ném lỗi có tên lô gây ra chu trình (LineageCycleError / ValueError).
3. Duyệt BFS theo tầng dùng Queue, không dùng đệ quy để tránh tràn ngăn xếp với đồ thị sâu.
4. Trả về đúng danh sách tổ tiên theo tầng và danh sách lô gốc (không có cha).
"""

from datetime import date
import json
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Đảm bảo import được package `app`
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.lineage import (
    LineageBFSResult,
    LineageCycleError,
    find_ancestors_bfs,
    trace_ancestors_bfs,
)
from app.models import Base, Batch, BatchLineage, Farm, RELATION_MERGE, RELATION_SPLIT


@pytest.fixture
def benchmark_data():
    """Tải dữ liệu chuẩn từ backend/data/lineage_benchmark.json."""
    data_path = BASE_DIR / "data" / "lineage_benchmark.json"
    assert data_path.exists(), f"Không tìm thấy file {data_path}"
    with open(data_path, "r", encoding="utf-8") as f:
        return json.load(f)


class TestLineageBFSAcceptanceDoD:
    """Các bài kiểm thử tiêu chí nghiệm thu DoD của T-48 (SCRUM-64)."""

    def test_matches_ground_truth_benchmark_perfectly(self, benchmark_data):
        """DoD 1: Khớp hoàn toàn với bộ dữ liệu mẫu benchmark."""
        relations = benchmark_data["graph_structure"]["relations"]
        ground_truth = benchmark_data["ground_truth_ancestors"]
        expected_roots = set(benchmark_data["graph_structure"]["root_batches"])

        for batch_code, expected_ancestors in ground_truth.items():
            result = trace_ancestors_bfs(batch_code=batch_code, relations=relations)

            # 1. Khớp danh sách phẳng toàn bộ tổ tiên theo thứ tự BFS
            assert result.all_ancestors == expected_ancestors, (
                f"Lô {batch_code}: all_ancestors {result.all_ancestors} != expected {expected_ancestors}"
            )

            # 2. Kiểm tra danh sách lô gốc (root_batches)
            if not expected_ancestors:
                # Nếu không có tổ tiên, bản thân nó là lô gốc
                assert result.root_batches == [batch_code]
                assert result.ancestors_by_level == []
            else:
                # Mọi lô gốc tìm được phải thuộc tập expected_roots
                assert set(result.root_batches).issubset(expected_roots)
                assert len(result.root_batches) > 0

        # Kiểm tra chi tiết 2 ca gộp quan trọng
        # B-MERGE-G1: gộp từ B-SPLIT-1A và B-SPLIT-2A
        res_g1 = trace_ancestors_bfs("B-MERGE-G1", relations=relations)
        assert res_g1.ancestors_by_level == [
            ["B-SPLIT-1A", "B-SPLIT-2A"],
            ["B-ROOT-01", "B-ROOT-02"],
        ]
        assert set(res_g1.root_batches) == {"B-ROOT-01", "B-ROOT-02"}

        # B-MERGE-G2: gộp từ B-SPLIT-2B và B-SPLIT-3A
        res_g2 = trace_ancestors_bfs("B-MERGE-G2", relations=relations)
        assert res_g2.ancestors_by_level == [
            ["B-SPLIT-2B", "B-SPLIT-3A"],
            ["B-ROOT-02", "B-ROOT-03"],
        ]
        assert set(res_g2.root_batches) == {"B-ROOT-02", "B-ROOT-03"}

    def test_cycle_detection_throws_error_with_batch_name(self):
        """DoD 2: Ca chu trình ném lỗi có tên lô gây ra chu trình."""
        # Ca 1: Chu trình đơn giản A -> A (tự trỏ chính nó)
        self_cycle = [{"parent": "LÔ-A", "child": "LÔ-A"}]
        with pytest.raises(LineageCycleError) as exc_info:
            trace_ancestors_bfs("LÔ-A", relations=self_cycle)
        assert "LÔ-A" in str(exc_info.value)
        assert exc_info.value.node == "LÔ-A"
        assert exc_info.value.batch_code == "LÔ-A"

        # Ca 2: Chu trình 2 mắt xích: LÔ-1 -> LÔ-2 -> LÔ-1
        cycle_2 = [
            {"parent": "LÔ-CHA", "child": "LÔ-CON"},
            {"parent": "LÔ-CON", "child": "LÔ-CHA"},
        ]
        with pytest.raises(LineageCycleError) as exc_info:
            trace_ancestors_bfs("LÔ-CON", relations=cycle_2)
        # Báo lỗi có tên lô gây ra chu trình
        err_msg = str(exc_info.value)
        assert "LÔ-CON" in err_msg or "LÔ-CHA" in err_msg
        assert exc_info.value.node in ("LÔ-CON", "LÔ-CHA")

        # Ca 3: Chu trình sâu: START -> P1 -> P2 -> P3 -> P1
        deep_cycle = [
            {"parent": "P1", "child": "START"},
            {"parent": "P2", "child": "P1"},
            {"parent": "P3", "child": "P2"},
            {"parent": "P1", "child": "P3"},  # Chu trình khép kín tại P1
        ]
        with pytest.raises(LineageCycleError) as exc_info:
            trace_ancestors_bfs("START", relations=deep_cycle)
        assert "P1" in str(exc_info.value)
        assert exc_info.value.node == "P1"

        # Khẳng định ném lỗi kế thừa ValueError
        with pytest.raises(ValueError):
            trace_ancestors_bfs("START", relations=deep_cycle)

    def test_non_recursive_queue_deep_graph_no_stack_overflow(self):
        """Lưu ý kỹ thuật: Duyệt BFS theo tầng dùng Queue, không dùng đệ quy để tránh tràn ngăn xếp."""
        # Tạo chuỗi phả hệ sâu 1.200 tầng (vượt qua giới hạn đệ quy 1.000 của Python)
        chain_depth = 1200
        deep_relations = []
        for i in range(chain_depth):
            deep_relations.append({
                "parent": f"BATCH-{i+1}",
                "child": f"BATCH-{i}",
            })

        # Duyệt từ BATCH-0 ngược lên BATCH-1200
        # Nếu dùng đệ quy sẽ bị RecursionError
        result = trace_ancestors_bfs("BATCH-0", relations=deep_relations)
        assert len(result.ancestors_by_level) == chain_depth
        assert result.root_batches == [f"BATCH-{chain_depth}"]
        assert result.all_ancestors[0] == "BATCH-1"
        assert result.all_ancestors[-1] == f"BATCH-{chain_depth}"

    def test_tuple_unpacking_and_dict_access(self):
        """Kiểm tra tính linh hoạt của kết quả trả về (tuple unpacking & dict access)."""
        rels = [
            {"parent": "R1", "child": "C1"},
            {"parent": "R2", "child": "C1"},
        ]
        res = trace_ancestors_bfs("C1", relations=rels)

        # 1. Unpacking tuple: ancestors_by_level, root_batches
        levels, roots = res
        assert levels == [["R1", "R2"]]
        assert set(roots) == {"R1", "R2"}

        # 2. Dict-style access
        assert res["ancestors_by_level"] == [["R1", "R2"]]
        assert set(res["root_batches"]) == {"R1", "R2"}
        assert set(res["all_ancestors"]) == {"R1", "R2"}

        # 3. Attributes access
        assert res.ancestors_by_level == [["R1", "R2"]]
        assert set(res.root_batches) == {"R1", "R2"}

    def test_read_directly_from_database_session(self):
        """Kiểm tra đọc quan hệ trực tiếp từ database (bảng batch_lineage)."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        SessionTest = sessionmaker(bind=engine)
        db = SessionTest()

        farm = Farm(name="Trang trại test", location="Hà Nội", area=2.0, owner="HTX")
        db.add(farm)
        db.commit()

        b_root = Batch(farm_id=farm.id, product_name="Gốc", quantity=1000.0, harvest_date=date(2026, 1, 1), code="ROOT0001")
        b_mid = Batch(farm_id=farm.id, product_name="Trung gian", quantity=500.0, harvest_date=date(2026, 1, 2), code="MID00002")
        b_leaf = Batch(farm_id=farm.id, product_name="Thành phẩm", quantity=250.0, harvest_date=date(2026, 1, 3), code="LEAF0003")
        db.add_all([b_root, b_mid, b_leaf])
        db.commit()

        # Thêm quan hệ: ROOT -> MID -> LEAF
        lin1 = BatchLineage(parent_batch_id=b_root.id, child_batch_id=b_mid.id, transferred_quantity=500.0, relation_type=RELATION_SPLIT, created_at="2026-10-07T00:00:00Z")
        lin2 = BatchLineage(parent_batch_id=b_mid.id, child_batch_id=b_leaf.id, transferred_quantity=250.0, relation_type=RELATION_SPLIT, created_at="2026-10-07T01:00:00Z")
        db.add_all([lin1, lin2])
        db.commit()

        # Đọc trực tiếp từ DB
        res = trace_ancestors_bfs("LEAF0003", db=db)
        assert res.ancestors_by_level == [["MID00002"], ["ROOT0001"]]
        assert res.root_batches == ["ROOT0001"]
        assert res.all_ancestors == ["MID00002", "ROOT0001"]
        db.close()
