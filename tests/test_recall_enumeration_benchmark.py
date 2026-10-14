"""Kiểm thử **kịch bản thu hồi mẫu & đáp án đếm tay** (S-22).

User Story: *"Là thành viên phát triển tôi muốn một kịch bản thu hồi mẫu có đáp án
đếm tay để thuật toán liệt kê thu hồi được kiểm bằng đáp án độc lập."*

Tiêu chí nghiệm thu (DoD / AC):
1. Giả sử chạy script, khi xong, thì có đồ thị **bốn tầng qua ba tổ chức** với ít
   nhất một **lô gộp từ nhiều nguồn** và một **lô đã tiêu thụ**.
2. Giả sử mở tệp đáp án, khi tra lô gốc, thì thấy **danh sách hậu duệ và khối lượng
   còn lại từng lô do hai người đếm độc lập**, và thuật toán liệt kê thu hồi phải
   khớp 100% với đáp án đó.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import BATCH_STATUS_CONSUMED, Batch, BatchRelation
from app.recall_enumeration import (
    RecallBatchFact,
    RecallScope,
    build_recall_scope,
    enumerate_recall_scope,
    load_lineage_relations,
)
from scripts.seed_recall_sample import (
    ANSWER_KEY_PATH,
    ORG_GROWER,
    ORG_PROCESSOR,
    ORG_RETAILER,
    SAMPLE_BATCH_PREFIX,
    seed_recall_sample,
)


@pytest.fixture()
def answer_key() -> dict:
    """Tệp đáp án đếm tay đi kèm kịch bản (S-22)."""
    with open(ANSWER_KEY_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture()
def recall_sample_db() -> Session:
    """CSDL SQLite in-memory đã dựng sẵn kịch bản thu hồi mẫu 4 tầng."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as session:
        seed_recall_sample(session)
        yield session
    engine.dispose()


def _batches_by_code(db: Session) -> dict[str, Batch]:
    """Toàn bộ lô của kịch bản mẫu, khoá theo mã lô."""
    batches = db.scalars(
        select(Batch)
        .where(Batch.batch_code.like(f"{SAMPLE_BATCH_PREFIX}%"))
        .order_by(Batch.batch_code.asc())
    ).all()
    return {batch.batch_code: batch for batch in batches}


def _assert_scope_matches_answer_key(scope: RecallScope, expected: dict) -> None:
    """Khẳng định phạm vi thu hồi khớp 100% đáp án (mã lô, thứ tự tầng, khối lượng)."""
    root = expected["root"]
    assert scope.root.batch_code == root["batch_code"]
    assert scope.root.remaining_quantity == pytest.approx(root["remaining_quantity"])
    assert scope.root.consumed_quantity == pytest.approx(root["consumed_quantity"])
    assert scope.root.organization == root["organization"]

    expected_codes = [entry["batch_code"] for entry in expected["descendants"]]
    assert [entry.batch_code for entry in scope.descendants] == expected_codes, (
        "Danh sách hậu duệ (và thứ tự duyệt theo tầng) phải khớp đáp án đếm tay"
    )

    for entry, expected_entry in zip(scope.descendants, expected["descendants"]):
        assert entry.depth == expected_entry["depth"]
        assert entry.organization == expected_entry["organization"]
        assert entry.remaining_quantity == pytest.approx(expected_entry["remaining_quantity"])
        assert entry.consumed_quantity == pytest.approx(expected_entry["consumed_quantity"])
        assert entry.total_quantity == pytest.approx(expected_entry["total_quantity"])
        assert entry.is_recallable is expected_entry["is_recallable"]

    assert scope.descendant_count == expected["descendant_count"]
    assert scope.descendant_remaining_quantity == pytest.approx(
        expected["descendant_remaining_quantity"]
    )
    assert scope.total_remaining_quantity == pytest.approx(expected["total_remaining_quantity"])
    assert scope.consumed_quantity == pytest.approx(expected["consumed_quantity"])


# --------------------------------------------------------------------------- AC 1 ---
def test_seed_script_builds_four_tier_graph_across_three_organizations(
    recall_sample_db: Session, answer_key: dict
) -> None:
    """AC 1: Script dựng đồ thị 4 tầng qua 3 tổ chức khớp đáp án về từng lô."""
    graph = answer_key["graph_structure"]
    batches = _batches_by_code(recall_sample_db)

    assert len(graph["tiers"]) == 4, "Kịch bản mẫu phải có đúng bốn tầng phả hệ"
    assert len(batches) == graph["totals"]["batch_count"] == 18

    for tier in graph["tiers"]:
        for expected in tier["batches"]:
            batch = batches[expected["batch_code"]]
            assert batch.quantity == pytest.approx(expected["quantity"])
            assert (batch.consumed_quantity or 0.0) == pytest.approx(expected["consumed_quantity"])
            assert batch.status == expected["status"]
            assert batch.current_holder_org == tier["organization"]
            assert batch.current_location.organization == tier["organization"]

    organizations = {batch.current_holder_org for batch in batches.values()}
    assert organizations == {ORG_GROWER, ORG_PROCESSOR, ORG_RETAILER}

    # Đúng 4 tầng: lô gốc ở tầng 1, lô sơ chế/gộp ở tầng 2-3, thành phẩm ở tầng 4.
    root = batches["LOT-S22-A1"]
    root_scope = build_recall_scope(recall_sample_db, root)
    assert root_scope.max_depth == 3, "Đồ thị phải sâu bốn tầng tính từ lô gốc"


