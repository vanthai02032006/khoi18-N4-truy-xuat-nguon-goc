"""Bộ kiểm thử độc lập cho 3 kịch bản nghiệm thu (Acceptance Scenarios) của Phân tách lô nông sản (Batch Split).

Kịch bản 1: Giả sử lô mẹ còn 40 kg, Khi tách với tổng 50 kg,
            Thì bị chặn kèm thông báo nêu phần còn lại
Kịch bản 2: Giả sử hai người cùng tách một lô mẹ còn 40 kg, mỗi người 30 kg,
            Khi cả hai lưu gần như cùng lúc, Thì chỉ một người thành công, người kia nhận lỗi phần còn lại không đủ
Kịch bản 3: Giả sử tách nhiều lần liên tiếp, Khi cộng dồn,
            Thì tổng khối lượng mọi lô con bằng đúng khối lượng đã trừ khỏi lô mẹ, không lệch một gram
"""

import sys
import threading
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Thêm thư mục backend vào sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import Base
from app.models import Farm, Batch, User, ROLE_FARMER
from app.schemas import BatchSplitRequest, BatchSplitChildItem
from app.routers.batches import split_batch


def run_split_scenarios_tests():
    print("=" * 70)
    print("KIỂM THỬ 3 KỊCH BẢN NGHIỆM THU PHÂN TÁCH LÔ NÔNG SẢN (BATCH SPLIT)")
    print("=" * 70)

    # Sử dụng SQLite file-based hoặc memory
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionTest = sessionmaker(bind=engine)
    db = SessionTest()

    # Dữ liệu nền tảng
    farm = Farm(name="Vườn Sầu Riêng Xuất Khẩu", location="Tiền Giang", area=5.0, owner="HTX Tiền Giang")
    db.add(farm)
    user_farmer = User(username="farmer_a", password="pwd", role=ROLE_FARMER)
    user_farmer_2 = User(username="farmer_b", password="pwd", role=ROLE_FARMER)
    db.add_all([user_farmer, user_farmer_2])
    db.commit()
    db.refresh(farm)
    db.refresh(user_farmer)
    db.refresh(user_farmer_2)

    # -------------------------------------------------------------------------
    # Kịch bản 1: Giả sử lô mẹ còn 40 kg, Khi tách với tổng 50 kg,
    #             Thì bị chặn kèm thông báo nêu phần còn lại
    # -------------------------------------------------------------------------
    print("\n[KỊCH BẢN 1] Lô mẹ còn 40 kg, tách tổng 50 kg -> Chặn kèm thông báo nêu phần còn lại...")
    batch_mother = Batch(
        code="MOTH0001",
        farm_id=farm.id,
        product_name="Sầu Riêng Ri6",
        quantity=40.0,
        harvest_date="2026-03-15",
        status="ACTIVE",
    )
    db.add(batch_mother)
    db.commit()
    db.refresh(batch_mother)

    req_50kg = BatchSplitRequest(
        children=[
            BatchSplitChildItem(product_name="Sầu Riêng Loại 1", quantity=30.0),
            BatchSplitChildItem(product_name="Sầu Riêng Loại 2", quantity=20.0),
        ]
    )

    blocked_scenario_1 = False
    error_detail_1 = ""
    try:
        split_batch(batch_id=batch_mother.id, data=req_50kg, current_user=user_farmer, db=db)
    except HTTPException as e:
        blocked_scenario_1 = True
        error_detail_1 = str(e.detail)
        assert e.status_code == 400

    assert blocked_scenario_1 is True, "Phải chặn khi tách 50 kg từ lô mẹ 40 kg!"
    # Khẳng định thông báo nêu rõ phần còn lại của lô mẹ (40 kg hoặc 40.0 kg)
    assert "40" in error_detail_1, f"Thông báo phải nêu rõ phần còn lại của lô mẹ, thực tế: '{error_detail_1}'"
    assert "vượt quá" in error_detail_1 or "còn lại" in error_detail_1
    print(f"  -> Bị chặn chính xác: HTTP 400")
    print(f"  -> Nội dung thông báo lỗi: '{error_detail_1}'")
    print("  -> PASSED: Kịch bản 1 hoàn thành - Chặn tách quá khối lượng và thông báo nêu phần còn lại.")

    # -------------------------------------------------------------------------
    # Kịch bản 2: Hai người cùng tách một lô mẹ còn 40 kg, mỗi người 30 kg,
    #             Khi cả hai lưu gần như cùng lúc -> Chỉ 1 người thành công,
    #             người kia nhận lỗi phần còn lại không đủ
    # -------------------------------------------------------------------------
    print("\n[KỊCH BẢN 2] Hai người cùng tách lô mẹ 40 kg mỗi người 30 kg gần như cùng lúc...")
    # Chuẩn bị một lô mẹ mới 40 kg
    batch_conc = Batch(
        code="MOTHCONC",
        farm_id=farm.id,
        product_name="Xoài Cát Chu",
        quantity=40.0,
        harvest_date="2026-03-15",
        status="ACTIVE",
    )
    db.add(batch_conc)
    db.commit()
    db.refresh(batch_conc)

    req_person1 = BatchSplitRequest(
        children=[
            BatchSplitChildItem(product_name="Xoài Xuất Khẩu A", quantity=15.0),
            BatchSplitChildItem(product_name="Xoài Xuất Khẩu B", quantity=15.0),
        ]
    )  # Tổng 30 kg

    req_person2 = BatchSplitRequest(
        children=[
            BatchSplitChildItem(product_name="Xoài Nội Địa C", quantity=15.0),
            BatchSplitChildItem(product_name="Xoài Nội Địa D", quantity=15.0),
        ]
    )  # Tổng 30 kg

    results = []
    errors = []

    # Sử dụng Session riêng biệt cho 2 request đồng thời
    def worker_split(user, req, worker_id):
        worker_db = SessionTest()
        try:
            res = split_batch(batch_id=batch_conc.id, data=req, current_user=user, db=worker_db)
            results.append((worker_id, res))
        except HTTPException as exc:
            errors.append((worker_id, exc))
        finally:
            worker_db.close()

    t1 = threading.Thread(target=worker_split, args=(user_farmer, req_person1, "Người 1"))
    t2 = threading.Thread(target=worker_split, args=(user_farmer_2, req_person2, "Người 2"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Kiểm tra kết quả: Đúng 1 người thành công và 1 người thất bại
    assert len(results) == 1, f"Phải có đúng 1 người thành công, thực tế: {len(results)}"
    assert len(errors) == 1, f"Phải có đúng 1 người nhận lỗi, thực tế: {len(errors)}"

    success_worker, children_created = results[0]
    fail_worker, fail_exc = errors[0]

    assert fail_exc.status_code == 400
    # Người thất bại nhận lỗi phần còn lại không đủ (lúc này lô mẹ chỉ còn 10 kg)
    assert "10" in fail_exc.detail or "không đủ" in fail_exc.detail or "vượt quá" in fail_exc.detail or "còn lại" in fail_exc.detail

    db.refresh(batch_conc)
    assert batch_conc.quantity == 10.0, f"Lô mẹ sau lần tách thứ 1 phải còn 10.0 kg, thực tế: {batch_conc.quantity}"

    print(f"  -> {success_worker} thực hiện thành công: Tách 30 kg, tạo {len(children_created)} lô con.")
    print(f"  -> {fail_worker} bị từ chối với lỗi: HTTP {fail_exc.status_code} - '{fail_exc.detail}'")
    print(f"  -> Khối lượng lô mẹ được bảo toàn chính xác: {batch_conc.quantity} kg.")
    print("  -> PASSED: Kịch bản 2 hoàn thành - Kiểm soát tương tranh chuẩn xác, chỉ 1 người thành công.")

    # -------------------------------------------------------------------------
    # Kịch bản 3: Tách nhiều lần liên tiếp, Khi cộng dồn,
    #             Thì tổng khối lượng mọi lô con bằng đúng khối lượng đã trừ
    #             khỏi lô mẹ, không lệch một gram (0.001 kg)
    # -------------------------------------------------------------------------
    print("\n[KỊCH BẢN 3] Tách nhiều lần liên tiếp -> Khối lượng bảo toàn tuyệt đối, không lệch một gram...")
    initial_mother_qty = 150.0000  # 150 kg
    batch_multi = Batch(
        code="MOTHSERI",
        farm_id=farm.id,
        product_name="Bưởi Da Xanh Bến Tre",
        quantity=initial_mother_qty,
        harvest_date="2026-03-15",
        status="ACTIVE",
    )
    db.add(batch_multi)
    db.commit()
    db.refresh(batch_multi)

    # 4 đợt tách liên tiếp với các khối lượng lẻ đến gram
    splits_data = [
        [("Bưởi Cắt Lần 1-A", 12.345), ("Bưởi Cắt Lần 1-B", 17.655)],  # Tổng 30.000 kg
        [("Bưởi Cắt Lần 2-A", 25.120), ("Bưởi Cắt Lần 2-B", 14.880)],  # Tổng 40.000 kg
        [("Bưởi Cắt Lần 3-A", 8.250),  ("Bưởi Cắt Lần 3-B", 11.750)],  # Tổng 20.000 kg
        [("Bưởi Cắt Lần 4-A", 16.505), ("Bưởi Cắt Lần 4-B", 13.495)],  # Tổng 30.000 kg
    ]

    all_children_quantities = []
    total_deducted_expected = 0.0

    for i, split_items in enumerate(splits_data):
        req = BatchSplitRequest(
            children=[BatchSplitChildItem(product_name=name, quantity=q) for name, q in split_items]
        )
        children = split_batch(batch_id=batch_multi.id, data=req, current_user=user_farmer, db=db)
        round_total = sum(c.quantity for c in children)
        total_deducted_expected += round_total
        all_children_quantities.extend([c.quantity for c in children])
        print(f"  -> Lần tách {i+1}: Tách {round_total:.3f} kg -> Lô mẹ còn: {batch_multi.quantity:.3f} kg")

    db.refresh(batch_multi)

    total_children_qty = round(sum(all_children_quantities), 4)
    actual_deducted_from_mother = round(initial_mother_qty - batch_multi.quantity, 4)
    gram_difference = abs(total_children_qty - actual_deducted_from_mother) * 1000  # đổi sang gram

    print(f"  -> Ban đầu lô mẹ: {initial_mother_qty:.4f} kg")
    print(f"  -> Hiện tại lô mẹ còn: {batch_multi.quantity:.4f} kg")
    print(f"  -> Tổng khối lượng đã trừ khỏi lô mẹ: {actual_deducted_from_mother:.4f} kg")
    print(f"  -> Tổng cộng dồn 8 lô con sinh ra: {total_children_qty:.4f} kg")
    print(f"  -> Độ lệch khối lượng: {gram_difference:.6f} gram")

    assert total_children_qty == actual_deducted_from_mother
    assert gram_difference == 0.0, f"Bị lệch {gram_difference} gram!"
    assert round(batch_multi.quantity, 4) == 30.0000

    print("  -> PASSED: Kịch bản 3 hoàn thành - Khối lượng bảo toàn 100%, không lệch một gram.")

    print("\n" + "=" * 70)
    print("KẾT QUẢ: TOÀN BỘ 3 KỊCH BẢN NGHIỆM THU TÁCH LÔ ĐẠT 100% TIÊU CHÍ (PASSED)!")
    print("=" * 70)


if __name__ == "__main__":
    run_split_scenarios_tests()
