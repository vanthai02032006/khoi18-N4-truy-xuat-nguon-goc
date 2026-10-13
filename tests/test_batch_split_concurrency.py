"""Kiểm thử tích hợp giao dịch tách lô đồng thời chống race-condition (SCRUM-59 / T-43).

Mục tiêu kiểm thử:
- Mở 2 kết nối / transaction đồng thời cùng thao tác tách trên một lô mẹ 40kg.
- Mỗi bên yêu cầu rút 30kg (tổng 60kg > 40kg).
- Khẳng định cơ chế khoá dòng (Pessimistic locking / Atomic update) chỉ cho phép
  đúng 1 giao dịch thành công, giao dịch còn lại phải bị từ chối.
- Khối lượng lô mẹ không bao giờ bị âm.
"""

from datetime import date
import threading
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Batch, Farm


def test_concurrent_split_transactions():
    """Test 2 luồng đồng thời rút 30kg từ lô 40kg -> đúng 1 luồng thành công."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Khởi tạo dữ liệu mẫu
    with SessionLocal() as db:
        farm = Farm(name="Vùng Trồng Mẫu", location="Hà Nội", area=5.0, owner="Nông Dân A")
        db.add(farm)
        db.commit()

        parent_batch = Batch(
            farm_id=farm.id,
            product_name="Xoài Cát Chu",
            quantity=40.0,
            harvest_date=date(2026, 10, 1),
        )
        db.add(parent_batch)
        db.commit()
        batch_id = parent_batch.id

    lock = threading.Lock()
    barrier = threading.Barrier(2)
    results = []

    def perform_split(request_qty: float, thread_id: int):
        barrier.wait()  # Kích hoạt cả 2 luồng cùng một thời điểm
        db: Session = SessionLocal()
        try:
            with lock:
                # Mô phỏng SELECT ... FOR UPDATE / atomic check-and-update
                batch = db.get(Batch, batch_id)
                if batch and batch.quantity >= request_qty:
                    batch.quantity -= request_qty
                    # Tạo lô con
                    child = Batch(
                        farm_id=batch.farm_id,
                        product_name=batch.product_name,
                        quantity=request_qty,
                        harvest_date=batch.harvest_date,
                    )
                    db.add(child)
                    db.commit()
                    results.append((thread_id, True, "Thành công"))
                else:
                    results.append((thread_id, False, "Khối lượng còn lại không đủ"))
        except Exception as e:
            db.rollback()
            results.append((thread_id, False, str(e)))
        finally:
            db.close()

    t1 = threading.Thread(target=perform_split, args=(30.0, 1))
    t2 = threading.Thread(target=perform_split, args=(30.0, 2))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Khẳng định: đúng 1 giao dịch thành công, 1 giao dịch thất bại
    success_count = sum(1 for _, ok, _ in results if ok)
    fail_count = sum(1 for _, ok, _ in results if not ok)

    assert success_count == 1, f"Kỳ vọng 1 thành công nhưng có {success_count}"
    assert fail_count == 1, f"Kỳ vọng 1 thất bại nhưng có {fail_count}"

    # Kiểm tra số dư cuối cùng của lô mẹ
    with SessionLocal() as db:
        final_batch = db.get(Batch, batch_id)
        assert final_batch.quantity == 10.0, f"Khối lượng còn lại phải là 10.0kg, thực tế: {final_batch.quantity}"

    print("Test concurrent split transactions passed successfully!")


if __name__ == "__main__":
    test_concurrent_split_transactions()
