"""Script dựng **kịch bản thu hồi mẫu** 4 tầng qua 3 tổ chức + in đáp án đối chiếu (S-22).

================================================================================
🎯 MỤC TIÊU & MÔ TẢ NGHIỆP VỤ:
================================================================================
Dùng CHÍNH HÀM TÁCH (`split_batch`) VÀ GỘP (`merge_batches`) CỦA ỨNG DỤNG để dựng
đồ thị mẫu phả hệ **4 tầng qua 3 tổ chức**, có **lô gộp từ nhiều nguồn** và
**lô đã tiêu thụ** - đúng các điều kiện để kiểm thuật toán liệt kê thu hồi
(`app/recall_enumeration.py`) bằng đáp án đếm tay độc lập.

Sơ đồ dữ liệu tạo ra (khối lượng kg, ``→`` là phép tách, ``+`` là phép gộp):

    Tầng 1 (F0) - HTX Xoai My Xuong      : A1(1000) A2(1000) A3(1000)
    Tầng 2 (F1) - Cong Ty Che Bien Mekong: A1→B11(400) B12(350) | A2→B21(300) B22(400) | A3→B31(450) B32(250)
    Tầng 3 (F2) - Cong Ty Che Bien Mekong: M1 = B11(250) + B21(200) = 450   (gộp 2 nguồn)
                                           M2 = B12(150) + B31(250) = 400   (gộp 2 nguồn)
                                           M3 = B22(200) + B32(100) = 300   (gộp 2 nguồn)
    Tầng 4 (F3) - Chuoi Sieu Thi Xanh    : M1→F11(200) F12(150) | M2→F21(150) F22(150) | M3→F31(100) F32(100)
    Tiêu thụ                             : F12 bán hết 150 kg (trạng thái ``consumed``), F22 bán 50 kg

✅ TIÊU CHÍ NGHIỆM THU (DoD / AC):
1. Chạy script xong thì CSDL có đồ thị 4 tầng qua 3 tổ chức, có ít nhất một lô gộp
   từ nhiều nguồn (``M1``/``M2``/``M3`` - mỗi lô nhận từ 2 nhánh khác nhau) và ít
   nhất một lô đã tiêu thụ (``F12``).
2. Script in ra phạm vi thu hồi tính bằng thuật toán để người đọc đối chiếu tay với
   tệp đáp án ``backend/data/recall_benchmark.json`` (hai người đếm độc lập).
3. Chạy lại nhiều lần cho cùng một kết quả (Idempotent & Deterministic): lần chạy
   sau tự dọn dữ liệu mẫu cũ trước khi dựng lại.

🛠️ RÀNG BUỘC KỸ THUẬT:
- Script kiểm tra biến môi trường trước khi chạy, TUYỆT ĐỐI KHÔNG CHẠY TRÊN MÔI
  TRƯỜNG DỮ LIỆU THẬT (production/prod/live).
- Phả hệ dựng bằng hàm thật của ứng dụng: phép tách ghi ``batches.parent_id``,
  phép gộp ghi bảng ``batch_relations``.
- Tên tổ chức/địa điểm đặt ASCII vì tổ chức còn được truyền qua header
  ``X-Organization-Id`` (header HTTP không nhận ký tự có dấu).
"""

from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

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
from app.models import (
    BATCH_STATUS_CONSUMED,
    Batch,
    BatchRelation,
    Farm,
    Location,
)
from app.recall_enumeration import build_recall_scope

#: Prefix định danh thống nhất cho mọi mã lô của kịch bản thu hồi mẫu.
SAMPLE_BATCH_PREFIX = "LOT-S22-"

#: Tên vùng trồng xuất xứ của kịch bản mẫu.
SAMPLE_FARM_NAME = "Vung Trong Xoai Cat Chu S-22 (kich ban thu hoi mau)"

#: Ba tổ chức của chuỗi cung ứng trong kịch bản (mỗi tầng thuộc một mắt xích).
ORG_GROWER = "HTX Xoai My Xuong"
ORG_PROCESSOR = "Cong Ty Che Bien Mekong"
ORG_RETAILER = "Chuoi Sieu Thi Xanh"

#: Địa điểm của từng tổ chức - lô thành phẩm nằm ở hai chi nhánh bán lẻ khác nhau.
LOCATION_GROWER = "Kho HTX My Xuong"
LOCATION_PROCESSOR = "Nha May Che Bien Mekong"
LOCATION_RETAIL_1 = "Sieu Thi Xanh - Chi Nhanh 1"
LOCATION_RETAIL_2 = "Sieu Thi Xanh - Chi Nhanh 2"

