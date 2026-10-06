"""Bộ kiểm thử toàn diện nghiệp vụ Tách Lô (S-17 / T-39 / T-19).

Bao quát đầy đủ các tiêu chí nghiệm thu (DoD / AC):
1. Tách đúng với cả 3 ca kiểm thử của S-17:
   - Ca 1: Tách một phần (Lô mẹ 1000 kg -> tách [300, 400] kg -> mẹ còn 300 kg, 2 lô con tạo thành công).
   - Ca 2: Tách toàn bộ (Lô mẹ 500 kg -> tách [200, 300] kg -> mẹ còn 0 kg, 2 lô con tạo thành công).
   - Ca 3: Biên lỗi (Vượt khối lượng khả dụng hoặc khối lượng <= 0, danh sách rỗng -> Báo lỗi, không đổi dữ liệu).
2. Test ném lỗi ở lô con thứ hai thì toàn bộ giao dịch được rollback sạch sẽ:
   - Khi có lỗi ở lô con thứ 2, lô mẹ giữ nguyên khối lượng, lô con 1 không được lưu trong DB.
3. Ràng buộc kỹ thuật:
   - Lô con kế thừa loại sản phẩm (product_name) và nguồn gốc (farm_id, harvest_date) của lô mẹ.
   - Ghi quan hệ ở T-39 (parent_id = parent.id).
   - Sinh mã lô con bằng hàm sinh mã ở T-19.
"""

import sys
from datetime import date

# Đảm bảo in UTF-8 không lỗi font trên console Windows
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# Thêm thư mục backend vào sys.path
sys.path.insert(0, "backend")

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.batch_split import generate_batch_code, split_batch
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Batch, Farm, User

# Đảm bảo schema database được nâng cấp đầy đủ cột batch_code
init_db()


def test_t19_batch_code_generator():
    """Kiểm tra hàm sinh mã lô con ở T-19."""
    print("\n--- [T-19] KIỂM TRA HÀM SINH MÃ LÔ ---")
    harvest = date(2026, 9, 25)

    # Sinh mã lô gốc F0
    root_code = generate_batch_code(farm_id=1, harvest_date=harvest, parent_id=None, sequence=1)
    print("Mã lô gốc F0:", root_code)
    assert root_code == "LOT-01-20260925-01"

    # Sinh mã lô con tách từ mẹ (T-39)
    child_code_1 = generate_batch_code(farm_id=1, harvest_date=harvest, parent_id=10, sequence=1)
    child_code_2 = generate_batch_code(farm_id=1, harvest_date=harvest, parent_id=10, sequence=2)
    print("Mã lô con 1:", child_code_1)
    print("Mã lô con 2:", child_code_2)
    assert child_code_1 == "LOT-01-P10.01-20260925"
    assert child_code_2 == "LOT-01-P10.02-20260925"
    print("[PASS] T-19: Hàm sinh mã lô con hoạt động chính xác theo quy chuẩn!")


