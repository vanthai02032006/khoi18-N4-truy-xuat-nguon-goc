"""Đồ thị phả hệ lô hàng dùng chung cho truy ngược, truy xuôi và sơ đồ (E-05).

Một chỗ duy nhất trả lời hai câu hỏi ngược chiều nhau:

- *Lô này đến từ đâu?*  -> :meth:`LineageGraph.ancestors`   (S-21 / SCRUM-105)
- *Lô này đã đi tới đâu?* -> :meth:`LineageGraph.descendants` (S-26 / SCRUM-107)

Nguồn cạnh cha -> con:

1. Bảng ``batch_relations`` (tách và gộp đều ghi vào đây từ Sprint 3).
2. Cột ``batches.parent_id`` cho dữ liệu tách cũ chưa có dòng ``batch_relations``.

Nguyên tắc an toàn (R-03, R-04):

- Dữ liệu lỗi tạo chu trình -> ném :class:`LineageCycleError`, KHÔNG bao giờ trả về
  danh sách rỗng hay cắt bớt (thà báo lỗi còn hơn sót lô khi thu hồi).
- Duyệt bằng hàng đợi / ngăn xếp tự quản, không đệ quy, nên đồ thị sâu không tràn
  ngăn xếp.
- Toàn bộ cạnh nạp bằng 2 câu SQL; thông tin lô nạp bằng 1 câu cho cả tập.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.lineage import LineageCycleError
from app.models import Batch, BatchRelation

RELATION_SPLIT = "SPLIT"
RELATION_MERGE = "MERGE"


@dataclass
class Edge:
    parent_id: int
    child_id: int
    quantity: float | None
    kind: str = RELATION_SPLIT


@dataclass
class LineageGraph:
    """Đồ thị cha -> con của toàn bộ lô, nạp một lần cho mỗi request."""

    edges: list[Edge] = field(default_factory=list)
    parents_of: dict[int, list[int]] = field(default_factory=dict)
    children_of: dict[int, list[int]] = field(default_factory=dict)

    # ------------------------------------------------------------------ nạp
    @classmethod
    def load(cls, db: Session) -> "LineageGraph":
        graph = cls()
        seen: set[tuple[int, int]] = set()

        rows = db.execute(
            select(
                BatchRelation.parent_batch_id,
                BatchRelation.child_batch_id,
                BatchRelation.used_quantity,
            )
        ).all()
        for parent_id, child_id, qty in rows:
            if (parent_id, child_id) in seen:
                continue
            seen.add((parent_id, child_id))
            graph.edges.append(
                Edge(parent_id, child_id, float(qty) if qty is not None else None)
            )

        legacy = db.execute(
            select(Batch.parent_id, Batch.id, Batch.quantity).where(Batch.parent_id.is_not(None))
        ).all()
        for parent_id, child_id, qty in legacy:
            if (parent_id, child_id) in seen:
                continue
            seen.add((parent_id, child_id))
            graph.edges.append(Edge(parent_id, child_id, float(qty) if qty is not None else None))

        for e in graph.edges:
            graph.parents_of.setdefault(e.child_id, []).append(e.parent_id)
            graph.children_of.setdefault(e.parent_id, []).append(e.child_id)

        # Lô con có từ 2 lô mẹ trở lên là kết quả gộp; còn lại là tách.
        for e in graph.edges:
            if len(graph.parents_of.get(e.child_id, [])) > 1:
                e.kind = RELATION_MERGE
        return graph

    # ------------------------------------------------------------- duyệt
    def _check_acyclic(self, start: int, adjacency: dict[int, list[int]]) -> None:
        """DFS ba màu trên phần đồ thị đi tới được từ ``start``; có chu trình thì ném lỗi."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[int, int] = {}
        stack: list[tuple[int, int]] = [(start, 0)]
        path: list[int] = []
        while stack:
            node, idx = stack.pop()
            if idx == 0:
                color[node] = GRAY
                path.append(node)
            nexts = adjacency.get(node, [])
            if idx < len(nexts):
                stack.append((node, idx + 1))
                nxt = nexts[idx]
                c = color.get(nxt, WHITE)
                if c == GRAY:
                    cycle = path[path.index(nxt):] + [nxt]
                    raise LineageCycleError(
                        "Phát hiện chu trình phả hệ qua các lô: "
                        + " -> ".join(f"#{i}" for i in cycle)
                    )
                if c == WHITE:
                    stack.append((nxt, 0))
            else:
                color[node] = BLACK
                path.pop()

    def _bfs(self, start: int, adjacency: dict[int, list[int]]) -> dict[int, int]:
        self._check_acyclic(start, adjacency)
        depth: dict[int, int] = {}
        queue = deque([(start, 0)])
        visited = {start}
        while queue:
            node, d = queue.popleft()
            for nxt in adjacency.get(node, []):
                if nxt not in visited:
                    visited.add(nxt)
                    depth[nxt] = d + 1
                    queue.append((nxt, d + 1))
        return depth

    def ancestors(self, batch_id: int) -> dict[int, int]:
        """``{id_tổ_tiên: số_tầng}`` (1 = lô mẹ trực tiếp). Không gồm chính lô."""
        return self._bfs(batch_id, self.parents_of)

    def descendants(self, batch_id: int) -> dict[int, int]:
        """``{id_hậu_duệ: số_tầng}`` (1 = lô con trực tiếp). Không gồm chính lô."""
        return self._bfs(batch_id, self.children_of)

    def roots_among(self, ids: set[int]) -> set[int]:
        """Các lô trong ``ids`` không có lô mẹ nào - tức lô thu hoạch gốc."""
        return {i for i in ids if not self.parents_of.get(i)}

    def edges_within(self, ids: set[int]) -> list[Edge]:
        return [e for e in self.edges if e.parent_id in ids and e.child_id in ids]


def load_batches(db: Session, ids: set[int]) -> dict[int, Batch]:
    """Nạp nhiều lô kèm vùng trồng và địa điểm trong một câu truy vấn."""
    if not ids:
        return {}
    rows = db.scalars(
        select(Batch)
        .options(joinedload(Batch.farm), joinedload(Batch.current_location))
        .where(Batch.id.in_(ids))
    ).unique().all()
    return {b.id: b for b in rows}


def batch_code(b: Batch) -> str:
    return b.batch_code or f"LOT-{b.id:04d}"


__all__ = [
    "Edge",
    "LineageCycleError",
    "LineageGraph",
    "RELATION_MERGE",
    "RELATION_SPLIT",
    "batch_code",
    "load_batches",
]
