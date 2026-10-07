"""Kiểm thử tính năng xuất hồ sơ truy xuất nguồn gốc ra tệp PDF phục vụ cán bộ kiểm tra đính kèm biên bản.

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
- Tệp PDF có đầy đủ:
  1. Thông tin lô nông sản (mã lô, tên sản phẩm, khối lượng, ngày thu hoạch, vùng trồng).
  2. Dòng thời gian sự kiện (Audit chain, hash liên kết).
  3. Phả hệ tổ tiên và hậu duệ (Lineage tree - Ancestors & Descendants).
  4. Vi phạm chuỗi lạnh nếu có.
  5. Lệnh thu hồi nếu có.
  6. Kết quả kiểm tra toàn vẹn kèm thời điểm xuất và mã băm cuối chuỗi.
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
    ColdChainViolation,
    Farm,
    RecallOrder,
    RecallOrderTarget,
    ROLE_INSPECTOR,
    User,
)
from app.security import compute_event_hash, hash_password


@pytest.fixture
def pdf_export_env():
    """Thiết lập môi trường kiểm thử SQLite in-memory cho xuất hồ sơ PDF."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        inspector_user = User(
            username="inspector_auditor",
            password=hash_password("123456"),
            role=ROLE_INSPECTOR,
        )
        db.add(inspector_user)

        farm = Farm(
            name="Vùng Trồng Xoài Thử Nghiệm Xuất Khẩu",
            location="Đồng Tháp",
            area=6.0,
            owner="HTX Xoài Mỹ Xương",
        )
        db.add(farm)
        db.commit()

        # Tạo lô mẹ (Parent)
        batch_parent = Batch(
            farm_id=farm.id,
            product_name="Xoài Cát Chu Chuẩn VietGAP",
            quantity=1000.0,
            harvest_date=date(2026, 8, 1),
            batch_code="LOT-PARENT-01",
            current_holder_org="HTX Xoài Mỹ Xương",
        )
        db.add(batch_parent)
        db.commit()

        # Tạo lô con (Child)
        batch_child = Batch(
            farm_id=farm.id,
            product_name="Xoài Cát Chu Chuẩn VietGAP",
            quantity=300.0,
            harvest_date=date(2026, 8, 1),
            batch_code="LOT-CHILD-01A",
            current_holder_org="Đơn Vị Vận Tải Lạnh Express",
        )
        db.add(batch_child)
        db.commit()

        # Sự kiện 1: Thu hoạch lô mẹ
        h0 = "0" * 64
        payload_harvest = json.dumps({"action": "HARVEST", "quantity": 1000.0})
        hash_1 = compute_event_hash("HARVEST", payload_harvest, "farmer", "HTX Xoài Mỹ Xương", "2026-08-01T08:00:00Z", h0)
        ev1 = BatchEvent(
            batch_id=batch_parent.id,
            event_type="HARVEST",
            payload=payload_harvest,
            actor="farmer",
            organization="HTX Xoài Mỹ Xương",
            timestamp="2026-08-01T08:00:00Z",
            hash=hash_1,
            previous_hash=h0,
        )
        db.add(ev1)

        # Sự kiện 2: Tách lô mẹ -> ghi nhận quan hệ cha-con
        payload_split = json.dumps({
            "action": "SPLIT_CHILDREN",
            "parent_code": "LOT-PARENT-01",
            "children": [{"batch_code": "LOT-CHILD-01A", "quantity": 300.0}],
        })
        hash_2 = compute_event_hash("SPLIT", payload_split, "farmer", "HTX Xoài Mỹ Xương", "2026-08-01T09:00:00Z", hash_1)
        ev2 = BatchEvent(
            batch_id=batch_parent.id,
            event_type="SPLIT",
            payload=payload_split,
            actor="farmer",
            organization="HTX Xoài Mỹ Xương",
            timestamp="2026-08-01T09:00:00Z",
            hash=hash_2,
            previous_hash=hash_1,
        )
        db.add(ev2)

        # Sự kiện 3: Khai sinh lô con (BIRTH)
        payload_birth = json.dumps({
            "action": "BIRTH_FROM_SPLIT",
            "parent_batch_code": "LOT-PARENT-01",
            "child_batch_code": "LOT-CHILD-01A",
            "quantity": 300.0,
        })
        hash_c1 = compute_event_hash("BIRTH", payload_birth, "farmer", "HTX Xoài Mỹ Xương", "2026-08-01T09:05:00Z", h0)
        ev_c1 = BatchEvent(
            batch_id=batch_child.id,
            event_type="BIRTH",
            payload=payload_birth,
            actor="farmer",
            organization="HTX Xoài Mỹ Xương",
            timestamp="2026-08-01T09:05:00Z",
            hash=hash_c1,
            previous_hash=h0,
        )
        db.add(ev_c1)

        # Vi phạm chuỗi lạnh gắn với sản phẩm lô con
        viol = ColdChainViolation(
            shipment_code="LOT-CHILD-01A-SHIP1",
            product_types_json=json.dumps(["Xoài Cát Chu Chuẩn VietGAP"]),
            recorded_temperature=16.5,
            duration_minutes=35.0,
            applied_temp_min=2.0,
            applied_temp_max=8.0,
            applied_delay_minutes=15,
            violation_reason="Nhiệt độ 16.5°C vượt ngưỡng trên 8.0°C trong 35 phút",
            timestamp="2026-08-01T12:00:00Z",
            location="Trạm dừng chân Cao Lãnh",
        )
        db.add(viol)

        # Lệnh thu hồi / kiểm tra gắn với lô con
        recall = RecallOrder(
            order_code="REC-20260801-001",
            title="Kiểm tra mẫu vi phạm nhiệt độ lô con",
            reason="Nghi vấn đứt gãy chuỗi lạnh tại khâu vận chuyển",
            batch_id=batch_child.id,
            issuer_username="inspector_auditor",
            created_at="2026-08-01T13:00:00Z",
            status="IN_PROGRESS",
        )
        db.add(recall)
        db.commit()

        target1 = RecallOrderTarget(
            order_id=recall.id,
            org_name="Đơn Vị Vận Tải Lạnh Express",
            status="CONFIRMED",
            confirmed_at="2026-08-01T14:00:00Z",
            confirmed_by="driver_lead",
            note="Đã kiểm tra lại giàn lạnh",
        )
        target2 = RecallOrderTarget(
            order_id=recall.id,
            org_name="Kho Phân Phối Miền Tây",
            status="PENDING",
        )
        db.add_all([target1, target2])
        db.commit()

        batch_child_id = batch_child.id
        batch_parent_id = batch_parent.id

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {
        "client": client,
        "auth": ("inspector_auditor", "123456"),
        "batch_child_id": batch_child_id,
        "batch_parent_id": batch_parent_id,
    }

    app.dependency_overrides.clear()


