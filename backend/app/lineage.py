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
        LineageCycleError: Nếu phát hiện chu trình đồ thị (vòng lặp phả hệ).
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

    # Để phát hiện chu trình chính xác trong đồ thị có hướng (DAG),
    # kiểm tra xem từ bất kỳ tổ tiên nào có đường đi ngược lại chính một nút đang duyệt.
    # Trong BFS duyệt ngược: nếu một nút quay trở lại chính batch_code hoặc chuỗi lặp lại
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

    # Kiểm tra toàn diện chu trình trong tập hợp tổ tiên tìm được
    # (nếu có chu trình kín giữa các tổ tiên của batch_code)
    subgraph_nodes = set(ancestors) | {batch_code}
    in_degree = {node: 0 for node in subgraph_nodes}
    adj = {node: [] for node in subgraph_nodes}

    for child in subgraph_nodes:
        for parent in parent_map.get(child, []):
            if parent in subgraph_nodes:
                adj[child].append(parent)
                in_degree[parent] += 1

    # Kiểm tra Topological sort trên subgraph các tổ tiên
    topo_queue = deque([node for node in subgraph_nodes if in_degree[node] == 0])
    count_visited = 0
    while topo_queue:
        u = topo_queue.popleft()
        count_visited += 1
        for v in adj[u]:
            in_degree[v] -= 1
            if in_degree[v] == 0:
                topo_queue.append(v)

    if count_visited < len(subgraph_nodes):
        raise LineageCycleError(f"Phát hiện chu trình phả hệ giữa các tổ tiên của lô {batch_code}!")

    return ancestors


def find_descendants_bfs(batch_code: str, relations: list[dict]) -> list[str]:
    """Tìm toàn bộ danh sách hậu duệ (các lô con, cháu được tách ra từ lô này) bằng BFS.

    Args:
        batch_code: Mã lô cần tìm hậu duệ.
        relations: Danh sách quan hệ [{'parent': '...', 'child': '...'}, ...].

    Returns:
        list[str]: Danh sách mã lô hậu duệ theo thứ tự duyệt BFS.
    """
    # Xây dựng danh sách kề thuận: parent -> list of children
    child_map: dict[str, list[str]] = {}
    for rel in relations:
        parent = rel["parent"]
        child = rel["child"]
        child_map.setdefault(parent, []).append(child)

    queue = deque([batch_code])
    visited = set()
    descendants = []

    while queue:
        current = queue.popleft()
        children = child_map.get(current, [])

        for c in children:
            if c not in visited and c != batch_code:
                visited.add(c)
                descendants.append(c)
                queue.append(c)

    return descendants


def extract_lineage_relations_from_events(events: list) -> list[dict]:
    """Trích xuất danh sách quan hệ cha-con từ các sự kiện SPLIT và MERGE trong database.

    Hỗ trợ parse payload của các sự kiện SPLIT (chứa parent_batch_code và child_batches)
    và MERGE (chứa merged_batch_code và parent_batches).
    """
    import json
    relations: list[dict] = []

    for ev in events:
        try:
            payload_data = json.loads(ev.payload) if isinstance(ev.payload, str) else ev.payload
        except Exception:
            continue

        ev_type = getattr(ev, "event_type", "") or ""

        if ev_type == "SPLIT":
            # payload: {"action": "SPLIT_CHILDREN", "parent_code": "...", "children": [{"batch_code": "..."}, ...]}
            parent_code = payload_data.get("parent_code")
            children = payload_data.get("children", [])
            if parent_code:
                for c in children:
                    c_code = c.get("batch_code") if isinstance(c, dict) else str(c)
                    if c_code:
                        relations.append({"parent": str(parent_code), "child": str(c_code), "type": "SPLIT"})

        elif ev_type == "BIRTH":
            # payload: {"action": "BIRTH_FROM_SPLIT", "parent_batch_code": "...", "child_batch_code": "..."}
            p_code = payload_data.get("parent_batch_code")
            c_code = payload_data.get("child_batch_code")
            if p_code and c_code:
                relations.append({"parent": str(p_code), "child": str(c_code), "type": "SPLIT"})

        elif ev_type == "MERGE":
            # payload: {"action": "MERGE_BATCHES", "new_batch_code": "...", "parents": [{"batch_code": "..."}, ...]}
            new_code = payload_data.get("new_batch_code")
            parents = payload_data.get("parents", [])
            if new_code:
                for p in parents:
                    p_code = p.get("batch_code") if isinstance(p, dict) else str(p)
                    if p_code:
                        relations.append({"parent": str(p_code), "child": str(new_code), "type": "MERGE"})

    # Loại bỏ quan hệ trùng lặp
    unique_relations = []
    seen = set()
    for r in relations:
        key = (r["parent"], r["child"])
        if key not in seen:
            seen.add(key)
            unique_relations.append(r)

    return unique_relations

