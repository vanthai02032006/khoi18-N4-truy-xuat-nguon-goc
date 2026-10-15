"""Kiểm thử sinh mã QR in ấn (PNG/SVG) và cổng tra cứu công khai cho người tiêu dùng (SCRUM-127 & SCRUM-128).

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
- SCRUM-127:
  1. Sinh mã QR dạng PNG: kiểm tra HTTP 200, header Content-Type image/png, file tải về hợp lệ.
  2. Sinh mã QR dạng SVG: kiểm tra HTTP 200, header Content-Type image/svg+xml, nội dung vector XML chuẩn in ấn.
  3. Hỗ trợ sinh mã QR theo cả ID và mã lô (batch_code).
  4. Lô không tồn tại trả về HTTP 404.
- SCRUM-128:
  1. Cổng tra cứu công khai: không cần đăng nhập, trả về đầy đủ thông tin xuất xứ.
  2. Hiển thị tên vùng trồng, tỉnh, ngày thu hoạch.
  3. Các mốc sơ chế, vận chuyển, phân phối theo thứ tự thời gian.
  4. Lô gộp từ nhiều nguồn: hiện mọi vùng trồng xuất xứ gốc.
  5. Mã không tồn tại trả về HTTP 404 "không tìm thấy", không lộ thông tin nhạy cảm.
"""
from __future__ import annotations

from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, BatchEvent, BatchRelation, Farm
from app.security import compute_event_hash


