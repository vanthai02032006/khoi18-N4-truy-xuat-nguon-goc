"""Bộ kiểm thử độc lập cho tính năng Bản đồ hành trình công khai (S-06 / Tier Later - 3 SP).

Tiêu chí nghiệm thu:
1. Hiện điểm vùng trồng xuất xứ (toạ độ đại diện S-06) và các điểm dừng chính theo thứ tự thời gian.
2. Điểm là cấp xã hoặc huyện, tuyệt đối không hiện toạ độ chính xác của thửa đất nông hộ (bảo vệ quyền riêng tư).
3. Người dùng công khai (không cần đăng nhập) vẫn có thể tra cứu và hiển thị bản đồ hành trình.
"""

import sys
from pathlib import Path
from datetime import date
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Thêm thư mục backend vào sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import Base
from app.models import Farm, Batch, BatchEvent
from app.routers.batches import get_batch_public_map


def run_public_map_scenarios_tests():
    print("=" * 70)
    print("KIỂM THỬ TÍNH NĂNG BẢN ĐỒ HÀNH TRÌNH CÔNG KHAI (S-06 - 3 SP TIER LATER)")
    print("=" * 70)

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionTest = sessionmaker(bind=engine)
    db = SessionTest()

    # Tạo dữ liệu mẫu
    farm = Farm(
        name="Thửa Đất #12 Vườn Xoài Cát Chu",
        location="Xã Mỹ Xương, Huyện Cao Lãnh, Đồng Tháp",
        area=3.5,
        owner="HTX Xoài Mỹ Xương",
    )
    db.add(farm)
    db.commit()

    batch = Batch(
        code="MAPTEST1",
        farm_id=farm.id,
        product_name="Xoài Cát Chu Tiêu Chuẩn VietGAP",
        quantity=1500.0,
        harvest_date=date(2026, 3, 10),
        status="ACTIVE",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    # Thêm các sự kiện hành trình theo chuỗi cung ứng
    ev1 = BatchEvent(
        batch_id=batch.id,
        event_type="HARVEST",
        payload='{"note": "Thu hoạch buổi sáng"}',
        actor="farmer_1",
        organization="HTX Xoài Mỹ Xương",
        timestamp="2026-03-10T06:30:00Z",
        hash="hash_1",
        previous_hash="0" * 64,
    )
    ev2 = BatchEvent(
        batch_id=batch.id,
        event_type="PROCESSING",
        payload='{"note": "Làm mát và khử khuẩn 5°C"}',
        actor="operator_2",
        organization="Nhà máy sơ chế Tiền Giang",
        timestamp="2026-03-10T11:00:00Z",
        hash="hash_2",
        previous_hash="hash_1",
    )
    ev3 = BatchEvent(
        batch_id=batch.id,
        event_type="HANDOVER",
        payload='{"note": "Bàn giao xe lạnh vận chuyển"}',
        actor="logistics_3",
        organization="Đơn vị Vận chuyển Chuỗi lạnh",
        timestamp="2026-03-10T16:00:00Z",
        hash="hash_3",
        previous_hash="hash_2",
    )
    ev4 = BatchEvent(
        batch_id=batch.id,
        event_type="EXPORT",
        payload='{"note": "Kiểm dịch và xếp container"}',
        actor="inspector_4",
        organization="Chi cục Kiểm dịch Thực vật Cảng Cát Lái",
        timestamp="2026-03-11T08:00:00Z",
        hash="hash_4",
        previous_hash="hash_3",
    )
    db.add_all([ev1, ev2, ev3, ev4])
    db.commit()

    # 1. Gọi endpoint bản đồ hành trình công khai
    print("\n[TEST 1] Gọi API tra cứu bản đồ hành trình cho mã lô 'MAPTEST1'...")
    map_resp = get_batch_public_map(code=batch.code, db=db)

    assert map_resp.batch_code == "MAPTEST1"
    assert map_resp.product_name == "Xoài Cát Chu Tiêu Chuẩn VietGAP"
    print(f"  -> Lô nông sản: '{map_resp.product_name}' (Mã: {map_resp.batch_code})")

    # 2. Kiểm tra điểm vùng trồng xuất xứ (S-06) & Bảo vệ quyền riêng tư cấp xã/huyện
    print("\n[TEST 2] Kiểm tra điểm vùng trồng và bảo vệ quyền riêng tư thửa đất...")
    origin = map_resp.origin_point
    assert origin.order == 1
    assert "Mỹ Xương" in origin.name or "Cao Lãnh" in origin.name
    assert "Cấp Xã" in origin.location_level or "Huyện" in origin.location_level
    # Toạ độ làm tròn cấp xã/huyện, không chứa số thập phân vi mô thửa đất (> 5 chữ số)
    assert len(str(origin.latitude).split(".")[1]) <= 3, "Toạ độ vĩ độ không được chi tiết quá 3 chữ số thập phân"
    assert len(str(origin.longitude).split(".")[1]) <= 3, "Toạ độ kinh độ không được chi tiết quá 3 chữ số thập phân"
    assert "thửa" not in origin.name.lower() or "đã ẩn" in origin.location_level.lower()
    print(f"  -> Điểm xuất xứ: '{origin.name}' (Địa bàn: {origin.location_level})")
    print(f"  -> Toạ độ cấp Xã/Huyện: {origin.latitude}°N, {origin.longitude}°E (Bảo toàn bí mật thửa đất).")
    print("  -> PASSED: Tiêu chí toạ độ cấp xã/huyện, không hiện toạ độ chính xác thửa đất.")

    # 3. Kiểm tra các điểm dừng chính theo thứ tự thời gian
    print("\n[TEST 3] Kiểm tra chuỗi điểm dừng theo thứ tự hành trình...")
    waypoints = map_resp.waypoints
    assert len(waypoints) == 3, f"Phải có 3 điểm dừng chính từ các sự kiện tiếp theo, thực tế: {len(waypoints)}"

    orders = [wp.order for wp in waypoints]
    assert orders == [2, 3, 4], f"Thứ tự các điểm dừng phải tăng dần [2, 3, 4], thực tế: {orders}"

    for wp in waypoints:
        assert "Cấp" in wp.location_level
        assert wp.latitude > 0 and wp.longitude > 0
        print(f"  -> Điểm dừng #{wp.order}: '{wp.name}' - {wp.action} (Đơn vị: {wp.organization})")

    # 4. Kiểm tra ghi chú bảo mật quyền riêng tư
    print("\n[TEST 4] Kiểm tra ghi chú bảo vệ quyền riêng tư công khai...")
    assert "bảo vệ" in map_resp.privacy_note.lower() and "thửa đất" in map_resp.privacy_note.lower()
    print(f"  -> Ghi chú bảo mật: '{map_resp.privacy_note}'")
    print("  -> PASSED: Bản đồ hành trình đầy đủ điểm dừng và minh bạch chuỗi cung ứng.")

    print("\n" + "=" * 70)
    print("KẾT QUẢ: BẢN ĐỒ HÀNH TRÌNH CÔNG KHAI S-06 ĐẠT 100% TIÊU CHÍ (PASSED)!")
    print("=" * 70)


if __name__ == "__main__":
    run_public_map_scenarios_tests()
