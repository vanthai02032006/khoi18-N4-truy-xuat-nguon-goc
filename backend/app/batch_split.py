"""Nghiệp vụ tách lô nông sản có khoá dòng lô mẹ và số học Decimal (T-40 / T-41 / SCRUM-57).

================================================================================
🎯 TÀI LIỆU KỸ THUẬT: VÌ SAO ĐỌC-RỒI-GHI THÔNG THƯỜNG KHÔNG ĐỦ AN TOÀN?
================================================================================
1. Hiện tượng Race Condition / Lost Update / Overselling (Bán khống nông sản):
   Trong hệ thống quản lý chuỗi cung ứng nông sản phân tán, nhiều nhân viên kho
   hoặc dây chuyền đóng gói có thể cùng lúc thực hiện thao tác tách lô nông sản.
   Giả sử lô mẹ M đang có khối lượng khả dụng là 1,000.0000 kg.
   Có 2 giao dịch đồng thời T1 và T2 cùng truy cập lô M:
     - T1 muốn tách 600.0000 kg.
     - T2 muốn tách 700.0000 kg.

   Nếu hệ thống chỉ dùng cơ chế ĐỌC-RỒI-GHI thông thường (Non-locking Read-then-Write):
     - Bước 1 (t1): T1 thực thi `SELECT * FROM batches WHERE id = M`: nhận được 1,000.0000 kg.
                    T1 kiểm tra: 600.0000 <= 1,000.0000 -> HỢP LỆ.
     - Bước 2 (t2): T2 cũng thực thi `SELECT * FROM batches WHERE id = M` (vì T1 chưa commit):
                    T2 cũng nhận được 1,000.0000 kg.
                    T2 kiểm tra: 700.0000 <= 1,000.0000 -> HỢP LỆ.
     - Bước 3 (t3): T1 trừ kho: 1,000 - 600 = 400.0000 kg, ghi vào DB và commit.
     - Bước 4 (t4): T2 trừ kho dựa trên số cũ nó đọc được: 1,000 - 700 = 300.0000 kg và commit!
   => HẬU QUẢ NGHIÊM TRỌNG:
     - Tổng khối lượng đã tách thực tế ra thị trường là 600 + 700 = 1,300.0000 kg!
     - Hệ thống bị BÁN KHỐNG / XUẤT KHỐNG 300.0000 kg nông sản ảo không có thật.
     - Dữ liệu phả hệ bị sai lệch, vi phạm nguyên tắc bảo toàn vật chất trong chuỗi cung ứng.

2. Cơ chế khắc phục triệt để bằng `SELECT ... FOR UPDATE` (Khóa dòng bi quan - Pessimistic Lock):
   - Khi T1 bắt đầu giao dịch, câu lệnh đầu tiên là:
     `SELECT * FROM batches WHERE id = :id FOR UPDATE`
   - Hệ quản trị cơ sở dữ liệu (PostgreSQL/MySQL/RDBMS) sẽ đặt ngay một Khóa Độc Quyền
     (Exclusive Row-Level Lock) trên bản ghi lô mẹ đó.
   - Khi T2 đến sau và cũng gọi `SELECT ... FOR UPDATE` với cùng `id`, T2 BUỘC PHẢI CHỜ (BLOCK)
     tại tầng database cho đến khi T1 hoàn tất giao dịch (`commit()` hoặc `rollback()`).
   - Khi T1 commit xong (lô mẹ đã trừ còn 400.0000 kg), khóa được giải phóng cho T2.
   - T2 được đánh thức và đọc được GIÁ TRỊ MỚI NHẤT ĐÃ TRỪ là 400.0000 kg.
   - T2 thực hiện kiểm tra nghiệp vụ: 700.0000 > 400.0000 kg
     => T2 BỊ TỪ CHỐI NGAY LẬP TỨC (raise ValueError / HTTP 400 Bad Request).
   - Đảm bảo 100% không bao giờ xảy ra tình trạng âm kho hay xuất khống!

3. Ràng buộc số học: Tuyệt đối dùng số thập phân cố định (Decimal / Numeric), KHÔNG DÙNG float:
   - Chuẩn IEEE 754 float gây sai số nhị phân (ví dụ 0.1 + 0.2 = 0.30000000000000004).
   - Khi trừ dần khối lượng nhiều lần, lỗi làm tròn float sẽ tích tụ thành sai số âm
     (như -0.00000000000001), làm đứt gãy kiểm tra điều kiện hoặc hỏng dữ liệu.
   - Dùng `Decimal` với độ chính xác cố định 4 chữ số thập phân (`Decimal('0.0001')`)
     đảm bảo mọi phép cộng trừ khối lượng đều chính xác tuyệt đối từng miligram.
================================================================================
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache import trace_cache
from app.models import Batch, User

# Độ chính xác thập phân cố định cho khối lượng nông sản: 4 chữ số sau dấu phẩy (0.0001 kg = 100 mg)
QUANTITY_PRECISION = Decimal("0.0001")


def to_fixed_decimal(val: Decimal | float | str | int) -> Decimal:
    """Chuyển đổi an toàn giá trị bất kỳ sang Decimal với 4 chữ số thập phân cố định.

    Tuyệt đối không để sai số float lọt vào: chuyển float sang str trước khi tạo Decimal.
    """
    if isinstance(val, Decimal):
        d = val
    elif isinstance(val, (int, str)):
        try:
            d = Decimal(str(val))
        except InvalidOperation as err:
            raise ValueError(f"Giá trị khối lượng không hợp lệ: {val!r}") from err
    elif isinstance(val, float):
        # Chuyển qua chuỗi trước để không mang rác float IEEE 754 vào Decimal
        d = Decimal(str(val))
    else:
        try:
            d = Decimal(str(val))
        except (InvalidOperation, TypeError) as err:
            raise ValueError(f"Kiểu dữ liệu khối lượng không hợp lệ: {type(val).__name__}") from err

    # Làm tròn chuẩn ROUND_HALF_UP về 4 chữ số thập phân
    return d.quantize(QUANTITY_PRECISION, rounding=ROUND_HALF_UP)


def generate_batch_code(
    farm_id: int,
    harvest_date: date,
    parent_id: int | None = None,
    sequence: int = 1,
) -> str:
    """Hàm sinh mã lô nông sản theo chuẩn T-19.

    Format quy chuẩn:
    - Nếu là lô con (phả hệ T-39 / T-40 / T-41):
      LOT-{farm_id:02d}-P{parent_id}.{sequence:02d}-{YYYYMMDD}
      Ví dụ: LOT-01-P01.01-20260925
    - Nếu là lô gốc (F0):
      LOT-{farm_id:02d}-{YYYYMMDD}-{sequence:02d}
      Ví dụ: LOT-01-20260925-01
    """
    date_str = harvest_date.strftime("%Y%m%d")
    if parent_id is not None:
        return f"LOT-{farm_id:02d}-P{parent_id}.{sequence:02d}-{date_str}"
    return f"LOT-{farm_id:02d}-{date_str}-{sequence:02d}"


def split_batch(
    db: Session,
    parent_batch_id: int,
    child_quantities: list[Decimal | float | str | int],
    operator_user: User | None = None,
    error_at_child_index: int | None = None,
    child_codes: list[str] | None = None,
    child_product_names: list[str] | None = None,
) -> tuple[Batch, list[Batch]]:
    """Hàm tách nhận lô mẹ và danh sách khối lượng lô con với khoá dòng và số học Decimal.

    Quy trình thực thi có khóa dòng (T-41 / SCRUM-57):
    1. Kiểm tra danh sách khối lượng con đầu vào (dùng Decimal, > 0).
    2. Thực thi `SELECT ... FOR UPDATE` để KHÓA DÒNG LÔ MẸ ngay từ đầu giao dịch.
    3. Đọc số dư mới nhất của lô mẹ (sau khi mọi giao dịch trước đó đã kết thúc).
    4. Kiểm tra: nếu tổng tách vượt khối lượng còn lại -> TỪ CHỐI NGAY LẬP TỨC.
    5. Trừ khối lượng còn lại của lô mẹ (đảm bảo không bao giờ âm).
    6. Tạo từng lô con bằng hàm sinh mã T-19 hoặc mã chỉ định, kế thừa nguồn gốc của lô mẹ.
    7. Commit transaction nguyên tử hoặc Rollback sạch sẽ nếu có lỗi.
    8. Invalidate cache phả hệ của lô mẹ.

    Args:
        db: SQLAlchemy Session đang kết nối database.
        parent_batch_id: ID của lô nông sản mẹ cần tách.
        child_quantities: Danh sách khối lượng (Decimal/kg) của các lô con cần tạo.
        operator_user: Tài khoản người dùng thực hiện thao tác (nếu có).
        error_at_child_index: Hook giả lập lỗi ở lô con thứ N (1-indexed) để kiểm thử rollback.
        child_codes: Danh sách mã định danh tùy chỉnh cho các lô con (nếu có).
        child_product_names: Danh sách tên sản phẩm cho các lô con (nếu có).

    Returns:
        tuple[Batch, list[Batch]]: (Lô mẹ sau khi trừ khối lượng, Danh sách các lô con đã tạo).

    Raises:
        ValueError: Khi dữ liệu không hợp lệ (danh sách rỗng, khối lượng <= 0,
                    lô mẹ không tồn tại, hoặc tổng khối lượng tách vượt quá khối lượng lô mẹ).
        RuntimeError / Exception: Khi có lỗi phát sinh trong transaction, tự động rollback sạch sẽ.
    """
    # -------------------------------------------------------------------------
    # Bước 1: Validate và chuẩn hoá danh sách khối lượng lô con sang Decimal cố định
    # -------------------------------------------------------------------------
    if not child_quantities:
        raise ValueError("Danh sách khối lượng lô con không được để trống.")

    child_decimals: list[Decimal] = []
    zero_decimal = Decimal("0.0000")

    for idx, raw_qty in enumerate(child_quantities, start=1):
        if raw_qty is None:
            raise ValueError(f"Khối lượng lô con thứ {idx} không được để trống (None).")
        try:
            qty_dec = to_fixed_decimal(raw_qty)
        except ValueError as e:
            raise ValueError(f"Khối lượng lô con thứ {idx} không hợp lệ: {e}") from e

        if qty_dec <= zero_decimal:
            raise ValueError(
                f"Khối lượng lô con thứ {idx} phải lớn hơn 0 (nhận được: {qty_dec} kg)."
            )
        child_decimals.append(qty_dec)

    # Tính tổng khối lượng yêu cầu tách bằng Decimal (tuyệt đối không sai số)
    total_split: Decimal = sum(child_decimals)

    # -------------------------------------------------------------------------
    # Bước 2: Bắt đầu giao dịch và ĐỌC LÔ MẸ VỚI KHÓA DÒNG BI QUAN
    # (SELECT ... FOR UPDATE) TRƯỚC KHI KIỂM TRA TỔNG KHỐI LƯỢNG
    # -------------------------------------------------------------------------
    try:
        # Cú pháp SQLAlchemy 2.0 with_for_update():
        # Sinh câu lệnh SQL: SELECT ... FROM batches WHERE batches.id = :id FOR UPDATE
        # Khi giao dịch thứ hai cùng truy vấn dòng này, CSDL sẽ bắt giao dịch thứ hai
        # PHẢI CHỜ (BLOCK) cho đến khi giao dịch thứ nhất commit xong.
        stmt = (
            select(Batch)
            .where(Batch.id == parent_batch_id)
            .with_for_update()
        )
        parent_batch = db.scalar(stmt)

        if parent_batch is None:
            raise ValueError(f"Không tìm thấy lô mẹ với ID #{parent_batch_id}.")

        # Chuyển khối lượng hiện tại của lô mẹ sang Decimal cố định
        parent_qty: Decimal = to_fixed_decimal(parent_batch.quantity)

        # ---------------------------------------------------------------------
        # Bước 3: KIỂM TỔNG KHỐI LƯỢNG TRONG GIAO DỊCH ĐÃ KHÓA
        # Tiêu chí nghiệm thu (AC): Thao tác tách vượt khối lượng còn lại bị từ chối ngay!
        # ---------------------------------------------------------------------
        if total_split > parent_qty:
            raise ValueError(
                f"Tổng khối lượng tách ({total_split} kg) vượt quá "
                f"khối lượng khả dụng của lô mẹ ({parent_qty} kg). "
                f"Thao tác bị từ chối ngay lập tức để bảo vệ tính toàn vẹn kho."
            )

        # ---------------------------------------------------------------------
        # Bước 4: TRỪ KHỐI LƯỢNG CHÍNH XÁC KHÔNG BỊ SAI LỆCH SỐ ÂM (AC)
        # ---------------------------------------------------------------------
        remaining_qty: Decimal = parent_qty - total_split

        # Kiểm tra bất biến an toàn (Invariance check): số dư không bao giờ âm
        if remaining_qty < zero_decimal:
            raise ValueError(
                f"Lỗi tính toán: Khối lượng còn lại bị âm ({remaining_qty} kg). Giao dịch bị hủy."
            )

        # Cập nhật khối lượng mới vào lô mẹ
        parent_batch.quantity = remaining_qty

        # Đếm số lượng lô con hiện có để đánh số thứ tự sequence T-19
        existing_children_count = len(parent_batch.children) if parent_batch.children else 0

        created_children: list[Batch] = []
        for i, qty_dec in enumerate(child_decimals, start=1):
            # Test hook: Ném lỗi giả lập ở lô con thứ N để kiểm tra cơ chế rollback sạch sẽ
            if error_at_child_index is not None and i == error_at_child_index:
                raise RuntimeError(
                    f"Giả lập lỗi tại lô con thứ {i} để kiểm tra cơ chế rollback sạch sẽ."
                )

            seq = existing_children_count + i

            # Sinh mã định danh lô con theo chuẩn T-19 hoặc lấy từ child_codes nếu có
            if child_codes and i - 1 < len(child_codes) and child_codes[i - 1]:
                code = child_codes[i - 1]
            else:
                code = generate_batch_code(
                    farm_id=parent_batch.farm_id,
                    harvest_date=parent_batch.harvest_date,
                    parent_id=parent_batch.id,
                    sequence=seq,
                )

            # Tên sản phẩm kế thừa hoặc lấy từ child_product_names nếu có
            if child_product_names and i - 1 < len(child_product_names) and child_product_names[i - 1]:
                prod_name = child_product_names[i - 1]
            else:
                prod_name = parent_batch.product_name

            # Ràng buộc kỹ thuật & Phả hệ T-39:
            # Lô con kế thừa nguồn gốc của lô mẹ, không cho phép nhập sai lệch
            child = Batch(
                farm_id=parent_batch.farm_id,             # Kế thừa nguồn gốc vùng trồng
                product_name=prod_name,                   # Tên sản phẩm
                quantity=qty_dec,                         # Khối lượng Decimal chuẩn
                harvest_date=parent_batch.harvest_date,   # Kế thừa ngày thu hoạch
                parent_id=parent_batch.id,                # Ghi quan hệ cha - con T-39
                batch_code=code,                          # Sinh mã T-19 hoặc mã tùy chỉnh
                is_restricted=parent_batch.is_restricted, # Kế thừa cờ bảo mật T-54
                owner=operator_user.username if operator_user else parent_batch.owner,
            )
            db.add(child)
            created_children.append(child)

        # ---------------------------------------------------------------------
        # Bước 5: Commit giao dịch nguyên tử (Atomic Commit)
        # Khi commit thành công, khóa Exclusive Lock được giải phóng cho các giao dịch khác
        # ---------------------------------------------------------------------
        db.commit()
        db.refresh(parent_batch)
        for c in created_children:
            db.refresh(c)

        # Invalidate cache phả hệ của lô mẹ vì cấu trúc cây đã có thêm nhánh mới
        trace_cache.invalidate(parent_batch.id)

        return parent_batch, created_children

    except Exception:
        # Nếu có bất kỳ lỗi nào ném ra (kể cả ở lô con thứ hai):
        # Toàn bộ giao dịch được ROLLBACK sạch sẽ:
        # - Khối lượng của lô mẹ được phục hồi lại nguyên trạng ban đầu
        # - Không có bất kỳ lô con mồ côi nào được lưu vào cơ sở dữ liệu
        db.rollback()
        raise
