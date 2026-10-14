"""Thuật toán **liệt kê phạm vi thu hồi** theo phả hệ lô nông sản (S-22).

User Story:
    *"Là thành viên phát triển, tôi muốn một kịch bản thu hồi mẫu có đáp án đếm tay
    để thuật toán liệt kê thu hồi được kiểm bằng đáp án độc lập."*

Đặc tính kỹ thuật:
- Duyệt **xuôi** từ lô gốc xuống các lô hậu duệ theo từng tầng (BFS) - cùng cách
  tiếp cận với :func:`app.lineage.find_descendants_bfs`, nhưng trả kèm **khối lượng
  còn lại** của mỗi lô để biết phải thu hồi bao nhiêu ở đâu.
- Trong cùng một tầng, lô được sắp theo **mã lô** nên kết quả ổn định
  (deterministic) giữa các lần chạy và giữa các môi trường CSDL.
- Tập ``visited`` chặn mọi vòng lặp nên hàm không thể treo khi dữ liệu bị lặp cạnh.

Hai tầng sử dụng:
1. :func:`enumerate_recall_scope` - **hàm thuần** (không cần CSDL), nhận quan hệ
   cha-con + số liệu từng lô. Nhờ tách khỏi CSDL, thuật toán đối chiếu trực tiếp
   với **tệp đáp án đếm tay** ``backend/data/recall_benchmark.json`` (S-22).
2. :func:`build_recall_scope` - đọc dữ liệu thật từ CSDL (bảng ``batch_relations``
   cho các phép gộp và cột ``batches.parent_id`` cho các phép tách) rồi gọi lại
   hàm thuần ở trên.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BATCH_STATUS_CONSUMED, Batch, BatchRelation


def batch_code_of(batch: Batch) -> str:
    """Mã lô hiển thị của một lô (lô cũ chưa có ``batch_code`` thì dùng ``LOT-<id>``)."""
    return batch.batch_code or f"LOT-{batch.id:04d}"


@dataclass(frozen=True)
class RecallBatchFact:
    """Số liệu của **một lô** phục vụ liệt kê thu hồi.

    Attributes:
        batch_code: Mã lô.
        remaining_quantity: Khối lượng **còn lại, còn thu hồi được** (kg).
        consumed_quantity: Khối lượng **đã bán cho người tiêu dùng** (kg).
        organization: Tổ chức đang giữ lô.
        status: Trạng thái lô (``available`` / ``consumed``).
        batch_id: ID lô trong CSDL (``None`` khi chỉ có dữ liệu mẫu).
        product_name: Tên sản phẩm của lô.
    """

    batch_code: str
    remaining_quantity: float
    consumed_quantity: float = 0.0
    organization: str = ""
    status: str = ""
    batch_id: Optional[int] = None
    product_name: str = ""


@dataclass(frozen=True)
class RecallScopeEntry:
    """Một lô nằm trong phạm vi thu hồi kèm khối lượng phải xử lý.

    Attributes:
        batch_code: Mã lô.
        depth: Khoảng cách theo tầng so với lô gốc (1 = lô con trực tiếp).
        remaining_quantity: Khối lượng còn lại (kg) - phần cần thu hồi.
        consumed_quantity: Khối lượng đã ra thị trường (kg).
        total_quantity: Tổng khối lượng của lô (còn lại + đã tiêu thụ).
        organization: Tổ chức đang giữ lô.
        status: Trạng thái lô.
        is_recallable: Còn hàng để thu hồi (chưa tiêu thụ hết và còn khối lượng).
        batch_id: ID lô trong CSDL (``None`` khi chỉ có dữ liệu mẫu).
        product_name: Tên sản phẩm của lô.
    """

    batch_code: str
    depth: int
    remaining_quantity: float
    consumed_quantity: float
    total_quantity: float
    organization: str
    status: str
    is_recallable: bool
    batch_id: Optional[int] = None
    product_name: str = ""


@dataclass(frozen=True)
class RecallScope:
    """Kết quả liệt kê thu hồi của một lô gốc: chính lô gốc + toàn bộ hậu duệ."""

    root: RecallScopeEntry
    descendants: list[RecallScopeEntry] = field(default_factory=list)

    @property
    def descendant_count(self) -> int:
        """Số lô hậu duệ phải rà soát khi lô gốc có sự cố."""
        return len(self.descendants)

    @property
    def max_depth(self) -> int:
        """Số tầng sâu nhất tính từ lô gốc (0 nếu lô gốc chưa tách/gộp lần nào)."""
        return max((entry.depth for entry in self.descendants), default=0)

    @property
    def descendant_remaining_quantity(self) -> float:
        """Tổng khối lượng còn thu hồi được ở **các lô hậu duệ** (kg)."""
        return round(sum(entry.remaining_quantity for entry in self.descendants), 4)

    @property
    def total_remaining_quantity(self) -> float:
        """Tổng khối lượng còn thu hồi được của **lô gốc và mọi hậu duệ** (kg)."""
        return round(self.root.remaining_quantity + self.descendant_remaining_quantity, 4)

    @property
    def consumed_quantity(self) -> float:
        """Tổng khối lượng đã ra thị trường trong phạm vi (kg) - phần không thu hồi được."""
        return round(
            self.root.consumed_quantity
            + sum(entry.consumed_quantity for entry in self.descendants),
            4,
        )


def _to_entry(fact: RecallBatchFact, depth: int) -> RecallScopeEntry:
    """Chuyển số liệu thô của lô thành một dòng kết quả liệt kê (đã làm tròn 4 số)."""
    remaining = round(float(fact.remaining_quantity), 4)
    consumed = round(float(fact.consumed_quantity or 0.0), 4)
    return RecallScopeEntry(
        batch_code=fact.batch_code,
        depth=depth,
        remaining_quantity=remaining,
        consumed_quantity=consumed,
        total_quantity=round(remaining + consumed, 4),
        organization=fact.organization or "",
        status=fact.status or "",
        is_recallable=fact.status != BATCH_STATUS_CONSUMED and remaining > 0,
        batch_id=fact.batch_id,
        product_name=fact.product_name or "",
    )


def _build_child_map(relations: Sequence[Mapping[str, object]]) -> dict[str, list[str]]:
    """Dựng danh sách kề xuôi ``lô mẹ -> [lô con]``, đã sắp xếp theo mã lô."""
    child_map: dict[str, list[str]] = {}
    for relation in relations:
        parent = str(relation["parent"])
        child = str(relation["child"])
        if parent == child:
            continue
        children = child_map.setdefault(parent, [])
        if child not in children:
            children.append(child)

    for children in child_map.values():
        children.sort()
    return child_map


def enumerate_recall_scope(
    root_code: str,
    relations: Sequence[Mapping[str, object]],
    facts: Mapping[str, RecallBatchFact],
) -> list[RecallScopeEntry]:
    """Liệt kê toàn bộ hậu duệ của một lô gốc kèm khối lượng còn lại từng lô.

    Duyệt BFS từ ``root_code``: hết tầng này mới sang tầng kế tiếp, trong cùng tầng
    sắp theo mã lô nên kết quả lặp lại được (deterministic) - điều kiện để đối chiếu
    với **đáp án đếm tay** ở ``backend/data/recall_benchmark.json``.

    Args:
        root_code: Mã lô gốc cần liệt kê phạm vi thu hồi (ví dụ lô thu hoạch có sự cố).
        relations: Quan hệ cha-con ``[{"parent": "LOT-A", "child": "LOT-B"}, ...]``.
        facts: Số liệu từng lô theo mã lô (khối lượng còn lại, đã tiêu thụ, tổ chức...).

    Returns:
        list[RecallScopeEntry]: Các lô hậu duệ theo thứ tự tầng, **rỗng** nếu lô gốc
        chưa tách/gộp lần nào (không có hậu duệ).

    Raises:
        ValueError: Nếu thiếu số liệu của lô gốc hoặc của một lô hậu duệ.
    """
    if root_code not in facts:
        raise ValueError(f"Không có số liệu của lô gốc '{root_code}' để liệt kê thu hồi.")

    child_map = _build_child_map(relations)

    queue: deque[tuple[str, int]] = deque([(root_code, 0)])
    visited: set[str] = {root_code}
    entries: list[RecallScopeEntry] = []

    while queue:
        current_code, depth = queue.popleft()
        for child_code in child_map.get(current_code, []):
            if child_code in visited:
                continue
            visited.add(child_code)
            fact = facts.get(child_code)
            if fact is None:
                raise ValueError(
                    f"Thiếu số liệu của lô hậu duệ '{child_code}' khi liệt kê thu hồi từ '{root_code}'."
                )
            entries.append(_to_entry(fact, depth + 1))
            queue.append((child_code, depth + 1))

    return entries


def load_lineage_relations(db: Session) -> list[dict[str, str]]:
    """Đọc quan hệ phả hệ từ CSDL: cạnh **gộp** (``batch_relations``) và cạnh **tách** (``parent_id``).

    Phép gộp lưu nhiều lô mẹ cho một lô con trong bảng ``batch_relations`` (T-44),
    còn phép tách chỉ ghi ``batches.parent_id``. Hợp cả hai nguồn lại mới dựng đủ
    đồ thị 4 tầng của kịch bản mẫu S-22.
    """
    parent_child_pairs: set[tuple[int, int]] = set()

    for parent_id, child_id in db.execute(
        select(BatchRelation.parent_batch_id, BatchRelation.child_batch_id)
    ).all():
        parent_child_pairs.add((int(parent_id), int(child_id)))

    for batch_id, parent_id in db.execute(
        select(Batch.id, Batch.parent_id).where(Batch.parent_id.is_not(None))
    ).all():
        parent_child_pairs.add((int(parent_id), int(batch_id)))

    if not parent_child_pairs:
        return []

    involved_ids = {batch_id for pair in parent_child_pairs for batch_id in pair}
    code_by_id = {
        int(row_id): (row_code or f"LOT-{int(row_id):04d}")
        for row_id, row_code in db.execute(
            select(Batch.id, Batch.batch_code).where(Batch.id.in_(involved_ids))
        ).all()
    }

    relations: list[dict[str, str]] = []
    for parent_id, child_id in sorted(parent_child_pairs):
        relations.append(
            {
                "parent": code_by_id.get(parent_id, f"LOT-{parent_id:04d}"),
                "child": code_by_id.get(child_id, f"LOT-{child_id:04d}"),
                "type": "LINEAGE",
            }
        )
    return relations


def load_batch_facts(db: Session) -> dict[str, RecallBatchFact]:
    """Đọc số liệu phục vụ liệt kê thu hồi của **mọi lô** trong CSDL, khoá theo mã lô."""
    facts: dict[str, RecallBatchFact] = {}
    for batch in db.scalars(select(Batch).order_by(Batch.id.asc())).all():
        facts[batch_code_of(batch)] = RecallBatchFact(
            batch_code=batch_code_of(batch),
            remaining_quantity=batch.quantity,
            consumed_quantity=batch.consumed_quantity or 0.0,
            organization=batch.current_holder_org,
            status=batch.status,
            batch_id=batch.id,
            product_name=batch.product_name,
        )
    return facts


def build_recall_scope(db: Session, root_batch: Batch) -> RecallScope:
    """Liệt kê phạm vi thu hồi của một lô gốc **đọc từ CSDL**.

    Args:
        db: Session SQLAlchemy đang mở.
        root_batch: Lô gốc (lô có sự cố) cần biết phải thu hồi những lô nào.

    Returns:
        RecallScope: Lô gốc kèm danh sách hậu duệ và khối lượng còn lại từng lô.
    """
    root_code = batch_code_of(root_batch)
    facts = load_batch_facts(db)
    root_fact = facts.get(root_code) or RecallBatchFact(
        batch_code=root_code,
        remaining_quantity=root_batch.quantity,
        consumed_quantity=root_batch.consumed_quantity or 0.0,
        organization=root_batch.current_holder_org,
        status=root_batch.status,
        batch_id=root_batch.id,
        product_name=root_batch.product_name,
    )

    return RecallScope(
        root=_to_entry(root_fact, 0),
        descendants=enumerate_recall_scope(root_code, load_lineage_relations(db), facts),
    )


__all__ = [
    "RecallBatchFact",
    "RecallScope",
    "RecallScopeEntry",
    "batch_code_of",
    "build_recall_scope",
    "enumerate_recall_scope",
    "load_batch_facts",
    "load_lineage_relations",
]
