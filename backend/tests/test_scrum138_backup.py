"""Unit test & Integration test cho SCRUM-138: Sao lưu CSDL và diễn tập khôi phục."""

import os
import sqlite3
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import BackupLog, Farm, User, ROLE_ADMIN, ROLE_FARMER
from app.security import hash_password
from app.services.backup_service import (
    calculate_file_sha256,
    cleanup_old_backups,
    execute_database_backup,
    rehearse_restore,
    verify_sqlite_file_integrity,
)


@pytest.fixture
def temp_db_env(tmp_path):
    """Tạo môi trường SQLite cô lập cho test."""
    db_file = tmp_path / "test_app.db"
    backup_storage = tmp_path / "remote_storage"
    backup_storage.mkdir(parents=True, exist_ok=True)

    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    # Seed data
    db = TestingSession()
    admin = User(username="admin_backup", password=hash_password("123456"), role=ROLE_ADMIN, organization_id="ORG_ADMIN")
    farmer = User(username="farmer_backup", password=hash_password("123456"), role=ROLE_FARMER, organization_id="ORG_FARMER")
    farm = Farm(name="Vườn Thử Nghiệm", location="Đồng Tháp", area=3.5, owner="HTX")
    db.add_all([admin, farmer, farm])
    db.commit()

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    # Patch DATABASE_FILE trong backup_service
    import app.services.backup_service as bs
    original_db_file = bs.DATABASE_FILE
    bs.DATABASE_FILE = db_file

    client = TestClient(app)

    yield {
        "db": db,
        "db_file": db_file,
        "backup_storage": backup_storage,
        "client": client,
        "admin_auth": ("admin_backup", "123456"),
        "farmer_auth": ("farmer_backup", "123456"),
    }

    # Teardown
    bs.DATABASE_FILE = original_db_file
    app.dependency_overrides.clear()
    db.close()


def test_online_backup_execution_and_integrity(temp_db_env):
    """Kiểm tra sao lưu CSDL thành công bằng Online Backup API và kiểm tra integrity."""
    db = temp_db_env["db"]
    storage = temp_db_env["backup_storage"]

    backup_log = execute_database_backup(db, custom_dest_dir=storage)
    assert backup_log.status == "SUCCESS"
    assert backup_log.file_size_bytes > 0
    assert len(backup_log.sha256_checksum) == 64

    # File phải tồn tại trên đĩa
    backup_path = Path(backup_log.file_path)
    assert backup_path.exists()

    # Checksum khớp
    assert calculate_file_sha256(backup_path) == backup_log.sha256_checksum

    # Integrity check trên file SQLite backup
    is_valid, msg = verify_sqlite_file_integrity(backup_path)
    assert is_valid is True
    assert msg.strip() == "ok"


def test_retention_policy_keeps_max_14_backups(temp_db_env):
    """Kiểm tra chính sách duy trì giữ tối đa 14 bản sao lưu hợp lệ gần nhất."""
    db = temp_db_env["db"]
    storage = temp_db_env["backup_storage"]

    created_logs = []
    # Tạo 16 bản sao lưu giả lập
    for i in range(16):
        dummy_file = storage / f"dummy_backup_{i}.db"
        dummy_file.write_text(f"dummy content {i}")
        log = BackupLog(
            status="SUCCESS",
            file_path=str(dummy_file),
            file_size_bytes=100,
            sha256_checksum=f"hash_{i}",
            rehearsal_verified=False,
        )
        db.add(log)
        created_logs.append(log)
    db.commit()

    # Chạy dọn dẹp
    removed = cleanup_old_backups(db, max_retained=14)
    assert removed == 2

    # Kiểm tra chỉ còn đúng 14 bản trong DB
    remaining_logs = list(db.query(BackupLog).filter(BackupLog.status == "SUCCESS").all())
    assert len(remaining_logs) == 14


def test_dry_run_rehearsal_restore(temp_db_env):
    """Kiểm tra diễn tập khôi phục trên CSDL tạm, không ảnh hưởng CSDL thật."""
    db = temp_db_env["db"]
    storage = temp_db_env["backup_storage"]

    backup_log = execute_database_backup(db, custom_dest_dir=storage)
    rehearsal_res = rehearse_restore(db, backup_id=backup_log.id)

    assert rehearsal_res["status"] == "SUCCESS"
    assert rehearsal_res["integrity_check_output"].strip() == "ok"
    assert "users" in rehearsal_res["tables_checked"]
    assert rehearsal_res["tables_checked"]["users"] >= 2
    assert "farms" in rehearsal_res["tables_checked"]

    # Đã đánh dấu rehearsal_verified trong log
    db.refresh(backup_log)
    assert backup_log.rehearsal_verified is True


def test_backup_api_endpoints_permissions(temp_db_env):
    """Kiểm tra quyền gọi API /backups (chỉ admin mới có quyền)."""
    client = temp_db_env["client"]
    admin_auth = temp_db_env["admin_auth"]
    farmer_auth = temp_db_env["farmer_auth"]

    # 1. Chưa đăng nhập -> 401
    resp_anon = client.post("/backups/run")
    assert resp_anon.status_code == 401

    # 2. Farmer đăng nhập gọi -> 403
    resp_farmer = client.post("/backups/run", auth=farmer_auth)
    assert resp_farmer.status_code == 403

    # 3. Admin đăng nhập gọi -> 201
    resp_admin = client.post("/backups/run", auth=admin_auth)
    assert resp_admin.status_code == 201
    data = resp_admin.json()
    assert "backup" in data
    assert data["backup"]["status"] == "SUCCESS"

    # 4. Diễn tập qua API
    resp_rehearsal = client.post("/backups/rehearsal", json={"backup_id": data["backup"]["id"]}, auth=admin_auth)
    assert resp_rehearsal.status_code == 200
    assert resp_rehearsal.json()["status"] == "SUCCESS"
