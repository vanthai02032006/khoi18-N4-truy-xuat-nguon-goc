"""Router quản lý phả hệ lô hàng (Batch Lineage - T-37 / SCRUM-53).

Cung cấp các API:
- POST   /batches/lineage: Ghi nhận quan hệ tách (SPLIT) hoặc gộp (MERGE) giữa các lô hàng.
- GET    /batches/lineage: Lấy danh sách toàn bộ các quan hệ phả hệ.
- GET    /batches/{batch_id}/lineage/backward: Truy ngược (Backward trace) danh sách lô cha.
- GET    /batches/{batch_id}/lineage/forward: Truy xuôi (Forward trace) danh sách lô con.
- GET    /batches/{batch_id}/genealogy: Toàn bộ phả hệ cha & con của một lô.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.lineage import LineageCycleError, trace_ancestors_bfs, trace_descendants_bfs
from app.models import Batch, BatchLineage, RELATION_TYPES, User
from app.schemas import (
    BatchAncestorsBFSResponse,
    BatchGenealogyNode,
    BatchGenealogyResponse,
    BatchLineageCreate,
    BatchLineageResponse,
    RecallItem,
    RecallOrderResponse,
)
from app.security import require_farmer

router = APIRouter(
    prefix="/batches",
    tags=["Phả hệ lô hàng (Batch Lineage)"],
)


@router.post(
    "/lineage",
    response_model=BatchLineageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ghi nhận quan hệ phả hệ giữa các lô hàng (Tách / Gộp)",
    description=(
        "Khai báo liên kết giữa lô cha (parent_batch_id) và lô con (child_batch_id) "
        "kèm khối lượng chuyển và loại quan hệ (SPLIT hoặc MERGE).\n\n"
        "**Ràng buộc:** Cặp [lô cha, lô con] là DUY NHẤT (UNIQUE). "
        "Nếu ghi trùng sẽ trả về `409 Conflict`."
    ),
)
def record_batch_lineage(
    data: BatchLineageCreate,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> BatchLineage:
    """Ghi nhận quan hệ phả hệ giữa 2 lô hàng."""
    if data.parent_batch_id == data.child_batch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lô cha và lô con không được trùng nhau.",
        )

    parent_batch = db.get(Batch, data.parent_batch_id)
    if not parent_batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô cha với ID #{data.parent_batch_id}.",
        )

    child_batch = db.get(Batch, data.child_batch_id)
    if not child_batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô con với ID #{data.child_batch_id}.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    lineage_record = BatchLineage(
        parent_batch_id=data.parent_batch_id,
        child_batch_id=data.child_batch_id,
        transferred_quantity=data.transferred_quantity,
        relation_type=data.relation_type,
        created_at=now_iso,
    )

    try:
        db.add(lineage_record)
        db.commit()
        db.refresh(lineage_record)
        return lineage_record
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Quan hệ giữa lô cha #{data.parent_batch_id} và lô con #{data.child_batch_id} "
                "đã tồn tại trong hệ thống (vi phạm ràng buộc UNIQUE)."
            ),
        )


@router.get(
    "/lineage",
    response_model=list[BatchLineageResponse],
    summary="Danh sách tất cả các quan hệ phả hệ",
)
def list_batch_lineages(
    relation_type: str | None = Query(None, description="Lọc theo loại: SPLIT hoặc MERGE"),
    db: Session = Depends(get_db),
) -> list[BatchLineage]:
    """Lấy danh sách các quan hệ phả hệ trong hệ thống."""
    stmt = select(BatchLineage).order_by(BatchLineage.id.desc())
    if relation_type:
        stmt = stmt.where(BatchLineage.relation_type == relation_type.upper())
    return list(db.scalars(stmt).all())


@router.get(
    "/{batch_id}/lineage/backward",
    response_model=list[BatchGenealogyNode],
    summary="Truy ngược phả hệ (Backward trace - Tìm danh sách lô cha)",
    description=(
        "Tìm tất cả các lô cha đã đóng góp tạo nên lô hàng này (dựa trên child_batch_id, "
        "tối ưu bằng index ix_batch_lineage_child_batch_id)."
    ),
)
def trace_backward(
    batch_id: int,
    db: Session = Depends(get_db),
) -> list[BatchGenealogyNode]:
    """Truy ngược danh sách lô cha."""
    target_batch = db.get(Batch, batch_id)
    if not target_batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô hàng với ID #{batch_id}.",
        )

    stmt = (
        select(BatchLineage)
        .where(BatchLineage.child_batch_id == batch_id)
        .order_by(BatchLineage.id.asc())
    )
    records = db.scalars(stmt).all()

    result = []
    for r in records:
        parent = db.get(Batch, r.parent_batch_id)
        result.append(
            BatchGenealogyNode(
                batch_id=r.parent_batch_id,
                batch_code=parent.code if parent else "UNKNOWN",
                product_name=parent.product_name if parent else "UNKNOWN",
                transferred_quantity=r.transferred_quantity,
                relation_type=r.relation_type,
                created_at=r.created_at,
            )
        )
    return result


@router.get(
    "/{batch_id}/lineage/forward",
    response_model=list[BatchGenealogyNode],
    summary="Truy xuôi phả hệ (Forward trace - Tìm danh sách lô con)",
    description=(
        "Tìm tất cả các lô con được phân tách hoặc sinh ra từ lô hàng này (dựa trên parent_batch_id, "
        "tối ưu bằng index ix_batch_lineage_parent_batch_id)."
    ),
)
def trace_forward(
    batch_id: int,
    db: Session = Depends(get_db),
) -> list[BatchGenealogyNode]:
    """Truy xuôi danh sách lô con."""
    target_batch = db.get(Batch, batch_id)
    if not target_batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô hàng với ID #{batch_id}.",
        )

    stmt = (
        select(BatchLineage)
        .where(BatchLineage.parent_batch_id == batch_id)
        .order_by(BatchLineage.id.asc())
    )
    records = db.scalars(stmt).all()

    result = []
    for r in records:
        child = db.get(Batch, r.child_batch_id)
        result.append(
            BatchGenealogyNode(
                batch_id=r.child_batch_id,
                batch_code=child.code if child else "UNKNOWN",
                product_name=child.product_name if child else "UNKNOWN",
                transferred_quantity=r.transferred_quantity,
                relation_type=r.relation_type,
                created_at=r.created_at,
            )
        )
    return result


@router.get(
    "/{batch_id}/genealogy",
    response_model=BatchGenealogyResponse,
    summary="Tổng hợp toàn bộ phả hệ lô hàng (Cả cha và con)",
    description="Truy vết đầy đủ cả nguồn gốc (Parents) lẫn đầu ra (Children) của lô hàng.",
)
def get_batch_genealogy(
    batch_id: int,
    db: Session = Depends(get_db),
) -> BatchGenealogyResponse:
    """Tổng hợp toàn bộ phả hệ."""
    target_batch = db.get(Batch, batch_id)
    if not target_batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô hàng với ID #{batch_id}.",
        )

    parents = trace_backward(batch_id=batch_id, db=db)
    children = trace_forward(batch_id=batch_id, db=db)

    return BatchGenealogyResponse(
        target_batch_id=target_batch.id,
        target_batch_code=target_batch.code,
        target_product_name=target_batch.product_name,
        parents=parents,
        children=children,
    )


@router.get(
    "/{identifier}/ancestors",
    response_model=BatchAncestorsBFSResponse,
    summary="Truy ngược nguồn gốc tổ tiên theo từng tầng bằng BFS (T-48 / SCRUM-64)",
    description=(
        "Nhận mã lô hoặc ID, duyệt ngược quan hệ con -> cha bằng BFS theo tầng dùng Queue, "
        "giữ tập đã thăm, trả về danh sách tổ tiên theo tầng và danh sách lô gốc (không có cha). "
        "Nếu gặp lô đã thăm trên đường đi hiện tại sẽ ngắt và ném lỗi chu trình kèm tên lô gây ra."
    ),
)
def get_batch_ancestors(
    identifier: str,
    db: Session = Depends(get_db),
) -> BatchAncestorsBFSResponse:
    """Truy ngược phả hệ theo tầng bằng BFS."""
    # Tìm mã lô nếu identifier là ID số
    batch_code = identifier
    if identifier.isdigit():
        b = db.get(Batch, int(identifier))
        if b:
            batch_code = b.code
    else:
        b = db.scalar(select(Batch).where(Batch.code == identifier))
        if not b:
            # Thử kiểm tra xem có quan hệ nào trong database hay benchmark không
            pass

    try:
        result = trace_ancestors_bfs(batch_code=batch_code, db=db)
    except LineageCycleError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return BatchAncestorsBFSResponse(
        target_batch=result.target_batch,
        ancestors_by_level=result.ancestors_by_level,
        root_batches=result.root_batches,
        all_ancestors=result.all_ancestors,
    )


@router.get(
    "/{identifier}/recall",
    response_model=RecallOrderResponse,
    summary="Phát lệnh thu hồi sản phẩm từ lô gốc (S-39 / Truy vết hậu duệ)",
    description=(
        "Khi mở lệnh thu hồi từ một lô gốc/lô cha, hệ thống duyệt toàn bộ hậu duệ theo các tầng tách và gộp, "
        "truy vết qua các tổ chức liên quan không để sót bất kỳ lô con nào. "
        "Nếu một hậu duệ là lô gộp có nguồn khác trộn vào, lô đó vẫn nằm trong danh sách và được đánh dấu rõ ràng. "
        "Hỗ trợ làm mới danh sách động khi có lô mới được tách ra."
    ),
)
def get_batch_recall(
    identifier: str,
    db: Session = Depends(get_db),
) -> RecallOrderResponse:
    """Truy vết toàn bộ hậu duệ phục vụ phát lệnh thu hồi."""
    batch_code = identifier
    if identifier.isdigit():
        b = db.get(Batch, int(identifier))
        if b:
            batch_code = b.code
    else:
        b = db.scalar(select(Batch).where(Batch.code == identifier))

    if not b:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô hàng với mã/ID '{identifier}'.",
        )

    descendants = trace_descendants_bfs(root_batch_code=batch_code, db=db)

    items = [
        RecallItem(
            batch_code=d["batch_code"],
            batch_id=d.get("batch_id"),
            product_name=d.get("product_name"),
            quantity=d.get("quantity"),
            organization=d.get("organization"),
            level=d.get("level", 1),
            relation_type=d.get("relation_type", "SPLIT"),
            is_merged_multiple_sources=d.get("is_merged_multiple_sources", False),
            other_sources=d.get("other_sources", []),
        )
        for d in descendants
    ]

    affected_orgs = sorted(list(set(d["organization"] for d in descendants if d.get("organization"))))

    return RecallOrderResponse(
        root_batch_code=batch_code,
        total_affected_batches=len(items),
        total_affected_organizations=len(affected_orgs),
        affected_organizations=affected_orgs,
        items=items,
    )


