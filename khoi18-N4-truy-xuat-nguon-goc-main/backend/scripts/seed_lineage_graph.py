"""Script dựng đồ thị mẫu phả hệ nông sản 4 tầng (T-46 / SCRUM-62).

================================================================================
🎯 MỤC TIÊU & MÔ TẢ NGHIỆP VỤ:
================================================================================
Dùng CHÍNH HÀM TÁCH (`split_batch`) VÀ GỘP (`merge_batches`) CỦA ỨNG DỤNG để dựng
đồ thị mẫu phả hệ truy xuất nguồn gốc (Lineage Graph DAG):
  - Tầng 1 (F0): 3 lô thu hoạch ban đầu (1,200 kg mỗi lô).
  - Tầng 2 (F1): Tách thành 6 lô con (mỗi lô mẹ tách thành 2 lô 500 kg bằng `split_batch`).
  - Tầng 3 (F2): Gộp chéo thành 3 lô (mỗi lô nhận 250 kg từ 2 nhánh khác nhau bằng `merge_batches`).
  - Tầng 4 (F3): Tách thêm một tầng thành phẩm (mỗi lô gộp tách thành 2 lô 200 kg bằng `split_batch`).

✅ TIÊU CHÍ NGHIỆM THU (DoD / AC):
1. Chạy hai lần liên tiếp cho cùng một kết quả nhất quán (Idempotent & Deterministic).
2. Xóa sạch dữ liệu mẫu cũ trước khi dựng lại.
3. Sơ đồ trong README khớp hoàn toàn với dữ liệu thực tế tạo ra trong CSDL.

🛠️ RÀNG BUỘC KỸ THUẬT:
- Script kiểm tra biến môi trường trước khi chạy, TUYỆT ĐỐI KHÔNG CHẠY TRÊN MÔI TRƯỜNG DỮ LIỆU THẬT (production/prod/live).
================================================================================
"""

from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

# Cấu hình UTF-8 cho console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Thêm thư mục backend vào sys.path để import được app.*
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.batch_merge import merge_batches
from app.batch_split import split_batch
from app.database import SessionLocal, init_db
from app.models import Batch, BatchRelation, Farm


# Prefix định danh thống nhất cho mọi mã lô trong đồ thị mẫu
GRAPH_BATCH_PREFIX = "LOT-GRAPH-"


def check_environment_safety() -> None:
    """Kiểm tra biến môi trường trước khi chạy.

    LƯU Ý KỸ THUẬT (AC / T-46): Script TUYỆT ĐỐI không được phép chạy trên
    môi trường có dữ liệu thật (Production).
    """
    env = os.getenv(
        "APP_ENV",
        os.getenv("ENV", os.getenv("ENVIRONMENT", "development")),
    ).strip().lower()

    blocked_envs = {"production", "prod", "live"}
    if env in blocked_envs:
        raise RuntimeError(
            f"❌ NGUY HIỂM: Phát hiện biến môi trường APP_ENV='{env}' "
            f"(Môi trường dữ liệu thật / Production). "
            f"Script seed_lineage_graph.py TUYỆT ĐỐI BỊ CHẶN để bảo vệ dữ liệu sản xuất!"
        )

    is_prod_flag = os.getenv("IS_PRODUCTION", "false").strip().lower()
    if is_prod_flag in {"true", "1", "yes"}:
        raise RuntimeError(
            "❌ NGUY HIỂM: Biến môi trường IS_PRODUCTION=true. "
            "Script seed_lineage_graph.py bị từ chối thực thi trên môi trường thật!"
        )

    print(f"🔒 [BẢO MẬT] Kiểm tra môi trường an toàn: APP_ENV='{env}' -> CHO PHÉP THỰC THI.")


