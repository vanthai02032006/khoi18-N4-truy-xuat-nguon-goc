"""Bộ kiểm thử đơn vị & kiểm thử chấp nhận (DoD / Acceptance Criteria) cho Task T-18 (SCRUM-34).

Tiêu chí nghiệm thu:
1. Sinh 10.000 mã trong 1 vòng lặp khẳng định 100% không trùng (Zero Collision).
2. Mã sinh ra không chứa bất kỳ ký tự nào trong danh sách cấm ('0', 'O', '1', 'I', 'l').
3. Độ dài mọi mã luôn cố định bằng 8 ký tự.
4. Nguồn ngẫu nhiên an toàn bảo mật (CSPRNG via secrets module).
5. Cơ chế phát hiện trùng mã (ràng buộc UNIQUE) và tự động sinh lại (Retry logic).
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

# Đảm bảo import được package `app` từ thư mục `backend/`
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.code_generator import (
    CodeCollisionError,
    execute_with_unique_retry,
    generate_code,
    generate_unique_code,
)
from app.config import (
    CODE_ALPHABET,
    CODE_CONFIG,
    CODE_LENGTH,
    FORBIDDEN_CHARACTERS,
    MAX_CODE_RETRIES,
)


class TestCodeGeneratorDoD:
    """Các bài kiểm thử tiêu chí nghiệm thu (DoD / Acceptance Criteria)."""

    def test_single_source_of_truth_configuration(self):
        """Lưu ý kỹ thuật: Bảng ký tự và độ dài khai báo ở một hằng số duy nhất trong cấu hình."""
        assert CODE_CONFIG is not None
        assert CODE_CONFIG["length"] == 8
        assert CODE_CONFIG["alphabet"] == CODE_ALPHABET
        assert CODE_CONFIG["forbidden_chars"] == FORBIDDEN_CHARACTERS

        # Bảng ký tự cấu hình không được chứa bất kỳ ký tự cấm nào
        for forbidden in FORBIDDEN_CHARACTERS:
            assert forbidden not in CODE_ALPHABET, (
                f"Bảng ký tự cấu hình chứa ký tự cấm: '{forbidden}'"
            )

    def test_generate_10000_codes_unique_and_no_forbidden_chars_in_one_loop(self):
        """DoD 1 & 2: Sinh 10.000 mã trong 1 vòng lặp khẳng định 100% không trùng và không chứa ký tự cấm."""
        total_codes = 10_000
        generated_list: list[str] = []
        unique_set: set[str] = set()

        # Thực thi sinh 10.000 mã trong 1 vòng lặp duy nhất
        for _ in range(total_codes):
            code = generate_code()
            generated_list.append(code)
            unique_set.add(code)

            # Khẳng định độ dài luôn luôn là 8
            assert len(code) == CODE_LENGTH, (
                f"Mã '{code}' có độ dài {len(code)}, khác độ dài yêu cầu {CODE_LENGTH}."
            )

            # Khẳng định mã không chứa bất kỳ ký tự cấm nào trong ('0', 'O', '1', 'I', 'l')
            for char in code:
                assert char not in FORBIDDEN_CHARACTERS, (
                    f"Mã '{code}' chứa ký tự cấm '{char}'!"
                )

        # Khẳng định 1: Tổng số mã sinh ra đúng bằng 10.000
        assert len(generated_list) == total_codes

        # Khẳng định 2: Tỉ lệ không trùng là 100% (len(set) == 10.000)
        assert len(unique_set) == total_codes, (
            f"Phát hiện trùng mã: Có {total_codes - len(unique_set)} mã bị trùng trong {total_codes} mã sinh ra!"
        )

    def test_random_source_is_cryptographically_secure(self):
        """Khẳng định hàm sử dụng nguồn ngẫu nhiên an toàn (secrets.choice)."""
        with patch("secrets.choice", side_effect=lambda alph: alph[0]) as mock_secrets:
            code = generate_code()
            assert mock_secrets.called
            assert mock_secrets.call_count == CODE_LENGTH
            assert code == CODE_ALPHABET[0] * CODE_LENGTH

    def test_unique_collision_detection_and_retry(self):
        """Khi trùng mã (ràng buộc unique bắt được) thì tự động sinh lại."""
        attempts = 0

        # Giả lập database hoặc bộ nhớ đã tồn tại:
        # Lần 1: trả về True (bị trùng)
        # Lần 2: trả về True (bị trùng)
        # Lần 3: trả về False (thành công)
        def mock_is_exists(candidate_code: str) -> bool:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return True  # Giả lập trùng mã
            return False  # Mã hợp lệ

        final_code = generate_unique_code(is_exists_fn=mock_is_exists, max_retries=5)

        assert final_code is not None
        assert len(final_code) == CODE_LENGTH
        assert attempts == 3  # Đã thử lại 2 lần và thành công ở lần thứ 3

    def test_collision_exceeding_max_retries_raises_error(self):
        """Nếu liên tục bị trùng vượt quá max_retries thì ném ra CodeCollisionError."""
        # Giả lập luôn luôn trùng mã
        always_exists = lambda _: True

        with pytest.raises(CodeCollisionError) as exc_info:
            generate_unique_code(is_exists_fn=always_exists, max_retries=4)

        assert "sau 4 lần thử" in str(exc_info.value)


class TestDatabaseIntegrityErrorRetry:
    """Kiểm thử bắt lỗi ràng buộc UNIQUE từ SQLAlchemy/SQLite và tự động sinh lại mã."""

    def test_sqlalchemy_unique_constraint_retry(self):
        TestBase = declarative_base()

        class DummyBatch(TestBase):
            __tablename__ = "dummy_batches"
            id = Column(Integer, primary_key=True, autoincrement=True)
            code = Column(String(8), unique=True, nullable=False)

        engine = create_engine("sqlite:///:memory:")
        TestBase.metadata.create_all(engine)
        SessionTest = sessionmaker(bind=engine)
        session = SessionTest()

        # Tạo trước một bản ghi có code cố định
        existing_code = "ABCDEF23"
        session.add(DummyBatch(code=existing_code))
        session.commit()

        # Giả lập generate_code sinh ra 'ABCDEF23' ở lần đầu tiên (gây IntegrityError),
        # và sinh ra 'ZYXWV987' ở lần thứ hai
        simulated_codes = iter([existing_code, "ZYXWV987"])

        with patch("app.code_generator.generate_code", side_effect=lambda: next(simulated_codes)):
            def save_dummy(code: str) -> DummyBatch:
                item = DummyBatch(code=code)
                session.add(item)
                session.commit()
                return item

            success_code, item = execute_with_unique_retry(save_fn=save_dummy, db=session, max_retries=3)

            assert success_code == "ZYXWV987"
            assert item.code == "ZYXWV987"

        # Kiểm tra database hiện có đúng 2 bản ghi với mã khác nhau
        records = session.query(DummyBatch).all()
        assert len(records) == 2
        assert {r.code for r in records} == {existing_code, "ZYXWV987"}

        session.close()