def test_s17_case_1_partial_split():
    """Ca kiểm thử 1 của S-17: Tách một phần khối lượng lô mẹ."""
    print("\n--- [S-17 CA 1] TÁCH MỘT PHẦN KHỐI LƯỢNG LÔ MẸ ---")
    db = SessionLocal()
    try:
        # Chuẩn bị một lô mẹ với 1000 kg
        parent = Batch(
            farm_id=1,
            product_name="Xoài Cát Chu VietGAP Thượng Hạng",
            quantity=1000.0,
            harvest_date=date(2026, 9, 25),
            parent_id=None,
            is_restricted=False,
            owner="farmer",
        )
        db.add(parent)
        db.commit()
        db.refresh(parent)
        parent_id = parent.id

        print(f"Lô mẹ ban đầu: #{parent_id} - {parent.product_name}, Khối lượng: {parent.quantity} kg")

        # Thực hiện tách 2 lô con: 300.0 kg và 400.0 kg (tổng 700 kg)
        split_qtys = [300.0, 400.0]
        updated_parent, children = split_batch(
            db=db,
            parent_batch_id=parent_id,
            child_quantities=split_qtys,
        )

        print(f"Lô mẹ sau khi tách: Khối lượng còn lại = {updated_parent.quantity} kg")
        assert updated_parent.quantity == 300.0, f"Khối lượng mẹ phải còn 300.0 kg, nhận {updated_parent.quantity}"
        assert len(children) == 2, f"Phải tạo được đúng 2 lô con, nhận {len(children)}"

        # Kiểm tra chi tiết từng lô con
        for i, child in enumerate(children, start=1):
            expected_qty = split_qtys[i - 1]
            print(f"  • Lô con {i}: #{child.id}, Mã: {child.batch_code}, Khối lượng: {child.quantity} kg, "
                  f"Sản phẩm: {child.product_name}, Vùng trồng: {child.farm_id}, Lô cha: {child.parent_id}")

            assert child.quantity == expected_qty, f"Lô con {i} phải có khối lượng {expected_qty} kg"
            # Ràng buộc kỹ thuật: Lô con kế thừa loại sản phẩm và nguồn gốc của lô mẹ
            assert child.product_name == parent.product_name, "Lô con phải kế thừa chính xác product_name của mẹ"
            assert child.farm_id == parent.farm_id, "Lô con phải kế thừa chính xác farm_id của mẹ"
            assert child.harvest_date == parent.harvest_date, "Lô con phải kế thừa chính xác harvest_date của mẹ"
            # Ghi quan hệ ở T-39
            assert child.parent_id == parent_id, f"parent_id của lô con phải là #{parent_id}"
            assert child.batch_code is not None and "LOT-" in child.batch_code, "Mã lô con phải được sinh theo T-19"

        print("[PASS] S-17 Ca 1: Tách một phần thành công, khối lượng trừ chính xác, kế thừa 100% thuộc tính!")
    finally:
        db.close()


def test_s17_case_2_full_split():
    """Ca kiểm thử 2 của S-17: Tách toàn bộ khối lượng lô mẹ (khối lượng còn lại = 0)."""
    print("\n--- [S-17 CA 2] TÁCH TOÀN BỘ KHỐI LƯỢNG LÔ MẸ ---")
    db = SessionLocal()
    try:
        # Chuẩn bị một lô mẹ với 500 kg
        parent = Batch(
            farm_id=2,
            product_name="Sầu Riêng Ri6 Chín Cây Chuẩn Xuất Khẩu",
            quantity=500.0,
            harvest_date=date(2026, 9, 27),
            parent_id=None,
            is_restricted=False,
            owner="farmer",
        )
        db.add(parent)
        db.commit()
        db.refresh(parent)
        parent_id = parent.id

        print(f"Lô mẹ ban đầu: #{parent_id} - Khối lượng: {parent.quantity} kg")

        # Tách hết 500 kg thành 2 lô con: 200 kg và 300 kg
        split_qtys = [200.0, 300.0]
        updated_parent, children = split_batch(
            db=db,
            parent_batch_id=parent_id,
            child_quantities=split_qtys,
        )

        print(f"Lô mẹ sau khi tách hết: Khối lượng còn lại = {updated_parent.quantity} kg")
        assert updated_parent.quantity == 0.0, f"Khối lượng mẹ phải còn 0.0 kg, nhận {updated_parent.quantity}"
        assert len(children) == 2, f"Phải tạo được đúng 2 lô con"
        assert children[0].quantity == 200.0
        assert children[1].quantity == 300.0
        assert children[0].parent_id == parent_id
        assert children[1].parent_id == parent_id

        print("[PASS] S-17 Ca 2: Tách toàn bộ khối lượng thành công, khối lượng còn lại của mẹ về 0!")
    finally:
        db.close()


