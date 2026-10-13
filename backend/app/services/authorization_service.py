"""Quy tắc phân quyền xem lô nông sản theo phả hệ (Genealogy Authorization Rule) - SCRUM-70.

Quy tắc:
Một organization / user được quyền VIEW một batch nếu:
CASE 1: Organization/user hiện tại đang giữ batch đó.
HOẶC
CASE 2: Organization/user hiện tại từng giữ batch đó.
HOẶC
CASE 3: Batch đó là tổ tiên (ancestor) của một batch mà organization/user hiện tại đang hoặc từng giữ.

Ví dụ:
A -> B -> C -> D
Nếu tổ chức X đang giữ D, thì X có quyền xem D, C, B, A (vì A, B, C là ancestor của D).
X KHÔNG được xem các batch không liên quan.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ROLE_ADMIN, Batch, BatchCustodyHistory, BatchRelation, User


def get_batches_held_by_org(db: Session, org_id: str) -> set[int]:
    """Tìm tất cả ID các lô nông sản mà organization đang giữ (CASE 1) hoặc từng giữ (CASE 2)."""
    if not org_id or not str(org_id).strip():
        return set()

    clean_org = str(org_id).strip()

    # CASE 1: Đang giữ (Batch.organization_id == org_id)
    currently_held = set(
        db.scalars(select(Batch.id).where(Batch.organization_id == clean_org)).all()
    )

    # CASE 2: Từng giữ (BatchCustodyHistory.organization_id == org_id)
    previously_held = set(
        db.scalars(
            select(BatchCustodyHistory.batch_id).where(
                BatchCustodyHistory.organization_id == clean_org
            )
        ).all()
    )

    return currently_held | previously_held


def get_all_ancestor_ids(db: Session, initial_batch_ids: set[int] | list[int]) -> set[int]:
    """Truy ngược toàn bộ tổ tiên (ancestors) từ danh sách các lô ban đầu.

    Bảo vệ chống vòng lặp vô tận (infinite cycle protection) bằng cấu trúc tập hợp `visited`.
    """
    ancestor_ids: set[int] = set()
    queue = list(initial_batch_ids)
    visited = set(initial_batch_ids)

    while queue:
        current_batch_id = queue.pop(0)

        # Lấy tất cả parent_batch_id của current_batch_id từ bảng quan hệ phả hệ
        parents = db.scalars(
            select(BatchRelation.parent_batch_id).where(
                BatchRelation.child_batch_id == current_batch_id
            )
        ).all()

        for pid in parents:
            ancestor_ids.add(pid)
            if pid not in visited:
                visited.add(pid)
                queue.append(pid)

    return ancestor_ids


def can_view(db: Session, org_id: str | None, batch_id: int) -> bool:
    """Kiểm tra quyền xem lô nông sản theo đúng 3 case của SCRUM-70.

    Args:
        db: Session SQLAlchemy.
        org_id: Mã định danh tổ chức cần kiểm tra.
        batch_id: Mã định danh lô cần xem.

    Returns:
        bool: True nếu tổ chức có quyền xem lô; False nếu bị từ chối hoặc lô không tồn tại.
    """
    if not org_id or not str(org_id).strip():
        return False

    clean_org = str(org_id).strip()

    # Kiểm tra lô cần xem có tồn tại trong CSDL không
    target_batch = db.get(Batch, batch_id)
    if target_batch is None:
        return False

    # Lấy danh sách các lô mà org đang hoặc từng giữ
    held_batches = get_batches_held_by_org(db, clean_org)
    if not held_batches:
        return False

    # CASE 1 & CASE 2: Đang giữ hoặc từng giữ trực tiếp lô này
    if batch_id in held_batches:
        return True

    # CASE 3: Lô được yêu cầu là tổ tiên (ancestor) của một lô mà tổ chức đang/từng giữ
    ancestors = get_all_ancestor_ids(db, held_batches)
    if batch_id in ancestors:
        return True

    return False


def can_user_view_batch(db: Session, user: User, batch_id: int) -> bool:
    """Kiểm tra quyền xem lô của một người dùng đăng nhập.

    - Tài khoản `admin`: toàn quyền xem mọi lô trong hệ thống.
    - Tài khoản khác: áp dụng quy tắc `can_view` dựa trên `organization_id` của tài khoản.
    """
    if getattr(user, "role", None) == ROLE_ADMIN:
        # Admin có toàn quyền quản trị
        return db.get(Batch, batch_id) is not None

    org_id = getattr(user, "organization_id", None) or getattr(user, "username", None)
    return can_view(db, org_id, batch_id)


def filter_viewable_batches(db: Session, user: User, batches: list[Batch]) -> list[Batch]:
    """Lọc danh sách các lô nông sản mà người dùng được phép xem theo quy tắc SCRUM-70."""
    if getattr(user, "role", None) == ROLE_ADMIN:
        return batches

    org_id = getattr(user, "organization_id", None) or getattr(user, "username", None)
    if not org_id:
        return []

    held_batches = get_batches_held_by_org(db, org_id)
    if not held_batches:
        return []

    ancestor_ids = get_all_ancestor_ids(db, held_batches)
    allowed_ids = held_batches | ancestor_ids

    return [b for b in batches if b.id in allowed_ids]


__all__ = [
    "can_user_view_batch",
    "can_view",
    "filter_viewable_batches",
    "get_all_ancestor_ids",
    "get_batches_held_by_org",
]
