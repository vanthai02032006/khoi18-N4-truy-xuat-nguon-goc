"""Kiểm phần lõi phả hệ mới (services/lineage_service.py - dùng cho truy ngược,
truy xuôi, sơ đồ và thu hồi) bằng tệp đáp án đếm tay độc lập (T-53 / SCRUM-69),
KHÔNG bằng chính mã của mình (DoD: story chạm đồ thị phả hệ)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.lineage import LineageCycleError
from app.models import Batch, BatchRelation, Farm
from app.services.lineage_service import LineageGraph

BENCH = json.loads(
    (Path(__file__).resolve().parents[1] / "backend" / "data" / "lineage_benchmark.json").read_text(encoding="utf-8")
)


@pytest.fixture()
def seeded():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    farm = Farm(name="F", location="L", area=1, owner="O")
    db.add(farm)
    db.flush()
    codes = set(BENCH["graph_structure"]["root_batches"])
    for r in BENCH["graph_structure"]["relations"]:
        codes |= {r["parent"], r["child"]}
    ids = {}
    for c in sorted(codes):
        b = Batch(farm_id=farm.id, product_name="Xoài", quantity=1, harvest_date=date(2026, 5, 1), batch_code=c)
        db.add(b)
        db.flush()
        ids[c] = b.id
    for r in BENCH["graph_structure"]["relations"]:
        db.add(BatchRelation(parent_batch_id=ids[r["parent"]], child_batch_id=ids[r["child"]], used_quantity=r["quantity"]))
    db.commit()
    yield db, ids
    db.close()


def test_ancestors_and_descendants_match_hand_counted_answers(seeded):
    db, ids = seeded
    code = {v: k for k, v in ids.items()}
    g = LineageGraph.load(db)
    for c, expected in BENCH["ground_truth_ancestors"].items():
        assert {code[i] for i in g.ancestors(ids[c])} == set(expected), c
    for c, expected in BENCH["ground_truth_descendants"].items():
        assert {code[i] for i in g.descendants(ids[c])} == set(expected), c


def test_cycle_is_reported_in_both_directions(seeded):
    db, ids = seeded
    leaf = next(c for c, d in BENCH["ground_truth_descendants"].items() if not d)
    root = BENCH["graph_structure"]["root_batches"][0]
    if leaf not in BENCH["ground_truth_descendants"].get(root, []):
        leaf = BENCH["ground_truth_descendants"][root][-1]
    db.add(BatchRelation(parent_batch_id=ids[leaf], child_batch_id=ids[root], used_quantity=1))
    db.commit()
    g = LineageGraph.load(db)
    with pytest.raises(LineageCycleError):
        g.descendants(ids[root])
    with pytest.raises(LineageCycleError):
        g.ancestors(ids[leaf])
