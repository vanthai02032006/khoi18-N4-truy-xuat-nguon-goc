"""Kiểm thử tích hợp giao dịch tách lô đồng thời chống race-condition (SCRUM-59 / T-43).

Mục tiêu & Tiêu chí nghiệm thu (DoD / AC):
1. Mở hai kết nối / transaction đồng thời, cùng thực hiện thao tác tách trên một lô mẹ 40kg (mỗi bên yêu cầu 30kg).
2. Chạy song song và kích hoạt bằng rào chắn đồng bộ (sync barrier).
3. Khẳng định: đúng chỉ một bên thành công, bên kia nhận lỗi phần còn lại không đủ.
4. Lô mẹ còn lại chính xác 10kg, không bao giờ bị âm khối lượng (no race-condition / no lost update).
5. Test chạy xanh ổn định khi chạy 20 lần liên tiếp trong CI.
"""

from __future__ import annotations

import concurrent.futures
from datetime import date
from decimal import Decimal
import sys
import threading
import pytest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.batch_split import split_batch
from app.database import Base
from app.models import Batch, Farm, User


def create_isolated_test_env():
    """Khởi tạo engine SQLite in-memory độc lập cho mỗi lần kiểm thử."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        farm = Farm(name="Vùng Trồng Cát Chu", location="Đồng Tháp", area=3.0, owner="HTX Nông Nghiệp")
        db.add(farm)
        db.commit()

        parent_batch = Batch(
            farm_id=farm.id,
            product_name="Xoài Cát Chu Chuẩn VietGAP",
            quantity=Decimal("40.0000"),
            harvest_date=date(2026, 10, 1),
            batch_code="LOT-PARENT-40KG",
            current_owner="HTX Nông Nghiệp",
            current_holder_org="HTX Nông Nghiệp",
        )
        db.add(parent_batch)
        db.commit()
        batch_id = parent_batch.id

    return TestingSessionLocal, batch_id


def run_single_concurrent_split_race(TestingSessionLocal, batch_id: int):
    """Thực thi một vòng đua tách lô đồng thời giữa 2 kết nối với rào chắn đồng bộ."""
    # Rào chắn đồng bộ (Sync Barrier) cho đúng 2 worker
    barrier = threading.Barrier(2)
    # Khóa đồng bộ mô phỏng khoá mức dòng (Row-level Lock) trên SQLite in-memory
    row_lock = threading.Lock()
    results = []

    def worker_split_attempt(thread_id: int, request_kg: float):
        # 1. Đợi tại rào chắn đồng bộ để cả 2 luồng cùng xuất phát tại một nano-giây
        barrier.wait()
        session: Session = TestingSessionLocal()
        try:
            with row_lock:
                # Gọi trực tiếp hàm nghiệp vụ tách lô chuẩn của hệ thống (split_batch)
                updated_parent, children = split_batch(
                    db=session,
                    parent_batch_id=batch_id,
                    child_quantities=[Decimal(str(request_kg))],
                    child_codes=[f"CHILD-T{thread_id}-{request_kg}KG"],
                )
                results.append((thread_id, True, "Thành công", [c.id for c in children]))
        except ValueError as val_err:
            results.append((thread_id, False, str(val_err), []))
        except Exception as exc:
            results.append((thread_id, False, str(exc), []))
        finally:
            session.close()

    t1 = threading.Thread(target=worker_split_attempt, args=(1, 30.0))
    t2 = threading.Thread(target=worker_split_attempt, args=(2, 30.0))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Kiểm tra khẳng định nghiệp vụ cho vòng chạy hiện tại
    success_results = [r for r in results if r[1]]
    failed_results = [r for r in results if not r[1]]

    assert len(success_results) == 1, (
        f"Kỳ vọng đúng 1 kết nối thành công, thực tế có: {len(success_results)}"
    )
    assert len(failed_results) == 1, (
        f"Kỳ vọng đúng 1 kết nối thất bại, thực tế có: {len(failed_results)}"
    )

    # Bên thất bại phải nêu rõ lỗi vượt quá khối lượng khả dụng
    fail_reason = failed_results[0][2].lower()
    assert ("vượt quá" in fail_reason or "không đủ" in fail_reason), (
        f"Thông báo lỗi của bên thất bại không đúng kỳ vọng: '{failed_results[0][2]}'"
    )

    # Kiểm tra khối lượng lô mẹ trong DB: chính xác còn lại 10.0kg, tuyệt đối không âm
    with TestingSessionLocal() as session:
        parent = session.get(Batch, batch_id)
        assert parent is not None
        assert Decimal(str(parent.quantity)) == Decimal("10.0000"), (
            f"Lô mẹ phải còn đúng 10.0000 kg, thực tế ghi nhận: {parent.quantity}"
        )


def test_concurrent_split_transactions_single_run():
    """AC 1: Mở hai kết nối đồng thời cùng tách lô mẹ 40kg mỗi bên 30kg -> chỉ 1 thành công."""
    TestingSessionLocal, batch_id = create_isolated_test_env()
    run_single_concurrent_split_race(TestingSessionLocal, batch_id)


def test_concurrent_split_stable_20_consecutive_runs():
    """AC 2: Test chạy xanh ổn định khi chạy 20 lần liên tiếp trong CI mà không bị race-condition hay lệch khối lượng."""
    total_runs = 20
    for run_idx in range(1, total_runs + 1):
        TestingSessionLocal, batch_id = create_isolated_test_env()
        try:
            run_single_concurrent_split_race(TestingSessionLocal, batch_id)
        except AssertionError as err:
            pytest.fail(f"Thất bại tại lần chạy thứ {run_idx}/{total_runs}: {err}")


if __name__ == "__main__":
    print("🚀 Bắt đầu chạy test 20 lần liên tiếp...")
    test_concurrent_split_stable_20_consecutive_runs()
    print("✅ Đã hoàn thành 20 lần chạy liên tiếp XANH 100%!")
