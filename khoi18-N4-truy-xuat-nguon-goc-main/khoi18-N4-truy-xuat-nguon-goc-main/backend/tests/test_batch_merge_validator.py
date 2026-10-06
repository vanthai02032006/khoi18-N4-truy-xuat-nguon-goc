"""Kiểm thử đơn vị (Unit Tests) cho Lớp kiểm tra đầu vào hàm gộp lô (T-21 / T-44).

Kiểm tra toàn diện 3 ca kiểm tra sai theo tiêu chí nghiệm thu (DoD / AC):
- Ca 1: Từ chối khi khác product_id và nêu rõ mã lô vi phạm.
- Ca 2: Từ chối khi lô không do tổ chức hiện tại giữ và nêu rõ mã lô vi phạm.
- Ca 3: Từ chối khi khối lượng lấy <= 0 hoặc vượt quá số còn lại và nêu rõ mã lô vi phạm.
- Trường hợp hợp lệ: Cho phép gộp khi tất cả điều kiện đều thoả mãn.
"""

import unittest

from app.validators.batch_merge import (
    BatchMergeInputItem,
    BatchMergeValidationError,
    BatchRecord,
    DifferentProductError,
    InsufficientQuantityError,
    InvalidQuantityError,
    MergeValidationErrorCode,
    OrgMismatchError,
    validate_batch_merge_input,
)


