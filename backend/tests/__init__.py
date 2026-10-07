"""Bộ test backend (pytest) - chạy trong CI bằng lệnh::

    PYTHONPATH=backend pytest backend/tests -v

Đây là package (có ``__init__.py``) để pytest import nhất quán theo đường dẫn
``tests.test_<tên>``, tránh trùng tên module khi bộ test lớn dần.
"""
