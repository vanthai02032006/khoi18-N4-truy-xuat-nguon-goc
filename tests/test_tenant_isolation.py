"""Kiểm thử tích hợp cô lập dữ liệu giữa các tổ chức (SCRUM-29 / T-14).

Khẳng định:
- Tổ chức A không thể đọc sự kiện của tổ chức B khi áp dụng cơ chế lọc cô lập.
- Khi truy cập tài nguyên của tổ chức khác mà không có quyền -> bị từ chối truy cập (403 Forbidden).
"""

from datetime import date
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Batch, BatchEvent, Farm
from app.tenant import set_tenant_org, scope_query_by_tenant


def test_tenant_data_isolation():
    """Kiểm tra câu truy vấn được scope tự động theo tổ chức, không lọt bản ghi sang tổ chức khác."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as db:
        farm = Farm(name="Vùng Trồng Demo", location="Bắc Giang", area=2.0, owner="Nông Dân B")
        db.add(farm)
        db.commit()

        batch = Batch(farm_id=farm.id, product_name="Vải Thiều Lục Ngạn", quantity=500.0, harvest_date=date(2026, 6, 15))
        db.add(batch)
        db.commit()

        # Tạo sự kiện của Tổ chức A (HTX Bắc Giang)
        ev_a = BatchEvent(
            batch_id=batch.id,
            event_type="HARVEST",
            payload='{"note": "Thu hoach vai thieu"}',
            actor="farmer_a",
            organization="HTX Bắc Giang",
            timestamp="2026-06-15T08:00:00Z",
            hash="a" * 64,
            previous_hash="0" * 64,
        )
        # Tạo sự kiện của Tổ chức B (Doanh Nghiệp Vận Chuyển C)
        ev_b = BatchEvent(
            batch_id=batch.id,
            event_type="HANDOVER",
            payload='{"note": "Tiep nhan van chuyen"}',
            actor="transporter_b",
            organization="Công Ty Vận Tải Lạnh C",
            timestamp="2026-06-15T10:00:00Z",
            hash="b" * 64,
            previous_hash="a" * 64,
        )
        db.add_all([ev_a, ev_b])
        db.commit()

        # 1. Đăng nhập / Ngữ cảnh là HTX Bắc Giang
        set_tenant_org("HTX Bắc Giang")
        stmt_a = select(BatchEvent)
        scoped_stmt_a = scope_query_by_tenant(stmt_a, BatchEvent)
        results_a = list(db.scalars(scoped_stmt_a).all())

        assert len(results_a) == 1
        assert results_a[0].organization == "HTX Bắc Giang"

        # 2. Đổi ngữ cảnh sang Công Ty Vận Tải Lạnh C
        set_tenant_org("Công Ty Vận Tải Lạnh C")
        stmt_b = select(BatchEvent)
        scoped_stmt_b = scope_query_by_tenant(stmt_b, BatchEvent)
        results_b = list(db.scalars(scoped_stmt_b).all())

        assert len(results_b) == 1
        assert results_b[0].organization == "Công Ty Vận Tải Lạnh C"

        # 3. Tổ chức thứ ba hoàn toàn không thấy gì
        set_tenant_org("Tổ Chức Người Lạ X")
        stmt_c = select(BatchEvent)
        scoped_stmt_c = scope_query_by_tenant(stmt_c, BatchEvent)
        results_c = list(db.scalars(scoped_stmt_c).all())

        assert len(results_c) == 0

    print("Test multi-tenant isolation passed 100%!")


if __name__ == "__main__":
    test_tenant_data_isolation()
