"""Kiểm thử tính năng sinh mã lô thu hoạch theo chuẩn T-19 (SCRUM-35).

Mục tiêu & Nghiệm thu:
1. Ghi nhận lô thu hoạch mới (POST /batches) với thông tin thửa, sản phẩm, khối lượng, ngày thu hoạch.
2. Mã lô được tự động sinh theo cấu trúc T-19: LOT-{farm_id:02d}-{YYYYMMDD}-{sequence:02d}.
3. Nhiều lô thu hoạch trong cùng một ngày từ cùng một thửa được đánh số thứ tự sequence tăng dần.
4. Trường batch_code được trả về đầy đủ trong BatchResponse phục vụ hiển thị cỡ chữ lớn trên giao diện.
"""

from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine, init_db
from app.main import app
from app.models import Batch, Farm, User
from app.security import hash_password


@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        # Dọn dẹp dữ liệu cũ
        session.query(Batch).delete()
        session.query(Farm).delete()
        session.query(User).delete()

        # Tạo farm mẫu
        farm = Farm(
            id=1,
            name="Vườn Xoài Cát Chu Cao Lãnh #1",
            location="Xã Mỹ Xương, Huyện Cao Lãnh, Đồng Tháp",
            area=5.2,
            owner="HTX Xoài Mỹ Xương",
        )
        session.add(farm)

        # Tạo user farmer
        farmer = User(
            username="farmer",
            password=hash_password("123456"),
            role="farmer",
        )
        session.add(farmer)
        session.commit()
    finally:
        session.close()


def test_t19_harvest_batch_code_generation():
    """Kiểm tra mã lô thu hoạch sinh tự động đúng chuẩn T-19."""
    client = TestClient(app)
    auth_farmer = ("farmer", "123456")

    # Lô thu hoạch thứ nhất ngày 2026-10-05
    payload_1 = {
        "farm_id": 1,
        "product_name": "Xoài Cát Chu VietGAP Thượng Hạng",
        "quantity": 1250.5,
        "harvest_date": "2026-10-05",
    }
    resp_1 = client.post("/batches", auth=auth_farmer, json=payload_1)
    assert resp_1.status_code == 201, resp_1.text
    data_1 = resp_1.json()
    assert data_1["batch_code"] == "LOT-01-20261005-01"
    assert data_1["product_name"] == payload_1["product_name"]
    assert data_1["quantity"] == 1250.5
    assert data_1["farm_id"] == 1

    # Lô thu hoạch thứ hai cùng ngày từ cùng thửa đất -> sequence tăng lên 02
    payload_2 = {
        "farm_id": 1,
        "product_name": "Xoài Cát Chu Loại 2",
        "quantity": 800.0,
        "harvest_date": "2026-10-05",
    }
    resp_2 = client.post("/batches", auth=auth_farmer, json=payload_2)
    assert resp_2.status_code == 201, resp_2.text
    data_2 = resp_2.json()
    assert data_2["batch_code"] == "LOT-01-20261005-02"

    # Lô thu hoạch ngày khác -> sequence bắt đầu lại từ 01
    payload_3 = {
        "farm_id": 1,
        "product_name": "Xoài Cát Chu Đợt 2",
        "quantity": 950.0,
        "harvest_date": "2026-10-06",
    }
    resp_3 = client.post("/batches", auth=auth_farmer, json=payload_3)
    assert resp_3.status_code == 201, resp_3.text
    data_3 = resp_3.json()
    assert data_3["batch_code"] == "LOT-01-20261006-01"
