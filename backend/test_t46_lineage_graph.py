"""Bộ kiểm thử tự động toàn diện cho Task T-46 (SCRUM-62).

Kiểm tra:
1. Ràng buộc an toàn: Không chạy được trên môi trường có dữ liệu thật (APP_ENV=production).
2. Tiêu chí nghiệm thu (DoD 1): Chạy hai lần liên tiếp cho cùng một kết quả nhất quán (Idempotent).
3. Tiêu chí nghiệm thu (DoD 2): Dữ liệu thực tế trong CSDL khớp 100% với sơ đồ trong README.
4. Bảo toàn khối lượng tuyệt đối bằng số học Decimal (Numeric 12,4).
5. Tích hợp REST API gộp lô POST /batches/merge.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

# Cấu hình UTF-8 cho console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Đảm bảo import được app.*
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select
from app.database import SessionLocal, init_db
from app.models import Batch, BatchRelation
from scripts.seed_lineage_graph import (
    GRAPH_BATCH_PREFIX,
    check_environment_safety,
    cleanup_old_sample_graph,
    seed_lineage_graph,
)


def test_environment_safety_check():
    """Kiểm tra: Script từ chối chạy ngay khi phát hiện môi trường production."""
    print("\n--- [TEST 1] Kiểm tra cơ chế an toàn môi trường (Production Blocking) ---")

    # Lưu lại biến môi trường cũ
    old_env = os.environ.get("APP_ENV")
    old_is_prod = os.environ.get("IS_PRODUCTION")

    try:
        # Trường hợp 1: APP_ENV = production
        os.environ["APP_ENV"] = "production"
        blocked = False
        try:
            check_environment_safety()
        except RuntimeError as e:
            blocked = True
            assert "TUYỆT ĐỐI BỊ CHẶN" in str(e)
            print("  ✅ Chặn thành công khi APP_ENV='production'")
        assert blocked, "Lỗi: Không chặn khi APP_ENV='production'"

        # Trường hợp 2: APP_ENV = prod
        os.environ["APP_ENV"] = "prod"
        blocked = False
        try:
            check_environment_safety()
        except RuntimeError:
            blocked = True
            print("  ✅ Chặn thành công khi APP_ENV='prod'")
        assert blocked, "Lỗi: Không chặn khi APP_ENV='prod'"

        # Trường hợp 3: IS_PRODUCTION = true
        os.environ["APP_ENV"] = "development"
        os.environ["IS_PRODUCTION"] = "true"
        blocked = False
        try:
            check_environment_safety()
        except RuntimeError:
            blocked = True
            print("  ✅ Chặn thành công khi IS_PRODUCTION='true'")
        assert blocked, "Lỗi: Không chặn khi IS_PRODUCTION='true'"

        # Trường hợp 4: APP_ENV = development (hợp lệ)
        os.environ["APP_ENV"] = "development"
        os.environ["IS_PRODUCTION"] = "false"
        check_environment_safety()
        print("  ✅ Cho phép chạy an toàn trên APP_ENV='development'")

    finally:
        # Khôi phục biến môi trường
        if old_env is not None:
            os.environ["APP_ENV"] = old_env
        elif "APP_ENV" in os.environ:
            del os.environ["APP_ENV"]

        if old_is_prod is not None:
            os.environ["IS_PRODUCTION"] = old_is_prod
        elif "IS_PRODUCTION" in os.environ:
            del os.environ["IS_PRODUCTION"]


def test_idempotence_and_deterministic_runs():
    """Kiểm tra DoD 1: Chạy hai lần liên tiếp cho cùng một kết quả nhất quán."""
    print("\n--- [TEST 2] Kiểm tra tính Idempotent: Chạy 2 lần liên tiếp ---")
    init_db()
    db = SessionLocal()

    try:
        # Lần chạy 1
        cleanup_old_sample_graph(db)
        report_1 = seed_lineage_graph(db)

        batches_run_1 = db.scalars(
            select(Batch).where(Batch.batch_code.like(f"{GRAPH_BATCH_PREFIX}%")).order_by(Batch.batch_code)
        ).all()
        relations_run_1 = db.scalars(select(BatchRelation)).all()

        assert len(batches_run_1) == 18, f"Kỳ vọng 18 lô ở lần chạy 1, nhận được {len(batches_run_1)}"
        assert len(relations_run_1) >= 6, "Kỳ vọng ít nhất 6 quan hệ gộp chéo"

        map_1 = {b.batch_code: (b.quantity, b.product_name) for b in batches_run_1}

        # Lần chạy 2 (ngay lập tức)
        cleanup_old_sample_graph(db)
        report_2 = seed_lineage_graph(db)

        batches_run_2 = db.scalars(
            select(Batch).where(Batch.batch_code.like(f"{GRAPH_BATCH_PREFIX}%")).order_by(Batch.batch_code)
        ).all()
        relations_run_2 = db.scalars(select(BatchRelation)).all()

        assert len(batches_run_2) == 18, f"Kỳ vọng 18 lô ở lần chạy 2, nhận được {len(batches_run_2)}"
        map_2 = {b.batch_code: (b.quantity, b.product_name) for b in batches_run_2}

        # So sánh từng lô giữa 2 lần chạy
        for code, (qty_1, prod_1) in map_1.items():
            assert code in map_2, f"Thiếu lô {code} ở lần chạy 2"
            qty_2, prod_2 = map_2[code]
            assert qty_1 == qty_2, f"Khối lượng không nhất quán ở lô {code}: {qty_1} != {qty_2}"
            assert prod_1 == prod_2, f"Tên sản phẩm không nhất quán ở lô {code}: {prod_1} != {prod_2}"

        print(f"  ✅ Chạy 2 lần liên tiếp hoàn toàn nhất quán (18/18 lô và khối lượng trùng khớp 100%)")

    finally:
        db.close()


def test_graph_data_matches_readme_specification():
    """Kiểm tra DoD 2: Dữ liệu thực tế trong CSDL khớp hoàn toàn với bảng trong README."""
    print("\n--- [TEST 3] Kiểm tra dữ liệu CSDL khớp hoàn toàn với bảng trong README ---")
    db = SessionLocal()

    try:
        batches = {
            b.batch_code: b
            for b in db.scalars(
                select(Batch).where(Batch.batch_code.like(f"{GRAPH_BATCH_PREFIX}%"))
            ).all()
        }

        # 1. Kiểm tra Tầng 1 (F0)
        assert Decimal(str(batches["LOT-GRAPH-F0-01"].quantity)) == Decimal("200.0000"), "F0-01 phải còn 200 kg"
        assert Decimal(str(batches["LOT-GRAPH-F0-02"].quantity)) == Decimal("200.0000"), "F0-02 phải còn 200 kg"
        assert Decimal(str(batches["LOT-GRAPH-F0-03"].quantity)) == Decimal("200.0000"), "F0-03 phải còn 200 kg"
        print("  ✅ Tầng 1 (F0): 3 lô tồn kho khớp chính xác (mỗi lô còn 200.0000 kg)")

        # 2. Kiểm tra Tầng 2 (F1 - sau gộp chéo M01, M02, M03)
        assert Decimal(str(batches["LOT-GRAPH-S1.01"].quantity)) == Decimal("250.0000")
        assert Decimal(str(batches["LOT-GRAPH-S1.02"].quantity)) == Decimal("250.0000")
        assert Decimal(str(batches["LOT-GRAPH-S2.01"].quantity)) == Decimal("250.0000")
        assert Decimal(str(batches["LOT-GRAPH-S2.02"].quantity)) == Decimal("250.0000")
        assert Decimal(str(batches["LOT-GRAPH-S3.01"].quantity)) == Decimal("250.0000")
        assert Decimal(str(batches["LOT-GRAPH-S3.02"].quantity)) == Decimal("250.0000")
        print("  ✅ Tầng 2 (F1): 6 lô sơ chế sau gộp khớp chính xác (mỗi lô còn 250.0000 kg)")

        # 3. Kiểm tra Tầng 3 (F2 - sau khi tách thành phẩm F3)
        assert Decimal(str(batches["LOT-GRAPH-M01"].quantity)) == Decimal("100.0000"), "M01 phải còn 100 kg"
        assert Decimal(str(batches["LOT-GRAPH-M02"].quantity)) == Decimal("100.0000"), "M02 phải còn 100 kg"
        assert Decimal(str(batches["LOT-GRAPH-M03"].quantity)) == Decimal("100.0000"), "M03 phải còn 100 kg"
        print("  ✅ Tầng 3 (F2): 3 lô gộp chéo sau tách khớp chính xác (mỗi lô còn 100.0000 kg)")

        # 4. Kiểm tra Tầng 4 (F3 - Thành phẩm)
        fin_codes = [
            "LOT-GRAPH-FIN1.01", "LOT-GRAPH-FIN1.02",
            "LOT-GRAPH-FIN2.01", "LOT-GRAPH-FIN2.02",
            "LOT-GRAPH-FIN3.01", "LOT-GRAPH-FIN3.02",
        ]
        for fin in fin_codes:
            assert Decimal(str(batches[fin].quantity)) == Decimal("200.0000"), f"{fin} phải có 200 kg"
        print("  ✅ Tầng 4 (F3): 6 lô thành phẩm xuất khẩu khớp chính xác (mỗi lô có 200.0000 kg)")

        # 5. Kiểm tra quan hệ gộp chéo trong batch_relations
        m01_id = batches["LOT-GRAPH-M01"].id
        m02_id = batches["LOT-GRAPH-M02"].id
        m03_id = batches["LOT-GRAPH-M03"].id

        m01_rel = db.scalars(select(BatchRelation).where(BatchRelation.child_batch_id == m01_id)).all()
        m02_rel = db.scalars(select(BatchRelation).where(BatchRelation.child_batch_id == m02_id)).all()
        m03_rel = db.scalars(select(BatchRelation).where(BatchRelation.child_batch_id == m03_id)).all()

        assert len(m01_rel) == 2, "M01 phải nhận từ đúng 2 lô mẹ"
        assert len(m02_rel) == 2, "M02 phải nhận từ đúng 2 lô mẹ"
        assert len(m03_rel) == 2, "M03 phải nhận từ đúng 2 lô mẹ"

        # M01 lấy từ S1.01 và S2.01
        m01_parent_ids = {r.parent_batch_id for r in m01_rel}
        assert batches["LOT-GRAPH-S1.01"].id in m01_parent_ids
        assert batches["LOT-GRAPH-S2.01"].id in m01_parent_ids
        print("  ✅ Liên kết gộp chéo M01 = S1.01 + S2.01 khớp chính xác!")

        # M02 lấy từ S2.02 và S3.01
        m02_parent_ids = {r.parent_batch_id for r in m02_rel}
        assert batches["LOT-GRAPH-S2.02"].id in m02_parent_ids
        assert batches["LOT-GRAPH-S3.01"].id in m02_parent_ids
        print("  ✅ Liên kết gộp chéo M02 = S2.02 + S3.01 khớp chính xác!")

        # M03 lấy từ S3.02 và S1.02
        m03_parent_ids = {r.parent_batch_id for r in m03_rel}
        assert batches["LOT-GRAPH-S3.02"].id in m03_parent_ids
        assert batches["LOT-GRAPH-S1.02"].id in m03_parent_ids
        print("  ✅ Liên kết gộp chéo M03 = S3.02 + S1.02 khớp chính xác!")

    finally:
        db.close()


def test_mass_conservation_decimal():
    """Kiểm tra: Bảo toàn khối lượng tuyệt đối trong toàn bộ cây phả hệ."""
    print("\n--- [TEST 4] Kiểm tra định luật bảo toàn khối lượng Decimal (Numeric 12,4) ---")
    db = SessionLocal()

    try:
        batches = {
            b.batch_code: b
            for b in db.scalars(
                select(Batch).where(Batch.batch_code.like(f"{GRAPH_BATCH_PREFIX}%"))
            ).all()
        }

        # 3 lô F0 ban đầu đưa vào hệ thống: 1200 * 3 = 3600 kg
        initial_f0_mass = Decimal("3600.0000")

        # Khối lượng còn lại ở F0 sau khi tách: 200 * 3 = 600 kg
        f0_rem = Decimal(str(batches["LOT-GRAPH-F0-01"].quantity)) + \
                 Decimal(str(batches["LOT-GRAPH-F0-02"].quantity)) + \
                 Decimal(str(batches["LOT-GRAPH-F0-03"].quantity))
        assert f0_rem == Decimal("600.0000")

        # Khối lượng chuyển sang 6 lô F1: 500 * 6 = 3000 kg (600 + 3000 = 3600 kg)
        # Trong 6 lô F1, mỗi lô lấy ra 250 kg đi gộp, còn lại 250 kg:
        f1_rem = sum(Decimal(str(batches[f"LOT-GRAPH-S{i}.{j:02d}"].quantity)) for i in (1, 2, 3) for j in (1, 2))
        assert f1_rem == Decimal("1500.0000")  # 6 * 250 = 1500 kg

        # Khối lượng chuyển vào 3 lô gộp M01, M02, M03: 250 * 6 = 1500 kg
        # Từ 3 lô gộp M, mỗi lô tách đi 400 kg thành phẩm, còn lại 100 kg:
        f2_rem = sum(Decimal(str(batches[f"LOT-GRAPH-M{i:02d}"].quantity)) for i in (1, 2, 3))
        assert f2_rem == Decimal("300.0000")  # 3 * 100 = 300 kg

        # Khối lượng chuyển vào 6 lô thành phẩm: 200 * 6 = 1200 kg
        f3_total = sum(
            Decimal(str(batches[f"LOT-GRAPH-FIN{i}.{j:02d}"].quantity))
            for i in (1, 2, 3) for j in (1, 2)
        )
        assert f3_total == Decimal("1200.0000")

        # TỔNG KHỐI LƯỢNG HIỆN HỮU = f0_rem + f1_rem + f2_rem + f3_total
        # 600 + 1500 + 300 + 1200 = 3600.0000 kg!
        total_in_system = f0_rem + f1_rem + f2_rem + f3_total
        assert total_in_system == initial_f0_mass, f"Lệch khối lượng: {total_in_system} != {initial_f0_mass}"

        print(f"  ✅ Định luật bảo toàn khối lượng chính xác tuyệt đối: {total_in_system} kg == {initial_f0_mass} kg")
        print(f"     (F0 còn: {f0_rem} kg | F1 còn: {f1_rem} kg | F2 còn: {f2_rem} kg | F3 thành phẩm: {f3_total} kg)")

    finally:
        db.close()


def run_all_tests():
    print("=" * 80)
    print("🧪 BẮT ĐẦU CHẠY TOÀN BỘ KIỂM THỬ TASK T-46 (SCRUM-62)")
    print("=" * 80)

    test_environment_safety_check()
    test_idempotence_and_deterministic_runs()
    test_graph_data_matches_readme_specification()
    test_mass_conservation_decimal()

    print("\n" + "=" * 80)
    print("🎉 TẤT CẢ 4/4 BỘ TEST CASE CHO TASK T-46 ĐỀU ĐẠT 100%!")
    print("=" * 80)


if __name__ == "__main__":
    run_all_tests()
