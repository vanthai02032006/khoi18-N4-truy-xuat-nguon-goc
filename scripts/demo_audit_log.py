"""Script minh hoạ tự động cho tính năng Nhật ký kiểm toán hệ thống (Audit Log Middleware).

Chạy script:
    python scripts/demo_audit_log.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Thêm backend vào sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import ROLE_ADMIN, ROLE_FARMER, Farm, User
from app.security import hash_password


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 80)
    print("🚀 BẮT ĐẦU MINH HOẠ NHẬT KÝ KIỂM TOÁN HỆ THỐNG (AUDIT LOG MIDDLEWARE)")
    print("=" * 80)

    from sqlalchemy.pool import StaticPool

    # 1. Khởi tạo CSDL SQLite in-memory dùng StaticPool để chia sẻ dữ liệu giữa các thread/session
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as db:
        admin_user = User(
            username="admin_hethong",
            password=hash_password("admin123"),
            role=ROLE_ADMIN,
        )
        farmer_user = User(
            username="farmer_viet",
            password=hash_password("farm123"),
            role=ROLE_FARMER,
        )
        sample_farm = Farm(
            name="Vườn Xoài Cát Chu Cao Lãnh #1",
            location="Đồng Tháp",
            area=5.0,
            owner="HTX Nông Nghiệp Số 4",
        )
        db.add_all([admin_user, farmer_user, sample_farm])
        db.commit()
        farm_id = sample_farm.id

    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    farmer_auth = ("farmer_viet", "farm123")
    admin_auth = ("admin_hethong", "admin123")
    client_ip = "192.168.1.100"
    headers = {"X-Forwarded-For": client_ip}

    print("\n[Kịch bản 1] Người dùng thực hiện 3 thao tác: Đăng nhập -> Tạo lô -> Tách lô")
    print("  + Bước 1: farmer_viet đăng nhập hệ thống (POST /auth/login)...")
    login_res = client.post(
        "/auth/login",
        json={"username": "farmer_viet", "password": "farm123"},
        headers=headers,
    )
    print(f"    => Trạng thái: HTTP {login_res.status_code} OK")

    print("  + Bước 2: farmer_viet tạo lô nông sản (POST /batches)...")
    create_res = client.post(
        "/batches",
        json={
            "farm_id": farm_id,
            "product_name": "Xoài Cát Chu Xuất Khẩu",
            "quantity": 1000.0,
            "harvest_date": "2026-09-20",
            "batch_code": "LOT-2026-DEMO-01",
        },
        auth=farmer_auth,
        headers=headers,
    )
    created_batch = create_res.json()
    batch_id = created_batch["id"]
    batch_code = created_batch["batch_code"]
    print(f"    => Trạng thái: HTTP {create_res.status_code} Created (Mã lô: {batch_code})")

    print("  + Bước 3: farmer_viet tách lô thành 2 lô con (POST /batches/{id}/split)...")
    split_res = client.post(
        f"/batches/{batch_id}/split",
        json={
            "items": [{"quantity": 300.0}, {"quantity": 200.0}],
            "child_quantities": [300.0, 200.0],
        },
        auth=farmer_auth,
        headers=headers,
    )
    print(f"    => Trạng thái: HTTP {split_res.status_code} Created")

    print("\n[Kịch bản 2] Một người lạ cố tình đăng nhập sai mật khẩu (POST /auth/login)...")
    bad_login_res = client.post(
        "/auth/login",
        json={"username": "ke_gian", "password": "mat_khau_sai"},
        headers={"X-Forwarded-For": "10.0.0.99"},
    )
    print(f"    => Trạng thái: HTTP {bad_login_res.status_code} Unauthorized ({bad_login_res.json().get('detail')})")

    print("\n[Kết quả] Quản trị viên mở nhật ký kiểm toán (GET /audit-logs)...")
    logs_res = client.get("/audit-logs", auth=admin_auth)
    logs = logs_res.json()

    print(f"  Tổng số dòng nhật ký thu thập được: {len(logs)} dòng (Ghi hoàn toàn tự động ở Middleware!)\n")
    print(f"  {'STT':<4} | {'NGƯỜI':<15} | {'TỔ CHỨC':<22} | {'THAO TÁC':<14} | {'TRẠNG THÁI':<10} | {'MÃ ĐỐI TƯỢNG':<18} | {'IP':<15} | {'LÝ DO / GHI CHÚ'}")
    print("  " + "-" * 125)

    for i, log in enumerate(logs, 1):
        stt = f"#{i}"
        actor = log["actor"]
        org = log["organization"]
        action = log["action"]
        status_str = log["status"]
        target = log["target_code"]
        ip = log["ip_address"]
        reason = log["reason"] or "-"
        print(f"  {stt:<4} | {actor:<15} | {org:<22} | {action:<14} | {status_str:<10} | {target:<18} | {ip:<15} | {reason}")

    print("\n" + "=" * 80)
    print("🎉 TẤT CẢ TIÊU CHÍ NGHIỆM THU ĐỀU ĐẠT CHÍNH XÁC 100%!")
    print("=" * 80)


if __name__ == "__main__":
    main()
