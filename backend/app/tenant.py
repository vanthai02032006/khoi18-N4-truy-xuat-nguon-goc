"""Quản lý ngữ cảnh tổ chức đa người dùng (Multi-tenant context - SCRUM-27 & SCRUM-28).

Nguyên tắc:
- Mọi request vào hệ thống đều được middleware / dependency phân giải ngữ cảnh tổ chức.
- Tự động lọc dữ liệu theo tổ chức (Data isolation) để tránh rò rỉ dữ liệu giữa các bên trong chuỗi.
"""

from contextvars import ContextVar
from fastapi import Header, HTTPException, status
from sqlalchemy import Select

# ContextVar lưu trữ tổ chức của request hiện tại
_current_tenant_org: ContextVar[str] = ContextVar("current_tenant_org", default="HTX Nông Nghiệp Số 4")


def set_tenant_org(org_name: str) -> None:
    """Thiết lập tổ chức cho request hiện tại."""
    _current_tenant_org.set(org_name)


def get_tenant_org() -> str:
    """Lấy tổ chức của request hiện tại."""
    return _current_tenant_org.get()


def require_tenant_context(x_organization: str | None = Header(None, alias="X-Organization-Id")) -> str:
    """Dependency kiểm tra bắt buộc có header tổ chức hợp lệ (SCRUM-27)."""
    if x_organization and x_organization.strip():
        set_tenant_org(x_organization.strip())
        return x_organization.strip()
    # Mặc định lấy tổ chức tiêu chuẩn nếu không chỉ định header riêng
    default_org = get_tenant_org()
    return default_org


def scope_query_by_tenant(stmt: Select, model_cls) -> Select:
    """Tự động tiêm điều kiện lọc theo tổ chức vào câu truy vấn (SCRUM-28)."""
    current_org = get_tenant_org()
    if hasattr(model_cls, "organization"):
        return stmt.where(model_cls.organization == current_org)
    return stmt
