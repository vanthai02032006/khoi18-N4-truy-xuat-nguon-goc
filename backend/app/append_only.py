"""Quy ước **bảng chỉ thêm (append-only)** và cơ chế bảo vệ bất biến ở tầng CSDL.

Quy ước: một bảng nằm trong ``APPEND_ONLY_TABLES`` là **sổ nhật ký bất biến** -
tài khoản ứng dụng **chỉ được ``SELECT`` và ``INSERT``**, tuyệt đối không được
``UPDATE`` / ``DELETE``. Danh sách này là **nguồn duy nhất** (single source of
truth) cho cả hai lớp bảo vệ bên dưới.

Vì sao cần quy ước này: nhật ký hành trình lô nông sản là bằng chứng truy xuất
nguồn gốc. Nếu sửa được nhiệt độ chuỗi lạnh hay xoá dấu vết bàn giao thì toàn
bộ hồ sơ mất giá trị pháp lý. Quy ước chặn ngay ở CSDL - không phụ thuộc vào
việc lập trình viên có nhớ kiểm tra hay không.

Hai lớp bảo vệ (defense-in-depth), cả hai đều nằm **trong CSDL** chứ không phải
trong code nghiệp vụ:

1. **Quyền của tài khoản ứng dụng** (application account) - lớp thứ nhất:

   - *PostgreSQL (production)*: role ``agri_app_user`` chỉ được ``GRANT SELECT,
     INSERT`` trên bảng chỉ thêm; ``UPDATE`` / ``DELETE`` / ``TRUNCATE`` bị
     ``REVOKE`` - xem ``migrations/002_batch_events_append_only.sql``.
   - *SQLite (chạy local/demo và trong CI)*: SQLite không có role/``GRANT`` nên
     lớp tương đương là ``sqlite3.Connection.set_authorizer``. Engine **từ chối
     thẳng** câu lệnh ``UPDATE``/``DELETE`` trên bảng chỉ thêm, độc lập hoàn toàn
     với ORM và không thể vô hiệu hoá bằng cách viết sai ở tầng Python.

2. **Trigger ``BEFORE UPDATE`` / ``BEFORE DELETE``** - lớp thứ hai (van an toàn):
   chặn ở mọi kết nối, kể cả tài khoản quản trị/migration.

Nhờ lớp 2, ``tests/test_append_only_immutability.py`` khẳng định được rằng ngay
cả một kết nối **toàn quyền** (giả lập tài khoản migration) cũng không sửa/xoá
được nhật ký.
"""

from __future__ import annotations

import sqlite3


from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

# ------------------------------------------------------------------ Quy ước ---
#: Các bảng chỉ thêm của hệ thống. Thêm bảng mới vào đây thì cả 2 lớp bảo vệ
#: (authorizer + trigger) và bộ test bất biến sẽ tự áp dụng cho bảng đó.
APPEND_ONLY_TABLES: tuple[str, ...] = ("batch_events",)

#: Thông điệp CSDL trả về khi từ chối sửa/xoá bảng chỉ thêm.
REJECTION_MESSAGE_TEMPLATE: str = (
    "CSDL TỪ CHỐI: bảng {table} là bảng chỉ thêm (append-only) - "
    "nghiêm cấm mọi thao tác UPDATE/DELETE."
)


def rejection_message(table: str) -> str:
    """Sinh thông điệp từ chối cho một bảng chỉ thêm cụ thể."""
    return REJECTION_MESSAGE_TEMPLATE.format(table=table)


def is_append_only(table: str | None) -> bool:
    """``True`` nếu tên bảng thuộc quy ước bảng chỉ thêm."""
    return bool(table) and str(table).lower() in APPEND_ONLY_TABLES


def trigger_names(table: str) -> tuple[str, str]:
    """Tên 2 trigger bảo vệ bất biến của ``table`` (cập nhật, xoá)."""
    return (
        f"trg_prevent_update_{table}",
        f"trg_prevent_delete_{table}",
    )


