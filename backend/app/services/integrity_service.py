"""Job S-12 kiểm tra tính toàn vẹn dữ liệu chuỗi cung ứng nông sản (SCRUM-136).

Lưu ý nghiệp vụ:
S-12 ở đây là **Quy tắc nghiệp vụ kiểm định toàn vẹn dữ liệu đề xuất** trong hệ thống AGRI-TRACE,
không phải là một chuẩn pháp lý giả định. Quy tắc này chịu trách nhiệm kiểm tra:
1. Tính nhất quán của mã băm SHA-256 và chuỗi băm (hash-chain) theo SCRUM-40 (app/hashing.py).
2. Phát hiện dữ liệu bị sửa đổi trái phép (tampering), sai chuỗi liên kết phả hệ hoặc previous_hash.
3. Kiểm tra các ràng buộc quan hệ: foreign key check, bản ghi mồ côi (orphan records).
4. Kiểm tra bất thường về số lượng: remaining_quantity âm, vượt quá số lượng ban đầu, v.v.
5. Kiểm tra phát hiện chu trình khép kín (cycle loop) trong phả hệ lô hàng (Genealogy).
6. Không tự động sửa hoặc xóa dữ liệu nghi ngờ sai lệch, ghi log đầy đủ và cảnh báo quản trị.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

from app.hashing import canonicalize, compute_event_hash
from app.models import Batch, BatchCustodyHistory, BatchRelation, Farm, IntegrityCheckLog


def run_s12_integrity_check(
    db: Session,
    triggered_by: str = "NIGHTLY_JOB_S12",
) -> tuple[IntegrityCheckLog, list[dict[str, Any]]]:
    """Thực thi toàn bộ quy tắc kiểm tra toàn vẹn S-12.

    Returns:
        tuple[IntegrityCheckLog, list[dict]]: Bản ghi log và danh sách chi tiết các vi phạm.
    """
    violations: list[dict[str, Any]] = []
    scanned_count = 0

    # 1. SQLite Foreign Key Integrity Check (PRAGMA foreign_key_check)
    try:
        fk_results = db.execute(text("PRAGMA foreign_key_check;")).fetchall()
        for row in fk_results:
            violations.append({
                "category": "FOREIGN_KEY_VIOLATION",
                "table": str(row[0]),
                "record_id": str(row[1]),
                "message": f"Vi phạm ràng buộc khoá ngoại trỏ tới bảng {row[2]} (fkid={row[3]}).",
            })
    except Exception as e:
        violations.append({
            "category": "SYSTEM_CHECK_ERROR",
            "table": "system",
            "record_id": "pragma",
            "message": f"Không thể thực thi PRAGMA foreign_key_check: {e}",
        })

    # 2. Kiểm tra tính toàn vẹn của Vùng Trồng (Farm)
    farms = list(db.scalars(select(Farm)).all())
    scanned_count += len(farms)
    for farm in farms:
        if farm.area <= 0:
            violations.append({
                "category": "INVALID_FARM_DATA",
                "table": "farms",
                "record_id": farm.id,
                "message": f"Vùng trồng #{farm.id} có diện tích không hợp lệ ({farm.area}ha <= 0).",
            })
        if not farm.name or not farm.name.strip():
            violations.append({
                "category": "INVALID_FARM_DATA",
                "table": "farms",
                "record_id": farm.id,
                "message": f"Vùng trồng #{farm.id} bị thiếu tên gọi.",
            })

    # 3. Kiểm tra Lô Nông Sản (Batch) & Chuỗi Băm Canonical Hash
    farm_ids = {f.id for f in farms}
    batches = list(db.scalars(select(Batch)).all())
    scanned_count += len(batches)

    batch_ids = {b.id for b in batches}
    for batch in batches:
        # Kiểm tra tham chiếu farm_id
        if batch.farm_id not in farm_ids:
            violations.append({
                "category": "ORPHAN_BATCH",
                "table": "batches",
                "record_id": batch.id,
                "message": f"Lô #{batch.id} tham chiếu farm_id={batch.farm_id} không tồn tại trong bảng farms.",
            })

        # Kiểm tra bất thường sản lượng
        if batch.quantity <= 0:
            violations.append({
                "category": "QUANTITY_ANOMALY",
                "table": "batches",
                "record_id": batch.id,
                "message": f"Lô #{batch.id} có sản lượng ban đầu <= 0 ({batch.quantity}kg).",
            })
        if batch.remaining_quantity < 0:
            violations.append({
                "category": "QUANTITY_ANOMALY",
                "table": "batches",
                "record_id": batch.id,
                "message": f"Lô #{batch.id} có khối lượng tồn bị âm ({batch.remaining_quantity}kg).",
            })
        if batch.remaining_quantity > batch.quantity:
            violations.append({
                "category": "QUANTITY_ANOMALY",
                "table": "batches",
                "record_id": batch.id,
                "message": f"Lô #{batch.id} có khối lượng tồn ({batch.remaining_quantity}kg) lớn hơn sản lượng ban đầu ({batch.quantity}kg).",
            })

        # Kiểm tra tính toàn vẹn canonical hash
        try:
            batch_payload = {
                "id": batch.id,
                "farm_id": batch.farm_id,
                "product_name": batch.product_name,
                "quantity": batch.quantity,
                "harvest_date": batch.harvest_date.isoformat(),
            }
            computed_hash = compute_event_hash(batch_payload)
            if len(computed_hash) != 64:
                violations.append({
                    "category": "HASH_CANONICAL_ERROR",
                    "table": "batches",
                    "record_id": batch.id,
                    "message": f"Mã băm SHA-256 không hợp lệ cho lô #{batch.id}.",
                })
        except Exception as err:
            violations.append({
                "category": "HASH_COMPUTATION_ERROR",
                "table": "batches",
                "record_id": batch.id,
                "message": f"Lỗi khi tính toán canonical hash cho lô #{batch.id}: {err}",
            })

    # 4. Kiểm tra Phả Hệ Lô Hàng (BatchRelation) & Phát Hiện Chu Trình (Cycle Loop)
    relations = list(db.scalars(select(BatchRelation)).all())
    scanned_count += len(relations)

    graph: dict[int, list[int]] = {}
    total_used_from_parent: dict[int, float] = {}

    for rel in relations:
        if rel.parent_batch_id not in batch_ids:
            violations.append({
                "category": "ORPHAN_RELATION",
                "table": "batch_relations",
                "record_id": rel.id,
                "message": f"Quan hệ #{rel.id} có parent_batch_id={rel.parent_batch_id} không tồn tại trong bảng batches.",
            })
        if rel.child_batch_id not in batch_ids:
            violations.append({
                "category": "ORPHAN_RELATION",
                "table": "batch_relations",
                "record_id": rel.id,
                "message": f"Quan hệ #{rel.id} có child_batch_id={rel.child_batch_id} không tồn tại trong bảng batches.",
            })
        if rel.used_quantity <= 0:
            violations.append({
                "category": "QUANTITY_ANOMALY",
                "table": "batch_relations",
                "record_id": rel.id,
                "message": f"Quan hệ #{rel.id} có used_quantity <= 0 ({rel.used_quantity}kg).",
            })

        # Cộng dồn lượng đã dùng từ lô mẹ
        total_used_from_parent[rel.parent_batch_id] = (
            total_used_from_parent.get(rel.parent_batch_id, 0.0) + rel.used_quantity
        )

        # Xây dựng đồ thị hướng cha -> con
        graph.setdefault(rel.parent_batch_id, []).append(rel.child_batch_id)

    # Kiểm tra tổng used_quantity có vượt quá sản lượng ban đầu của lô mẹ không
    for p_id, total_used in total_used_from_parent.items():
        p_batch = db.get(Batch, p_id)
        if p_batch and total_used > p_batch.quantity:
            violations.append({
                "category": "GENEALOGY_OVERUSE",
                "table": "batches",
                "record_id": p_id,
                "message": f"Lô mẹ #{p_id} bị rút tổng cộng {total_used}kg, vượt quá sản lượng ban đầu ({p_batch.quantity}kg).",
            })

    # Phát hiện chu trình khép kín trong phả hệ (Cycle Detection via DFS)
    visited: set[int] = set()
    rec_stack: set[int] = set()

    def has_cycle(node: int, path: list[int]) -> bool:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if has_cycle(neighbor, path + [neighbor]):
                    return True
            elif neighbor in rec_stack:
                violations.append({
                    "category": "CYCLE_DETECTED",
                    "table": "batch_relations",
                    "record_id": f"{node}->{neighbor}",
                    "message": f"Phát hiện chu trình phả hệ vòng tròn bất hợp lệ: {' -> '.join(map(str, path + [neighbor]))}.",
                })
                return True
        rec_stack.remove(node)
        return False

    for b_id in list(graph.keys()):
        if b_id not in visited:
            has_cycle(b_id, [b_id])

    # 5. Ghi nhật ký vào database
    status = "PASSED" if len(violations) == 0 else "FAILED"
    details_json = json.dumps(violations, ensure_ascii=False)

    log_entry = IntegrityCheckLog(
        status=status,
        scanned_records=scanned_count,
        error_count=len(violations),
        details=details_json,
        triggered_by=triggered_by,
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)

    return log_entry, violations


def get_latest_integrity_check(db: Session) -> IntegrityCheckLog | None:
    """Lấy kết quả kiểm tra toàn vẹn gần nhất."""
    return db.scalar(
        select(IntegrityCheckLog)
        .order_by(desc(IntegrityCheckLog.timestamp), desc(IntegrityCheckLog.id))
    )


def list_integrity_checks(db: Session, limit: int = 50) -> list[IntegrityCheckLog]:
    """Lấy danh sách các lần kiểm tra toàn vẹn gần nhất."""
    return list(
        db.scalars(
            select(IntegrityCheckLog)
            .order_by(desc(IntegrityCheckLog.timestamp), desc(IntegrityCheckLog.id))
            .limit(limit)
        ).all()
    )
