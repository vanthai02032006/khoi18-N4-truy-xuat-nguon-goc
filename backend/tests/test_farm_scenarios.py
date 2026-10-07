"""Bộ kiểm thử độc lập cho 4 kịch bản nghiệm thu (Acceptance Scenarios) của Quản lý thửa đất / vùng trồng.

Kịch bản 1: Giả sử đã đăng nhập bằng vai trò vùng trồng, Khi khai báo thửa với tên, diện tích và toạ độ hợp lệ,
            Thì thửa được lưu và thuộc tổ chức của tôi
Kịch bản 2: Giả sử tôi nhập diện tích âm hoặc bằng 0, Khi lưu, Thì bị chặn kèm thông báo rõ lý do
Kịch bản 3: Giả sử tôi xem danh sách thửa, Khi tải trang, Thì chỉ thấy thửa của tổ chức mình
Kịch bản 4: Giả sử tôi sửa tên thửa, Khi lưu, Thì lô đã gắn với thửa vẫn trỏ đúng thửa đó
"""

import sys
from pathlib import Path
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Thêm thư mục backend vào sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import Base
from app.models import Farm, Batch, User, ROLE_ADMIN, ROLE_FARMER
from app.schemas import FarmCreate, FarmUpdate
from app.routers.farms import create_farm, list_farms, update_farm


