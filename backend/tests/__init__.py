"""Bộ test backend (pytest) - chạy trong CI bằng lệnh::

    PYTHONPATH=backend pytest backend/tests -v

Mỗi test dùng **file SQLite tạm** (``tmp_path``) nên không chạm vào dữ liệu thật
``backend/ttcs.db`` và không cần dịch vụ CSDL bên ngoài.
"""
