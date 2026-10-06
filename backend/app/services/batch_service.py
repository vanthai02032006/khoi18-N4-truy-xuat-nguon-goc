"""Service xử lý nghiệp vụ Lô nông sản và Giao dịch gộp (Batch Merge) - SCRUM-60.

Yêu cầu transaction nghiệp vụ:
1. Validate các lô mẹ tồn tại, used_quantity > 0, không vượt tồn còn lại.
2. Không cho phép trùng lặp mã lô mẹ trong cùng một yêu cầu gộp.
3. Lock các lô mẹ theo thứ tự ID cố định (tăng dần) để chống deadlock và race condition.
4. Toàn bộ thao tác nằm trong database transaction:
   - Tạo lô mới
   - Trừ remaining_quantity của các lô mẹ
   - Lưu vết quan hệ phả hệ BatchRelation cho tất cả lô mẹ
   - Nếu xảy ra bất kỳ lỗi nào -> ROLLBACK toàn bộ.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Batch, BatchCustodyHistory, BatchRelation, Farm, User
from app.schemas import (
    BatchMergeRequest,
    BatchMergeResponse,
    BatchRelationResponse,
    BatchResponse,
    ParentRemainingResponse,
)


def merge_batches_transaction(
    db: Session,
    payload: BatchMergeRequest,
    current_user: User | None = None,
) -> BatchMergeResponse:
    """Thực thi giao dịch gộp nhiều lô mẹ thành một lô mới trong Database Transaction.

    Args:
        db: SQLAlchemy Session.
        payload: Thông tin gộp lô gồm danh sách lô mẹ và thông tin lô mới.
        current_user: Tài khoản người dùng đang thực hiện (để gán organization).

    Returns:
        BatchMergeResponse: Lô mới hình thành, các quan hệ phả hệ và số dư còn lại của lô mẹ.

    Raises:
        HTTPException 400: Nếu dữ liệu không hợp lệ, trùng lặp lô mẹ, hoặc vượt tồn kho.
        HTTPException 404: Nếu lô mẹ hoặc vùng trồng không tồn tại.
    """
    if not payload.parents or len(payload.parents) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Giao dịch gộp yêu cầu tối thiểu 2 lô mẹ.",
        )

    # Kiểm tra trùng lặp mã lô mẹ
    parent_ids = [item.parent_batch_id for item in payload.parents]
    if len(parent_ids) != len(set(parent_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Danh sách lô mẹ bị trùng lặp mã lô (duplicate parent batch ID).",
        )

    # Kiểm tra khối lượng lấy > 0
    for item in payload.parents:
        if item.used_quantity <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Khối lượng lấy từ lô mẹ #{item.parent_batch_id} phải lớn hơn 0 (nhận {item.used_quantity}kg).",
            )

    # Sắp xếp các parent_batch_id theo thứ tự cố định (tăng dần) để tránh deadlock khi lock
    sorted_ids = sorted(parent_ids)
    items_by_id = {item.parent_batch_id: item for item in payload.parents}

    # BẮT ĐẦU TRANSACTION NGHIỆP VỤ
    try:
        # Bước 1: Lock và nạp các parent batch theo thứ tự sắp xếp cố định
        parents_by_id: dict[int, Batch] = {}
        for pid in sorted_ids:
            # Query với with_for_update() để chống race condition khi nhiều request cùng lúc
            query = select(Batch).where(Batch.id == pid)
            try:
                query = query.with_for_update()
            except Exception:
                pass  # Một số database/dialect không hỗ trợ with_for_update thì query thông thường

            parent_batch = db.scalar(query)
            if parent_batch is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Không tìm thấy lô mẹ có ID={pid}.",
                )
            parents_by_id[pid] = parent_batch

        # Bước 2: Kiểm tra số lượng tồn còn lại của từng lô mẹ
        for pid in sorted_ids:
            parent_batch = parents_by_id[pid]
            needed_qty = items_by_id[pid].used_quantity
            # Lấy remaining_quantity; nếu chưa có thì lấy quantity
            current_rem = (
                parent_batch.remaining_quantity
                if parent_batch.remaining_quantity is not None
                else parent_batch.quantity
            )

            if needed_qty > current_rem:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Lô mẹ #{pid} ('{parent_batch.product_name}') chỉ còn {current_rem}kg, "
                        f"không đủ để lấy {needed_qty}kg."
                    ),
                )

        # Bước 3: Xác định vùng trồng xuất xứ
        first_parent = parents_by_id[sorted_ids[0]]
        target_farm_id = payload.farm_id or first_parent.farm_id
        farm = db.get(Farm, target_farm_id)
        if farm is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy vùng trồng có ID={target_farm_id}.",
            )

        # Bước 4: Xác định tổ chức sở hữu
        org_id = (
            payload.organization_id
            or getattr(current_user, "organization_id", None)
            or first_parent.organization_id
            or "ORG_MY_XUONG"
        )

        # Bước 5: Tính tổng sản lượng lô mới
        new_quantity = round(sum(item.used_quantity for item in payload.parents), 4)

        # Bước 6: Tạo lô mới
        new_batch = Batch(
            farm_id=target_farm_id,
            product_name=payload.product_name.strip(),
            quantity=new_quantity,
            remaining_quantity=new_quantity,
            harvest_date=payload.harvest_date,
            organization_id=org_id,
        )
        db.add(new_batch)
        db.flush()  # Sinh new_batch.id để làm foreign key cho quan hệ phả hệ

        # Bước 7: Trừ số lượng lô mẹ và tạo quan hệ BatchRelation
        relations_responses: list[BatchRelationResponse] = []
        parent_remainings: list[ParentRemainingResponse] = []

        for pid in sorted_ids:
            parent_batch = parents_by_id[pid]
            used_qty = items_by_id[pid].used_quantity
            current_rem = (
                parent_batch.remaining_quantity
                if parent_batch.remaining_quantity is not None
                else parent_batch.quantity
            )

            new_rem = round(current_rem - used_qty, 4)
            parent_batch.remaining_quantity = new_rem
            parent_batch.quantity = new_rem  # Cập nhật đồng bộ để hiển thị

            relation = BatchRelation(
                parent_batch_id=pid,
                child_batch_id=new_batch.id,
                used_quantity=used_qty,
            )
            db.add(relation)
            db.flush()

            relations_responses.append(
                BatchRelationResponse(
                    id=relation.id,
                    parent_batch_id=relation.parent_batch_id,
                    child_batch_id=relation.child_batch_id,
                    used_quantity=relation.used_quantity,
                )
            )
            parent_remainings.append(
                ParentRemainingResponse(
                    parent_batch_id=pid,
                    remaining_quantity=new_rem,
                )
            )

        # Ghi nhận lịch sử nắm giữ cho lô mới (SCRUM-70)
        db.add(BatchCustodyHistory(batch_id=new_batch.id, organization_id=org_id))

        # Bước 8: COMMIT TOÀN BỘ TRANSACTION
        db.commit()
        db.refresh(new_batch)

        return BatchMergeResponse(
            new_batch=BatchResponse.model_validate(new_batch),
            relations=relations_responses,
            parent_remainings=parent_remainings,
        )

    except Exception:
        # Nếu bất kỳ bước nào gặp lỗi -> ROLLBACK TOÀN BỘ
        db.rollback()
        raise


__all__ = ["merge_batches_transaction"]