@pytest.fixture
def qr_public_env():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = TestingSessionLocal()
    farm1 = Farm(
        name="Vùng Trồng Xoài VietGAP Cao Lãnh",
        location="Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp",
        area=5.0,
        owner="HTX Nông Nghiệp Mỹ Xương",
    )
    farm2 = Farm(
        name="Vùng Trồng Rau Thủy Canh Đà Lạt",
        location="Phường 7, Thành phố Đà Lạt, Tỉnh Lâm Đồng",
        area=3.5,
        owner="Nông trại Rau Sạch Cao Nguyên",
    )
    db.add_all([farm1, farm2])
    db.commit()

    # Lô 1: Lô đơn lẻ
    batch1 = Batch(
        farm_id=farm1.id,
        batch_code="LOT-2026-X01",
        product_name="Xoài Cát Chu Xuất Khẩu",
        quantity=1000.0,
        remaining_quantity=1000.0,
        current_holder_org="HTX Nông Nghiệp Số 4",
        status="ĐÓNG_GÓI",
        harvest_date=date(2026, 10, 5),
    )
    # Lô 2: Thu hoạch từ farm 2
    batch2 = Batch(
        farm_id=farm2.id,
        batch_code="LOT-2026-R02",
        product_name="Xà Lách Thủy Canh VietGAP",
        quantity=500.0,
        remaining_quantity=500.0,
        current_holder_org="HTX Rau Sạch Lâm Đồng",
        status="THU_HOẠCH",
        harvest_date=date(2026, 10, 6),
    )
    db.add_all([batch1, batch2])
    db.commit()

    # Lô 3: Lô gộp từ batch1 và batch2 (SCRUM-128: Lô gộp từ nhiều nguồn)
    batch_merged = Batch(
        farm_id=farm1.id,
        batch_code="LOT-2026-MIX03",
        product_name="Combo Nông Sản Xuất Khẩu (Xoài & Xà Lách)",
        quantity=800.0,
        remaining_quantity=800.0,
        current_holder_org="Công ty Xuất Nhập Khẩu Mekong",
        status="ĐANG_LƯU_THÔNG",
        harvest_date=date(2026, 10, 7),
    )
    db.add(batch_merged)
    db.commit()

    # Quan hệ gộp trong BatchRelation
    rel1 = BatchRelation(parent_batch_id=batch1.id, child_batch_id=batch_merged.id, used_quantity=400.0)
    rel2 = BatchRelation(parent_batch_id=batch2.id, child_batch_id=batch_merged.id, used_quantity=400.0)
    db.add_all([rel1, rel2])

    # Sự kiện cho batch1 theo thứ tự thời gian
    genesis_hash = "0" * 64
    h1 = compute_event_hash("HARVEST", '{"note": "Thu hoạch chọn lọc độ chín 85%"}', "farmer_01", "HTX Nông Nghiệp Mỹ Xương", "2026-10-05T07:00:00Z", genesis_hash)
    ev1 = BatchEvent(
        batch_id=batch1.id,
        event_type="HARVEST",
        payload='{"note": "Thu hoạch chọn lọc độ chín 85%"}',
        actor="farmer_01",
        organization="HTX Nông Nghiệp Mỹ Xương",
        timestamp="2026-10-05T07:00:00Z",
        hash=h1,
        previous_hash=genesis_hash,
    )
    h2 = compute_event_hash("PREPROCESSING", '{"note": "Rửa sạch, sục ozon và phân loại size 1"}', "worker_02", "HTX Nông Nghiệp Số 4", "2026-10-05T10:30:00Z", h1)
    ev2 = BatchEvent(
        batch_id=batch1.id,
        event_type="PREPROCESSING",
        payload='{"note": "Rửa sạch, sục ozon và phân loại size 1"}',
        actor="worker_02",
        organization="HTX Nông Nghiệp Số 4",
        timestamp="2026-10-05T10:30:00Z",
        hash=h2,
        previous_hash=h1,
    )
    h3 = compute_event_hash("PACKAGING", '{"note": "Đóng gói màng bọc khí và dán tem QR 3x3cm"}', "packer_03", "HTX Nông Nghiệp Số 4", "2026-10-05T14:00:00Z", h2)
    ev3 = BatchEvent(
        batch_id=batch1.id,
        event_type="PACKAGING",
        payload='{"note": "Đóng gói màng bọc khí và dán tem QR 3x3cm"}',
        actor="packer_03",
        organization="HTX Nông Nghiệp Số 4",
        timestamp="2026-10-05T14:00:00Z",
        hash=h3,
        previous_hash=h2,
    )
    h4 = compute_event_hash("TRANSPORT", '{"note": "Vận chuyển xe lạnh 6°C về siêu thị"}', "driver_04", "Đơn vị Vận Tải Lạnh Express", "2026-10-06T08:00:00Z", h3)
    ev4 = BatchEvent(
        batch_id=batch1.id,
        event_type="TRANSPORT",
        payload='{"note": "Vận chuyển xe lạnh 6°C về siêu thị"}',
        actor="driver_04",
        organization="Đơn vị Vận Tải Lạnh Express",
        timestamp="2026-10-06T08:00:00Z",
        hash=h4,
        previous_hash=h3,
    )
    db.add_all([ev1, ev2, ev3, ev4])
    db.commit()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "client": client,
        "batch1_id": batch1.id,
        "batch1_code": batch1.batch_code,
        "merged_id": batch_merged.id,
        "merged_code": batch_merged.batch_code,
    }

    app.dependency_overrides.clear()
    db.close()


# =====================================================================
# KIỂM THỬ SCRUM-127: SINH MÃ QR IN ẤN (PNG / SVG)
# =====================================================================

def test_generate_qr_code_png(qr_public_env):
    """Giả sử yêu cầu tải QR dạng PNG, Khi gọi API, Thì nhận về file ảnh PNG hợp lệ."""
    client = qr_public_env["client"]
    batch_id = qr_public_env["batch1_id"]

    res = client.get(f"/batches/{batch_id}/qr?format=png")
    assert res.status_code == 200
    assert "image/png" in res.headers["content-type"]
    assert len(res.content) > 100
    # Header PNG chuẩn: \x89PNG
    assert res.content[:4] == b"\x89PNG"


def test_generate_qr_code_svg(qr_public_env):
    """Giả sử yêu cầu tải QR dạng SVG cho in ấn công nghiệp, Khi gọi API, Thì nhận về file SVG vector."""
    client = qr_public_env["client"]
    batch_id = qr_public_env["batch1_id"]

    res = client.get(f"/batches/{batch_id}/qr?format=svg")
    assert res.status_code == 200
    assert "image/svg+xml" in res.headers["content-type"]
    assert b"<svg" in res.content
    assert b"</svg>" in res.content


