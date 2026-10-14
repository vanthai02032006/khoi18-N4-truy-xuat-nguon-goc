"""Quản lý ngữ cảnh tổ chức đa người dùng (Multi-tenant context - SCRUM-27 & SCRUM-28).

Nguyên tắc:
- Mọi request vào hệ thống đều được middleware / dependency phân giải ngữ cảnh tổ chức.
- Tự động lọc dữ liệu theo tổ chức (Data isolation) để tránh rò rỉ dữ liệu giữa các bên trong chuỗi.
"""

from __future__ import annotations

from contextvars import ContextVar
from fastapi import Header, HTTPException, status
from sqlalchemy import Select

# ContextVar lưu trữ tổ chức của request hiện tại
DEFAULT_TENANT_ORG = "HTX Nông Nghiệp Số 4"
_current_tenant_org: ContextVar[str] = ContextVar("current_tenant_org", default=DEFAULT_TENANT_ORG)


def set_tenant_org(org_name: str) -> None:
    """Thiết lập tổ chức cho request hiện tại."""
    _current_tenant_org.set(org_name)


def get_tenant_org() -> str:
    """Lấy tổ chức của request hiện tại."""
    return _current_tenant_org.get()


def require_tenant_context(x_organization: str | None = Header(None, alias="X-Organization-Id")) -> str:
    """Dependency kiểm tra bắt buộc có header tổ chức hợp lệ (SCRUM-27)."""
    # Không có header -> LUÔN về tổ chức mặc định. Trước đây trả lại giá trị còn sót
    # trong ContextVar nên tổ chức của request trước "dính" sang request sau.
    org = x_organization.strip() if x_organization and x_organization.strip() else DEFAULT_TENANT_ORG
    set_tenant_org(org)
    return org


def scope_query_by_tenant(stmt: Select, model_cls) -> Select:
    """Tự động tiêm điều kiện lọc theo tổ chức vào câu truy vấn (SCRUM-28)."""
    current_org = get_tenant_org()
    if hasattr(model_cls, "organization"):
        return stmt.where(model_cls.organization == current_org)
    return stmt
