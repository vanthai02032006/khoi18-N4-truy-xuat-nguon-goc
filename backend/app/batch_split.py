"""Nghiệp vụ tách lô nông sản (S-17 / T-39 / SCRUM-55).

Mục tiêu & Mô tả công việc:
- Hàm tách nhận lô mẹ và danh sách khối lượng lô con.
- Mở transaction bảo đảm tính nguyên tử (atomic transaction).
- Tạo từng lô con bằng hàm sinh mã ở T-19.
- Ghi quan hệ ở T-39 (parent_id = parent_batch.id).
- Trừ khối lượng còn lại của lô mẹ: parent_batch.quantity -= sum(child_quantities).
- Rollback sạch sẽ khi có lỗi ném ra ở bất kỳ lô con nào (kể cả lô con thứ hai).
- Ràng buộc: Lô con kế thừa loại sản phẩm và nguồn gốc của lô mẹ, không cho phép nhập sai lệch.
"""

from datetime import date
from typing import Any
from sqlalchemy.orm import Session

from app.cache import trace_cache
from app.models import Batch, User


def generate_batch_code(
    farm_id: int,
    harvest_date: date,
    parent_id: int | None = None,
    sequence: int = 1,
) -> str:
    """Hàm sinh mã lô nông sản theo chuẩn T-19.

    Format quy chuẩn:
    - Nếu là lô con (phả hệ T-39): LOT-{farm_id:02d}-P{parent_id}.{sequence:02d}-{YYYYMMDD}
      Ví dụ: LOT-01-P01.01-20260925
    - Nếu là lô gốc (F0): LOT-{farm_id:02d}-{YYYYMMDD}-{sequence:02d}
      Ví dụ: LOT-01-20260925-01
    """
    date_str = harvest_date.strftime("%Y%m%d")
    if parent_id is not None:
        return f"LOT-{farm_id:02d}-P{parent_id}.{sequence:02d}-{date_str}"
    return f"LOT-{farm_id:02d}-{date_str}-{sequence:02d}"


def split_batch(
    db: Session,
    parent_batch_id: int,
    child_quantities: list[float],
    operator_user: User | None = None,
    error_at_child_index: int | None = None,
) -> tuple[Batch, list[Batch]]:
    """Hàm tách nhận lô mẹ và danh sách khối lượng lô con.

    Args:
        db: SQLAlchemy Session đang kết nối database.
        parent_batch_id: ID của lô nông sản mẹ cần tách.
        child_quantities: Danh sách khối lượng (kg) của các lô con cần tạo.
        operator_user: Tài khoản người dùng thực hiện thao tác (nếu có).
        error_at_child_index: Hook giả lập lỗi ở lô con thứ N (1-indexed) để kiểm thử rollback.

    Returns:
        tuple[Batch, list[Batch]]: (Lô mẹ sau khi trừ khối lượng, Danh sách các lô con đã tạo).

    Raises:
        ValueError: Khi dữ liệu không hợp lệ (danh sách rỗng, khối lượng <= 0,
                    lô mẹ không tồn tại, hoặc tổng khối lượng tách vượt quá khối lượng lô mẹ).
        RuntimeError / Exception: Khi có lỗi phát sinh trong transaction, tự động rollback sạch sẽ.
    """
    # 1. Validate danh sách khối lượng đầu vào
    if not child_quantities:
        raise ValueError("Danh sách khối lượng lô con không được để trống.")

    for idx, qty in enumerate(child_quantities, start=1):
        if qty is None or qty <= 0:
            raise ValueError(f"Khối lượng lô con thứ {idx} phải lớn hơn 0 (nhận được: {qty}).")

    total_split = round(sum(child_quantities), 4)

    # 2. Mở transaction nguyên tử
    try:
        parent_batch = db.get(Batch, parent_batch_id)
        if not parent_batch:
            raise ValueError(f"Không tìm thấy lô mẹ với ID #{parent_batch_id}.")

        if total_split > parent_batch.quantity:
            raise ValueError(
                f"Tổng khối lượng tách ({total_split:.2f} kg) vượt quá "
                f"khối lượng khả dụng của lô mẹ ({parent_batch.quantity:.2f} kg)."
            )

        # Trừ khối lượng còn lại của lô mẹ
        parent_batch.quantity = round(parent_batch.quantity - total_split, 4)

        # Số lượng lô con hiện có để đánh số thứ tự sequence T-19
        existing_children_count = len(parent_batch.children) if parent_batch.children else 0

        created_children: list[Batch] = []
        for i, qty in enumerate(child_quantities, start=1):
            # Test hook: Ném lỗi ở lô con thứ N để kiểm thử tính toàn vẹn và rollback sạch sẽ
            if error_at_child_index is not None and i == error_at_child_index:
                raise RuntimeError(
                    f"Giả lập lỗi tại lô con thứ {i} để kiểm tra cơ chế rollback sạch sẽ."
                )

            seq = existing_children_count + i

            # Sinh mã lô con bằng hàm sinh mã ở T-19
            code = generate_batch_code(
                farm_id=parent_batch.farm_id,
                harvest_date=parent_batch.harvest_date,
                parent_id=parent_batch.id,
                sequence=seq,
            )

            # Ràng buộc kỹ thuật: Lô con kế thừa loại sản phẩm và nguồn gốc của lô mẹ, không cho phép nhập sai lệch.
            # Ghi quan hệ ở T-39: parent_id = parent_batch.id
            child = Batch(
                farm_id=parent_batch.farm_id,             # Kế thừa nguồn gốc vùng trồng
                product_name=parent_batch.product_name,   # Kế thừa loại sản phẩm
                quantity=round(qty, 4),
                harvest_date=parent_batch.harvest_date,   # Kế thừa ngày thu hoạch
                parent_id=parent_batch.id,                # Ghi quan hệ T-39
                batch_code=code,                          # Sinh mã T-19
                is_restricted=parent_batch.is_restricted, # Kế thừa cờ bảo mật T-54
                owner=operator_user.username if operator_user else parent_batch.owner,
            )
            db.add(child)
            created_children.append(child)

        # Commit giao dịch
        db.commit()
        db.refresh(parent_batch)
        for c in created_children:
            db.refresh(c)

        # Invalidate cache truy vết của lô mẹ vì phả hệ đã có thêm lô con
        trace_cache.invalidate(parent_batch.id)

        return parent_batch, created_children

    except Exception:
        # Toàn bộ giao dịch được rollback sạch sẽ:
        # - Khối lượng của lô mẹ được khôi phục lại nguyên trạng
        # - Không có bất kỳ lô con nào được ghi vào cơ sở dữ liệu
        db.rollback()
        raise