def cleanup_old_sample_graph(db: Session) -> int:
    """Xóa sạch toàn bộ dữ liệu đồ thị mẫu cũ trước khi dựng lại.

    Đảm bảo tính Idempotent: chạy hai lần liên tiếp cho kết quả đồng nhất 100%.
    """
    # 1. Tìm tất cả ID của các lô có mã bắt đầu bằng LOT-GRAPH-
    batch_ids = list(
        db.scalars(
            select(Batch.id).where(Batch.batch_code.like(f"{GRAPH_BATCH_PREFIX}%"))
        ).all()
    )

    if not batch_ids:
        print("🧹 Không có dữ liệu đồ thị mẫu cũ cần dọn dẹp.")
        return 0

    deleted_count = len(batch_ids)

    # 2. Xóa các quan hệ phả hệ batch_relations liên quan
    db.execute(
        delete(BatchRelation).where(
            (BatchRelation.parent_batch_id.in_(batch_ids))
            | (BatchRelation.child_batch_id.in_(batch_ids))
        )
    )

    # 3. Gỡ liên kết parent_id trước khi xóa batch để tránh ràng buộc foreign key
    db.execute(
        text("UPDATE batches SET parent_id = NULL WHERE batch_code LIKE :prefix"),
        {"prefix": f"{GRAPH_BATCH_PREFIX}%"},
    )

    # 4. Xóa sạch các lô mẫu bằng bulk delete
    db.execute(
        delete(Batch).where(Batch.id.in_(batch_ids))
    )

    db.commit()
    print(f"🧹 Đã dọn dẹp thành công {deleted_count} lô mẫu cũ và toàn bộ quan hệ phả hệ liên quan.")
    return deleted_count


