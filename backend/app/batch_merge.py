"""Nghiệp vụ gộp lô nông sản (Batch Merge) có khóa dòng chống deadlock và bảo toàn khối lượng (T-44 / T-46 / SCRUM-60 / SCRUM-62).

================================================================================
🎯 TÀI LIỆU KỸ THUẬT: CƠ CHẾ CHỐNG DEADLOCK & BẢO TOÀN KHỐI LƯỢNG TRONG GIAO DỊCH GỘP
================================================================================
1. Vấn đề Deadlock trong giao dịch gộp nhiều lô mẹ (Deadlock in Multi-row Locking):
   Khi gộp nông sản, một giao dịch phải khóa cùng lúc nhiều bản ghi lô mẹ.
   Giả sử có 2 giao dịch đồng thời:
     - Giao dịch T1: Gộp lô #1 và lô #2.
     - Giao dịch T2: Gộp lô #2 và lô #1.
   Nếu không sắp xếp thứ tự:
     - T1 khóa dòng #1, chuẩn bị xin khóa dòng #2.
     - Đồng thời, T2 khóa dòng #2, chuẩn bị xin khóa dòng #1.
     - T1 bị chặn bởi T2 (chờ dòng #2).
     - T2 bị chặn bởi T1 (chờ dòng #1).
   => DEADLOCK CHU KỲ (Circular Wait)! CSDL buộc phải hủy một giao dịch.

2. Giải pháp: Thứ tự khóa toàn cục cố định (Global Lock Ordering / Ascending ID Sorting):
   - Trước khi thực thi bất kỳ lệnh `SELECT ... FOR UPDATE` nào, hệ thống luôn
     sắp xếp danh sách `parent_batch_id` theo thứ tự TĂNG DẦN: `sorted(parent_ids)`.
   - Cả T1 và T2 đều phải xin khóa dòng #1 trước, rồi mới tới dòng #2.
   - Nhờ vậy, loại trừ 100% điều kiện "Chờ đợi vòng tròn" (Circular Wait),
     triệt tiêu hoàn toàn nguy cơ Deadlock giữa các giao dịch gộp đồng thời.

3. Bảo toàn khối lượng và kiểm tra tồn kho bằng số học Decimal (Numeric 12,4):
   - Mọi số lượng `used_quantity` và tồn kho `quantity` đều dùng kiểu Decimal với 4 chữ
     số thập phân cố định. Tuyệt đối không dùng float để tránh sai số nhị phân IEEE 754.
   - Kiểm tra `used_quantity <= parent_batch.quantity` sau khi đã khóa dòng.
   - Trừ kho nguyên tử trên từng lô mẹ và sinh bản ghi `BatchRelation` lưu vết phả hệ.
================================================================================
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.batch_split import to_fixed_decimal
from app.cache import trace_cache
from app.models import Batch, BatchRelation, Farm, User


def merge_batches(
    db: Session,
    parents: list[tuple[int, Decimal | float | str | int]],
    product_name: str,
    harvest_date: date,
    farm_id: int | None = None,
    batch_code: str | None = None,
    operator_user: User | None = None,
    is_restricted: bool = False,
    error_after_flush: bool = False,
) -> tuple[Batch, list[BatchRelation]]:
    """Thực thi nghiệp vụ gộp nhiều lô mẹ thành một lô mới với khóa dòng chống deadlock.

    Args:
        db: SQLAlchemy Session đang kết nối CSDL.
        parents: Danh sách các tuple (parent_batch_id, used_quantity) cần lấy từ mỗi lô mẹ.
        product_name: Tên sản phẩm của lô gộp mới hình thành.
        harvest_date: Ngày thu hoạch hoặc ngày gộp lô mới.
        farm_id: ID vùng trồng xuất xứ (nếu None sẽ kế thừa từ lô mẹ đầu tiên).
        batch_code: Mã định danh lô gộp (nếu None sẽ sinh tự động).
        operator_user: Tài khoản người dùng thực hiện thao tác (nếu có).
        is_restricted: Cờ bảo mật hạn chế xem theo T-54.
        error_after_flush: Hook giả lập lỗi sau khi flush để kiểm thử rollback giao dịch.

    Returns:
        tuple[Batch, list[BatchRelation]]: (Lô mới được tạo, Danh sách các bản ghi quan hệ phả hệ).

    Raises:
        ValueError: Khi dữ liệu không hợp lệ (ít hơn 2 lô mẹ, trùng lặp lô mẹ,
                    khối lượng <= 0, lô mẹ không tồn tại, hoặc vượt quá khối lượng khả dụng).
        RuntimeError / Exception: Khi có lỗi trong transaction, tự động rollback sạch sẽ.
    """
    # -------------------------------------------------------------------------
    # Bước 1: Validate dữ liệu đầu vào
    # -------------------------------------------------------------------------
    if not parents or len(parents) < 2:
        raise ValueError("Giao dịch gộp yêu cầu tối thiểu 2 lô nông sản mẹ.")

    parent_ids = [p[0] for p in parents]
    if len(parent_ids) != len(set(parent_ids)):
        raise ValueError(
            "Danh sách lô mẹ bị trùng lặp mã lô (duplicate parent batch ID trong cùng yêu cầu gộp)."
        )

    # Chuẩn hoá danh sách sang Decimal
    validated_parents: list[tuple[int, Decimal]] = []
    zero_decimal = Decimal("0.0000")

    for pid, raw_qty in parents:
        if raw_qty is None:
            raise ValueError(f"Khối lượng lấy từ lô mẹ #{pid} không được để trống (None).")
        try:
            qty_dec = to_fixed_decimal(raw_qty)
        except ValueError as e:
            raise ValueError(f"Khối lượng lấy từ lô mẹ #{pid} không hợp lệ: {e}") from e

        if qty_dec <= zero_decimal:
            raise ValueError(
                f"Khối lượng lấy từ lô mẹ #{pid} phải lớn hơn 0 (nhận được: {qty_dec} kg)."
            )
        validated_parents.append((pid, qty_dec))

    # -------------------------------------------------------------------------
    # Bước 2: CHỐNG DEADLOCK BẰNG THỨ TỰ KHÓA TOÀN CỤC CỐ ĐỊNH (ASCENDING ID)
    # -------------------------------------------------------------------------
    sorted_ids = sorted(parent_ids)
    items_by_id = {pid: qty_dec for pid, qty_dec in validated_parents}

    try:
        # Bước 3: Lock và nạp các parent batch theo thứ tự ID tăng dần
        parents_by_id: dict[int, Batch] = {}
        for pid in sorted_ids:
            # Query với with_for_update() để khóa dòng bi quan
            stmt = select(Batch).where(Batch.id == pid).with_for_update()
            parent_batch = db.scalar(stmt)
            if parent_batch is None:
                raise ValueError(f"Không tìm thấy lô mẹ với ID #{pid}.")
            parents_by_id[pid] = parent_batch

        # Bước 4: Kiểm tra khối lượng khả dụng của từng lô mẹ
        for pid in sorted_ids:
            parent_batch = parents_by_id[pid]
            needed_qty = items_by_id[pid]
            current_qty = to_fixed_decimal(parent_batch.quantity)

            if needed_qty > current_qty:
                code_str = f" ('{parent_batch.batch_code}')" if parent_batch.batch_code else ""
                raise ValueError(
                    f"Lô mẹ #{pid}{code_str} chỉ còn {current_qty} kg, "
                    f"không đủ để lấy {needed_qty} kg gộp."
                )

        # Bước 5: Xác định vùng trồng xuất xứ và mã lô mới
        first_parent = parents_by_id[sorted_ids[0]]
        target_farm_id = farm_id if farm_id is not None else first_parent.farm_id
        target_farm = db.get(Farm, target_farm_id)
        if target_farm is None:
            raise ValueError(f"Không tìm thấy vùng trồng với ID #{target_farm_id}.")

        # Tính tổng khối lượng lô mới bằng Decimal bảo toàn
        total_quantity = sum((items_by_id[pid] for pid in sorted_ids), zero_decimal)

        # Xác định batch_code
        new_code = batch_code
        if not new_code:
            date_clean = harvest_date.strftime("%Y%m%d")
            new_code = f"LOT-{target_farm_id:02d}-MERGE-{date_clean}-{sorted_ids[0]:02d}"

        # Bước 6: Tạo lô mới
        new_batch = Batch(
            farm_id=target_farm_id,
            product_name=product_name.strip(),
            quantity=total_quantity,
            harvest_date=harvest_date,
            parent_id=None,  # Lô gộp có nhiều cha nên ghi nhận chi tiết qua bảng batch_relations
            batch_code=new_code,
            is_restricted=is_restricted,
            owner=operator_user.username if operator_user else first_parent.owner,
        )
        db.add(new_batch)
        db.flush()  # Sinh new_batch.id để làm khóa ngoại cho batch_relations

        # Bước 7: Trừ số lượng lô mẹ và tạo quan hệ BatchRelation
        created_relations: list[BatchRelation] = []
        for pid in sorted_ids:
            parent_batch = parents_by_id[pid]
            used_qty = items_by_id[pid]
            current_qty = to_fixed_decimal(parent_batch.quantity)

            new_qty = current_qty - used_qty
            if new_qty < zero_decimal:
                raise ValueError(
                    f"Lỗi tính toán: Khối lượng lô mẹ #{pid} bị âm ({new_qty} kg). Hủy giao dịch."
                )

            parent_batch.quantity = new_qty

            relation = BatchRelation(
                parent_batch_id=pid,
                child_batch_id=new_batch.id,
                used_quantity=used_qty,
            )
            db.add(relation)
            created_relations.append(relation)

        # Hook giả lập lỗi phục vụ test rollback
        if error_after_flush:
            raise RuntimeError("Giả lập lỗi sau khi flush để kiểm thử cơ chế rollback sạch sẽ.")

        # Bước 8: Commit transaction nguyên tử
        db.commit()
        db.refresh(new_batch)
        for r in created_relations:
            db.refresh(r)
        for pid in sorted_ids:
            db.refresh(parents_by_id[pid])
            trace_cache.invalidate(pid)

        trace_cache.invalidate(new_batch.id)

        return new_batch, created_relations

    except Exception:
        db.rollback()
        raise


def merge_batches_transaction(
    db: Session,
    payload: Any,
    current_user: User | None = None,
) -> Any:
    """Wrapper chuyển tiếp gọi từ REST API endpoint `POST /batches/merge`.

    Tương thích với Pydantic schema BatchMergeRequest và trả về BatchMergeResponse.
    """
    from app.schemas import (
        BatchMergeResponse,
        BatchRelationResponse,
        BatchResponse,
        ParentRemainingResponse,
    )

    parents_tuples = [(p.parent_batch_id, p.used_quantity) for p in payload.parents]

    new_batch, relations = merge_batches(
        db=db,
        parents=parents_tuples,
        product_name=payload.product_name,
        harvest_date=payload.harvest_date,
        farm_id=payload.farm_id,
        batch_code=payload.batch_code,
        operator_user=current_user,
        is_restricted=getattr(payload, "is_restricted", False),
    )

    parent_remainings = [
        ParentRemainingResponse(
            parent_batch_id=r.parent_batch_id,
            remaining_quantity=db.get(Batch, r.parent_batch_id).quantity,
        )
        for r in relations
    ]

    return BatchMergeResponse(
        new_batch=BatchResponse.model_validate(new_batch),
        relations=[BatchRelationResponse.model_validate(r) for r in relations],
        parent_remainings=parent_remainings,
        message=(
            f"Gộp thành công {len(relations)} lô mẹ thành lô mới #{new_batch.id} "
            f"('{new_batch.product_name}', {new_batch.quantity} kg)."
        ),
    )


__all__ = [
    "merge_batches",
    "merge_batches_transaction",
]