def test_s17_case_3_boundary_and_validation_errors():
    """Ca kiểm thử 3 của S-17: Kiểm tra biên lỗi & validation (vượt khối lượng hoặc số lượng không hợp lệ)."""
    print("\n--- [S-17 CA 3] KIỂM TRA CÁC BIÊN LỖI & TÍNH TOÀN VẸN ---")
    db = SessionLocal()
    try:
        parent = Batch(
            farm_id=3,
            product_name="Bơ Sáp Đắk Lắk Loại 1",
            quantity=500.0,
            harvest_date=date(2026, 9, 29),
            parent_id=None,
            is_restricted=False,
            owner="farmer",
        )
        db.add(parent)
        db.commit()
        db.refresh(parent)
        parent_id = parent.id

        # 3a: Tổng khối lượng con vượt quá khối lượng khả dụng của mẹ (300 + 250 = 550 > 500)
        print("Test 3a: Tách vượt khối lượng mẹ (550 kg > 500 kg)...")
        try:
            split_batch(db=db, parent_batch_id=parent_id, child_quantities=[300.0, 250.0])
            assert False, "Phải ném ValueError khi tổng khối lượng tách vượt quá lô mẹ"
        except ValueError as e:
            print(f"  -> Bắt được lỗi mong muốn: {e}")
            assert "vượt quá" in str(e)

        # Kiểm tra sau lỗi: Lô mẹ vẫn giữ nguyên vẹn 500 kg
        db.refresh(parent)
        assert parent.quantity == 500.0, "Khối lượng lô mẹ không được thay đổi khi bị lỗi!"

        # 3b: Khối lượng con <= 0
        print("Test 3b: Khối lượng con không hợp lệ (<= 0)...")
        try:
            split_batch(db=db, parent_batch_id=parent_id, child_quantities=[-10.0, 200.0])
            assert False, "Phải ném ValueError khi có khối lượng con <= 0"
        except ValueError as e:
            print(f"  -> Bắt được lỗi mong muốn: {e}")
            assert "lớn hơn 0" in str(e)

        # 3c: Danh sách rỗng
        print("Test 3c: Danh sách khối lượng rỗng...")
        try:
            split_batch(db=db, parent_batch_id=parent_id, child_quantities=[])
            assert False, "Phải ném ValueError khi danh sách rỗng"
        except ValueError as e:
            print(f"  -> Bắt được lỗi mong muốn: {e}")

        # Xác nhận cuối cùng: Lô mẹ vẫn nguyên 500 kg
        db.refresh(parent)
        assert parent.quantity == 500.0, "Khối lượng lô mẹ vẫn phải là 500.0 kg!"

        print("[PASS] S-17 Ca 3: Các trường hợp biên lỗi đều được chặn chính xác và an toàn!")
    finally:
        db.close()


def test_rollback_on_second_child_error():
    """Kiểm tra tiêu chí DoD: Test ném lỗi ở lô con thứ hai thì toàn bộ giao dịch được rollback sạch sẽ."""
    print("\n--- [ROLLBACK TEST] NÉM LỖI Ở LÔ CON THỨ HAI ---")
    db = SessionLocal()
    try:
        # 1. Tạo một lô mẹ ban đầu có 1000 kg
        parent = Batch(
            farm_id=4,
            product_name="Thanh Long Ruột Đỏ Bình Thuận",
            quantity=1000.0,
            harvest_date=date(2026, 9, 28),
            parent_id=None,
            is_restricted=False,
            owner="farmer",
        )
        db.add(parent)
        db.commit()
        db.refresh(parent)
        parent_id = parent.id

        # Đếm số lượng Batch hiện có trong toàn hệ thống trước khi thực hiện
        count_before = db.scalar(select(Batch.id).where(Batch.parent_id == parent_id))
        total_batches_before = len(db.scalars(select(Batch)).all())

        print(f"Trước giao dịch: Lô mẹ #{parent_id} có {parent.quantity} kg, chưa có lô con nào.")

        # 2. Thực hiện tách 2 lô con: [400.0, 350.0], nhưng CỐ TÌNH NÉM LỖI TẠI LÔ CON THỨ HAI (error_at_child_index=2)
        print("Thực hiện tách [400 kg, 350 kg] với hook ném lỗi ở lô con thứ 2...")
        try:
            split_batch(
                db=db,
                parent_batch_id=parent_id,
                child_quantities=[400.0, 350.0],
                error_at_child_index=2, # Ném RuntimeError khi đến lô con thứ hai
            )
            assert False, "Giao dịch phải bị ném lỗi ở lô con thứ hai!"
        except RuntimeError as e:
            print(f"  -> Đã ném lỗi giả lập thành công tại lô con 2: {e}")

        # 3. Mở Session mới để kiểm tra tính toàn vẹn độc lập trong DB
        db.close()
        verify_db = SessionLocal()
        try:
            parent_after = verify_db.get(Batch, parent_id)
            children_after = verify_db.scalars(select(Batch).where(Batch.parent_id == parent_id)).all()
            total_batches_after = len(verify_db.scalars(select(Batch)).all())

            print(f"Sau khi rollback: Khối lượng lô mẹ = {parent_after.quantity} kg (Ban đầu: 1000.0 kg)")
            print(f"Số lượng lô con của mẹ #{parent_id} trong DB = {len(children_after)} (Phải là 0)")

            # Kiểm tra nghiêm ngặt:
            # - Khối lượng của lô mẹ KHÔNG ĐƯỢC BỊ TRỪ (vẫn phải là 1000.0 kg)
            assert parent_after.quantity == 1000.0, (
                f"Khối lượng lô mẹ bị trừ sai lệch sau rollback! Hiện tại: {parent_after.quantity}"
            )
            # - Lô con thứ nhất KHÔNG ĐƯỢC PHÉP lưu vào DB (toàn bộ transaction phải bị rollback sạch sẽ)
            assert len(children_after) == 0, (
                f"Lô con thứ nhất vẫn còn lưu trong DB sau rollback! Số lượng: {len(children_after)}"
            )
            # - Tổng số batch trong hệ thống không thay đổi
            assert total_batches_after == total_batches_before, "Tổng số batch trong DB bị sai lệch!"

            print("[PASS] ROLLBACK HOÀN HẢO: Lô mẹ nguyên vẹn 1000 kg, không có lô con mồ côi nào trong DB!")
        finally:
            verify_db.close()
    finally:
        pass


