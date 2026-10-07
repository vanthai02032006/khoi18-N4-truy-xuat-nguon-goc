"""Bộ kiểm thử độc lập cho 3 kịch bản nghiệm thu (Acceptance Scenarios) của Lệnh thu hồi sản phẩm (S-39 Recall Orders).

Kịch bản 1: Giả sử lô gốc đã đi qua bốn tầng tách và gộp ở ba tổ chức,
            Khi mở lệnh thu hồi, Thì danh sách khớp đáp án đếm tay ở S-39, không sót
Kịch bản 2: Giả sử một hậu duệ là lô gộp có nguồn khác trộn vào,
            Khi liệt kê, Thì lô gộp đó vẫn nằm trong danh sách và được đánh dấu là gộp từ nhiều nguồn
Kịch bản 3: Giả sử có lô mới được tách ra từ một hậu duệ sau khi lệnh đã mở,
            Khi làm mới danh sách, Thì lô mới xuất hiện
"""

import sys
from pathlib import Path
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Thêm thư mục backend vào sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import Base
from app.models import Farm, Batch, BatchLineage, RELATION_SPLIT, RELATION_MERGE
from app.lineage import trace_descendants_bfs
from app.routers.lineage import get_batch_recall


def run_recall_scenarios_tests():
    print("=" * 70)
    print("KIỂM THỬ 3 KỊCH BẢN NGHIỆM THU LỆNH THU HỒI SẢN PHẨM (S-39 RECALL)")
    print("=" * 70)

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionTest = sessionmaker(bind=engine)
    db = SessionTest()

    # 3 tổ chức khác nhau trong chuỗi cung ứng
    farm_a = Farm(name="Nông Trường A", location="Đồng Tháp", area=10.0, owner="Tổ Chức A - HTX Canh Tác")
    farm_b = Farm(name="Nhà Máy B", location="Tiền Giang", area=5.0, owner="Tổ Chức B - Nhà Máy Chế Biến")
    farm_c = Farm(name="Tổng Kho C", location="TP.HCM", area=2.0, owner="Tổ Chức C - Công Ty Phân Phối")
    db.add_all([farm_a, farm_b, farm_c])
    db.commit()

    # Tạo Lô gốc: ROOT-01 (Tổ chức A)
    b_root = Batch(farm_id=farm_a.id, product_name="Xoài Cát Chu Gốc", quantity=1000.0, harvest_date=date(2026, 3, 1), code="ROOT0001")
    db.add(b_root)
    db.commit()

    # -------------------------------------------------------------------------
    # Xây dựng đồ thị 4 tầng tách và gộp ở 3 tổ chức:
    #
    # Tầng 1 (Tại Tổ chức A):
    #   ROOT-01 -> tách thành T1-SPLIT-1 (600kg) và T1-SPLIT-2 (400kg)
    #
    # Tầng 2 (Chuyển sang Tổ chức B):
    #   T1-SPLIT-1 -> tách thành T2-SPLIT-A (300kg) và T2-SPLIT-B (300kg)
    #   T1-SPLIT-2 -> chuyển thành T2-DIRECT (400kg)
    #
    # Tầng 3 (Tại Tổ chức B - Gộp nguồn khác):
    #   Tạo một lô nguồn độc lập: B-EXTERNAL (Nguồn bên ngoài từ Nhà máy B)
    #   Gộp: T2-SPLIT-A (300kg) + B-EXTERNAL (200kg) -> T3-MERGED (500kg) (Gộp từ nhiều nguồn!)
    #
    # Tầng 4 (Chuyển sang Tổ chức C):
    #   T3-MERGED -> tách thành T4-FINAL-1 (250kg) và T4-FINAL-2 (250kg)
    # -------------------------------------------------------------------------

    # Tầng 1
    b_t1_1 = Batch(farm_id=farm_a.id, product_name="Tầng 1 - Tách 1", quantity=600.0, harvest_date=date(2026, 3, 2), code="T1SPLIT1")
    b_t1_2 = Batch(farm_id=farm_a.id, product_name="Tầng 1 - Tách 2", quantity=400.0, harvest_date=date(2026, 3, 2), code="T1SPLIT2")
    db.add_all([b_t1_1, b_t1_2])
    db.commit()

    lin_1_1 = BatchLineage(parent_batch_id=b_root.id, child_batch_id=b_t1_1.id, transferred_quantity=600.0, relation_type=RELATION_SPLIT, created_at="2026-03-02T08:00:00Z")
    lin_1_2 = BatchLineage(parent_batch_id=b_root.id, child_batch_id=b_t1_2.id, transferred_quantity=400.0, relation_type=RELATION_SPLIT, created_at="2026-03-02T08:30:00Z")
    db.add_all([lin_1_1, lin_1_2])

    # Tầng 2 (Tổ chức B)
    b_t2_a = Batch(farm_id=farm_b.id, product_name="Tầng 2 - Tách A", quantity=300.0, harvest_date=date(2026, 3, 3), code="T2SPLITA")
    b_t2_b = Batch(farm_id=farm_b.id, product_name="Tầng 2 - Tách B", quantity=300.0, harvest_date=date(2026, 3, 3), code="T2SPLITB")
    b_t2_dir = Batch(farm_id=farm_b.id, product_name="Tầng 2 - Trực tiếp", quantity=400.0, harvest_date=date(2026, 3, 3), code="T2DIRECT")
    db.add_all([b_t2_a, b_t2_b, b_t2_dir])
    db.commit()

    lin_2_a = BatchLineage(parent_batch_id=b_t1_1.id, child_batch_id=b_t2_a.id, transferred_quantity=300.0, relation_type=RELATION_SPLIT, created_at="2026-03-03T09:00:00Z")
    lin_2_b = BatchLineage(parent_batch_id=b_t1_1.id, child_batch_id=b_t2_b.id, transferred_quantity=300.0, relation_type=RELATION_SPLIT, created_at="2026-03-03T09:15:00Z")
    lin_2_dir = BatchLineage(parent_batch_id=b_t1_2.id, child_batch_id=b_t2_dir.id, transferred_quantity=400.0, relation_type=RELATION_SPLIT, created_at="2026-03-03T09:30:00Z")
    db.add_all([lin_2_a, lin_2_b, lin_2_dir])

    # Tầng 3 (Tổ chức B - Gộp nguồn khác)
    b_ext = Batch(farm_id=farm_b.id, product_name="Lô Bên Ngoài Nhà Máy B", quantity=200.0, harvest_date=date(2026, 3, 4), code="BEXTERNL")
    b_t3_merged = Batch(farm_id=farm_b.id, product_name="Tầng 3 - Lô Gộp Pha Trộn", quantity=500.0, harvest_date=date(2026, 3, 4), code="T3MERGED")
    db.add_all([b_ext, b_t3_merged])
    db.commit()

    lin_3_main = BatchLineage(parent_batch_id=b_t2_a.id, child_batch_id=b_t3_merged.id, transferred_quantity=300.0, relation_type=RELATION_MERGE, created_at="2026-03-04T10:00:00Z")
    lin_3_ext = BatchLineage(parent_batch_id=b_ext.id, child_batch_id=b_t3_merged.id, transferred_quantity=200.0, relation_type=RELATION_MERGE, created_at="2026-03-04T10:00:00Z")
    db.add_all([lin_3_main, lin_3_ext])

    # Tầng 4 (Tổ chức C)
    b_t4_1 = Batch(farm_id=farm_c.id, product_name="Tầng 4 - Thành Phẩm 1", quantity=250.0, harvest_date=date(2026, 3, 5), code="T4FINAL1")
    b_t4_2 = Batch(farm_id=farm_c.id, product_name="Tầng 4 - Thành Phẩm 2", quantity=250.0, harvest_date=date(2026, 3, 5), code="T4FINAL2")
    db.add_all([b_t4_1, b_t4_2])
    db.commit()

    lin_4_1 = BatchLineage(parent_batch_id=b_t3_merged.id, child_batch_id=b_t4_1.id, transferred_quantity=250.0, relation_type=RELATION_SPLIT, created_at="2026-03-05T14:00:00Z")
    lin_4_2 = BatchLineage(parent_batch_id=b_t3_merged.id, child_batch_id=b_t4_2.id, transferred_quantity=250.0, relation_type=RELATION_SPLIT, created_at="2026-03-05T14:30:00Z")
    db.add_all([lin_4_1, lin_4_2])
    db.commit()

    # -------------------------------------------------------------------------
    # KỊCH BẢN 1: Mở lệnh thu hồi -> Khớp đáp án đếm tay ở S-39, không sót
    #
    # Đáp án đếm tay chuẩn xác:
    # Tổng cộng có 8 lô hậu duệ:
    #   Tầng 1: T1SPLIT1, T1SPLIT2 (2 lô)
    #   Tầng 2: T2SPLITA, T2SPLITB, T2DIRECT (3 lô)
    #   Tầng 3: T3MERGED (1 lô)
    #   Tầng 4: T4FINAL1, T4FINAL2 (2 lô)
    # Tổng số = 2 + 3 + 1 + 2 = 8 lô, trải dài qua cả 3 tổ chức A, B, C!
    # -------------------------------------------------------------------------
    print("\n[KỊCH BẢN 1] Lô gốc qua 4 tầng tách/gộp ở 3 tổ chức -> Khớp đáp án đếm tay, không sót...")
    recall_resp = get_batch_recall(identifier=b_root.code, db=db)

    expected_codes_s39 = {
        "T1SPLIT1", "T1SPLIT2",
        "T2SPLITA", "T2SPLITB", "T2DIRECT",
        "T3MERGED",
        "T4FINAL1", "T4FINAL2",
    }
    actual_codes = {item.batch_code for item in recall_resp.items}

    assert actual_codes == expected_codes_s39, f"Lệch đáp án đếm tay! Thực tế: {actual_codes} != Mong đợi: {expected_codes_s39}"
    assert recall_resp.total_affected_batches == 8
    assert recall_resp.total_affected_organizations == 3
    assert "Tổ Chức A - HTX Canh Tác" in recall_resp.affected_organizations
    assert "Tổ Chức B - Nhà Máy Chế Biến" in recall_resp.affected_organizations
    assert "Tổ Chức C - Công Ty Phân Phối" in recall_resp.affected_organizations

    print(f"  -> Tổng số lô thu hồi: {recall_resp.total_affected_batches}/8 lô (Khớp 100% không sót một lô nào).")
    print(f"  -> Các tổ chức bị ảnh hưởng: {recall_resp.affected_organizations}")
    print("  -> PASSED: Kịch bản 1 hoàn thành - Khớp đáp án đếm tay chuẩn S-39.")

    # -------------------------------------------------------------------------
    # KỊCH BẢN 2: Hậu duệ là lô gộp có nguồn khác trộn vào
    # -> Vẫn nằm trong danh sách và được đánh dấu là gộp từ nhiều nguồn
    # -------------------------------------------------------------------------
    print("\n[KỊCH BẢN 2] Hậu duệ là lô gộp có nguồn khác trộn vào -> Vẫn nằm trong DS và được đánh dấu...")
    t3_item = next((item for item in recall_resp.items if item.batch_code == "T3MERGED"), None)
    assert t3_item is not None, "Lô gộp T3MERGED phải nằm trong danh sách thu hồi!"
    assert t3_item.is_merged_multiple_sources is True, "Phải đánh dấu is_merged_multiple_sources = True!"
    assert "BEXTERNL" in t3_item.other_sources, f"Phải phát hiện nguồn bên ngoài BEXTERNL, thực tế: {t3_item.other_sources}"

    print(f"  -> Lô gộp '{t3_item.batch_code}' nằm trong danh sách thu hồi: ĐẠT.")
    print(f"  -> Trạng thái cờ: is_merged_multiple_sources = {t3_item.is_merged_multiple_sources}")
    print(f"  -> Các nguồn bên ngoài được nhận diện tự động: {t3_item.other_sources}")
    print("  -> PASSED: Kịch bản 2 hoàn thành - Đánh dấu chính xác lô gộp từ nhiều nguồn.")

    # -------------------------------------------------------------------------
    # KỊCH BẢN 3: Có lô mới được tách ra từ một hậu duệ sau khi lệnh đã mở
    # -> Khi làm mới danh sách, Thì lô mới xuất hiện
    # -------------------------------------------------------------------------
    print("\n[KỊCH BẢN 3] Có lô mới tách ra từ hậu duệ sau khi lệnh đã mở -> Làm mới danh sách, lô mới xuất hiện...")
    # Tách thêm một lô con mới T4-NEW-3 từ T3-MERGED
    b_t4_new = Batch(farm_id=farm_c.id, product_name="Tầng 4 - Thành Phẩm Mới Tách", quantity=50.0, harvest_date=date(2026, 3, 6), code="T4NEW003")
    db.add(b_t4_new)
    db.commit()

    lin_4_new = BatchLineage(parent_batch_id=b_t3_merged.id, child_batch_id=b_t4_new.id, transferred_quantity=50.0, relation_type=RELATION_SPLIT, created_at="2026-03-06T15:00:00Z")
    db.add(lin_4_new)
    db.commit()

    # Thực hiện làm mới danh sách (Gọi lại API truy vết hậu duệ)
    refreshed_recall = get_batch_recall(identifier=b_root.code, db=db)
    refreshed_codes = {item.batch_code for item in refreshed_recall.items}

    assert "T4NEW003" in refreshed_codes, "Lô mới T4NEW003 phải xuất hiện sau khi làm mới danh sách!"
    assert refreshed_recall.total_affected_batches == 9, f"Tổng số lô phải tăng lên 9, thực tế: {refreshed_recall.total_affected_batches}"

    new_item = next(item for item in refreshed_recall.items if item.batch_code == "T4NEW003")
    assert new_item.level == 4
    assert new_item.quantity == 50.0

    print(f"  -> Đã làm mới lệnh thu hồi thành công.")
    print(f"  -> Lô mới '{new_item.batch_code}' đã xuất hiện tại Tầng {new_item.level} ({new_item.quantity} kg).")
    print(f"  -> Tổng số lô bị ảnh hưởng cập nhật: {refreshed_recall.total_affected_batches} lô.")
    print("  -> PASSED: Kịch bản 3 hoàn thành - Danh sách thu hồi phản ánh động và tức thì.")

    print("\n" + "=" * 70)
    print("KẾT QUẢ: TOÀN BỘ 3 KỊCH BẢN LỆNH THU HỒI ĐẠT 100% TIÊU CHÍ (PASSED)!")
    print("=" * 70)


if __name__ == "__main__":
    run_recall_scenarios_tests()
