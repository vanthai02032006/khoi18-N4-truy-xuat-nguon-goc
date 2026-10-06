"""Kịch bản kiểm thử tự động toàn diện cho Task T-41 (SCRUM-57):
"Kiểm tổng khối lượng trong giao dịch có khoá dòng lô mẹ"

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
1. Đọc lô mẹ bằng 'SELECT ... FOR UPDATE' trước khi kiểm tổng khối lượng.
2. Giải thích trong code vì sao cơ chế đọc-rồi-ghi thông thường không đủ an toàn (lost update / race condition / overselling).
3. Thao tác tách vượt khối lượng còn lại bị từ chối ngay lập tức (AC 1).
4. Kiểm thử trừ khối lượng chính xác không bị sai lệch số âm bằng Decimal/Numeric cố định, tuyệt đối không dùng float (AC 2).
5. Rollback sạch sẽ khi có lỗi phát sinh trong giao dịch.
6. Mô phỏng tranh chấp đồng thời hai giao dịch (concurrency test).
7. Kiểm thử tích hợp qua REST API POST /batches/{batch_id}/split.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import date
from decimal import Decimal

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Batch, Farm, User
from app.batch_split import split_batch, generate_batch_code, QUANTITY_PRECISION

BASE_URL = "http://127.0.0.1:8000"
AUTH_FARMER = ("farmer", "123456")


def test_code_documentation_and_locking_syntax():
    """Kiểm tra sự hiện diện của SELECT ... FOR UPDATE và tài liệu giải thích race condition."""
    print("\n--- [TEST 1] KIỂM TRA TÀI LIỆU VÀ CÚ PHÁP KHÓA DÒNG TRONG CODE ---")
    with open("app/batch_split.py", "r", encoding="utf-8") as f:
        content = f.read()

    # Kiểm tra giải thích lý do đọc-rồi-ghi thông thường không an toàn
    assert "Race Condition" in content or "Lost Update" in content or "Overselling" in content, (
        "Thiếu tài liệu phân tích hiện tượng Race Condition / Lost Update trong batch_split.py"
    )
    assert "FOR UPDATE" in content, (
        "Thiếu từ khóa FOR UPDATE trong phân tích tài liệu"
    )
    assert "with_for_update" in content, (
        "Thiếu câu lệnh with_for_update() trong mã nguồn split_batch"
    )
    assert "Decimal" in content, (
        "Mã nguồn phải sử dụng Decimal"
    )
    print("  [✓] Đã có tài liệu phân tích chi tiết vì sao đọc-rồi-ghi thông thường gây Overselling/Lost Update.")
    print("  [✓] Đã có lệnh with_for_update() để khóa dòng lô mẹ trước khi kiểm tổng khối lượng.")
    print("[PASS 1] Code documentation & locking syntax đạt 100% yêu cầu!")


def test_ac1_split_exceeding_rejected_immediately():
    """AC 1: Thao tác tách vượt khối lượng còn lại bị từ chối ngay lập tức."""
    print("\n--- [TEST 2] AC 1: TÁCH VƯỢT KHỐI LƯỢNG CÒN LẠI BỊ TỪ CHỐI NGAY ---")
    db: Session = SessionLocal()
    try:
        # Tạo lô mẹ có 500.0000 kg
        parent = Batch(
            farm_id=1,
            product_name="Xoài Cát Chu Thử Nghiệm T-41",
            quantity=Decimal("500.0000"),
            harvest_date=date(2026, 10, 6),
            parent_id=None,
            is_restricted=False,
            owner="farmer",
        )
        db.add(parent)
        db.commit()
        db.refresh(parent)
        parent_id = parent.id

        print(f"  • Đã tạo lô mẹ #{parent_id} với khối lượng ban đầu: {parent.quantity} kg")

        # Thử tách vượt: 300.0000 + 250.0000 = 550.0000 kg > 500.0000 kg
        print("  • Yêu cầu tách [300.0 kg, 250.0 kg] (tổng 550.0 kg > 500.0 kg)...")
        rejected = False
        try:
            split_batch(
                db=db,
                parent_batch_id=parent_id,
                child_quantities=[Decimal("300.0000"), Decimal("250.0000")],
            )
        except ValueError as err:
            rejected = True
            print(f"    -> [BỊ TỪ CHỐI NGAY] Ngoại lệ: {err}")
            assert "vượt quá" in str(err)

        assert rejected is True, "Thao tác tách vượt khối lượng phải bị từ chối ngay!"

        # Xác minh trong database: Lô mẹ không bị trừ, vẫn nguyên 500.0000 kg
        db.refresh(parent)
        assert parent.quantity == Decimal("500.0000"), (
            f"Lô mẹ bị trừ sai! Hiện tại: {parent.quantity}"
        )

        # Xác minh không có lô con nào được tạo
        children = db.scalars(select(Batch).where(Batch.parent_id == parent_id)).all()
        assert len(children) == 0, f"Không được tạo lô con mồ côi nào! Số lượng: {len(children)}"

        # Kiểm tra vượt một lượng rất nhỏ (0.0001 kg): 500.0001 kg > 500.0000 kg
        print("  • Yêu cầu tách 500.0001 kg (vượt đúng 1 đơn vị scale 0.0001)...")
        try:
            split_batch(
                db=db,
                parent_batch_id=parent_id,
                child_quantities=[Decimal("500.0001")],
            )
            assert False, "Phải từ chối khi vượt 0.0001 kg"
        except ValueError as err:
            print(f"    -> [BỊ TỪ CHỐI NGAY] Ngoại lệ: {err}")
            assert "vượt quá" in str(err)

        print("[PASS 2] Tiêu chí AC 1 đạt 100%: Thao tác tách vượt khối lượng bị từ chối ngay lập tức!")
    finally:
        db.close()


def test_ac2_exact_decimal_subtraction_no_negative():
    """AC 2: Trừ khối lượng chính xác tuyệt đối không bị sai lệch số âm bằng Decimal."""
    print("\n--- [TEST 3] AC 2: TRỪ KHỐI LƯỢNG CHÍNH XÁC, KHÔNG SAI LỆCH SỐ ÂM ---")
    db: Session = SessionLocal()
    try:
        # Tạo lô mẹ 1000.0000 kg
        parent = Batch(
            farm_id=2,
            product_name="Sầu Riêng Ri6 Thử Nghiệm Số Học Decimal",
            quantity=Decimal("1000.0000"),
            harvest_date=date(2026, 10, 6),
            parent_id=None,
            is_restricted=False,
            owner="farmer",
        )
        db.add(parent)
        db.commit()
        db.refresh(parent)
        parent_id = parent.id

        # Đợt 1: Tách các số lẻ mà float thường gây lỗi tích luỹ
        # [333.3333, 166.6667, 200.0000] -> Tổng tách đúng 700.0000 kg
        print("  • Đợt 1: Tách [333.3333 kg, 166.6667 kg, 200.0000 kg] (tổng 700.0000 kg)...")
        updated_parent, children_1 = split_batch(
            db=db,
            parent_batch_id=parent_id,
            child_quantities=[Decimal("333.3333"), Decimal("166.6667"), Decimal("200.0000")],
        )

        assert isinstance(updated_parent.quantity, Decimal), "Kiểu dữ liệu phải là Decimal"
        assert updated_parent.quantity == Decimal("300.0000"), (
            f"Khối lượng còn lại phải là 300.0000 kg, nhận được: {updated_parent.quantity}"
        )
        assert len(children_1) == 3
        assert children_1[0].quantity == Decimal("333.3333")
        assert children_1[1].quantity == Decimal("166.6667")
        assert children_1[2].quantity == Decimal("200.0000")
        print(f"    -> Lô mẹ còn lại chính xác: {updated_parent.quantity} kg (kiểu Decimal)")

        # Đợt 2: Tách hết toàn bộ 100% số lượng còn lại (300.0000 kg)
        # [150.0000, 150.0000] -> Tổng tách đúng 300.0000 kg
        print("  • Đợt 2: Tách toàn bộ số còn lại [150.0000 kg, 150.0000 kg] (tổng 300.0000 kg)...")
        updated_parent_2, children_2 = split_batch(
            db=db,
            parent_batch_id=parent_id,
            child_quantities=[Decimal("150.0000"), Decimal("150.0000")],
        )

        assert updated_parent_2.quantity == Decimal("0.0000"), (
            f"Khi tách hết toàn bộ, số dư phải là đúng 0.0000 kg, nhận được: {updated_parent_2.quantity}"
        )
        # Khẳng định không bị số âm (kể cả -0.000000000001 của float)
        assert updated_parent_2.quantity >= Decimal("0.0000"), "Số lượng không được âm"
        print(f"    -> Lô mẹ sau khi tách hết còn đúng: {updated_parent_2.quantity} kg (hoàn toàn không âm)")

        # Đợt 3: Khi lô mẹ đã còn 0.0000 kg, thử tách thêm 0.0001 kg -> Phải từ chối ngay
        print("  • Đợt 3: Thử tách khi mẹ đã hết (còn 0.0000 kg), yêu cầu 0.0001 kg...")
        try:
            split_batch(
                db=db,
                parent_batch_id=parent_id,
                child_quantities=[Decimal("0.0001")],
            )
            assert False, "Không thể tách từ lô đã hết số dư"
        except ValueError as err:
            print(f"    -> [BỊ TỪ CHỐI NGAY] Ngoại lệ: {err}")
            assert "vượt quá" in str(err)

        print("[PASS 3] Tiêu chí AC 2 đạt 100%: Số học Decimal chính xác tuyệt đối, không sai lệch số âm!")
    finally:
        db.close()


def test_atomic_rollback_on_error():
    """Kiểm tra tính nguyên tử: Rollback sạch sẽ khi có lỗi ném ra ở bất kỳ vị trí nào."""
    print("\n--- [TEST 4] KIỂM TRA ROLLBACK NGUYÊN TỬ KHI CÓ LỖI ---")
    db: Session = SessionLocal()
    try:
        parent = Batch(
            farm_id=3,
            product_name="Bưởi Da Xanh Rollback Test",
            quantity=Decimal("800.0000"),
            harvest_date=date(2026, 10, 6),
            parent_id=None,
            is_restricted=False,
            owner="farmer",
        )
        db.add(parent)
        db.commit()
        db.refresh(parent)
        parent_id = parent.id

        print(f"  • Lô mẹ #{parent_id} có {parent.quantity} kg.")
        print("  • Thực hiện tách [300.0 kg, 300.0 kg] với giả lập lỗi tại lô con thứ hai...")

        try:
            split_batch(
                db=db,
                parent_batch_id=parent_id,
                child_quantities=[Decimal("300.0000"), Decimal("300.0000")],
                error_at_child_index=2, # Giả lập lỗi tại lô con 2
            )
            assert False, "Phải ném lỗi tại lô con 2"
        except RuntimeError as err:
            print(f"    -> Đã ném RuntimeError thành công: {err}")

        # Kiểm tra trạng thái trong DB mới độc lập
        verify_db = SessionLocal()
        try:
            parent_check = verify_db.get(Batch, parent_id)
            children_check = verify_db.scalars(
                select(Batch).where(Batch.parent_id == parent_id)
            ).all()

            assert parent_check.quantity == Decimal("800.0000"), (
                f"Lô mẹ phải giữ nguyên 800.0000 kg sau rollback! Hiện tại: {parent_check.quantity}"
            )
            assert len(children_check) == 0, (
                f"Không được còn lô con nào sau rollback! Hiện tại: {len(children_check)}"
            )
            print("  [✓] Rollback hoàn hảo: Lô mẹ nguyên vẹn 800 kg, không có lô con mồ côi.")
        finally:
            verify_db.close()

        print("[PASS 4] Atomic Rollback hoạt động chính xác 100%!")
    finally:
        db.close()


def test_concurrency_race_condition_prevention():
    """Kiểm tra mô phỏng tranh chấp đồng thời: Giao dịch 2 phải thấy số đã trừ và bị từ chối nếu thiếu."""
    print("\n--- [TEST 5] MÔ PHỎNG TRANH CHẤP ĐỒNG THỜI (CONCURRENCY & LOCKING) ---")
    db: Session = SessionLocal()
    try:
        # Lô mẹ ban đầu có 1,000.0000 kg
        parent = Batch(
            farm_id=4,
            product_name="Thanh Long Bình Thuận Concurrency Test",
            quantity=Decimal("1000.0000"),
            harvest_date=date(2026, 10, 6),
            parent_id=None,
            is_restricted=False,
            owner="farmer",
        )
        db.add(parent)
        db.commit()
        db.refresh(parent)
        parent_id = parent.id

        results: dict[str, str] = {}

        def transaction_1():
            # T1: Muốn tách 600.0000 kg
            t1_db = SessionLocal()
            try:
                print("    [T1] Bắt đầu giao dịch 1: Tách 600 kg...")
                split_batch(
                    db=t1_db,
                    parent_batch_id=parent_id,
                    child_quantities=[Decimal("600.0000")],
                )
                results["T1"] = "SUCCESS"
                print("    [T1] Giao dịch 1 thành công!")
            except Exception as e:
                results["T1"] = f"ERROR: {e}"
            finally:
                t1_db.close()

        def transaction_2():
            # T2: Muốn tách 700.0000 kg
            time.sleep(0.05) # Chờ một nhịp nhỏ để T1 bắt đầu trước
            t2_db = SessionLocal()
            try:
                print("    [T2] Bắt đầu giao dịch 2: Tách 700 kg...")
                split_batch(
                    db=t2_db,
                    parent_batch_id=parent_id,
                    child_quantities=[Decimal("700.0000")],
                )
                results["T2"] = "SUCCESS"
                print("    [T2] Giao dịch 2 thành công!")
            except ValueError as e:
                results["T2"] = f"REJECTED: {e}"
                print(f"    [T2] Giao dịch 2 BỊ TỪ CHỐI ĐÚNG NHƯ KỲ VỌNG: {e}")
            except Exception as e:
                results["T2"] = f"ERROR: {e}"
            finally:
                t2_db.close()

        th1 = threading.Thread(target=transaction_1)
        th2 = threading.Thread(target=transaction_2)

        th1.start()
        th2.start()
        th1.join()
        th2.join()

        # Kiểm tra kết quả: T1 thành công (tách 600 kg), T2 bị từ chối vì 700 kg > 400 kg còn lại
        assert results["T1"] == "SUCCESS", f"T1 phải thành công, nhận được: {results.get('T1')}"
        assert results["T2"].startswith("REJECTED"), (
            f"T2 phải bị từ chối do không đủ khối lượng sau khi T1 trừ, nhận được: {results.get('T2')}"
        )

        # Kiểm tra số lượng cuối cùng của lô mẹ
        db.refresh(parent)
        assert parent.quantity == Decimal("400.0000"), (
            f"Khối lượng lô mẹ phải là 400.0000 kg, nhận được: {parent.quantity}"
        )

        print("  [✓] Giao dịch T1 hoàn tất trừ 600 kg. Giao dịch T2 đọc được số đã trừ (400 kg) và bị từ chối ngay!")
        print("[PASS 5] Ngăn chặn thành công 100% Race Condition và Overselling!")
    finally:
        db.close()


def test_api_split_endpoint():
    """Kiểm tra gọi endpoint REST API POST /batches/{id}/split."""
    print("\n--- [TEST 6] KIỂM TRA TÍCH HỢP REST API POST /batches/{id}/split ---")
    client = httpx.Client(base_url=BASE_URL, auth=AUTH_FARMER)

    # 1. Tạo lô mẹ qua API
    create_payload = {
        "farm_id": 1,
        "product_name": "Nhãn Lồng Hưng Yên Tuyển Chọn",
        "quantity": "800.0000",
        "harvest_date": "2026-10-06",
        "is_restricted": False,
    }
    r_create = client.post("/batches", json=create_payload)
    assert r_create.status_code == 201, f"Tạo lô thất bại: {r_create.text}"
    parent_id = r_create.json()["id"]
    print(f"  • Đã tạo lô mẹ qua API: #{parent_id} có 800.0000 kg")

    # 2. Gọi API tách vượt khối lượng (500 + 400 = 900 > 800) -> Phải trả về HTTP 400 Bad Request
    print("  • Gọi API tách [500.0 kg, 400.0 kg] (vượt 800 kg)...")
    r_exceed = client.post(
        f"/batches/{parent_id}/split",
        json={"child_quantities": ["500.0000", "400.0000"], "note": "Test vượt khối lượng"},
    )
    assert r_exceed.status_code == 400, (
        f"API phải trả về 400 Bad Request khi vượt khối lượng! Status: {r_exceed.status_code}, Body: {r_exceed.text}"
    )
    err_detail = r_exceed.json()["detail"]
    assert "vượt quá" in err_detail
    print(f"    -> [HTTP 400] Server từ chối ngay: {err_detail}")

    # 3. Gọi API tách hợp lệ [250.0000, 350.0000] -> Tổng tách 600 kg, còn lại 200 kg
    print("  • Gọi API tách hợp lệ [250.0 kg, 350.0 kg]...")
    r_valid = client.post(
        f"/batches/{parent_id}/split",
        json={"child_quantities": ["250.0000", "350.0000"], "note": "Tách đóng gói VietGAP"},
    )
    assert r_valid.status_code == 201, (
        f"API phải trả về 201 Created khi tách hợp lệ! Status: {r_valid.status_code}, Body: {r_valid.text}"
    )
    res_data = r_valid.json()

    print(f"    -> [HTTP 201] {res_data['message']}")
    print(f"    -> Khối lượng ban đầu: {res_data['initial_quantity']} kg")
    print(f"    -> Tổng tách: {res_data['total_split_quantity']} kg")
    print(f"    -> Khối lượng còn lại của mẹ: {res_data['remaining_quantity']} kg")
    print(f"    -> Số lô con tạo mới: {len(res_data['child_batches'])}")

    assert Decimal(str(res_data["remaining_quantity"])) == Decimal("200.0000")
    assert Decimal(str(res_data["total_split_quantity"])) == Decimal("600.0000")
    assert len(res_data["child_batches"]) == 2

    child1 = res_data["child_batches"][0]
    child2 = res_data["child_batches"][1]
    assert Decimal(str(child1["quantity"])) == Decimal("250.0000")
    assert Decimal(str(child2["quantity"])) == Decimal("350.0000")
    assert child1["parent_id"] == parent_id
    assert child2["parent_id"] == parent_id
    assert child1["product_name"] == "Nhãn Lồng Hưng Yên Tuyển Chọn"
    assert child1["batch_code"].startswith("LOT-")

    print("[PASS 6] REST API POST /batches/{id}/split hoạt động hoàn hảo 100%!")


def main():
    print("=" * 70)
    print("BẮT ĐẦU CHẠY BỘ KIỂM THỬ TOÀN DIỆN CHO TASK T-41 (SCRUM-57)")
    print("Kiểm tổng khối lượng trong giao dịch có khoá dòng lô mẹ")
    print("=" * 70)

    test_code_documentation_and_locking_syntax()
    test_ac1_split_exceeding_rejected_immediately()
    test_ac2_exact_decimal_subtraction_no_negative()
    test_atomic_rollback_on_error()
    test_concurrency_race_condition_prevention()
    test_api_split_endpoint()

    print("\n" + "=" * 70)
    print("🎉 TẤT CẢ TIÊU CHÍ NGHIỆM THU (DoD / AC) CỦA TASK T-41 ĐÃ ĐẠT 100%!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
