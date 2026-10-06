"""TTCS K18C4 - Backend package.

Đề tài: Truy xuất nguồn gốc và giám sát chuỗi lạnh nông sản.

Package `app` chứa toàn bộ mã nguồn backend FastAPI:
- ``main``      : khởi tạo ứng dụng FastAPI và đăng ký routers.
- ``database``  : cấu hình kết nối SQLite + SQLAlchemy, `get_db`, `init_db`.
- ``models``    : khai báo ORM models (`Farm`, `Batch`, `Product`, `User`)
  + hằng số vai trò.
- ``schemas``   : khai báo Pydantic schemas cho request/response.
- ``security``  : băm/kiểm tra mật khẩu + dependency phân quyền
  (`get_current_user`, `require_admin`, `require_farmer`).
- ``routers``   : nhóm các endpoint theo nghiệp vụ.
"""

__version__ = "0.2.0"