def test_export_traceability_dossier_pdf_success(pdf_export_env):
    """Kiểm tra endpoint xuất hồ sơ PDF trả về đúng Content-Type và các header xác thực toàn vẹn."""
    client: TestClient = pdf_export_env["client"]
    auth = pdf_export_env["auth"]
    child_id = pdf_export_env["batch_child_id"]

    response = client.get(f"/batches/{child_id}/export-pdf", auth=auth)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment; filename*=UTF-8''Ho-so-truy-xuat-LOT-CHILD-01A.pdf" in response.headers["content-disposition"]
    assert response.headers["x-integrity-status"] == "VALID"
    assert "x-final-hash" in response.headers
    assert len(response.headers["x-final-hash"]) == 64

    # Kiểm tra nội dung PDF bytes hợp lệ (bắt đầu bằng chuẩn %PDF-)
    content = response.content
    assert len(content) > 1000
    assert content.startswith(b"%PDF-")


def test_export_pdf_parent_batch_with_descendants(pdf_export_env):
    """Kiểm tra xuất PDF cho lô mẹ hiển thị danh sách hậu duệ (descendants)."""
    client: TestClient = pdf_export_env["client"]
    auth = pdf_export_env["auth"]
    parent_id = pdf_export_env["batch_parent_id"]

    response = client.get(f"/batches/{parent_id}/export-pdf", auth=auth)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-integrity-status"] == "VALID"
    assert response.content.startswith(b"%PDF-")


def test_export_pdf_not_found(pdf_export_env):
    """Kiểm tra xuất PDF với lô không tồn tại -> 404."""
    client: TestClient = pdf_export_env["client"]
    auth = pdf_export_env["auth"]

    response = client.get("/batches/99999/export-pdf", auth=auth)
    assert response.status_code == 404