class TestBatchMergeValidator(unittest.TestCase):
    """Bộ kiểm thử cho hàm validate_batch_merge_input."""

    def setUp(self) -> None:
        """Chuẩn bị dữ liệu mẫu cho các ca kiểm thử."""
        self.current_org_id = "ORG-COOP-01"

        # Dữ liệu lô mẫu trong database
        self.mock_db_batches: dict[str | int, BatchRecord] = {
            "BATCH-001": BatchRecord(
                batch_id="BATCH-001",
                product_id="PROD-MANGO-01",
                holder_org_id="ORG-COOP-01",
                remaining_quantity=100.0,
                product_name="Xoài cát Chu",
            ),
            "BATCH-002": BatchRecord(
                batch_id="BATCH-002",
                product_id="PROD-MANGO-01",
                holder_org_id="ORG-COOP-01",
                remaining_quantity=80.0,
                product_name="Xoài cát Chu",
            ),
            # Lô khác sản phẩm
            "BATCH-DIFF-PROD": BatchRecord(
                batch_id="BATCH-DIFF-PROD",
                product_id="PROD-DRAGONFRUIT-02",  # Khác product_id
                holder_org_id="ORG-COOP-01",
                remaining_quantity=50.0,
                product_name="Thanh Long ruột đỏ",
            ),
            # Lô thuộc tổ chức khác
            "BATCH-OTHER-ORG": BatchRecord(
                batch_id="BATCH-OTHER-ORG",
                product_id="PROD-MANGO-01",
                holder_org_id="ORG-FARM-99",  # Khác tổ chức
                remaining_quantity=60.0,
                product_name="Xoài cát Chu",
            ),
            # Lô có số lượng nhỏ để test vượt số còn lại
            "BATCH-LOW-STOCK": BatchRecord(
                batch_id="BATCH-LOW-STOCK",
                product_id="PROD-MANGO-01",
                holder_org_id="ORG-COOP-01",
                remaining_quantity=15.0,
                product_name="Xoài cát Chu",
            ),
        }

    # =========================================================================
    # 1. Ca thành công (Happy Path)
    # =========================================================================
    def test_valid_merge_input_success(self) -> None:
        """Kiểm tra gộp hợp lệ: cùng product_id, cùng tổ chức, khối lượng hợp lệ."""
        items = [
            BatchMergeInputItem(batch_id="BATCH-001", take_quantity=50.0),
            BatchMergeInputItem(batch_id="BATCH-002", take_quantity=30.0),
        ]

        result = validate_batch_merge_input(
            items=items,
            batches_map=self.mock_db_batches,
            current_org_id=self.current_org_id,
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.violating_batch_ids, [])

    # =========================================================================
    # 2. Ca 1: Từ chối khi khác product_id & Nêu đúng mã lô vi phạm
    # =========================================================================
    def test_reject_different_product_id_reports_violating_batch(self) -> None:
        """AC Ca 1: Khác product_id -> Bị từ chối với thông báo nêu đúng mã lô vi phạm."""
        items = [
            BatchMergeInputItem(batch_id="BATCH-001", take_quantity=20.0),  # PROD-MANGO-01
            BatchMergeInputItem(batch_id="BATCH-DIFF-PROD", take_quantity=15.0),  # PROD-DRAGONFRUIT-02
        ]

        result = validate_batch_merge_input(
            items=items,
            batches_map=self.mock_db_batches,
            current_org_id=self.current_org_id,
        )

        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.errors), 1)

        error = result.errors[0]
        self.assertEqual(error.code, MergeValidationErrorCode.DIFFERENT_PRODUCT)
        self.assertEqual(error.violating_batch_id, "BATCH-DIFF-PROD")
        self.assertIn("BATCH-DIFF-PROD", error.message)
        self.assertIn("PROD-DRAGONFRUIT-02", error.message)
        self.assertIn("PROD-MANGO-01", error.message)

        # Kiểm tra khi bật raise_exception
        with self.assertRaises(DifferentProductError) as ctx:
            validate_batch_merge_input(
                items=items,
                batches_map=self.mock_db_batches,
                current_org_id=self.current_org_id,
                raise_exception=True,
            )
        self.assertEqual(ctx.exception.violating_batch_id, "BATCH-DIFF-PROD")

    # =========================================================================
    # 3. Ca 2: Từ chối khi lô không do tổ chức mình giữ & Nêu đúng mã lô vi phạm
    # =========================================================================
    def test_reject_not_held_by_current_org_reports_violating_batch(self) -> None:
        """AC Ca 2: Lô do tổ chức khác giữ -> Bị từ chối với thông báo nêu đúng mã lô vi phạm."""
        items = [
            BatchMergeInputItem(batch_id="BATCH-001", take_quantity=20.0),
            BatchMergeInputItem(batch_id="BATCH-OTHER-ORG", take_quantity=15.0),  # ORG-FARM-99
        ]

        result = validate_batch_merge_input(
            items=items,
            batches_map=self.mock_db_batches,
            current_org_id=self.current_org_id,
        )

        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.errors), 1)

        error = result.errors[0]
        self.assertEqual(error.code, MergeValidationErrorCode.NOT_HELD_BY_ORG)
        self.assertEqual(error.violating_batch_id, "BATCH-OTHER-ORG")
        self.assertIn("BATCH-OTHER-ORG", error.message)
        self.assertIn("ORG-FARM-99", error.message)
        self.assertIn(self.current_org_id, error.message)

        # Kiểm tra khi bật raise_exception
        with self.assertRaises(OrgMismatchError) as ctx:
            validate_batch_merge_input(
                items=items,
                batches_map=self.mock_db_batches,
                current_org_id=self.current_org_id,
                raise_exception=True,
            )
        self.assertEqual(ctx.exception.violating_batch_id, "BATCH-OTHER-ORG")

    # =========================================================================
    # 4. Ca 3: Từ chối khi khối lượng không hợp lệ & Nêu đúng mã lô vi phạm
    # =========================================================================
    def test_reject_non_positive_quantity_reports_violating_batch(self) -> None:
        """AC Ca 3a: Khối lượng lấy <= 0 -> Bị từ chối với thông báo nêu đúng mã lô vi phạm."""
        items = [
            BatchMergeInputItem(batch_id="BATCH-001", take_quantity=20.0),
            BatchMergeInputItem(batch_id="BATCH-002", take_quantity=-5.0),  # Âm
        ]

        result = validate_batch_merge_input(
            items=items,
            batches_map=self.mock_db_batches,
            current_org_id=self.current_org_id,
        )

        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.errors), 1)

        error = result.errors[0]
        self.assertEqual(error.code, MergeValidationErrorCode.INVALID_QUANTITY_NON_POSITIVE)
        self.assertEqual(error.violating_batch_id, "BATCH-002")
        self.assertIn("BATCH-002", error.message)
        self.assertIn("-5.0", error.message)

        # Kiểm tra khi bật raise_exception
        with self.assertRaises(InvalidQuantityError) as ctx:
            validate_batch_merge_input(
                items=items,
                batches_map=self.mock_db_batches,
                current_org_id=self.current_org_id,
                raise_exception=True,
            )
        self.assertEqual(ctx.exception.violating_batch_id, "BATCH-002")

    def test_reject_zero_quantity_reports_violating_batch(self) -> None:
        """AC Ca 3a (tiếp): Khối lượng lấy = 0 -> Bị từ chối."""
        items = [
            BatchMergeInputItem(batch_id="BATCH-001", take_quantity=20.0),
            BatchMergeInputItem(batch_id="BATCH-002", take_quantity=0.0),  # Bằng 0
        ]

        result = validate_batch_merge_input(
            items=items,
            batches_map=self.mock_db_batches,
            current_org_id=self.current_org_id,
        )

        self.assertFalse(result.is_valid)
        self.assertEqual(result.errors[0].violating_batch_id, "BATCH-002")
        self.assertEqual(result.errors[0].code, MergeValidationErrorCode.INVALID_QUANTITY_NON_POSITIVE)

    def test_reject_exceeds_remaining_quantity_reports_violating_batch(self) -> None:
        """AC Ca 3b: Khối lượng lấy vượt quá số còn lại -> Bị từ chối và nêu rõ mã lô."""
        items = [
            BatchMergeInputItem(batch_id="BATCH-001", take_quantity=20.0),
            # BATCH-LOW-STOCK chỉ còn 15.0 kg nhưng yêu cầu lấy 50.0 kg
            BatchMergeInputItem(batch_id="BATCH-LOW-STOCK", take_quantity=50.0),
        ]

        result = validate_batch_merge_input(
            items=items,
            batches_map=self.mock_db_batches,
            current_org_id=self.current_org_id,
        )

        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.errors), 1)

        error = result.errors[0]
        self.assertEqual(error.code, MergeValidationErrorCode.EXCEEDS_REMAINING_QUANTITY)
        self.assertEqual(error.violating_batch_id, "BATCH-LOW-STOCK")
        self.assertIn("BATCH-LOW-STOCK", error.message)
        self.assertIn("50.0", error.message)
        self.assertIn("15.0", error.message)

        # Kiểm tra khi bật raise_exception
        with self.assertRaises(InsufficientQuantityError) as ctx:
            validate_batch_merge_input(
                items=items,
                batches_map=self.mock_db_batches,
                current_org_id=self.current_org_id,
                raise_exception=True,
            )
        self.assertEqual(ctx.exception.violating_batch_id, "BATCH-LOW-STOCK")

    # =========================================================================
    # 5. Các ca biên mở rộng (Edge Cases)
    # =========================================================================
    def test_reject_multiple_violations_aggregates_all_violating_batches(self) -> None:
        """Kiểm tra gom đồng thời nhiều lỗi vi phạm và trả danh sách các mã lô vi phạm."""
        items = [
            BatchMergeInputItem(batch_id="BATCH-001", take_quantity=20.0),
            BatchMergeInputItem(batch_id="BATCH-DIFF-PROD", take_quantity=10.0),  # Vi phạm Ca 1
            BatchMergeInputItem(batch_id="BATCH-OTHER-ORG", take_quantity=10.0),  # Vi phạm Ca 2
            BatchMergeInputItem(batch_id="BATCH-LOW-STOCK", take_quantity=100.0),  # Vi phạm Ca 3
        ]

        result = validate_batch_merge_input(
            items=items,
            batches_map=self.mock_db_batches,
            current_org_id=self.current_org_id,
        )

        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.errors), 3)

        violating_ids = result.violating_batch_ids
        self.assertIn("BATCH-DIFF-PROD", violating_ids)
        self.assertIn("BATCH-OTHER-ORG", violating_ids)
        self.assertIn("BATCH-LOW-STOCK", violating_ids)

    def test_reject_fewer_than_two_batches(self) -> None:
        """Kiểm tra danh sách gộp ít hơn 2 lô."""
        items = [BatchMergeInputItem(batch_id="BATCH-001", take_quantity=10.0)]
        result = validate_batch_merge_input(
            items=items,
            batches_map=self.mock_db_batches,
            current_org_id=self.current_org_id,
        )
        self.assertFalse(result.is_valid)
        self.assertEqual(result.errors[0].code, MergeValidationErrorCode.INSUFFICIENT_BATCH_COUNT)

    def test_reject_duplicate_batch_in_input(self) -> None:
        """Kiểm tra trùng mã lô trong danh sách gộp."""
        items = [
            BatchMergeInputItem(batch_id="BATCH-001", take_quantity=10.0),
            BatchMergeInputItem(batch_id="BATCH-001", take_quantity=15.0),
        ]
        result = validate_batch_merge_input(
            items=items,
            batches_map=self.mock_db_batches,
            current_org_id=self.current_org_id,
        )
        self.assertFalse(result.is_valid)
        self.assertEqual(result.errors[0].code, MergeValidationErrorCode.DUPLICATE_SOURCE_BATCH)
        self.assertEqual(result.errors[0].violating_batch_id, "BATCH-001")

    def test_reject_non_existent_batch(self) -> None:
        """Kiểm tra lô không tồn tại trong hệ thống."""
        items = [
            BatchMergeInputItem(batch_id="BATCH-001", take_quantity=10.0),
            BatchMergeInputItem(batch_id="BATCH-UNKNOWN-404", take_quantity=15.0),
        ]
        result = validate_batch_merge_input(
            items=items,
            batches_map=self.mock_db_batches,
            current_org_id=self.current_org_id,
        )
        self.assertFalse(result.is_valid)
        self.assertEqual(result.errors[0].code, MergeValidationErrorCode.BATCH_NOT_FOUND)
        self.assertEqual(result.errors[0].violating_batch_id, "BATCH-UNKNOWN-404")


if __name__ == "__main__":
    unittest.main()
