"""Service sao lưu dữ liệu và diễn tập khôi phục SQLite (SCRUM-138).

Yêu cầu kỹ thuật:
1. Sử dụng SQLite Online Backup API (sqlite3.Connection.backup) để đảm bảo sao lưu nhất quán,
   an toàn kể cả khi CSDL đang mở kết nối hoặc đang bật chế độ WAL.
2. Thư mục lưu trữ sao lưu được phân tách độc lập (cấu hình qua biến môi trường BACKUP_STORAGE_DIR).
   Nếu chưa có hạ tầng Cloud/S3/NFS, cung cấp cấu hình rõ ràng và lưu tại vùng storage riêng biệt.
3. Retention policy: Giữ tối đa 14 bản sao lưu hợp lệ gần nhất.
   Chỉ dọn các bản cũ SAU KHI bản mới đã được tạo và xác minh thành công.
4. Xác minh tính toàn vẹn (integrity_check) và tính checksum SHA-256 cho mỗi bản sao lưu.
5. Hỗ trợ diễn tập khôi phục (rehearsal restore) trên CSDL tạm, kiểm tra khả năng mở và dữ liệu
   mà tuyệt đối không ghi đè CSDL thật.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import DATABASE_FILE
from app.models import BackupLog

# Thư mục lưu trữ sao lưu (ưu tiên đọc từ biến môi trường BACKUP_STORAGE_DIR)
DEFAULT_BACKUP_DIR = Path(DATABASE_FILE).parent / "storage_backups"
BACKUP_STORAGE_DIR = Path(os.getenv("BACKUP_STORAGE_DIR", str(DEFAULT_BACKUP_DIR)))
MAX_RETAINED_BACKUPS = 14


def get_backup_storage_path() -> Path:
    """Đảm bảo thư mục lưu trữ sao lưu tồn tại và trả về Path."""
    BACKUP_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_STORAGE_DIR


def calculate_file_sha256(filepath: Path | str) -> str:
    """Tính mã băm SHA-256 của file nhị phân."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_sqlite_file_integrity(filepath: Path | str) -> tuple[bool, str]:
    """Kiểm tra tính toàn vẹn của file SQLite bằng PRAGMA integrity_check."""
    conn = None
    try:
        conn = sqlite3.connect(str(filepath))
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        rows = cursor.fetchall()
        result_text = "\n".join(r[0] for r in rows) if rows else "no result"
        is_ok = len(rows) == 1 and rows[0][0].lower() == "ok"
        return is_ok, result_text
    except Exception as e:
        return False, f"Lỗi mở SQLite: {e}"
    finally:
        if conn:
            conn.close()


def cleanup_old_backups(db: Session, max_retained: int = MAX_RETAINED_BACKUPS) -> int:
    """Dọn dẹp các bản sao lưu cũ, chỉ giữ lại tối đa `max_retained` bản sao lưu thành công gần nhất.

    Chỉ chạy sau khi bản mới nhất đã được tạo và xác minh thành công.
    """
    successful_backups = list(
        db.scalars(
            select(BackupLog)
            .where(BackupLog.status == "SUCCESS")
            .order_by(desc(BackupLog.timestamp), desc(BackupLog.id))
        ).all()
    )

    removed_count = 0
    if len(successful_backups) > max_retained:
        to_delete = successful_backups[max_retained:]
        for backup in to_delete:
            p = Path(backup.file_path)
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass  # Bỏ qua nếu file đã bị xoá ngoài OS
            db.delete(backup)
            removed_count += 1
        db.commit()

    return removed_count


def execute_database_backup(db: Session, custom_dest_dir: Path | None = None) -> BackupLog:
    """Thực thi sao lưu CSDL SQLite bằng Online Backup API.

    Quy trình:
    1. Tạo tên file theo timestamp: ttcs_backup_YYYYMMDD_HHMMSS.db
    2. Dùng sqlite3.Connection.backup để sao chép nguyên khối nhất quán.
    3. Xác minh integrity_check trên file mới tạo.
    4. Tính checksum SHA-256 và dung lượng.
    5. Ghi nhật ký vào BackupLog.
    6. Dọn dẹp bản sao lưu cũ (giữ tối đa 14 bản) nếu thành công.
    """
    source_db_path = Path(DATABASE_FILE)
    if not source_db_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file CSDL nguồn: {source_db_path}")

    storage_dir = custom_dest_dir or get_backup_storage_path()
    storage_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"ttcs_backup_{timestamp_str}.db"
    dest_path = storage_dir / backup_filename

    src_conn = None
    dst_conn = None
    try:
        # SQLite Online Backup API
        src_conn = sqlite3.connect(str(source_db_path))
        dst_conn = sqlite3.connect(str(dest_path))
        with dst_conn:
            src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()

        # Xác minh file backup
        is_valid, integrity_msg = verify_sqlite_file_integrity(dest_path)
        if not is_valid:
            raise RuntimeError(f"File sao lưu không vượt qua integrity_check: {integrity_msg}")

        file_size = dest_path.stat().st_size
        checksum = calculate_file_sha256(dest_path)

        log = BackupLog(
            status="SUCCESS",
            file_path=str(dest_path),
            file_size_bytes=file_size,
            sha256_checksum=checksum,
            details=f"SQLite Online Backup thành công. PRAGMA integrity_check: {integrity_msg}",
            rehearsal_verified=False,
        )
        db.add(log)
        db.commit()
        db.refresh(log)

        # Dọn dẹp các bản backup cũ
        cleanup_old_backups(db, MAX_RETAINED_BACKUPS)

        return log

    except Exception as exc:
        if dst_conn:
            dst_conn.close()
        if src_conn:
            src_conn.close()

        error_log = BackupLog(
            status="FAILED",
            file_path=str(dest_path),
            file_size_bytes=0,
            sha256_checksum="",
            details=f"Lỗi khi thực hiện sao lưu: {exc}",
            rehearsal_verified=False,
        )
        db.add(error_log)
        db.commit()
        db.refresh(error_log)
        raise exc


