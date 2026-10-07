"""Thuật toán duyệt phả hệ lô nông sản bằng BFS (SCRUM-65 / T-49).

Đặc tính kỹ thuật:
- Duyệt ngược từ con lên cha theo từng tầng (Level-order / Breadth-First Search).
- Quản lý tập đã thăm (visited set) để phát hiện và ngăn chặn chu trình (Cycle detection).
- Tránh tràn ngăn xếp (Stack Overflow) bằng cách sử dụng hàng đợi (Queue) thay vì đệ quy.
"""

from collections import deque


class LineageCycleError(Exception):
    """Ngoại lệ khi phát hiện vòng lặp / chu trình trong cây phả hệ."""
    pass


def find_ancestors_bfs(batch_code: str, relations: list[dict]) -> list[str]:
    """Tìm toàn bộ danh sách tổ tiên của một lô hàng bằng thuật toán BFS.

    Args:
        batch_code: Mã lô cần truy xuất tổ tiên.
        relations: Danh sách quan hệ [{'parent': '...', 'child': '...'}, ...].

    Returns:
        list[str]: Danh sách mã lô tổ tiên theo thứ tự duyệt BFS.

    Raises:
        LineageCycleError: Nếu phát hiện chu trình đồ thị.
    """
    # Xây dựng danh sách kề ngược: child -> list of parents
    parent_map: dict[str, list[str]] = {}
    for rel in relations:
        child = rel["child"]
        parent = rel["parent"]
        parent_map.setdefault(child, []).append(parent)

    queue = deque([batch_code])
    visited = set()
    ancestors = []

    while queue:
        current = queue.popleft()
        parents = parent_map.get(current, [])

        for p in parents:
            if p == batch_code:
                raise LineageCycleError(f"Phát hiện chu trình phả hệ: lô {batch_code} tự trỏ về chính nó!")
            if p not in visited:
                visited.add(p)
                ancestors.append(p)
                queue.append(p)

    return ancestors
