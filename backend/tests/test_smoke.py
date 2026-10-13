"""Smoke test kiem tra he thong khoi dong hop le."""
import pytest
from app.main import app


def test_app_instance():
    """Kiem tra khoi tao ung dung FastAPI thanh cong."""
    assert app is not None
    assert app.title is not None