def test_seed_script_creates_multi_source_merge_batches(
    recall_sample_db: Session, answer_key: dict
) -> None:
    """AC 1: Có ít nhất một lô gộp từ nhiều nguồn (nhận từ hai nhánh khác nhau)."""
    batches = _batches_by_code(recall_sample_db)
    code_by_id = {batch.id: code for code, batch in batches.items()}
    merge_sources: dict[str, list[str]] = answer_key["graph_structure"]["merge_sources"]
    assert merge_sources, "Đáp án phải ghi rõ các lô gộp và nguồn của chúng"

    for merged_code, sources in merge_sources.items():
        assert len(sources) >= 2, f"Lô gộp {merged_code} phải nhận từ ít nhất hai nguồn"
        parent_rows = recall_sample_db.scalars(
            select(BatchRelation).where(BatchRelation.child_batch_id == batches[merged_code].id)
        ).all()
        parent_codes = sorted(code_by_id[relation.parent_batch_id] for relation in parent_rows)
        assert parent_codes == sorted(sources)


def test_seed_script_creates_consumed_batch(
    recall_sample_db: Session, answer_key: dict
) -> None:
    """AC 1: Có ít nhất một lô đã tiêu thụ (bán hết cho người tiêu dùng)."""
    batches = _batches_by_code(recall_sample_db)
    consumed = [
        batch
        for batch in batches.values()
        if batch.status == BATCH_STATUS_CONSUMED or (batch.consumed_quantity or 0.0) > 0
    ]
    assert consumed, "Kịch bản mẫu phải có lô đã bán cho người tiêu dùng"

    sold_out = batches["LOT-S22-F12"]
    assert sold_out.status == BATCH_STATUS_CONSUMED
    assert sold_out.quantity == 0
    assert sold_out.consumed_quantity == pytest.approx(150.0)

    expected_sold_out = answer_key["graph_structure"]["consumed_batches"][0]
    assert expected_sold_out["batch_code"] == "LOT-S22-F12"
    assert expected_sold_out["remaining_quantity"] == 0.0


# --------------------------------------------------------------------------- AC 2 ---
def test_recall_enumeration_from_database_matches_hand_count(
    recall_sample_db: Session, answer_key: dict
) -> None:
    """AC 2: Tra từng lô gốc trong CSDL, thuật toán khớp 100% đáp án đếm tay."""
    batches = _batches_by_code(recall_sample_db)
    ground_truth: dict[str, dict] = answer_key["ground_truth_recall_scope"]

    for root_code, expected in ground_truth.items():
        scope = build_recall_scope(recall_sample_db, batches[root_code])
        _assert_scope_matches_answer_key(scope, expected)


def test_recall_enumeration_pure_function_matches_hand_count(answer_key: dict) -> None:
    """AC 2: Hàm thuần (chỉ cần quan hệ + số liệu của đáp án) cũng khớp 100%."""
    graph = answer_key["graph_structure"]
    facts = {
        batch["batch_code"]: RecallBatchFact(
            batch_code=batch["batch_code"],
            remaining_quantity=batch["quantity"],
            consumed_quantity=batch["consumed_quantity"],
            organization=tier["organization"],
            status=batch["status"],
        )
        for tier in graph["tiers"]
        for batch in tier["batches"]
    }

    for root_code, expected in answer_key["ground_truth_recall_scope"].items():
        entries = enumerate_recall_scope(root_code, graph["relations"], facts)
        assert [entry.batch_code for entry in entries] == [
            item["batch_code"] for item in expected["descendants"]
        ]
        assert sum(entry.remaining_quantity for entry in entries) == pytest.approx(
            expected["descendant_remaining_quantity"]
        )