def run_farm_scenarios_tests():
    print("=" * 70)
    print("KIỂM THỬ 4 KỊCH BẢN NGHIỆM THU QUẢN LÝ THỬA ĐẤT / VÙNG TRỒNG")
    print("=" * 70)

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionTest = sessionmaker(bind=engine)
    db = SessionTest()

    # Tạo tài khoản thử nghiệm
    user_farmer_a = User(username="htx_my_xuong", password="pwd", role=ROLE_FARMER)
    user_farmer_b = User(username="htx_cai_lay", password="pwd", role=ROLE_FARMER)
    user_admin = User(username="admin_root", password="pwd", role=ROLE_ADMIN)
    db.add_all([user_farmer_a, user_farmer_b, user_admin])
    db.commit()
    db.refresh(user_farmer_a)
    db.refresh(user_farmer_b)
    db.refresh(user_admin)

    # -------------------------------------------------------------------------
    # Kịch bản 1: Đăng nhập vai trò vùng trồng, khai báo thửa hợp lệ
    # -> Thửa được lưu và thuộc tổ chức của tôi
    # -------------------------------------------------------------------------
    print("\n[KỊCH BẢN 1] Khai báo thửa hợp lệ với vai trò vùng trồng...")
    payload_a = FarmCreate(
        name="Vườn Xoài Cát Chu Thửa #1",
        location="Xã Mỹ Xương, Cao Lãnh, Đồng Tháp",
        area=3.5,
        owner="",  # Bỏ trống owner để hệ thống tự động gán theo tổ chức của nông dân
    )
    farm_a = create_farm(payload=payload_a, current_user=user_farmer_a, db=db)
    assert farm_a.id is not None
    assert farm_a.name == "Vườn Xoài Cát Chu Thửa #1"
    assert farm_a.area == 3.5
    assert farm_a.location == "Xã Mỹ Xương, Cao Lãnh, Đồng Tháp"
    assert farm_a.owner == user_farmer_a.username, f"Thửa không thuộc tổ chức {user_farmer_a.username}"
    print(f"  -> Thửa #{farm_a.id} đã được lưu thành công: '{farm_a.name}', Diện tích: {farm_a.area}ha")
    print(f"  -> Chủ sở hữu (tổ chức) được ghi nhận chính xác: '{farm_a.owner}'")
    print("  -> PASSED: Kịch bản 1 hoàn thành - Thửa được lưu và thuộc tổ chức của người tạo.")

    # -------------------------------------------------------------------------
    # Kịch bản 2: Nhập diện tích âm hoặc bằng 0 -> Bị chặn kèm thông báo rõ lý do
    # -------------------------------------------------------------------------
    print("\n[KỊCH BẢN 2] Nhập diện tích âm hoặc bằng 0 -> Chặn kèm thông báo rõ lý do...")
    # Test diện tích = 0
    blocked_zero = False
    zero_error_msg = ""
    try:
        FarmCreate(
            name="Thửa lỗi diện tích 0",
            location="Cao Lãnh",
            area=0.0,
            owner="HTX",
        )
    except (ValidationError, ValueError) as e:
        blocked_zero = True
        zero_error_msg = str(e)

    assert blocked_zero is True, "Hệ thống phải chặn diện tích bằng 0"
    assert "Diện tích thửa đất phải lớn hơn 0" in zero_error_msg or "greater than 0" in zero_error_msg
    print(f"  -> Chặn diện tích = 0: Hợp lệ. Thông báo: '{zero_error_msg[:70]}...'")

    # Test diện tích âm (< 0)
    blocked_negative = False
    negative_error_msg = ""
    try:
        FarmCreate(
            name="Thửa lỗi diện tích âm",
            location="Cao Lãnh",
            area=-2.5,
            owner="HTX",
        )
    except (ValidationError, ValueError) as e:
        blocked_negative = True
        negative_error_msg = str(e)

    assert blocked_negative is True, "Hệ thống phải chặn diện tích âm"
    assert "Diện tích thửa đất phải lớn hơn 0" in negative_error_msg or "greater than 0" in negative_error_msg
    print(f"  -> Chặn diện tích = -2.5: Hợp lệ. Thông báo: '{negative_error_msg[:70]}...'")
    print("  -> PASSED: Kịch bản 2 hoàn thành - Diện tích <= 0 bị chặn hoàn toàn kèm thông báo lý do rõ ràng.")

    # -------------------------------------------------------------------------
    # Kịch bản 3: Xem danh sách thửa -> Chỉ thấy thửa của tổ chức mình
    # -------------------------------------------------------------------------
    print("\n[KỊCH BẢN 3] Xem danh sách thửa -> Cô lập dữ liệu theo tổ chức...")
    # Farmer B tạo một thửa đất riêng của B
    payload_b = FarmCreate(
        name="Vườn Sầu Riêng Thửa #2",
        location="Cai Lậy, Tiền Giang",
        area=5.0,
        owner="",
    )
    farm_b = create_farm(payload=payload_b, current_user=user_farmer_b, db=db)
    assert farm_b.owner == user_farmer_b.username

    # Farmer A truy vấn danh sách thửa
    farms_seen_by_a = list_farms(current_user=user_farmer_a, db=db)
    farm_ids_a = [f.id for f in farms_seen_by_a]
    assert farm_a.id in farm_ids_a
    assert farm_b.id not in farm_ids_a, "Farmer A không được thấy thửa của Farmer B!"
    for f in farms_seen_by_a:
        assert f.owner == user_farmer_a.username
    print(f"  -> Farmer A ({user_farmer_a.username}) chỉ thấy {len(farms_seen_by_a)} thửa của mình: IDs {farm_ids_a}")

    # Farmer B truy vấn danh sách thửa
    farms_seen_by_b = list_farms(current_user=user_farmer_b, db=db)
    farm_ids_b = [f.id for f in farms_seen_by_b]
    assert farm_b.id in farm_ids_b
    assert farm_a.id not in farm_ids_b, "Farmer B không được thấy thửa của Farmer A!"
    for f in farms_seen_by_b:
        assert f.owner == user_farmer_b.username
    print(f"  -> Farmer B ({user_farmer_b.username}) chỉ thấy {len(farms_seen_by_b)} thửa của mình: IDs {farm_ids_b}")

    # Admin truy vấn -> Thấy toàn bộ
    farms_seen_by_admin = list_farms(current_user=user_admin, db=db)
    admin_farm_ids = [f.id for f in farms_seen_by_admin]
    assert farm_a.id in admin_farm_ids and farm_b.id in admin_farm_ids
    print(f"  -> Quản trị viên (Admin) thấy toàn bộ {len(farms_seen_by_admin)} thửa của hệ thống: IDs {admin_farm_ids}")
    print("  -> PASSED: Kịch bản 3 hoàn thành - Dữ liệu thửa đất được cô lập chuẩn xác theo tổ chức.")

    # -------------------------------------------------------------------------
    # Kịch bản 4: Sửa tên thửa -> Lô đã gắn với thửa vẫn trỏ đúng thửa đó
    # -------------------------------------------------------------------------
    print("\n[KỊCH BẢN 4] Sửa tên thửa -> Lô đã gắn với thửa vẫn trỏ đúng thửa đó...")
    # Tạo 1 lô nông sản gắn với thửa A
    batch_test = Batch(
        code="LOTTEST1",
        farm_id=farm_a.id,
        product_name="Xoài Cát Chu Xuất Khẩu",
        quantity=1200.0,
        harvest_date="2026-03-10",
        status="ACTIVE",
    )
    db.add(batch_test)
    db.commit()
    db.refresh(batch_test)

    assert batch_test.farm_id == farm_a.id
    assert batch_test.farm.name == "Vườn Xoài Cát Chu Thửa #1"
    print(f"  -> Đã tạo lô #{batch_test.id} ({batch_test.code}) gắn với Thửa #{farm_a.id} ('{farm_a.name}')")

    # Đổi tên thửa A
    new_farm_name = "Khu Nông Nghiệp Công Nghệ Cao Xoài Cao Lãnh (Đổi Tên Mới)"
    update_payload = FarmUpdate(
        name=new_farm_name,
        location=farm_a.location,
        area=farm_a.area,
        owner=farm_a.owner,
    )
    updated_farm = update_farm(
        farm_id=farm_a.id,
        payload=update_payload,
        current_user=user_farmer_a,
        db=db,
    )
    assert updated_farm.name == new_farm_name

    # Đọc lại lô từ database và kiểm tra liên kết
    db.refresh(batch_test)
    assert batch_test.farm_id == farm_a.id, "Khoá ngoại farm_id của lô bị thay đổi!"
    assert batch_test.farm.id == farm_a.id
    assert batch_test.farm.name == new_farm_name, "Lô không cập nhật theo tên mới của thửa!"

    # Mô phỏng hàm farmLabel trên giao diện
    def farm_label(f_id, farm_list):
        f = next((item for item in farm_list if item.id == f_id), None)
        return f"#{f_id} — {f.name}" if f else f"#{f_id}"

    label_after_update = farm_label(batch_test.farm_id, [updated_farm])
    assert label_after_update == f"#{farm_a.id} — {new_farm_name}"

    print(f"  -> Thửa #{farm_a.id} đã đổi tên thành: '{updated_farm.name}'")
    print(f"  -> Lô #{batch_test.id} ({batch_test.code}) vẫn trỏ chính xác về Thửa #{batch_test.farm_id}")
    print(f"  -> Nhãn hiển thị của lô trên giao diện tự động phản ánh tên mới: '{label_after_update}'")
    print("  -> PASSED: Kịch bản 4 hoàn thành - Quan hệ lô và thửa luôn toàn vẹn sau khi cập nhật tên.")

    print("\n" + "=" * 70)
    print("KẾT QUẢ: TOÀN BỘ 4 KỊCH BẢN NGHIỆM THU QUẢN LÝ THỬA ĐẤT ĐẠT 100% TIÊU CHÍ (PASSED)!")
    print("=" * 70)


if __name__ == "__main__":
    run_farm_scenarios_tests()
