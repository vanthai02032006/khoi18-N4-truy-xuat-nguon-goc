"""Kiểm thử sinh mã QR in ấn (PNG/SVG) và cổng tra cứu công khai (Story 6).

Kịch bản kiểm thử:
1. Sinh mã QR dạng PNG: kiểm tra HTTP 200, header Content-Type image/png, file tải về hợp lệ.
2. Sinh mã QR dạng SVG: kiểm tra HTTP 200, header Content-Type image/svg+xml, nội dung vector XML.
3. Cổng tra cứu công khai: không cần đăng nhập, trả về đầy đủ thông tin nguồn gốc.
"""

from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Batch, Farm


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = TestingSessionLocal()
    farm = Farm(name="Vùng Trồng Xoài VietGAP", location="Đồng Tháp", area=5.0, owner="HTX Mỹ Xương")
    db.add(farm)
    db.commit()

    batch = Batch(
        farm_id=farm.id,
        product_name="Xoài Cát Chu Xuất Khẩu",
        quantity=1000.0,
        remaining_quantity=1000.0,
        organization="HTX Nông Nghiệp Số 4",
        status="ĐÓNG_GÓI",
        harvest_date=date(2026, 10, 5),
    )
    db.add(batch)
    db.commit()

    yield {
        "db": db,
        "batch_id": batch.id,
    }
    db.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session["db"]
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_generate_qr_code_png(client, db_session):
    """Giả sử yêu cầu tải QR dạng PNG, Khi gọi API, Thì nhận về file ảnh PNG hợp lệ."""
    batch_id = db_session["batch_id"]
    res = client.get(f"/batches/{batch_id}/qr?format=png")
    assert res.status_code == 200
    assert "image/png" in res.headers["content-type"]
    assert len(res.content) > 100
    # Header PNG chuẩn: \x89PNG
    assert res.content[:4] == b"\x89PNG"


def test_generate_qr_code_svg(client, db_session):
    """Giả sử yêu cầu tải QR dạng SVG cho in ấn công nghiệp, Khi gọi API, Thì nhận về file SVG vector."""
    batch_id = db_session["batch_id"]
    res = client.get(f"/batches/{batch_id}/qr?format=svg")
    assert res.status_code == 200
    assert "image/svg+xml" in res.headers["content-type"]
    assert b"<svg" in res.content
    assert b"</svg>" in res.content


def test_public_traceability_portal_no_auth(client, db_session):
    """Giả sử người tiêu dùng quét mã QR, Khi mở cổng công khai, Thì thấy thông tin mà không cần đăng nhập."""
    batch_id = db_session["batch_id"]
    res = client.get(f"/public/trace/{batch_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["product_name"] == "Xoài Cát Chu Xuất Khẩu"
    assert data["farm_name"] == "Vùng Trồng Xoài VietGAP"
    assert data["status"] == "ĐÓNG_GÓI"
    assert "trace_url" in data