#: Khối lượng (kg) của từng lô theo đúng sơ đồ ở docstring - nguồn của đáp án đếm tay.
ROOT_QUANTITY = Decimal("1000.0000")
TIER3_MERGE_INPUTS: dict[str, list[tuple[str, Decimal]]] = {
    "M1": [("B11", Decimal("250.0000")), ("B21", Decimal("200.0000"))],
    "M2": [("B12", Decimal("150.0000")), ("B31", Decimal("250.0000"))],
    "M3": [("B22", Decimal("200.0000")), ("B32", Decimal("100.0000"))],
}
TIER4_SPLITS: dict[str, list[tuple[str, Decimal]]] = {
    "M1": [("F11", Decimal("200.0000")), ("F12", Decimal("150.0000"))],
    "M2": [("F21", Decimal("150.0000")), ("F22", Decimal("150.0000"))],
    "M3": [("F31", Decimal("100.0000")), ("F32", Decimal("100.0000"))],
}

#: Lô thành phẩm đã bán cho người tiêu dùng: (mã lô, khối lượng đã bán).
CONSUMED_QUANTITIES: dict[str, Decimal] = {
    "F12": Decimal("150.0000"),  # bán hết -> lô chuyển trạng thái ``consumed``
    "F22": Decimal("50.0000"),   # bán một phần -> còn lại 100 kg để thu hồi
}

#: Lô thành phẩm nằm ở chi nhánh bán lẻ thứ hai (còn lại ở chi nhánh 1).
RETAIL_BRANCH_2_BATCHES: tuple[str, ...] = ("F12", "F22", "F32")

#: Tệp đáp án đếm tay đi kèm kịch bản (hai người đếm độc lập).
ANSWER_KEY_PATH = BACKEND_DIR / "data" / "recall_benchmark.json"


def sample_code(suffix: str) -> str:
    """Mã lô của kịch bản mẫu: ``LOT-S22-<suffix>``."""
    return f"{SAMPLE_BATCH_PREFIX}{suffix}"