# --------------------------------------------- Lớp 1: quyền tài khoản ứng dụng ---
def _sqlite_authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    db_name: str | None,
    trigger_name: str | None,
) -> int:
    """Hàm authorizer của SQLite: từ chối UPDATE/DELETE trên bảng chỉ thêm.

    Đây là lớp tương đương ``REVOKE UPDATE, DELETE`` của PostgreSQL: quyết định
    nằm ở engine CSDL, code Python không thể ghi đè.

    Args:
        action: Mã hành động SQLite muốn thực hiện (``SQLITE_UPDATE``...).
        arg1: Với ``SQLITE_UPDATE``/``SQLITE_DELETE`` là **tên bảng** bị tác động.
        arg2: Tên cột (với ``SQLITE_UPDATE``) hoặc ``None``.
        db_name: Tên schema/attached database.
        trigger_name: Tên trigger nếu hành động phát sinh từ trigger.

    Returns:
        int: ``SQLITE_DENY`` nếu là thao tác ghi lên bảng chỉ thêm, ngược lại
        ``SQLITE_OK`` để không ảnh hưởng các thao tác hợp lệ khác.
    """
    if action in (sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) and is_append_only(arg1):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _on_connect_apply_authorizer(dbapi_connection: object, connection_record: object) -> None:
    """Gắn authorizer vào **mỗi** kết nối SQLite mới của engine ứng dụng."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.set_authorizer(_sqlite_authorizer)


def install_append_only_authorizer(engine: Engine) -> None:
    """Bật lớp bảo vệ 1 (quyền tài khoản ứng dụng) cho ``engine``.

    Chỉ áp dụng với SQLite - với PostgreSQL, lớp này do ``REVOKE`` trong
    ``migrations/002_batch_events_append_only.sql`` đảm nhiệm nên hàm chỉ ghi
    log/không làm gì.

    Phải gọi **trước khi** engine mở kết nối đầu tiên (``app/database.py`` gọi
    ngay sau khi tạo ``engine``) để mọi kết nối trong pool đều được bảo vệ.

    Args:
        engine: Engine SQLAlchemy của tài khoản ứng dụng.
    """
    if engine.dialect.name != "sqlite":
        return  # PostgreSQL: đã bảo vệ bằng GRANT/REVOKE ở migration.

    # `event.contains` để gọi lại nhiều lần (idempotent) không gắn trùng listener.
    if not event.contains(engine, "connect", _on_connect_apply_authorizer):
        event.listen(engine, "connect", _on_connect_apply_authorizer)


# ------------------------------------------------------- Lớp 2: trigger CSDL ---
def create_append_only_triggers(connection: Connection) -> None:
    """Tạo trigger chặn ``UPDATE``/``DELETE`` cho mọi bảng chỉ thêm.

    Trigger dùng ``RAISE(ABORT, ...)`` nên câu lệnh bị huỷ và CSDL trả lỗi -
    kể cả khi kết nối **không** bị authorizer giới hạn (tài khoản admin).

    Hàm **idempotent** (``CREATE TRIGGER IF NOT EXISTS``) nên gọi lại mỗi lần
    khởi động server đều an toàn. Hiện hỗ trợ SQLite; PostgreSQL dùng trigger
    ``plpgsql`` tương đương trong ``migrations/002_batch_events_append_only.sql``.

    Args:
        connection: Kết nối SQLAlchemy đang trong transaction (bảng đã tồn tại).
    """
    if connection.dialect.name != "sqlite":
        return  # PostgreSQL: trigger đã được tạo bởi migration.

    for table in APPEND_ONLY_TABLES:
        # Tên bảng lấy từ hằng số nội bộ (không phải input người dùng) nên an
        # toàn khi nội suy; thông điệp escape nháy đơn để nhúng vào SQL.
        message = rejection_message(table).replace("'", "''")
        update_trigger, delete_trigger = trigger_names(table)

        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS {update_trigger}
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{message}');
            END
            """
        )
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS {delete_trigger}
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{message}');
            END
            """
        )


def install_append_only_protection(engine: Engine) -> None:
    """Bật **cả hai** lớp bảo vệ cho ``engine`` (dùng cho app và cho test).

    Args:
        engine: Engine SQLAlchemy cần bảo vệ.
    """
    install_append_only_authorizer(engine)
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            create_append_only_triggers(connection)


__all__ = [
    "APPEND_ONLY_TABLES",
    "REJECTION_MESSAGE_TEMPLATE",
    "create_append_only_triggers",
    "install_append_only_authorizer",
    "install_append_only_protection",
    "is_append_only",
    "rejection_message",
    "trigger_names",
]