def test_two_independent_hand_counts_agree_with_ground_truth(answer_key: dict) -> None:
    """AC 2: Hai người đếm độc lập ra cùng kết quả và cùng khớp đáp án chốt."""
    for root_code, expected in answer_key["ground_truth_recall_scope"].items():
        truth = {
            entry["batch_code"]: entry["remaining_quantity"] for entry in expected["descendants"]
        }
        sheets = expected["hand_count_sheets"]

        counter_1 = {}
        for raw_entry in sheets["counter_1"]["entries"]:
            code, _, quantity = raw_entry.partition("=")
            counter_1[code] = float(quantity)
        counter_2 = {
            code: float(quantity)
            for code, quantity in sheets["counter_2"]["entries_by_code"].items()
        }

        assert counter_1 == counter_2, f"Hai người đếm lệch nhau ở lô gốc {root_code}"
        assert counter_1 == truth, f"Người đếm thứ nhất lệch đáp án ở lô gốc {root_code}"
        assert counter_2 == truth, f"Người đếm thứ hai lệch đáp án ở lô gốc {root_code}"
        assert sum(counter_1.values()) == pytest.approx(sheets["counter_1"]["remaining_total"])
        assert sum(counter_2.values()) == pytest.approx(sheets["counter_2"]["remaining_total"])


def test_answer_key_conserves_quantity(recall_sample_db: Session, answer_key: dict) -> None:
    """Bảo toàn khối lượng: còn lại + đã bán của 18 lô = 3000 kg thu hoạch ban đầu."""
    totals = answer_key["graph_structure"]["totals"]
    tiers = answer_key["graph_structure"]["tiers"]

    remaining = sum(batch["quantity"] for tier in tiers for batch in tier["batches"])
    consumed = sum(batch["consumed_quantity"] for tier in tiers for batch in tier["batches"])

    assert remaining == pytest.approx(totals["remaining_quantity"])
    assert consumed == pytest.approx(totals["consumed_quantity"])
    assert remaining + consumed == pytest.approx(totals["harvested_quantity"])

    db_batches = _batches_by_code(recall_sample_db)
    db_remaining = sum(batch.quantity for batch in db_batches.values())
    db_consumed = sum(batch.consumed_quantity or 0.0 for batch in db_batches.values())
    assert db_remaining == pytest.approx(totals["remaining_quantity"])
    assert db_consumed == pytest.approx(totals["consumed_quantity"])


def test_seed_script_is_idempotent_and_deterministic(recall_sample_db: Session) -> None:
    """Chạy script lần nữa cho cùng kết quả: không nhân đôi dữ liệu, phả hệ y nguyên."""
    before = _batches_by_code(recall_sample_db)
    before_relations = load_lineage_relations(recall_sample_db)
    before_scope = build_recall_scope(recall_sample_db, before["LOT-S22-A2"])

    seed_recall_sample(recall_sample_db)

    after = _batches_by_code(recall_sample_db)
    assert sorted(after) == sorted(before), "Chạy lại không được sinh thêm lô mẫu"
    assert len(after) == 18
    assert load_lineage_relations(recall_sample_db) == before_relations

    after_scope = build_recall_scope(recall_sample_db, after["LOT-S22-A2"])
    assert [(entry.batch_code, entry.remaining_quantity) for entry in after_scope.descendants] == [
        (entry.batch_code, entry.remaining_quantity) for entry in before_scope.descendants
    ]


# ------------------------------------------------------------------------ Biên ---
def test_recall_scope_of_leaf_and_merged_batch(recall_sample_db: Session) -> None:
    """Lô thành phẩm cuối chuỗi không có hậu duệ; lô gộp chỉ có 2 lô thành phẩm."""
    batches = _batches_by_code(recall_sample_db)

    leaf_scope = build_recall_scope(recall_sample_db, batches["LOT-S22-F11"])
    assert leaf_scope.descendants == []
    assert leaf_scope.descendant_count == 0
    assert leaf_scope.total_remaining_quantity == pytest.approx(200.0)

    merged_scope = build_recall_scope(recall_sample_db, batches["LOT-S22-M1"])
    assert [entry.batch_code for entry in merged_scope.descendants] == [
        "LOT-S22-F11",
        "LOT-S22-F12",
    ]
    assert merged_scope.descendant_remaining_quantity == pytest.approx(200.0)


def test_recall_enumeration_rejects_unknown_root_and_completed_descendant() -> None:
    """Thiếu số liệu lô gốc thì báo lỗi rõ ràng; lô đã bán hết vẫn nằm trong phạm vi."""
    relations = [
        {"parent": "LOT-X", "child": "LOT-Y"},
        {"parent": "LOT-Y", "child": "LOT-Z"},
    ]
    facts = {
        "LOT-Y": RecallBatchFact(batch_code="LOT-Y", remaining_quantity=10.0),
        "LOT-Z": RecallBatchFact(
            batch_code="LOT-Z",
            remaining_quantity=0.0,
            consumed_quantity=5.0,
            status=BATCH_STATUS_CONSUMED,
        ),
    }

    with pytest.raises(ValueError, match="LOT-X"):
        enumerate_recall_scope("LOT-X", relations, facts)

    entries = enumerate_recall_scope("LOT-Y", relations, facts)
    assert [entry.batch_code for entry in entries] == ["LOT-Z"]
    assert entries[0].is_recallable is False
    assert entries[0].consumed_quantity == pytest.approx(5.0)
