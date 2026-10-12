"""Kiểm thử truy ngược vùng trồng (SCRUM-105), truy xuôi tồn kho/vị trí (SCRUM-108) và sơ đồ phả hệ (SCRUM-109).

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
• SCRUM-105:
  - Lô là kết quả gộp của 2 lô từ 2 vùng trồng khác nhau -> Thấy cả 2 lô thu hoạch gốc kèm tên vùng trồng & thửa.
  - Lô đi qua 3 tầng tách và gộp -> Khớp đáp án đếm tay trong bộ dữ liệu mẫu.
  - Lô chưa từng tách hay gộp -> Trả về chính nó (is_standalone=True).
  - Dữ liệu lỗi tạo chu trình cha-con -> Dừng và báo lỗi (HTTP 400) thay vì lặp vô hạn.
• SCRUM-108:
  - Kết quả truy xuôi kèm tổ chức đang giữ, địa điểm và khối lượng còn lại.
  - Hậu duệ đã bán hết (tiêu thụ) -> Hiện trạng thái 'Đã tiêu thụ' và khối lượng còn lại 0.
• SCRUM-109:
  - Sơ đồ phả hệ: lô hiện tại ở giữa (level=0), tổ tiên bên trái (level < 0), hậu duệ bên phải (level > 0).
"""

from __future__ import annotations

import json
from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Batch,
    BatchEvent,
    BatchRelation,
    Farm,
    User,
    ROLE_FARMER,
)
from app.security import hash_password


