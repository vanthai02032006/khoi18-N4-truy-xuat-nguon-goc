"""Script chạy độc lập kiểm thử DoD Task T-18 (SCRUM-34) không cần cài đặt pytest.

Cách chạy:
    cd backend
    python run_tests.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

# Thêm thư mục backend vào sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.code_generator import (
    CodeCollisionError,
    execute_with_unique_retry,
    generate_code,
    generate_unique_code,
)
from app.config import (
    CODE_ALPHABET,
    CODE_CONFIG,
    CODE_LENGTH,
    FORBIDDEN_CHARACTERS,
    MAX_CODE_RETRIES,
)


def run_all_tests():
    print("=" * 70)
    print("KIỂM THỬ CHẤP NHẬN (DoD) - TASK T-18 (SCRUM-34): HÀM SINH MÃ")
    print("=" * 70)

    # 1. Kiểm tra cấu hình duy nhất
    print("\n[TEST 1] Kiểm tra khai báo cấu hình bảng ký tự và độ dài duy nhất...")
    assert CODE_CONFIG["length"] == 8
    assert CODE_CONFIG["alphabet"] == CODE_ALPHABET
    assert CODE_CONFIG["forbidden_chars"] == FORBIDDEN_CHARACTERS
    for forbidden in FORBIDDEN_CHARACTERS:
        assert forbidden not in CODE_ALPHABET
    print(f"  -> Bảng ký tự ({len(CODE_ALPHABET)} ký tự): {CODE_ALPHABET}")
    print(f"  -> Ký tự cấm: {sorted(list(FORBIDDEN_CHARACTERS))}")
    print("  -> PASSED: Cấu hình chuẩn xác, không chứa ký tự cấm.")

    # 2. Sinh 10.000 mã trong 1 vòng lặp khẳng định 100% không trùng & không chứa ký tự cấm
    print("\n[TEST 2] Sinh 10.000 mã trong 1 vòng lặp kiểm tra 100% không trùng và không chứa ký tự cấm...")
    total = 10_000
    generated_list = []
    seen = set()

    for i in range(total):
        code = generate_code()
        generated_list.append(code)
        seen.add(code)

        assert len(code) == 8, f"Mã '{code}' không đủ 8 ký tự."
        for c in code:
            assert c not in FORBIDDEN_CHARACTERS, f"Mã '{code}' chứa ký tự cấm '{c}'"

    assert len(generated_list) == total
    assert len(seen) == total, f"Trùng mã: Chỉ có {len(seen)}/{total} mã duy nhất."
    print(f"  -> Đã sinh: {total:,} mã thành công.")
    print(f"  -> Số mã phân biệt: {len(seen):,} mã (Tỉ lệ duy nhất: 100%).")
    print(f"  -> Kiểm tra ký tự cấm {sorted(list(FORBIDDEN_CHARACTERS))}: 0 vi phạm.")
    print("  -> Mẫu 5 mã đầu tiên:", generated_list[:5])
    print("  -> PASSED: DoD 1 & DoD 2 đạt 100%.")

    # 3. Nguồn ngẫu nhiên an toàn (secrets)
    print("\n[TEST 3] Kiểm tra nguồn ngẫu nhiên an toàn (CSPRNG secrets)...")
    with patch("secrets.choice", side_effect=lambda a: a[0]) as mock_sec:
        c = generate_code()
        assert mock_sec.called
        assert c == CODE_ALPHABET[0] * 8
    print("  -> PASSED: Module secrets được sử dụng cho mọi ký tự.")

    # 4. Kiểm tra bắt trùng và tự động sinh lại
    print("\n[TEST 4] Kiểm tra phát hiện trùng mã và tự động sinh lại (Retry)...")
    retry_count = 0

    def mock_exists(candidate: str) -> bool:
        nonlocal retry_count
        retry_count += 1
        return retry_count < 3  # 2 lần đầu trùng, lần 3 hợp lệ

    res_code = generate_unique_code(is_exists_fn=mock_exists, max_retries=5)
    assert len(res_code) == 8
    assert retry_count == 3
    print(f"  -> Thử lại thành công sau 2 lần trùng, mã sinh được: {res_code}")
    print("  -> PASSED: Cơ chế retry hoạt động chính xác.")

    # 5. Kiểm tra ném ngoại lệ khi vượt quá số lần thử
    print("\n[TEST 5] Kiểm tra ném ngoại lệ CodeCollisionError khi quá max_retries...")
    try:
        generate_unique_code(is_exists_fn=lambda _: True, max_retries=3)
        assert False, "Đáng lẽ phải ném CodeCollisionError"
    except CodeCollisionError as e:
        print(f"  -> Đã bắt ngoại lệ dự kiến: {e}")
        print("  -> PASSED: Đã xử lý giới hạn số lần thử an toàn.")

    # 6. Kiểm tra bắt lỗi Unique Constraint của DB (SQLAlchemy / SQLite)
    print("\n[TEST 6] Kiểm tra bắt lỗi ràng buộc UNIQUE từ SQLAlchemy và sinh lại...")
    from sqlalchemy import Column, Integer, String, create_engine
    from sqlalchemy.orm import declarative_base, sessionmaker

    Base = declarative_base()

    class TestBatch(Base):
        __tablename__ = "test_batches"
        id = Column(Integer, primary_key=True)
        code = Column(String(8), unique=True, nullable=False)

    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    session = Session()

    session.add(TestBatch(code="CODE1234"))
    session.commit()

    simulated = iter(["CODE1234", "CODE9999"])
    with patch("app.code_generator.generate_code", side_effect=lambda: next(simulated)):
        def save_item(c):
            b = TestBatch(code=c)
            session.add(b)
            session.commit()
            return b

        success_code, item = execute_with_unique_retry(save_fn=save_item, db=session, max_retries=3)
        assert success_code == "CODE9999"
        assert item.code == "CODE9999"

    print(f"  -> Trùng UNIQUE mã ban đầu, đã tự động sinh lại mã mới: {success_code}")
    print("  -> PASSED: Bắt ràng buộc Unique từ database và sinh lại hoàn hảo.")

    print("\n" + "=" * 70)
    print("HOÀN THÀNH PHẦN 1 (TASK T-18 / SCRUM-34): 6/6 BÀI KIỂM THỬ ĐẠT 100%!")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # PHẦN 2: KIỂM THỬ CHẤP NHẬN TASK T-28 (SCRUM-44): THẨM ĐỊNH TÍNH TOÀN VẸN
    # -------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("KIỂM THỬ CHẤP NHẬN (DoD) - TASK T-28 (SCRUM-44): CÁN BỘ KIỂM TRA")
    print("=" * 70)

    from datetime import date, datetime, timezone
    from fastapi import HTTPException
    from app.models import (
        Base as AppBase,
        Batch,
        BatchEvent,
        Farm,
        InspectionLog,
        ROLE_ADMIN,
        ROLE_FARMER,
        ROLE_INSPECTOR,
        User,
    )
    from app.routers.inspections import verify_batch_integrity
    from app.security import compute_event_hash, hash_password, require_inspector

    test_eng = create_engine("sqlite:///:memory:")
    AppBase.metadata.create_all(test_eng)
    AppSession = sessionmaker(bind=test_eng)
    db = AppSession()

    admin = User(username="admin", password=hash_password("123456"), role=ROLE_ADMIN)
    inspector = User(username="inspector", password=hash_password("123456"), role=ROLE_INSPECTOR)
    farmer = User(username="farmer", password=hash_password("123456"), role=ROLE_FARMER)
    farm = Farm(name="Vùng xoài Cao Lãnh", location="Đồng Tháp", area=4.2, owner="HTX Mỹ Xương")
    batch = Batch(farm_id=1, product_name="Xoài Cát Chu", quantity=1000.0, harvest_date=date(2026, 1, 15), code="7X9KM2RP")
    db.add_all([admin, inspector, farmer, farm, batch])
    db.commit()

    # 7. Kiểm tra phân quyền: Chỉ inspector và admin mới được phép
    print("\n[TEST 7] Kiểm tra phân quyền: Chỉ inspector & admin mới có quyền kiểm định...")
    try:
        require_inspector(farmer)
        assert False, "Farmer không được phép gọi"
    except HTTPException as e:
        assert e.status_code == 403
        print("  -> Farmer gọi bị chặn 403 Forbidden (Đạt yêu cầu phân quyền).")

    assert require_inspector(inspector).username == "inspector"
    assert require_inspector(admin).username == "admin"
    print("  -> Inspector và Admin được cấp quyền truy cập hợp lệ.")
    print("  -> PASSED: Phân quyền vai trò chuẩn xác 100%.")

    # 8. Tạo chuỗi sự kiện nguyên vẹn & kiểm tra dấu hợp lệ màu xanh
    print("\n[TEST 8] Kiểm tra lô nguyên vẹn -> Kết quả HỢP LỆ (Màu xanh) & ghi log DB...")
    prev_h = "0" * 64
    for etype in ["HARVEST", "PROCESSING", "HANDOVER"]:
        ts = datetime.now(timezone.utc).isoformat()
        h = compute_event_hash(etype, f'{{"type": "{etype}"}}', "farmer", "HTX", ts, prev_h)
        ev = BatchEvent(batch_id=batch.id, event_type=etype, payload=f'{{"type": "{etype}"}}', actor="farmer", organization="HTX", timestamp=ts, hash=h, previous_hash=prev_h)
        db.add(ev)
        db.commit()
        prev_h = h

    res_valid = verify_batch_integrity(identifier="7X9KM2RP", current_user=inspector, db=db)
    assert res_valid.is_valid is True
    assert res_valid.error_type is None
    assert res_valid.tampered_index is None
    assert res_valid.total_events == 3
    print("  -> Kết quả thẩm định: is_valid = True (Hiển thị nhãn XANH).")
    print(f"  -> Chi tiết: {res_valid.details}")

    log_entry = db.query(InspectionLog).filter_by(batch_code="7X9KM2RP").first()
    assert log_entry is not None
    assert log_entry.is_valid is True
    assert log_entry.inspector == "inspector"
    print(f"  -> Đã ghi log đối chiếu vào bảng inspection_logs lúc {log_entry.timestamp}.")
    print("  -> PASSED: DoD Lô nguyên vẹn hiện dấu hợp lệ màu xanh & ghi log thành công.")

    # 9. Giả lập can thiệp sửa lén payload -> Cảnh báo đỏ & chỉ rõ vị trí
    print("\n[TEST 9] Kiểm tra lô bị can thiệp sửa lén payload -> Cảnh báo ĐỎ & chỉ rõ vị trí...")
    ev2 = db.query(BatchEvent).filter_by(event_type="PROCESSING").first()
    ev2.payload = '{"type": "PROCESSING", "tampered": true}'
    db.commit()

    res_tampered = verify_batch_integrity(identifier="7X9KM2RP", current_user=inspector, db=db)
    assert res_tampered.is_valid is False
    assert res_tampered.error_type == "TAMPERED_PAYLOAD"
    assert res_tampered.tampered_index == 1
    assert res_tampered.tampered_event_id == ev2.id
    print("  -> Kết quả thẩm định: is_valid = False (Hiển thị CẢNH BÁO ĐỎ).")
    print(f"  -> Loại lỗi phát hiện: {res_tampered.error_type}")
    print(f"  -> Vị trí phát hiện sai lệch: Mắt xích #{res_tampered.tampered_index + 1} (Sự kiện ID #{res_tampered.tampered_event_id})")
    print("  -> PASSED: DoD Lô bị can thiệp hiển thị cảnh báo đỏ và chỉ rõ vị trí.")

    print("\n" + "=" * 70)
    print("HOÀN THÀNH PHẦN 2 (TASK T-28 / SCRUM-44): 3/3 BÀI KIỂM THỬ ĐẠT 100%!")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # PHẦN 3: KIỂM THỬ CHẤP NHẬN TASK T-37 (SCRUM-53): BẢNG QUAN HỆ PHẢ HỆ LÔ HÀNG
    # -------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("KIỂM THỬ CHẤP NHẬN (DoD) - TASK T-37 (SCRUM-53): PHẢ HỆ LÔ HÀNG")
    print("=" * 70)

    from sqlalchemy import inspect as sa_inspect
    from app.models import BatchLineage, RELATION_MERGE, RELATION_SPLIT
    from app.routers.lineage import (
        get_batch_genealogy,
        record_batch_lineage,
        trace_backward,
        trace_forward,
    )
    from app.schemas import BatchLineageCreate
    from migrations.scrum_53_batch_lineage import downgrade, upgrade

    lineage_eng = create_engine("sqlite:///:memory:")
    AppBase.metadata.create_all(lineage_eng)
    LineageSession = sessionmaker(bind=lineage_eng)
    ldb = LineageSession()

    # Tạo các lô hàng phục vụ kiểm thử
    f_lin = Farm(name="Vườn Mẫu Lineage", location="Tiền Giang", area=5.0, owner="HTX Tiền Giang")
    ldb.add(f_lin)
    ldb.commit()

    b_p1 = Batch(farm_id=f_lin.id, product_name="Sầu riêng Ri6 Lô Cha 1", quantity=1000.0, harvest_date=date(2026, 1, 1), code="P1A2B3C4")
    b_p2 = Batch(farm_id=f_lin.id, product_name="Sầu riêng Ri6 Lô Cha 2", quantity=1500.0, harvest_date=date(2026, 1, 2), code="P2D5E6F7")
    b_c_merge = Batch(farm_id=f_lin.id, product_name="Sầu riêng Cơm Đóng Khay (Gộp)", quantity=2200.0, harvest_date=date(2026, 1, 3), code="CM8G9H0J")
    b_c_split1 = Batch(farm_id=f_lin.id, product_name="Sầu riêng Loại 1 (Tách)", quantity=400.0, harvest_date=date(2026, 1, 4), code="CS1K2L3M")
    b_c_split2 = Batch(farm_id=f_lin.id, product_name="Sầu riêng Loại 2 (Tách)", quantity=600.0, harvest_date=date(2026, 1, 4), code="CS2N3P4Q")
    ldb.add_all([b_p1, b_p2, b_c_merge, b_c_split1, b_c_split2])
    ldb.commit()

    # 10. DoD: Migration tiến (upgrade) và lùi (downgrade) an toàn
    print("\n[TEST 10] DoD: Migration tiến (upgrade) và lùi (downgrade) an toàn...")
    downgrade(lineage_eng)
    insp = sa_inspect(lineage_eng)
    assert "batch_lineage" not in insp.get_table_names(), "Bảng batch_lineage phải bị xoá sau downgrade"
    print("  -> Downgrade thành công: Đã xoá bảng và chỉ mục.")

    upgrade(lineage_eng)
    insp = sa_inspect(lineage_eng)
    assert "batch_lineage" in insp.get_table_names(), "Bảng batch_lineage phải được tạo sau upgrade"
    print("  -> Upgrade thành công: Đã tạo bảng batch_lineage.")
    print("  -> PASSED: DoD Migration tiến và lùi an toàn đạt 100%.")

    # 11. DoD: Tạo chỉ mục trên cả cột cha lẫn con để phục vụ truy ngược và truy xuôi tối ưu
    print("\n[TEST 11] DoD: Kiểm tra chỉ mục trên cả cột cha lẫn con (truy ngược & truy xuôi)...")
    indexes = insp.get_indexes("batch_lineage")
    parent_index = next((idx for idx in indexes if idx["name"] == "ix_batch_lineage_parent_batch_id"), None)
    child_index = next((idx for idx in indexes if idx["name"] == "ix_batch_lineage_child_batch_id"), None)

    assert parent_index is not None, "Thiếu index ix_batch_lineage_parent_batch_id"
    assert parent_index["column_names"] == ["parent_batch_id"], "Index cha phải trên cột parent_batch_id"
    assert child_index is not None, "Thiếu index ix_batch_lineage_child_batch_id"
    assert child_index["column_names"] == ["child_batch_id"], "Index con phải trên cột child_batch_id"

    print(f"  -> Chỉ mục cột cha (Truy xuôi): {parent_index['name']} trên {parent_index['column_names']}")
    print(f"  -> Chỉ mục cột con (Truy ngược): {child_index['name']} trên {child_index['column_names']}")
    print("  -> PASSED: DoD Tạo chỉ mục trên cả 2 cột cha và con tối ưu truy vết.")

    # 12. Ràng buộc kỹ thuật: UNIQUE trên cặp [lô cha, lô con]
    print("\n[TEST 12] Ràng buộc UNIQUE trên cặp [lô cha, lô con] tránh ghi trùng quan hệ...")
    req_l1 = BatchLineageCreate(
        parent_batch_id=b_p1.id,
        child_batch_id=b_c_merge.id,
        transferred_quantity=1000.0,
        relation_type=RELATION_MERGE,
    )
    rec1 = record_batch_lineage(data=req_l1, current_user=farmer, db=ldb)
    assert rec1.id is not None
    print(f"  -> Ghi nhận thành công quan hệ ban đầu: Cha #{b_p1.id} -> Con #{b_c_merge.id}")

    try:
        record_batch_lineage(data=req_l1, current_user=farmer, db=ldb)
        assert False, "Đáng lẽ phải ném HTTPException 409 Conflict do trùng UNIQUE"
    except HTTPException as e:
        assert e.status_code == 409
        print(f"  -> Bắt được lỗi trùng quan hệ: HTTP 409 - {e.detail}")
    print("  -> PASSED: Ràng buộc UNIQUE trên cặp [cha, con] bảo vệ chống ghi trùng tuyệt đối.")

    # 13. DoD: Một lô con của phép gộp có nhiều dòng cha (MERGE)
    print("\n[TEST 13] Mục tiêu: Một lô con của phép gộp có nhiều dòng cha (MERGE)...")
    req_l2 = BatchLineageCreate(
        parent_batch_id=b_p2.id,
        child_batch_id=b_c_merge.id,
        transferred_quantity=1200.0,
        relation_type=RELATION_MERGE,
    )
    rec2 = record_batch_lineage(data=req_l2, current_user=farmer, db=ldb)
    assert rec2.id is not None

    merge_parents = ldb.query(BatchLineage).filter_by(child_batch_id=b_c_merge.id).all()
    assert len(merge_parents) == 2
    assert {p.parent_batch_id for p in merge_parents} == {b_p1.id, b_p2.id}
    print(f"  -> Lô con #{b_c_merge.id} có {len(merge_parents)} dòng cha: {[p.parent_batch_id for p in merge_parents]}")
    print("  -> PASSED: Phép gộp (MERGE) lưu nhiều dòng cha thành công.")

    # 14. DoD: Phép tách (SPLIT) - Một lô cha có nhiều dòng con
    print("\n[TEST 14] Phép tách (SPLIT): Một lô cha có nhiều dòng con...")
    req_s1 = BatchLineageCreate(
        parent_batch_id=b_p1.id,
        child_batch_id=b_c_split1.id,
        transferred_quantity=400.0,
        relation_type=RELATION_SPLIT,
    )
    req_s2 = BatchLineageCreate(
        parent_batch_id=b_p1.id,
        child_batch_id=b_c_split2.id,
        transferred_quantity=600.0,
        relation_type=RELATION_SPLIT,
    )
    rec_s1 = record_batch_lineage(data=req_s1, current_user=farmer, db=ldb)
    rec_s2 = record_batch_lineage(data=req_s2, current_user=farmer, db=ldb)

    split_children = ldb.query(BatchLineage).filter_by(parent_batch_id=b_p1.id, relation_type=RELATION_SPLIT).all()
    assert len(split_children) == 2
    assert {c.child_batch_id for c in split_children} == {b_c_split1.id, b_c_split2.id}
    print(f"  -> Lô cha #{b_p1.id} đã tách thành {len(split_children)} dòng con: {[c.child_batch_id for c in split_children]}")
    print("  -> PASSED: Phép tách (SPLIT) lưu nhiều dòng con thành công.")

    # 15. DoD: Truy ngược (Backward trace) và Truy xuôi (Forward trace)
    print("\n[TEST 15] Kiểm tra API truy ngược (Backward trace) và truy xuôi (Forward trace)...")
    backward = trace_backward(batch_id=b_c_merge.id, db=ldb)
    assert len(backward) == 2
    print(f"  -> Truy ngược lô #{b_c_merge.id}: Tìm thấy {len(backward)} lô nguồn ({[b.batch_code for b in backward]})")

    forward = trace_forward(batch_id=b_p1.id, db=ldb)
    # b_p1 tham gia gộp vào b_c_merge và tách ra b_c_split1, b_c_split2 -> 3 lô con
    assert len(forward) == 3
    print(f"  -> Truy xuôi lô #{b_p1.id}: Tìm thấy {len(forward)} lô phân nhánh ({[f.batch_code for f in forward]})")

    genealogy = get_batch_genealogy(batch_id=b_c_merge.id, db=ldb)
    assert genealogy.target_batch_id == b_c_merge.id
    assert len(genealogy.parents) == 2
    print(f"  -> Cây phả hệ lô #{b_c_merge.id}: {len(genealogy.parents)} cha, {len(genealogy.children)} con.")
    print("  -> PASSED: Truy vết ngược xuôi và cây phả hệ hoạt động hoàn hảo.")

    print("\n" + "=" * 70)
    print("HOÀN THÀNH PHẦN 3 (TASK T-37 / SCRUM-53): 6/6 BÀI KIỂM THỬ ĐẠT 100%!")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # PHẦN 4: KIỂM THỬ CHẤP NHẬN TASK T-48 (SCRUM-64): DUYỆT PHẢ HỆ BFS THEO TẦNG
    # -------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("KIỂM THỬ CHẤP NHẬN (DoD) - TASK T-48 (SCRUM-64): BFS CON -> CHA")
    print("=" * 70)

    import json
    from app.lineage import (
        LineageBFSResult,
        LineageCycleError,
        find_ancestors_bfs,
        trace_ancestors_bfs,
    )

    # 16. DoD: Khớp hoàn toàn với bộ dữ liệu mẫu benchmark
    print("\n[TEST 16] DoD: Khớp hoàn toàn với bộ dữ liệu chuẩn benchmark (data/lineage_benchmark.json)...")
    bench_file = Path(__file__).resolve().parent / "data" / "lineage_benchmark.json"
    with open(bench_file, "r", encoding="utf-8") as f:
        bench_data = json.load(f)

    b_rels = bench_data["graph_structure"]["relations"]
    ground_truth = bench_data["ground_truth_ancestors"]

    for code, expected_ancestors in ground_truth.items():
        res = trace_ancestors_bfs(batch_code=code, relations=b_rels)
        assert res.all_ancestors == expected_ancestors, (
            f"Lô {code}: all_ancestors {res.all_ancestors} != expected {expected_ancestors}"
        )

    # Khẳng định 2 phép gộp phức tạp:
    g1_res = trace_ancestors_bfs("B-MERGE-G1", relations=b_rels)
    assert g1_res.ancestors_by_level == [["B-SPLIT-1A", "B-SPLIT-2A"], ["B-ROOT-01", "B-ROOT-02"]]
    assert set(g1_res.root_batches) == {"B-ROOT-01", "B-ROOT-02"}

    g2_res = trace_ancestors_bfs("B-MERGE-G2", relations=b_rels)
    assert g2_res.ancestors_by_level == [["B-SPLIT-2B", "B-SPLIT-3A"], ["B-ROOT-02", "B-ROOT-03"]]
    assert set(g2_res.root_batches) == {"B-ROOT-02", "B-ROOT-03"}

    print("  -> Đã kiểm tra 11/11 lô mẫu: Kết quả khớp chính xác 100% với ground truth.")
    print("  -> B-MERGE-G1: Tầng 1 [B-SPLIT-1A, B-SPLIT-2A] -> Tầng 2 [B-ROOT-01, B-ROOT-02]. Lô gốc: {B-ROOT-01, B-ROOT-02}")
    print("  -> B-MERGE-G2: Tầng 1 [B-SPLIT-2B, B-SPLIT-3A] -> Tầng 2 [B-ROOT-02, B-ROOT-03]. Lô gốc: {B-ROOT-02, B-ROOT-03}")
    print("  -> PASSED: DoD 1 Khớp hoàn toàn bộ dữ liệu mẫu đạt 100%.")

    # 17. DoD: Ca chu trình ném lỗi có tên lô gây ra chu trình
    print("\n[TEST 17] DoD: Ca chu trình ném lỗi có tên lô gây ra chu trình...")
    # Chu trình 1: Tự trỏ chính nó
    try:
        trace_ancestors_bfs("LÔ-X", relations=[{"parent": "LÔ-X", "child": "LÔ-X"}])
        assert False, "Đáng lẽ phải ném LineageCycleError"
    except LineageCycleError as e:
        assert "LÔ-X" in str(e)
        assert e.node == "LÔ-X"
        print(f"  -> Chu trình tự trỏ bắt thành công: {e}")

    # Chu trình 2: Khép kín nhiều tầng (A -> B -> C -> A)
    multi_cycle = [
        {"parent": "LÔ-1", "child": "LÔ-XUẤT-PHÁT"},
        {"parent": "LÔ-2", "child": "LÔ-1"},
        {"parent": "LÔ-CHU-TRÌNH", "child": "LÔ-2"},
        {"parent": "LÔ-1", "child": "LÔ-CHU-TRÌNH"},  # trỏ lại LÔ-1
    ]
    try:
        trace_ancestors_bfs("LÔ-XUẤT-PHÁT", relations=multi_cycle)
        assert False, "Đáng lẽ phải ném LineageCycleError"
    except LineageCycleError as e:
        assert "LÔ-1" in str(e)
        assert e.node == "LÔ-1"
        print(f"  -> Chu trình đa tầng bắt thành công: Lô gây ra chu trình là '{e.node}'")
    print("  -> PASSED: DoD 2 Ca chu trình ném lỗi kèm tên lô gây chu trình.")

    # 18. Lưu ý kỹ thuật: BFS dùng Queue, không dùng đệ quy (chống Stack Overflow đồ thị sâu)
    print("\n[TEST 18] Kiểm tra duyệt Queue chống tràn ngăn xếp với đồ thị sâu 1.200 tầng...")
    deep_rels = [{"parent": f"NODE-{i+1}", "child": f"NODE-{i}"} for i in range(1200)]
    deep_res = trace_ancestors_bfs("NODE-0", relations=deep_rels)
    assert len(deep_res.ancestors_by_level) == 1200
    assert deep_res.root_batches == ["NODE-1200"]
    print("  -> Duyệt thành công đồ thị 1.200 tầng mượt mà, không gặp lỗi tràn ngăn xếp RecursionError.")
    print("  -> PASSED: Ràng buộc kỹ thuật BFS Queue không đệ quy đạt 100%.")

    # 19. Kiểm tra tuple unpacking & dict access
    print("\n[TEST 19] Kiểm tra hỗ trợ unpacking (levels, roots) và dict-like...")
    t_levels, t_roots = deep_res
    assert len(t_levels) == 1200
    assert t_roots == ["NODE-1200"]
    assert deep_res["root_batches"] == ["NODE-1200"]
    print("  -> PASSED: Cấu trúc dữ liệu trả về hỗ trợ linh hoạt cả tuple unpacking và dict.")

    # 20. Kiểm tra đọc trực tiếp từ database session (bảng batch_lineage)
    print("\n[TEST 20] Kiểm tra đọc quan hệ phả hệ trực tiếp từ database ORM...")
    db_res = trace_ancestors_bfs(batch_code=b_c_merge.code, db=ldb)
    assert len(db_res.ancestors_by_level) >= 1
    assert set(db_res.root_batches) == {b_p1.code, b_p2.code}
    print(f"  -> Duyệt DB cho lô '{b_c_merge.code}': Tìm thấy các lô gốc xuất xứ: {db_res.root_batches}")
    print("  -> PASSED: Tích hợp cơ sở dữ liệu hoạt động chính xác 100%.")

    print("\n" + "=" * 70)
    print("HOÀN THÀNH PHẦN 4 (TASK T-48 / SCRUM-64): 5/5 BÀI KIỂM THỬ ĐẠT 100%!")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # PHẦN 5: KIỂM THỬ CHẤP NHẬN TASK T-58 (SCRUM-74): TRANG CHI TIẾT 3 TAB & THAO TÁC
    # -------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("KIỂM THỬ CHẤP NHẬN (DoD) - TASK T-58 (SCRUM-74): CHI TIẾT 3 TAB & THAO TÁC")
    print("=" * 70)

    from app.models import BATCH_STATUS_ACTIVE, BATCH_STATUS_PENDING_HANDOVER
    from app.routers.batches import (
        get_batch_full_detail,
        handover_batch,
        merge_batches,
        split_batch,
    )
    from app.schemas import (
        BatchHandoverRequest,
        BatchMergeRequest,
        BatchSplitChildItem,
        BatchSplitRequest,
    )

    t58_eng = create_engine("sqlite:///:memory:")
    AppBase.metadata.create_all(t58_eng)
    T58Session = sessionmaker(bind=t58_eng)
    sdb = T58Session()

    u_farmer = User(username="nongdan_01", password=hash_password("123456"), role=ROLE_FARMER)
    u_admin = User(username="admin_01", password=hash_password("123456"), role=ROLE_ADMIN)
    u_inspector = User(username="canbo_01", password=hash_password("123456"), role=ROLE_INSPECTOR)
    f_farm = Farm(name="Vườn Mẫu Long Khánh", location="Đồng Nai", area=6.5, owner="HTX Long Khánh")
    sdb.add_all([u_farmer, u_admin, u_inspector, f_farm])
    sdb.commit()

    b_test1 = Batch(farm_id=f_farm.id, product_name="Chôm Chôm Nhãn", quantity=2000.0, harvest_date=date(2026, 2, 1), code="CHOM0001", status=BATCH_STATUS_ACTIVE)
    b_test2 = Batch(farm_id=f_farm.id, product_name="Sầu Riêng Chín Cây", quantity=1500.0, harvest_date=date(2026, 2, 2), code="SAUR0002", status=BATCH_STATUS_ACTIVE)
    sdb.add_all([b_test1, b_test2])
    sdb.commit()

    # Thêm sự kiện chuỗi băm mẫu
    ts_now = datetime.now(timezone.utc).isoformat()
    ev_test = BatchEvent(
        batch_id=b_test1.id,
        event_type="HARVEST",
        payload='{"note": "Thu hoạch buổi sáng VietGAP"}',
        actor="nongdan_01",
        organization="HTX Long Khánh",
        timestamp=ts_now,
        hash=compute_event_hash("HARVEST", '{"note": "Thu hoạch buổi sáng VietGAP"}', "nongdan_01", "HTX Long Khánh", ts_now, "0" * 64),
        previous_hash="0" * 64,
    )
    sdb.add(ev_test)
    sdb.commit()

    # 21. DoD: Cả 3 tab hoạt động mượt mà và tổng hợp dữ liệu đầy đủ
    print("\n[TEST 21] DoD: Kiểm tra dữ liệu tổng hợp phục vụ mượt mà 3 Tab...")
    detail_res = get_batch_full_detail(batch_id=b_test1.id, db=sdb)
    assert detail_res.batch.id == b_test1.id
    assert detail_res.farm_name == f_farm.name
    # Tab 2: Timeline
    assert len(detail_res.timeline.events) == 1
    assert detail_res.timeline.is_valid is True
    # Tab 3: Phả hệ
    assert detail_res.genealogy.target_batch_id == b_test1.id
    assert detail_res.ancestors.target_batch == b_test1.code
    print("  -> Tab 1 (Tổng quan): Lô #1 'Chôm Chôm Nhãn', Vùng 'Vườn Mẫu Long Khánh' (Đồng Nai)")
    print("  -> Tab 2 (Dòng thời gian T-32): 1 sự kiện HARVEST, Trạng thái chuỗi: HỢP LỆ")
    print("  -> Tab 3 (Nguồn gốc T-50): Cây tổ tiên BFS & Lô gốc xuất xứ sẵn sàng")
    print("  -> PASSED: Cả 3 tab hoạt động mượt mà 100%.")

    # 22. DoD: Phân quyền thao tác chính xác (Inspector chặn 403, Farmer & Admin cấp quyền)
    print("\n[TEST 22] DoD: Phân quyền thao tác chính xác (Inspector chặn 403, Farmer & Admin hợp lệ)...")
    assert require_farmer(u_farmer).username == "nongdan_01"
    assert require_farmer(u_admin).username == "admin_01"
    try:
        require_farmer(u_inspector)
        assert False, "Inspector không được phép có quyền thao tác trên lô"
    except HTTPException as e:
        assert e.status_code == 403
        print("  -> Inspector bị chặn 403 Forbidden đối với thao tác trên lô.")
    print("  -> PASSED: Phân quyền thao tác chính xác.")

    # 23. Thao tác Bàn giao: Chuyển trạng thái sang PENDING_HANDOVER và ghi nhận mắt xích sự kiện
    print("\n[TEST 23] Thao tác Bàn giao: Chuyển trạng thái sang PENDING_HANDOVER...")
    req_handover = BatchHandoverRequest(target_organization="Công Ty Xuất Khẩu Nông Sản", note="Xe lạnh số 05")
    updated_b = handover_batch(batch_id=b_test1.id, data=req_handover, current_user=u_farmer, db=sdb)
    assert updated_b.status == BATCH_STATUS_PENDING_HANDOVER
    assert b_test1.status == BATCH_STATUS_PENDING_HANDOVER

    ev_ho = sdb.query(BatchEvent).filter_by(batch_id=b_test1.id, event_type="HANDOVER").first()
    assert ev_ho is not None
    assert "Xe lạnh số 05" in ev_ho.payload
    print(f"  -> Lô #{b_test1.id} đã chuyển trạng thái thành: {updated_b.status}")
    print(f"  -> Mắt xích sự kiện HANDOVER đã ghi thành công lúc {ev_ho.timestamp}")
    print("  -> PASSED: Thao tác Bàn giao hoàn tất xuất sắc.")

    # 24. DoD & Lưu ý kỹ thuật: Nút tự động ẩn ở UI và Máy chủ độc lập từ chối 400 khi lô đang chờ bàn giao
    print("\n[TEST 24] DoD: Máy chủ độc lập từ chối mọi thao tác khi lô đang chờ bàn giao (PENDING_HANDOVER)...")
    # Thử bàn giao lại
    try:
        handover_batch(batch_id=b_test1.id, data=req_handover, current_user=u_farmer, db=sdb)
        assert False, "Đáng lẽ phải bị từ chối 400"
    except HTTPException as e:
        assert e.status_code == 400
        print(f"  -> Bàn giao lại bị chặn: HTTP 400 - {e.detail}")

    # Thử tách lô khi lô đang chờ bàn giao
    req_split_fail = BatchSplitRequest(children=[
        BatchSplitChildItem(product_name="Tách A", quantity=500.0),
        BatchSplitChildItem(product_name="Tách B", quantity=500.0),
    ])
    try:
        split_batch(batch_id=b_test1.id, data=req_split_fail, current_user=u_farmer, db=sdb)
        assert False, "Đáng lẽ phải bị từ chối 400"
    except HTTPException as e:
        assert e.status_code == 400
        print(f"  -> Tách lô bị chặn: HTTP 400 - {e.detail}")

    # Thử gộp lô khi lô cha đang chờ bàn giao
    req_merge_fail = BatchMergeRequest(parent_batch_ids=[b_test1.id, b_test2.id], product_name="Lô gộp lỗi")
    try:
        merge_batches(batch_id=b_test2.id, data=req_merge_fail, current_user=u_farmer, db=sdb)
        assert False, "Đáng lẽ phải bị từ chối 400"
    except HTTPException as e:
        assert e.status_code == 400
        print(f"  -> Gộp lô bị chặn: HTTP 400 - {e.detail}")
    print("  -> PASSED: Máy chủ kiểm tra độc lập và từ chối 400 khi lô đang chờ bàn giao.")

    # 25. Thao tác Tách (SPLIT) và Gộp (MERGE) trên lô hợp lệ
    print("\n[TEST 25] Kiểm tra thao tác Tách và Gộp trên lô trạng thái hợp lệ (ACTIVE)...")
    req_split_ok = BatchSplitRequest(children=[
        BatchSplitChildItem(product_name="Sầu Riêng Loại A", quantity=600.0),
        BatchSplitChildItem(product_name="Sầu Riêng Loại B", quantity=900.0),
    ])
    children_ok = split_batch(batch_id=b_test2.id, data=req_split_ok, current_user=u_farmer, db=sdb)
    assert len(children_ok) == 2
    assert children_ok[0].quantity == 600.0
    assert children_ok[1].quantity == 900.0
    print(f"  -> Đã tách lô #{b_test2.id} thành 2 lô con: {[c.code for c in children_ok]}")

    req_merge_ok = BatchMergeRequest(
        parent_batch_ids=[children_ok[0].id, children_ok[1].id],
        product_name="Sầu Riêng Đóng Thùng Xuất Khẩu",
    )
    merged_ok = merge_batches(batch_id=0, data=req_merge_ok, current_user=u_farmer, db=sdb)
    assert merged_ok.id is not None
    assert merged_ok.quantity == 1500.0
    print(f"  -> Đã gộp 2 lô con thành lô #{merged_ok.id} ({merged_ok.code}): Sản lượng {merged_ok.quantity} kg")
    print("  -> PASSED: Thao tác Tách và Gộp hoàn tất trơn tru.")

    # 26. Kiểm thử 4 Kịch bản Nghiệm thu Dòng thời gian sự kiện (Acceptance Scenarios)
    print("\n[TEST 26] Kiểm thử 4 Kịch bản Nghiệm thu Dòng thời gian sự kiện...")
    from tests.test_timeline_scenarios import run_timeline_scenarios_tests
    run_timeline_scenarios_tests()
    print("  -> PASSED: Cả 4 Kịch bản Nghiệm thu dòng thời gian đạt 100%.")

    # 27. Kiểm thử 4 Kịch bản Nghiệm thu Quản lý thửa đất / Vùng trồng (Acceptance Scenarios)
    print("\n[TEST 27] Kiểm thử 4 Kịch bản Nghiệm thu Quản lý thửa đất / Vùng trồng...")
    from tests.test_farm_scenarios import run_farm_scenarios_tests
    run_farm_scenarios_tests()
    print("  -> PASSED: Cả 4 Kịch bản Nghiệm thu quản lý thửa đất đạt 100%.")

    # 28. Kiểm thử 3 Kịch bản Nghiệm thu Phân tách lô nông sản (Batch Split Scenarios)
    print("\n[TEST 28] Kiểm thử 3 Kịch bản Nghiệm thu Phân tách lô nông sản...")
    from tests.test_split_scenarios import run_split_scenarios_tests
    run_split_scenarios_tests()
    print("  -> PASSED: Cả 3 Kịch bản Nghiệm thu phân tách lô đạt 100%.")

    # 29. Kiểm thử 3 Kịch bản Nghiệm thu Lệnh thu hồi sản phẩm (S-39 Recall Orders)
    print("\n[TEST 29] Kiểm thử 3 Kịch bản Nghiệm thu Lệnh thu hồi sản phẩm...")
    from tests.test_recall_scenarios import run_recall_scenarios_tests
    run_recall_scenarios_tests()
    print("  -> PASSED: Cả 3 Kịch bản Nghiệm thu lệnh thu hồi đạt 100%.")

    # 30. Script đọc tệp kịch bản chuỗi lạnh & gọi endpoint S-41 (Tier Later - 3 SP)
    print("\n[TEST 30] Kiểm thử Script đọc tệp kịch bản và gọi endpoint chuỗi lạnh S-41...")
    from scripts.run_cold_chain_scenarios import run_cold_chain_scenarios_script
    run_cold_chain_scenarios_script()
    print("  -> PASSED: Cả 5 Kịch bản mẫu chuỗi lạnh IoT đạt 100% đáp án.")

    # 31. Bản đồ hành trình công khai S-06 (Tier Later - 3 SP)
    print("\n[TEST 31] Kiểm thử Bản đồ hành trình công khai S-06...")
    from tests.test_public_map_scenarios import run_public_map_scenarios_tests
    run_public_map_scenarios_tests()
    print("  -> PASSED: Bản đồ hành trình cấp xã/huyện, ẩn toạ độ thửa đất đạt 100%.")

    print("\n" + "=" * 70)
    print("TỔNG KẾT: TẤT CẢ 31/31 BÀI KIỂM THỬ ĐÃ VƯỢT QUA XUẤT SẮC (100% PASSED)!")
    print("  - Phần 1 (T-18 / SCRUM-34): 6/6 tests PASSED")
    print("  - Phần 2 (T-28 / SCRUM-44): 3/3 tests PASSED")
    print("  - Phần 3 (T-37 / SCRUM-53): 6/6 tests PASSED")
    print("  - Phần 4 (T-48 / SCRUM-64): 5/5 tests PASSED")
    print("  - Phần 5 (T-58 / SCRUM-74): 5/5 tests PASSED")
    print("  - Phần 6 (4 Kịch bản Dòng thời gian): 4/4 scenarios PASSED")
    print("  - Phần 7 (4 Kịch bản Quản lý thửa đất): 4/4 scenarios PASSED")
    print("  - Phần 8 (3 Kịch bản Phân tách lô hàng): 3/3 scenarios PASSED")
    print("  - Phần 9 (3 Kịch bản Lệnh thu hồi sản phẩm - 5 SP): 3/3 scenarios PASSED")
    print("  - Phần 10 (5 Kịch bản Giám sát chuỗi lạnh S-41 - 3 SP): 5/5 scenarios PASSED")
    print("  - Phần 11 (Bản đồ hành trình công khai S-06 - 3 SP): 4/4 tests PASSED")
    print("=" * 70)


if __name__ == "__main__":
    run_all_tests()