def test_generate_qr_code_by_code(qr_public_env):
    """Sinh mã QR trực tiếp bằng mã lô (batch_code)."""
    client = qr_public_env["client"]
    code = qr_public_env["batch1_code"]

    res = client.get(f"/batches/code/{code}/qr?format=png")
    assert res.status_code == 200
    assert "image/png" in res.headers["content-type"]
    assert res.content[:4] == b"\x89PNG"


def test_generate_qr_not_found(qr_public_env):
    """Lô không tồn tại trả về 404."""
    client = qr_public_env["client"]
    res = client.get("/batches/99999/qr?format=png")
    assert res.status_code == 404


# =====================================================================
# KIỂM THỬ SCRUM-128: CỔNG TRA CỨU CÔNG KHAI CHO NGƯỜI TIÊU DÙNG
# =====================================================================

def test_public_traceability_portal_no_auth(qr_public_env):
    """Giả sử người tiêu dùng quét mã QR, Khi mở cổng công khai, Thì thấy thông tin mà không cần đăng nhập."""
    client = qr_public_env["client"]
    batch_id = qr_public_env["batch1_id"]

    res = client.get(f"/public/trace/{batch_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["product_name"] == "Xoài Cát Chu Xuất Khẩu"
    assert data["farm_name"] == "Vùng Trồng Xoài VietGAP Cao Lãnh"
    assert data["status"] == "ĐÓNG_GÓI"
    assert "trace_url" in data
    assert "Đồng Tháp" in data["farm_province"]


def test_public_traceability_by_code(qr_public_env):
    """Tra cứu nguồn gốc bằng mã lô in trên bao bì."""
    client = qr_public_env["client"]
    code = qr_public_env["batch1_code"]

    res = client.get(f"/public/trace/{code}")
    assert res.status_code == 200
    data = res.json()
    assert data["batch_code"] == code
    assert data["harvest_date"] == "2026-10-05"


def test_public_trace_not_found_leaves_no_leak(qr_public_env):
    """Mã không tồn tại trả về 404 và thông báo thân thiện, không rò rỉ thông tin hệ thống."""
    client = qr_public_env["client"]

    res = client.get("/public/trace/LOT-UNKNOWN-999")
    assert res.status_code == 404
    detail = res.json()["detail"]
    assert "Không tìm thấy" in detail


def test_public_trace_chronological_events(qr_public_env):
    """Các mốc sơ chế, vận chuyển, phân phối hiển thị theo thứ tự thời gian tăng dần."""
    client = qr_public_env["client"]
    batch_id = qr_public_env["batch1_id"]

    res = client.get(f"/public/trace/{batch_id}")
    assert res.status_code == 200
    data = res.json()
    events = data["events"]
    assert len(events) == 4

    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps)

    # Hiển thị tên thân thiện cho người tiêu dùng
    event_names = [e["event_type_display"] for e in events]
    assert any("Thu hoạch" in name for name in event_names)
    assert any("Sơ chế" in name for name in event_names)
    assert any("Đóng gói" in name for name in event_names)
    assert any("Vận chuyển" in name for name in event_names)


def test_public_trace_merged_batch_shows_all_origin_farms(qr_public_env):
    """Lô gộp từ nhiều nguồn hiển thị đầy đủ mọi vùng trồng gốc (SCRUM-128)."""
    client = qr_public_env["client"]
    merged_id = qr_public_env["merged_id"]

    res = client.get(f"/public/trace/{merged_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["is_merged"] is True

    origin_farms = data["origin_farms"]
    assert len(origin_farms) == 2, "Lô gộp từ 2 nguồn phải hiển thị đúng 2 vùng trồng gốc"

    farm_names = [f["farm_name"] for f in origin_farms]
    assert "Vùng Trồng Xoài VietGAP Cao Lãnh" in farm_names
    assert "Vùng Trồng Rau Thủy Canh Đà Lạt" in farm_names

    provinces = [f["farm_province"] for f in origin_farms]
    assert any("Đồng Tháp" in p for p in provinces)
    assert any("Lâm Đồng" in p for p in provinces)