def test_split_api_endpoint():
    """Kiểm tra gọi endpoint API POST /batches/{batch_id}/split."""
    print("\n--- [API TEST] GỌI ENDPOINT POST /batches/{id}/split ---")
    client = TestClient(app)
    auth_farmer = ("farmer", "123456")

    # Tạo lô mẹ mới để test API
    create_res = client.post(
        "/batches",
        auth=auth_farmer,
        json={
            "farm_id": 1,
            "product_name": "Bưởi Da Xanh Bến Tre VietGAP",
            "quantity": 800.0,
            "harvest_date": "2026-09-29",
            "parent_id": None,
            "is_restricted": False,
        },
    )
    assert create_res.status_code == 201, f"Tạo lô thất bại: {create_res.text}"
    parent_id = create_res.json()["id"]

    # Gọi API tách lô con: [250.0, 350.0]
    split_res = client.post(
        f"/batches/{parent_id}/split",
        auth=auth_farmer,
        json={"child_quantities": [250.0, 350.0], "note": "Tách đóng thùng xuất khẩu"},
    )
    print("API Status Code:", split_res.status_code)
    assert split_res.status_code == 201, f"API tách thất bại: {split_res.text}"
    split_data = split_res.json()

    print("API Response:", split_data["message"])
    print(f"Lô mẹ sau tách: còn {split_data['remaining_quantity']} kg")
    print(f"Tổng tách: {split_data['total_split_quantity']} kg")
    print(f"Số lô con: {len(split_data['child_batches'])}")

    assert float(split_data["remaining_quantity"]) == 200.0 # 800 - 250 - 350 = 200
    assert len(split_data["child_batches"]) == 2
    assert float(split_data["child_batches"][0]["quantity"]) == 250.0
    assert float(split_data["child_batches"][1]["quantity"]) == 350.0
    assert split_data["child_batches"][0]["parent_id"] == parent_id
    assert split_data["child_batches"][0]["product_name"] == "Bưởi Da Xanh Bến Tre VietGAP"
    assert split_data["child_batches"][0]["farm_id"] == 1

    print("[PASS] API POST /batches/{id}/split hoạt động chính xác 100%!")


def main():
    print("==================================================================")
    print("BẮT ĐẦU CHẠY BỘ KIỂM THỬ S-17 / T-39 / T-19 (TÁCH LÔ NÔNG SẢN)")
    print("==================================================================")

    test_t19_batch_code_generator()
    test_s17_case_1_partial_split()
    test_s17_case_2_full_split()
    test_s17_case_3_boundary_and_validation_errors()
    test_rollback_on_second_child_error()
    test_split_api_endpoint()

    print("\n==================================================================")
    print("🎉 TẤT CẢ TIÊU CHÍ NGHIỆM THU (DoD / AC) CỦA S-17 ĐỀU ĐẠT 100%!")
    print("==================================================================\n")


if __name__ == "__main__":
    main()