@pytest.fixture
def lineage_feature_env():
    """Thiết lập môi trường kiểm thử SQLite in-memory cho các kịch bản phả hệ nâng cao."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        user = User(username="distributor_user", password=hash_password("123456"), role=ROLE_FARMER)
        db.add(user)

        # Tạo 2 vùng trồng khác nhau (thửa 1 và thửa 2)
        farm_a = Farm(
            name="Vùng Trồng Xoài Cát Chu Cao Lãnh",
            location="Xã Mỹ Xương, Cao Lãnh, Đồng Tháp",
            area=3.0,
            owner="HTX Xoài Mỹ Xương",
        )
        farm_b = Farm(
            name="Vùng Trồng Xoài Cát Hoà Lộc Cái Bè",
            location="Xã Hoà Hưng, Cái Bè, Tiền Giang",
            area=4.5,
            owner="HTX Hoà Lộc",
        )
        db.add_all([farm_a, farm_b])
        db.commit()

        # Tạo 2 lô thu hoạch gốc từ 2 vùng trồng khác nhau
        root_a = Batch(
            farm_id=farm_a.id,
            product_name="Xoài Cát Chu",
            quantity=500.0,
            harvest_date=date(2026, 5, 20),
            batch_code="B-ROOT-01",
            current_holder_org="HTX Xoài Mỹ Xương",
        )
        root_b = Batch(
            farm_id=farm_b.id,
            product_name="Xoài Cát Hoà Lộc",
            quantity=600.0,
            harvest_date=date(2026, 5, 21),
            batch_code="B-ROOT-02",
            current_holder_org="HTX Hoà Lộc",
        )
        # Lô độc lập chưa từng tách gộp
        standalone_batch = Batch(
            farm_id=farm_a.id,
            product_name="Xoài Tượng Da Xanh",
            quantity=200.0,
            harvest_date=date(2026, 5, 22),
            batch_code="B-STANDALONE-01",
            current_holder_org="HTX Xoài Mỹ Xương",
        )
        db.add_all([root_a, root_b, standalone_batch])
        db.commit()

        # Tầng 2: Tách từ root_a -> split_1a, split_1b; từ root_b -> split_2a, split_2b
        split_1a = Batch(
            farm_id=farm_a.id,
            product_name="Xoài Cát Chu",
            quantity=100.0,
            harvest_date=date(2026, 5, 20),
            batch_code="B-SPLIT-1A",
            parent_id=root_a.id,
            current_holder_org="HTX Xoài Mỹ Xương",
        )
        split_2a = Batch(
            farm_id=farm_b.id,
            product_name="Xoài Cát Hoà Lộc",
            quantity=200.0,
            harvest_date=date(2026, 5, 21),
            batch_code="B-SPLIT-2A",
            parent_id=root_b.id,
            current_holder_org="HTX Hoà Lộc",
        )
        db.add_all([split_1a, split_2a])
        db.commit()

        # Tầng 3: Gộp split_1a và split_2a thành merge_g1 (kết quả gộp từ 2 vùng trồng khác nhau)
        merge_g1 = Batch(
            farm_id=farm_a.id,
            product_name="Xoài Xuất Khẩu Hộp Quà",
            quantity=200.0,
            harvest_date=date(2026, 5, 22),
            batch_code="B-MERGE-G1",
            current_holder_org="Công ty Mekong Export",
        )
        db.add(merge_g1)
        db.commit()

        rel_g1_1a = BatchRelation(parent_batch_id=split_1a.id, child_batch_id=merge_g1.id, used_quantity=80.0)
        rel_g1_2a = BatchRelation(parent_batch_id=split_2a.id, child_batch_id=merge_g1.id, used_quantity=120.0)
        db.add_all([rel_g1_1a, rel_g1_2a])

        # Tầng 4: Tách merge_g1 thành 2 lô con hậu duệ: child_d1 (còn hàng) và child_d2 (đã tiêu thụ)
        child_d1 = Batch(
            farm_id=farm_a.id,
            product_name="Xoài Xuất Khẩu Hộp Quà",
            quantity=80.0,
            harvest_date=date(2026, 5, 22),
            batch_code="B-DESC-D1",
            parent_id=merge_g1.id,
            current_holder_org="Siêu Thị WinCommerce Hà Nội",
        )
        child_d2 = Batch(
            farm_id=farm_a.id,
            product_name="Xoài Xuất Khẩu Hộp Quà",
            quantity=0.0,  # Đã bán hết
            harvest_date=date(2026, 5, 22),
            batch_code="B-DESC-D2",
            parent_id=merge_g1.id,
            current_holder_org="Cửa Hàng Thực Phẩm Sạch Q1",
        )
        db.add_all([child_d1, child_d2])
        db.commit()

        # Sự kiện tiêu thụ cho child_d2
        sold_event = BatchEvent(
            batch_id=child_d2.id,
            event_type="CONSUMED",
            payload='{"action": "SOLD_OUT", "note": "Đã bán hết tại điểm bán", "store": "Cửa hàng Q1 TP.HCM"}',
            actor="cashier_01",
            organization="Cửa Hàng Thực Phẩm Sạch Q1",
            timestamp="2026-05-25T15:00:00Z",
            hash="1" * 64,
            previous_hash="0" * 64,
        )
        db.add(sold_event)

        # Sự kiện lưu kho cho child_d1
        store_event = BatchEvent(
            batch_id=child_d1.id,
            event_type="COLD_STORAGE",
            payload='{"location": "Kho lạnh siêu thị WinCommerce Hoàng Mai", "temp": 5.0}',
            actor="warehouse_manager",
            organization="Siêu Thị WinCommerce Hà Nội",
            timestamp="2026-05-24T08:00:00Z",
            hash="2" * 64,
            previous_hash="0" * 64,
        )
        db.add(store_event)
        db.commit()

        # Lưu lại IDs
        merge_g1_id = merge_g1.id
        standalone_id = standalone_batch.id
        root_a_id = root_a.id

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "client": client,
        "merge_g1_id": merge_g1_id,
        "standalone_id": standalone_id,
        "root_a_id": root_a_id,
        "session_factory": TestingSessionLocal,
    }

    app.dependency_overrides.clear()


# =====================================================================
# KIỂM THỬ SCRUM-105: TRUY NGƯỢC NGUỒN GỐC VÙNG TRỒNG
# =====================================================================

def test_scrum105_traceback_two_origin_farms_and_plots(lineage_feature_env):
    """SCRUM-105: Giả sử lô là kết quả gộp của hai lô từ hai vùng trồng khác nhau -> Thấy cả hai lô thu hoạch gốc kèm tên vùng trồng và thửa."""
    client = lineage_feature_env["client"]
    g1_id = lineage_feature_env["merge_g1_id"]

    res = client.get(f"/batches/{g1_id}/trace-back")
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["target_batch_code"] == "B-MERGE-G1"
    assert data["is_standalone"] is False
    assert len(data["origin_farms"]) == 2, "Phải tìm thấy đúng 2 vùng trồng xuất xứ gốc"

    farm_names = [f["farm_name"] for f in data["origin_farms"]]
    assert "Vùng Trồng Xoài Cát Chu Cao Lãnh" in farm_names
    assert "Vùng Trồng Xoài Cát Hoà Lộc Cái Bè" in farm_names

    batch_codes = [f["batch_code"] for f in data["origin_farms"]]
    assert "B-ROOT-01" in batch_codes
    assert "B-ROOT-02" in batch_codes

    for f in data["origin_farms"]:
        assert "Thửa" in f["plot_name"], f"Thông tin thửa phải có trong plot_name: {f['plot_name']}"
        assert f["farm_location"], "Phải có địa chỉ vùng trồng"
        assert f["farm_owner"], "Phải có chủ sở hữu vùng trồng"


def test_scrum105_traceback_standalone_batch_returns_itself(lineage_feature_env):
    """SCRUM-105: Giả sử lô chưa từng tách hay gộp -> Khi truy ngược, trả về chính nó."""
    client = lineage_feature_env["client"]
    std_id = lineage_feature_env["standalone_id"]

    res = client.get(f"/batches/{std_id}/trace-back")
    assert res.status_code == 200
    data = res.json()

    assert data["is_standalone"] is True
    assert data["target_batch_code"] == "B-STANDALONE-01"
    assert len(data["origin_farms"]) == 1
    assert data["origin_farms"][0]["batch_code"] == "B-STANDALONE-01"
    assert data["origin_farms"][0]["farm_name"] == "Vùng Trồng Xoài Cát Chu Cao Lãnh"
    assert "Thửa" in data["origin_farms"][0]["plot_name"]


def test_scrum105_traceback_detects_cycle_and_aborts(lineage_feature_env):
    """SCRUM-105: Giả sử dữ liệu lỗi tạo ra chu trình cha-con -> Khi truy ngược, dừng và báo lỗi thay vì lặp vô hạn."""
    client = lineage_feature_env["client"]
    g1_id = lineage_feature_env["merge_g1_id"]
    session_factory = lineage_feature_env["session_factory"]

    root_a_id = lineage_feature_env["root_a_id"]
    with session_factory() as db:
        cycle_rel = BatchRelation(
            parent_batch_id=g1_id,
            child_batch_id=root_a_id,
            used_quantity=50.0,
        )
        db.add(cycle_rel)
        db.commit()

    res = client.get(f"/batches/{g1_id}/trace-back")
    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "chu trình" in detail or "vòng lặp" in detail or "chống lặp" in detail


# =====================================================================
# KIỂM THỬ SCRUM-108: TRUY XUÔI NGUỒN GỐC (HẬU DUỆ, TỒN KHO, VỊ TRÍ)
# =====================================================================

def test_scrum108_traceforward_location_remaining_and_consumed_status(lineage_feature_env):
    """SCRUM-108: Kết quả truy xuôi nêu tổ chức đang giữ, địa điểm và khối lượng; lô đã bán hết hiện 'Đã tiêu thụ' và khối lượng 0."""
    client = lineage_feature_env["client"]
    g1_id = lineage_feature_env["merge_g1_id"]

    res = client.get(f"/batches/{g1_id}/trace-forward")
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["target_batch_code"] == "B-MERGE-G1"
    assert data["total_descendants"] == 2

    items = {d["batch_code"]: d for d in data["descendants"]}
    assert "B-DESC-D1" in items
    assert "B-DESC-D2" in items

    # Lô D1: còn hàng
    d1 = items["B-DESC-D1"]
    assert d1["current_holder_org"] == "Siêu Thị WinCommerce Hà Nội"
    assert "Kho lạnh" in d1["location"] or "Hà Nội" in d1["location"] or "Cao Lãnh" in d1["location"]
    assert d1["remaining_quantity"] == 80.0
    assert d1["status"] == "IN_STOCK"
    assert "Còn hàng" in d1["status_label"]

    # Lô D2: đã bán hết
    d2 = items["B-DESC-D2"]
    assert d2["current_holder_org"] == "Cửa Hàng Thực Phẩm Sạch Q1"
    assert "Q1" in d2["location"] or "TP.HCM" in d2["location"] or "Cao Lãnh" in d2["location"]
    assert d2["remaining_quantity"] == 0.0, "Lô đã bán hết phải có khối lượng còn lại = 0"
    assert d2["status"] == "CONSUMED"
    assert "Đã tiêu thụ" in d2["status_label"]


# =====================================================================
# KIỂM THỬ SCRUM-109: SƠ ĐỒ PHẢ HỆ (TRÁI - GIỮA - PHẢI)
# =====================================================================

def test_scrum109_lineage_graph_structure_left_center_right(lineage_feature_env):
    """SCRUM-109: Sơ đồ phả hệ có lô hiện tại ở giữa (level=0), tổ tiên bên trái (level < 0), hậu duệ bên phải (level > 0)."""
    client = lineage_feature_env["client"]
    g1_id = lineage_feature_env["merge_g1_id"]

    res = client.get(f"/batches/{g1_id}/lineage-graph")
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["target_batch_code"] == "B-MERGE-G1"
    nodes = {n["id"]: n for n in data["nodes"]}

    # Lô hiện tại: ở giữa (level = 0, group = 'current')
    assert "B-MERGE-G1" in nodes
    curr_node = nodes["B-MERGE-G1"]
    assert curr_node["level"] == 0
    assert curr_node["group"] == "current"

    # Tổ tiên: ở bên trái (level < 0, group = 'ancestor')
    assert "B-SPLIT-1A" in nodes
    assert "B-ROOT-01" in nodes
    assert nodes["B-SPLIT-1A"]["level"] < 0
    assert nodes["B-SPLIT-1A"]["group"] == "ancestor"
    assert nodes["B-ROOT-01"]["level"] < 0
    assert nodes["B-ROOT-01"]["group"] == "ancestor"

    # Hậu duệ: ở bên phải (level > 0, group = 'descendant')
    assert "B-DESC-D1" in nodes
    assert "B-DESC-D2" in nodes
    assert nodes["B-DESC-D1"]["level"] > 0
    assert nodes["B-DESC-D1"]["group"] == "descendant"
    assert nodes["B-DESC-D2"]["level"] > 0
    assert nodes["B-DESC-D2"]["group"] == "descendant"

    # Cạnh nối (Edges)
    edges = data["edges"]
    assert len(edges) >= 4  # (ROOT_01 -> SPLIT_1A, ROOT_02 -> SPLIT_2A, SPLIT_1A -> MERGE_G1, MERGE_G1 -> D1, D2...)
    edge_pairs = [(e["from_node"], e["to_node"]) for e in edges]
    assert ("B-SPLIT-1A", "B-MERGE-G1") in edge_pairs
    assert ("B-MERGE-G1", "B-DESC-D1") in edge_pairs