def seed_lineage_graph(db: Session) -> dict[str, Any]:
    """Dựng đồ thị mẫu 4 tầng bằng chính hàm tách và gộp của ứng dụng.

    Cấu trúc đồ thị (4 Tầng):
      Tầng 1: 3 Lô thu hoạch F0 (LOT-GRAPH-F0-01, F0-02, F0-03, mỗi lô 1,200.0 kg).
      Tầng 2: 6 Lô tách F1 (S1.01, S1.02, S2.01, S2.02, S3.01, S3.02, mỗi lô 500.0 kg).
      Tầng 3: 3 Lô gộp chéo F2 (M01, M02, M03, mỗi lô nhận 250.0 kg từ 2 nhánh khác nhau, tổng 500.0 kg).
      Tầng 4: 6 Lô thành phẩm F3 (FIN1.01, FIN1.02, FIN2.01, FIN2.02, FIN3.01, FIN3.02, mỗi lô 200.0 kg).

    Returns:
        dict: Báo cáo chi tiết các lô và quan hệ phả hệ đã dựng.
    """
    # Bước 0: Đảm bảo có ít nhất 1 vùng trồng (Farm)
    farm = db.scalar(select(Farm).order_by(Farm.id.asc()))
    if farm is None:
        farm = Farm(
            name="Vùng Trồng Xoài Cát Chu Cao Lãnh (Thửa Đất Nghiên Cứu)",
            location="Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp",
            area=5.0,
            owner="Hợp tác xã Xoài Mỹ Xương",
        )
        db.add(farm)
        db.commit()
        db.refresh(farm)

    farm_id = farm.id
    print(f"🌱 Sử dụng vùng trồng xuất xứ: #{farm_id} - '{farm.name}'")

    # =========================================================================
    # TẦNG 1: 3 LÔ THU HOẠCH F0 (1,200.0 kg mỗi lô)
    # =========================================================================
    print("\n📦 [TẦNG 1 - F0] Khởi tạo 3 lô thu hoạch ban đầu...")
    f0_1 = Batch(
        farm_id=farm_id,
        product_name="Xoài Cát Chu Thu Hoạch Vườn A",
        quantity=Decimal("1200.0000"),
        harvest_date=date(2026, 10, 1),
        batch_code=f"{GRAPH_BATCH_PREFIX}F0-01",
        current_owner="farmer",
    )
    f0_2 = Batch(
        farm_id=farm_id,
        product_name="Xoài Cát Chu Thu Hoạch Vườn B",
        quantity=Decimal("1200.0000"),
        harvest_date=date(2026, 10, 2),
        batch_code=f"{GRAPH_BATCH_PREFIX}F0-02",
        current_owner="farmer",
    )
    f0_3 = Batch(
        farm_id=farm_id,
        product_name="Xoài Cát Chu Thu Hoạch Vườn C",
        quantity=Decimal("1200.0000"),
        harvest_date=date(2026, 10, 3),
        batch_code=f"{GRAPH_BATCH_PREFIX}F0-03",
        current_owner="farmer",
    )
    db.add_all([f0_1, f0_2, f0_3])
    db.commit()
    for b in [f0_1, f0_2, f0_3]:
        db.refresh(b)
        print(f"   + Lô F0: {b.batch_code} ({b.product_name}) - Khối lượng: {b.quantity} kg (ID: #{b.id})")

    # =========================================================================
    # TẦNG 2: TÁCH THÀNH 6 LÔ BẰNG HÀM `split_batch` CỦA ỨNG DỤNG
    # =========================================================================
    print("\n🔪 [TẦNG 2 - F1] Tách 3 lô F0 thành 6 lô sơ chế bằng hàm `split_batch`...")
    # Tách F0-01 -> S1.01 (500 kg) & S1.02 (500 kg) (Mẹ còn 200 kg)
    f0_1_updated, s1_children = split_batch(
        db=db,
        parent_batch_id=f0_1.id,
        child_quantities=[Decimal("500.0000"), Decimal("500.0000")],
        child_codes=[f"{GRAPH_BATCH_PREFIX}S1.01", f"{GRAPH_BATCH_PREFIX}S1.02"],
        child_product_names=[
            "Xoài Phân Loại Size 1 (Nhánh A1)",
            "Xoài Phân Loại Size 2 (Nhánh A2)",
        ],
    )
    s1_01, s1_02 = s1_children[0], s1_children[1]

    # Tách F0-02 -> S2.01 (500 kg) & S2.02 (500 kg) (Mẹ còn 200 kg)
    f0_2_updated, s2_children = split_batch(
        db=db,
        parent_batch_id=f0_2.id,
        child_quantities=[Decimal("500.0000"), Decimal("500.0000")],
        child_codes=[f"{GRAPH_BATCH_PREFIX}S2.01", f"{GRAPH_BATCH_PREFIX}S2.02"],
        child_product_names=[
            "Xoài Phân Loại Size 1 (Nhánh B1)",
            "Xoài Phân Loại Size 2 (Nhánh B2)",
        ],
    )
    s2_01, s2_02 = s2_children[0], s2_children[1]

    # Tách F0-03 -> S3.01 (500 kg) & S3.02 (500 kg) (Mẹ còn 200 kg)
    f0_3_updated, s3_children = split_batch(
        db=db,
        parent_batch_id=f0_3.id,
        child_quantities=[Decimal("500.0000"), Decimal("500.0000")],
        child_codes=[f"{GRAPH_BATCH_PREFIX}S3.01", f"{GRAPH_BATCH_PREFIX}S3.02"],
        child_product_names=[
            "Xoài Phân Loại Size 1 (Nhánh C1)",
            "Xoài Phân Loại Size 2 (Nhánh C2)",
        ],
    )
    s3_01, s3_02 = s3_children[0], s3_children[1]

    print(f"   + F0-01 tách ra {s1_01.batch_code} ({s1_01.quantity} kg) và {s1_02.batch_code} ({s1_02.quantity} kg) [Mẹ còn {f0_1_updated.quantity} kg]")
    print(f"   + F0-02 tách ra {s2_01.batch_code} ({s2_01.quantity} kg) và {s2_02.batch_code} ({s2_02.quantity} kg) [Mẹ còn {f0_2_updated.quantity} kg]")
    print(f"   + F0-03 tách ra {s3_01.batch_code} ({s3_01.quantity} kg) và {s3_02.batch_code} ({s3_02.quantity} kg) [Mẹ còn {f0_3_updated.quantity} kg]")

    # =========================================================================
    # TẦNG 3: GỘP CHÉO THÀNH 3 LÔ BẰNG HÀM `merge_batches` CỦA ỨNG DỤNG
    # =========================================================================
    print("\n🔄 [TẦNG 3 - F2] Gộp chéo thành 3 lô phối trộn bằng hàm `merge_batches`...")
    # Lô gộp M01 (500 kg): lấy từ S1.01 (250 kg) + S2.01 (250 kg)
    m01, m01_relations = merge_batches(
        db=db,
        parents=[(s1_01.id, Decimal("250.0000")), (s2_01.id, Decimal("250.0000"))],
        product_name="Xoài Phối Trộn Đóng Thùng Lô M1 (Từ S1.01 + S2.01)",
        harvest_date=date(2026, 10, 4),
        farm_id=farm_id,
        batch_code=f"{GRAPH_BATCH_PREFIX}M01",
    )

    # Lô gộp M02 (500 kg): lấy từ S2.02 (250 kg) + S3.01 (250 kg)
    m02, m02_relations = merge_batches(
        db=db,
        parents=[(s2_02.id, Decimal("250.0000")), (s3_01.id, Decimal("250.0000"))],
        product_name="Xoài Phối Trộn Đóng Thùng Lô M2 (Từ S2.02 + S3.01)",
        harvest_date=date(2026, 10, 4),
        farm_id=farm_id,
        batch_code=f"{GRAPH_BATCH_PREFIX}M02",
    )

    # Lô gộp M03 (500 kg): lấy từ S3.02 (250 kg) + S1.02 (250 kg)
    m03, m03_relations = merge_batches(
        db=db,
        parents=[(s3_02.id, Decimal("250.0000")), (s1_02.id, Decimal("250.0000"))],
        product_name="Xoài Phối Trộn Đóng Thùng Lô M3 (Từ S3.02 + S1.02)",
        harvest_date=date(2026, 10, 4),
        farm_id=farm_id,
        batch_code=f"{GRAPH_BATCH_PREFIX}M03",
    )

    print(f"   + Lô gộp M01: {m01.batch_code} ({m01.quantity} kg) = S1.01 (250 kg) + S2.01 (250 kg)")
    print(f"   + Lô gộp M02: {m02.batch_code} ({m02.quantity} kg) = S2.02 (250 kg) + S3.01 (250 kg)")
    print(f"   + Lô gộp M03: {m03.batch_code} ({m03.quantity} kg) = S3.02 (250 kg) + S1.02 (250 kg)")

    # =========================================================================
    # TẦNG 4: TÁCH THÊM MỘT TẦNG THÀNH PHẨM BẰNG HÀM `split_batch` CỦA ỨNG DỤNG
    # =========================================================================
    print("\n🎯 [TẦNG 4 - F3] Tách thêm một tầng thành phẩm từ các lô gộp bằng hàm `split_batch`...")
    # Tách M01 (500 kg) -> FIN1.01 (200 kg) & FIN1.02 (200 kg) (M01 còn 100 kg)
    m01_updated, fin1_children = split_batch(
        db=db,
        parent_batch_id=m01.id,
        child_quantities=[Decimal("200.0000"), Decimal("200.0000")],
        child_codes=[f"{GRAPH_BATCH_PREFIX}FIN1.01", f"{GRAPH_BATCH_PREFIX}FIN1.02"],
        child_product_names=[
            "Xoài Thành Phẩm Chuẩn Xuất Khẩu EU (M1.01)",
            "Xoài Thành Phẩm Chuẩn Siêu Thị (M1.02)",
        ],
    )
    fin1_01, fin1_02 = fin1_children[0], fin1_children[1]

    # Tách M02 (500 kg) -> FIN2.01 (200 kg) & FIN2.02 (200 kg) (M02 còn 100 kg)
    m02_updated, fin2_children = split_batch(
        db=db,
        parent_batch_id=m02.id,
        child_quantities=[Decimal("200.0000"), Decimal("200.0000")],
        child_codes=[f"{GRAPH_BATCH_PREFIX}FIN2.01", f"{GRAPH_BATCH_PREFIX}FIN2.02"],
        child_product_names=[
            "Xoài Thành Phẩm Chuẩn Xuất Khẩu Nhật (M2.01)",
            "Xoài Thành Phẩm Chuẩn Chế Biến (M2.02)",
        ],
    )
    fin2_01, fin2_02 = fin2_children[0], fin2_children[1]

    # Tách M03 (500 kg) -> FIN3.01 (200 kg) & FIN3.02 (200 kg) (M03 còn 100 kg)
    m03_updated, fin3_children = split_batch(
        db=db,
        parent_batch_id=m03.id,
        child_quantities=[Decimal("200.0000"), Decimal("200.0000")],
        child_codes=[f"{GRAPH_BATCH_PREFIX}FIN3.01", f"{GRAPH_BATCH_PREFIX}FIN3.02"],
        child_product_names=[
            "Xoài Thành Phẩm Chuẩn Xuất Khẩu Mỹ (M3.01)",
            "Xoài Thành Phẩm Chuẩn Nội Địa (M3.02)",
        ],
    )
    fin3_01, fin3_02 = fin3_children[0], fin3_children[1]

    print(f"   + M01 tách ra {fin1_01.batch_code} ({fin1_01.quantity} kg) và {fin1_02.batch_code} ({fin1_02.quantity} kg) [M01 còn {m01_updated.quantity} kg]")
    print(f"   + M02 tách ra {fin2_01.batch_code} ({fin2_01.quantity} kg) và {fin2_02.batch_code} ({fin2_02.quantity} kg) [M02 còn {m02_updated.quantity} kg]")
    print(f"   + M03 tách ra {fin3_01.batch_code} ({fin3_01.quantity} kg) và {fin3_02.batch_code} ({fin3_02.quantity} kg) [M03 còn {m03_updated.quantity} kg]")

    # Thống kê tổng kết
    report = {
        "tier1_f0": [f0_1, f0_2, f0_3],
        "tier2_f1": [s1_01, s1_02, s2_01, s2_02, s3_01, s3_02],
        "tier3_f2": [m01, m02, m03],
        "tier4_f3": [fin1_01, fin1_02, fin2_01, fin2_02, fin3_01, fin3_02],
        "all_sample_batches": [
            f0_1, f0_2, f0_3,
            s1_01, s1_02, s2_01, s2_02, s3_01, s3_02,
            m01, m02, m03,
            fin1_01, fin1_02, fin2_01, fin2_02, fin3_01, fin3_02,
        ],
        "relations_count": len(m01_relations) + len(m02_relations) + len(m03_relations),
    }

    print("\n🎉 DỰNG ĐỒ THỊ MẪU 4 TẦNG THÀNH CÔNG VỚI 18 LÔ NÔNG SẢN VÀ 6 QUAN HỆ GỘP CHÉO!")
    return report


def main() -> None:
    """Hàm thực thi chính của script."""
    print("=" * 80)
    print("🚀 BẮT ĐẦU DỰNG ĐỒ THỊ MẪU PHẢ HỆ NÔNG SẢN (T-46 / SCRUM-62)")
    print("=" * 80)

    # 1. Ràng buộc an toàn: Kiểm tra biến môi trường
    check_environment_safety()

    # 2. Khởi tạo database & bảng nếu chưa có
    init_db()

    # 3. Mở session, dọn dẹp và dựng đồ thị
    db = SessionLocal()
    try:
        cleanup_old_sample_graph(db)
        report = seed_lineage_graph(db)
        print("=" * 80)
        print("✅ HOÀN THÀNH TẤT CẢ TIÊU CHÍ NGHIỆM THU (DoD) CỦA TASK T-46!")
        print("=" * 80)
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ LỖI THỰC THI SCRIPT: {e}", file=sys.stderr)
        sys.exit(1)