def check_environment_safety() -> None:
    """Kiểm tra biến môi trường trước khi chạy.

    LƯU Ý KỸ THUẬT (AC / S-22): Script TUYỆT ĐỐI không được phép chạy trên
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
            f"Script seed_recall_sample.py TUYỆT ĐỐI BỊ CHẶN để bảo vệ dữ liệu sản xuất!"
        )

    is_prod_flag = os.getenv("IS_PRODUCTION", "false").strip().lower()
    if is_prod_flag in {"true", "1", "yes"}:
        raise RuntimeError(
            "❌ NGUY HIỂM: Biến môi trường IS_PRODUCTION=true. "
            "Script seed_recall_sample.py bị từ chối thực thi trên môi trường thật!"
        )

    print(f"🔒 [BẢO MẬT] Kiểm tra môi trường an toàn: APP_ENV='{env}' -> CHO PHÉP THỰC THI.")


def cleanup_recall_sample(db: Session) -> int:
    """Xóa sạch kịch bản thu hồi mẫu cũ trước khi dựng lại (idempotent).

    Xóa theo đúng phạm vi của kịch bản: mọi lô mang prefix ``LOT-S22-`` kèm quan hệ
    phả hệ của chúng, và các địa điểm mẫu đã khai báo ở :data:`LOCATION_*`.

    Returns:
        int: Số lô mẫu đã xóa.
    """
    batch_ids = list(
        db.scalars(
            select(Batch.id).where(Batch.batch_code.like(f"{SAMPLE_BATCH_PREFIX}%"))
        ).all()
    )

    deleted_count = len(batch_ids)

    if batch_ids:
        db.execute(
            delete(BatchRelation).where(
                (BatchRelation.parent_batch_id.in_(batch_ids))
                | (BatchRelation.child_batch_id.in_(batch_ids))
            )
        )
        # Gỡ liên kết cha - con trước khi xóa lô để không vướng khóa ngoại tự tham chiếu.
        db.execute(
            text("UPDATE batches SET parent_id = NULL WHERE batch_code LIKE :prefix"),
            {"prefix": f"{SAMPLE_BATCH_PREFIX}%"},
        )
        db.execute(delete(Batch).where(Batch.id.in_(batch_ids)))

    sample_locations = (
        (ORG_GROWER, LOCATION_GROWER),
        (ORG_PROCESSOR, LOCATION_PROCESSOR),
        (ORG_RETAILER, LOCATION_RETAIL_1),
        (ORG_RETAILER, LOCATION_RETAIL_2),
    )
    for organization, name in sample_locations:
        db.execute(
            delete(Location).where(
                Location.organization == organization,
                Location.name == name,
            )
        )

    db.commit()
    if deleted_count:
        print(f"🧹 Đã dọn dẹp {deleted_count} lô mẫu cũ và quan hệ phả hệ liên quan.")
    else:
        print("🧹 Không có dữ liệu kịch bản thu hồi mẫu cũ cần dọn dẹp.")
    return deleted_count


def _get_or_create_farm(db: Session) -> Farm:
    """Lấy (hoặc tạo) vùng trồng xuất xứ của kịch bản mẫu."""
    farm = db.scalar(select(Farm).where(Farm.name == SAMPLE_FARM_NAME))
    if farm is None:
        farm = Farm(
            name=SAMPLE_FARM_NAME,
            location="Xa My Xuong, Huyen Cao Lanh, Tinh Dong Thap",
            area=6.0,
            owner=ORG_GROWER,
        )
        db.add(farm)
        db.commit()
        db.refresh(farm)
    return farm


def _create_locations(db: Session) -> dict[str, Location]:
    """Khai báo 4 địa điểm mẫu (1 kho HTX + 1 nhà máy + 2 chi nhánh siêu thị)."""
    definitions = (
        (LOCATION_GROWER, ORG_GROWER, "Ap My An, Xa My Xuong, Dong Thap"),
        (LOCATION_PROCESSOR, ORG_PROCESSOR, "Lo B2 Khu Cong Nghiep My Tra, Dong Thap"),
        (LOCATION_RETAIL_1, ORG_RETAILER, "So 12 Nguyen Hue, TP Cao Lanh, Dong Thap"),
        (LOCATION_RETAIL_2, ORG_RETAILER, "So 5 Tran Phu, TP Sa Dec, Dong Thap"),
    )

    locations: dict[str, Location] = {}
    for name, organization, address in definitions:
        location = db.scalar(
            select(Location).where(
                Location.organization == organization,
                Location.name == name,
            )
        )
        if location is None:
            location = Location(organization=organization, name=name, address=address)
            db.add(location)
            db.flush()
        locations[name] = location
    db.commit()
    return locations


def _assign_holder(
    db: Session,
    batches: list[Batch],
    organization: str,
    location: Location | None = None,
) -> None:
    """Gán tổ chức đang giữ (và địa điểm) cho lô.

    Kịch bản mẫu mô phỏng sẵn chuỗi bàn giao HTX → nhà máy → siêu thị nên gán
    trực tiếp ``current_holder_org`` theo mắt xích của từng tầng, thay vì chạy
    từng bước bàn giao qua API.
    """
    for batch in batches:
        batch.current_holder_org = organization
        if location is not None:
            batch.current_location_id = location.id
    db.commit()


def seed_recall_sample(db: Session) -> dict[str, Any]:
    """Dựng kịch bản thu hồi mẫu 4 tầng qua 3 tổ chức bằng hàm tách/gộp của ứng dụng.

    Returns:
        dict: Báo cáo các lô theo tầng, tổ chức, nguồn gộp và các lô đã tiêu thụ.
    """
    cleanup_recall_sample(db)

    farm = _get_or_create_farm(db)
    farm_id = farm.id
    locations = _create_locations(db)
    print(f"🌱 Vùng trồng xuất xứ: #{farm_id} - '{farm.name}'")

    # ---------------------------------------------------------------- TẦNG 1 ---
    print("\n📦 [TẦNG 1 - F0] 3 lô thu hoạch tại HTX (1000 kg/lô)...")
    tier1: list[Batch] = []
    for suffix in ("A1", "A2", "A3"):
        batch = Batch(
            farm_id=farm_id,
            product_name=f"Xoai Cat Chu Thu Hoach Lo {suffix} (S-22)",
            quantity=ROOT_QUANTITY,
            harvest_date=date(2026, 10, 1),
            batch_code=sample_code(suffix),
            current_holder_org=ORG_GROWER,
            current_location_id=locations[LOCATION_GROWER].id,
        )
        db.add(batch)
        tier1.append(batch)
    db.commit()
    for batch in tier1:
        db.refresh(batch)
        print(f"   + {batch.batch_code}: {batch.quantity} kg @ {batch.current_holder_org}")

    # ---------------------------------------------------------------- TẦNG 2 ---
    print("\n🔪 [TẦNG 2 - F1] Tách mỗi lô gốc thành 2 lô tại nhà máy...")
    tier2: list[Batch] = []
    split_plan = {
        "A1": [("B11", Decimal("400.0000")), ("B12", Decimal("350.0000"))],
        "A2": [("B21", Decimal("300.0000")), ("B22", Decimal("400.0000"))],
        "A3": [("B31", Decimal("450.0000")), ("B32", Decimal("250.0000"))],
    }
    for parent_suffix, children in split_plan.items():
        parent = next(b for b in tier1 if b.batch_code == sample_code(parent_suffix))
        _, created = split_batch(
            db=db,
            parent_batch_id=parent.id,
            child_quantities=[quantity for _, quantity in children],
            child_codes=[sample_code(suffix) for suffix, _ in children],
            child_product_names=[
                f"Xoai Cat Chu So Che Lo {suffix} (S-22)" for suffix, _ in children
            ],
        )
        tier2.extend(created)
    _assign_holder(db, tier2, ORG_PROCESSOR, locations[LOCATION_PROCESSOR])
    for batch in tier2:
        print(f"   + {batch.batch_code}: {batch.quantity} kg @ {batch.current_holder_org}")

    # ---------------------------------------------------------------- TẦNG 3 ---
    print("\n🔄 [TẦNG 3 - F2] Gộp chéo từ nhiều nguồn (mỗi lô gộp nhận từ 2 nhánh)...")
    batch_by_suffix = {batch.batch_code: batch for batch in tier2}
    tier3: list[Batch] = []
    merge_sources: dict[str, list[str]] = {}
    for merge_suffix, inputs in TIER3_MERGE_INPUTS.items():
        merged, _ = merge_batches(
            db=db,
            parents=[
                (batch_by_suffix[sample_code(source_suffix)].id, quantity)
                for source_suffix, quantity in inputs
            ],
            product_name=f"Xoai Cat Chu Phoi Tron Lo {merge_suffix} (S-22)",
            harvest_date=date(2026, 10, 5),
            farm_id=farm_id,
            batch_code=sample_code(merge_suffix),
        )
        tier3.append(merged)
        merge_sources[merged.batch_code] = [
            sample_code(source_suffix) for source_suffix, _ in inputs
        ]
    _assign_holder(db, tier3, ORG_PROCESSOR, locations[LOCATION_PROCESSOR])
    for batch in tier3:
        sources = " + ".join(merge_sources[batch.batch_code])
        print(f"   + {batch.batch_code}: {batch.quantity} kg = {sources}")

    # ---------------------------------------------------------------- TẦNG 4 ---
    print("\n🎯 [TẦNG 4 - F3] Tách thành phẩm bán lẻ tại chuỗi siêu thị...")
    batch_by_suffix = {batch.batch_code: batch for batch in tier3}
    tier4: list[Batch] = []
    for merge_suffix, children in TIER4_SPLITS.items():
        parent = batch_by_suffix[sample_code(merge_suffix)]
        _, created = split_batch(
            db=db,
            parent_batch_id=parent.id,
            child_quantities=[quantity for _, quantity in children],
            child_codes=[sample_code(suffix) for suffix, _ in children],
            child_product_names=[
                f"Xoai Cat Chu Thanh Pham Lo {suffix} (S-22)" for suffix, _ in children
            ],
        )
        tier4.extend(created)

    branch_1 = [b for b in tier4 if not any(b.batch_code == sample_code(s) for s in RETAIL_BRANCH_2_BATCHES)]
    branch_2 = [b for b in tier4 if any(b.batch_code == sample_code(s) for s in RETAIL_BRANCH_2_BATCHES)]
    _assign_holder(db, branch_1, ORG_RETAILER, locations[LOCATION_RETAIL_1])
    _assign_holder(db, branch_2, ORG_RETAILER, locations[LOCATION_RETAIL_2])

    # ------------------------------------------------------------ TIÊU THỤ ---
    print("\n🛒 [TIÊU THỤ] Ghi nhận hàng đã bán cho người tiêu dùng...")
    consumed_batches: list[dict[str, Any]] = []
    for suffix, sold_quantity in CONSUMED_QUANTITIES.items():
        batch = db.scalar(
            select(Batch).where(Batch.batch_code == sample_code(suffix))
        )
        batch.quantity = round(batch.quantity - float(sold_quantity), 4)
        batch.consumed_quantity = round((batch.consumed_quantity or 0.0) + float(sold_quantity), 4)
        if batch.quantity <= 0:
            batch.quantity = 0.0
            batch.status = BATCH_STATUS_CONSUMED
        db.commit()
        db.refresh(batch)
        consumed_batches.append(
            {
                "batch_code": batch.batch_code,
                "consumed_quantity": batch.consumed_quantity,
                "remaining_quantity": batch.quantity,
                "status": batch.status,
            }
        )
        print(
            f"   + {batch.batch_code}: đã bán {batch.consumed_quantity} kg, "
            f"còn {batch.quantity} kg ({batch.status})"
        )

    for batch in tier4:
        db.refresh(batch)
        print(
            f"   = {batch.batch_code}: {batch.quantity} kg còn lại @ "
            f"{batch.current_location_name}"
        )

    relations_count = len(
        db.scalars(
            select(BatchRelation).where(
                BatchRelation.child_batch_id.in_([batch.id for batch in tier3])
            )
        ).all()
    )

    print("\n🎉 HOÀN THÀNH: 4 tầng - 3 tổ chức - "
          f"{len(tier1) + len(tier2) + len(tier3) + len(tier4)} lô, "
          f"{relations_count} quan hệ gộp nhiều nguồn, 1 lô đã tiêu thụ.")

    return {
        "farm_id": farm_id,
        "organizations": [ORG_GROWER, ORG_PROCESSOR, ORG_RETAILER],
        "tiers": {
            "1": tier1,
            "2": tier2,
            "3": tier3,
            "4": tier4,
        },
        "merge_sources": merge_sources,
        "consumed_batches": consumed_batches,
        "relations_count": relations_count,
    }


def print_recall_scope_preview(db: Session) -> None:
    """In phạm vi thu hồi của từng lô gốc để người đọc đối chiếu với tệp đáp án."""
    print("\n🔎 PHẠM VI THU HỒI TÍNH BẰNG THUẬT TOÁN (đối chiếu với tệp đáp án đếm tay):")
    print(f"   📄 Tệp đáp án: {ANSWER_KEY_PATH}")

    roots = list(
        db.scalars(
            select(Batch)
            .where(Batch.batch_code.like(f"{SAMPLE_BATCH_PREFIX}A%"))
            .order_by(Batch.batch_code.asc())
        ).all()
    )
    for root in roots:
        scope = build_recall_scope(db, root)
        print(
            f"\n   ● Lô gốc {scope.root.batch_code} còn {scope.root.remaining_quantity} kg "
            f"→ {scope.descendant_count} hậu duệ qua {scope.max_depth} tầng, "
            f"còn thu hồi được {scope.descendant_remaining_quantity} kg "
            f"(cả lô gốc: {scope.total_remaining_quantity} kg, "
            f"đã ra thị trường: {scope.consumed_quantity} kg)"
        )
        for entry in scope.descendants:
            recall_note = "" if entry.is_recallable else " [đã hết hàng]"
            print(
                f"      - tầng {entry.depth} | {entry.batch_code} | "
                f"{entry.organization} | còn {entry.remaining_quantity} kg | "
                f"đã bán {entry.consumed_quantity} kg{recall_note}"
            )


def main() -> None:
    """Hàm thực thi chính của script."""
    print("=" * 80)
    print("🚀 BẮT ĐẦU DỰNG KỊCH BẢN THU HỒI MẪU 4 TẦNG QUA 3 TỔ CHỨC (S-22)")
    print("=" * 80)

    check_environment_safety()
    init_db()

    db = SessionLocal()
    try:
        seed_recall_sample(db)
        print_recall_scope_preview(db)
        print("\n" + "=" * 80)
        print("✅ KỊCH BẢN THU HỒI MẪU SẴN SÀNG ĐỂ ĐỐI CHIẾU ĐÁP ÁN ĐẾM TAY (S-22)!")
        print("=" * 80)
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - chỉ dùng khi chạy bằng dòng lệnh
        print(f"\n❌ LỖI THỰC THI SCRIPT: {exc}", file=sys.stderr)
        sys.exit(1)