def rehearse_restore(
    db: Session,
    backup_id: int | None = None,
    backup_file_path: str | None = None,
) -> dict[str, Any]:
    """Diễn tập khôi phục trên CSDL tạm thời (Dry-run Restore).

    Tuyệt đối không ghi đè CSDL thật. Sử dụng file tạm và kiểm tra:
    - Khả năng mở CSDL.
    - PRAGMA integrity_check.
    - Đếm số lượng bảng và dữ liệu: farms, batches, users...
    """
    target_path: Path | None = None
    backup_log_entry: BackupLog | None = None

    if backup_id is not None:
        backup_log_entry = db.get(BackupLog, backup_id)
        if backup_log_entry is None:
            raise ValueError(f"Không tìm thấy bản ghi sao lưu ID={backup_id}")
        target_path = Path(backup_log_entry.file_path)
    elif backup_file_path:
        target_path = Path(backup_file_path)
    else:
        # Lấy bản backup thành công gần nhất
        backup_log_entry = db.scalar(
            select(BackupLog)
            .where(BackupLog.status == "SUCCESS")
            .order_by(desc(BackupLog.timestamp), desc(BackupLog.id))
        )
        if backup_log_entry is None:
            raise ValueError("Chưa có bản sao lưu nào để diễn tập khôi phục.")
        target_path = Path(backup_log_entry.file_path)

    if not target_path or not target_path.exists():
        raise FileNotFoundError(f"File sao lưu không tồn tại trên đĩa: {target_path}")

    # Tạo file database tạm thời để diễn tập
    temp_dir = Path(tempfile.gettempdir())
    temp_restored_db = temp_dir / f"rehearsal_restore_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.db"

    src_conn = None
    temp_conn = None
    tables_info: dict[str, int] = {}
    try:
        # Dùng Online Backup để restore từ backup sang temp db
        src_conn = sqlite3.connect(str(target_path))
        temp_conn = sqlite3.connect(str(temp_restored_db))
        with temp_conn:
            src_conn.backup(temp_conn)
        src_conn.close()

        # Kiểm tra integrity trên database tạm
        cursor = temp_conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        rows = cursor.fetchall()
        integrity_str = "\n".join(r[0] for r in rows) if rows else "none"
        is_ok = len(rows) == 1 and rows[0][0].lower() == "ok"

        # Kiểm tra các bảng nghiệp vụ
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        all_tables = [r[0] for r in cursor.fetchall() if not r[0].startswith("sqlite_")]
        for tbl in all_tables:
            cursor.execute(f"SELECT COUNT(*) FROM [{tbl}];")  # noqa: S608 - an toàn vì lấy từ sqlite_master
            count_val = cursor.fetchone()[0]
            tables_info[tbl] = count_val

        temp_conn.close()

        if not is_ok:
            raise RuntimeError(f"Database tạm không đạt toàn vẹn: {integrity_str}")

        # Cập nhật rehearsal_verified cho backup_log nếu có
        if backup_log_entry:
            backup_log_entry.rehearsal_verified = True
            db.commit()

        return {
            "status": "SUCCESS",
            "tested_file": str(target_path),
            "integrity_check_output": integrity_str,
            "tables_checked": tables_info,
            "details": f"Diễn tập khôi phục thành công trên DB tạm: {temp_restored_db.name}. Dữ liệu các bảng hợp lệ.",
        }

    finally:
        if src_conn:
            src_conn.close()
        if temp_conn:
            temp_conn.close()
        # Dọn dẹp file tạm
        try:
            if temp_restored_db.exists():
                temp_restored_db.unlink()
        except OSError:
            pass


def list_backups(db: Session, limit: int = 50) -> list[BackupLog]:
    """Lấy danh sách các bản ghi sao lưu gần nhất."""
    return list(
        db.scalars(
            select(BackupLog)
            .order_by(desc(BackupLog.timestamp), desc(BackupLog.id))
            .limit(limit)
        ).all()
    )
