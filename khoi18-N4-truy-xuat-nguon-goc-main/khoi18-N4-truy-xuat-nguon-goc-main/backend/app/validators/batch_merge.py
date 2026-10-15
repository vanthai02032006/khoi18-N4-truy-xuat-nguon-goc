"""Lớp kiểm tra đầu vào cho hàm gộp lô nông sản (Batch Merge Input Validator).

Đáp ứng nghiệp vụ theo chuẩn T-21 / T-44 (SCRUM-60):
1. Các lô phải cùng `product_id`.
2. Các lô phải cùng do tổ chức hiện tại nắm giữ (`holder_org_id == current_org_id`).
3. Khối lượng lấy phải dương (> 0) và không vượt quá số lượng còn lại (`take_quantity <= remaining_quantity`).

Mọi trường hợp vi phạm đều bị từ chối và thông báo NÊU RÕ MÃ LÔ VI PHẠM để người dùng
dễ dàng nhận biết và loại bỏ lô không hợp lệ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MergeValidationErrorCode(str, Enum):
    """Mã định danh loại lỗi kiểm tra đầu vào khi gộp lô."""

    DIFFERENT_PRODUCT = "DIFFERENT_PRODUCT"
    NOT_HELD_BY_ORG = "NOT_HELD_BY_ORG"
    INVALID_QUANTITY_NON_POSITIVE = "INVALID_QUANTITY_NON_POSITIVE"
    EXCEEDS_REMAINING_QUANTITY = "EXCEEDS_REMAINING_QUANTITY"
    BATCH_NOT_FOUND = "BATCH_NOT_FOUND"
    DUPLICATE_SOURCE_BATCH = "DUPLICATE_SOURCE_BATCH"
    INSUFFICIENT_BATCH_COUNT = "INSUFFICIENT_BATCH_COUNT"


@dataclass(frozen=True)
class BatchRecord:
    """Thông tin lô nông sản hiện có trong cơ sở dữ liệu / hệ thống.

    Attributes:
        batch_id: Mã định danh duy nhất của lô (ví dụ: 1 hoặc "BATCH-001").
        product_id: Mã sản phẩm của lô (ví dụ: "XOAI-CAT-CHU", 10).
        holder_org_id: Mã tổ chức đang nắm giữ lô hiện tại (ví dụ: "ORG-01", 1).
        remaining_quantity: Khối lượng còn lại khả dụng của lô (kg).
        product_name: Tên hiển thị của sản phẩm (tuỳ chọn).
    """

    batch_id: str | int
    product_id: str | int
    holder_org_id: str | int
    remaining_quantity: float
    product_name: str | None = None


@dataclass(frozen=True)
class BatchMergeInputItem:
    """Dữ liệu yêu cầu lấy từ một lô nguồn để gộp.

    Attributes:
        batch_id: Mã định danh lô cần lấy.
        take_quantity: Khối lượng cần trích xuất từ lô này để gộp (kg).
    """

    batch_id: str | int
    take_quantity: float


@dataclass
class ValidationErrorItem:
    """Chi tiết một vi phạm phát hiện trong quá trình kiểm tra.

    Attributes:
        code: Mã định danh lỗi (MergeValidationErrorCode).
        violating_batch_id: Mã lô vi phạm (đáp ứng tiêu chí DoD/AC).
        message: Thông báo lỗi chi tiết, thân thiện với người dùng.
        details: Thông tin bổ sung phục vụ debug / audit log.
    """

    code: MergeValidationErrorCode
    violating_batch_id: str | int | None
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationResult:
    """Kết quả tổng thể của quá trình kiểm tra đầu vào."""

    is_valid: bool
    errors: list[ValidationErrorItem] = field(default_factory=list)

    @property
    def violating_batch_ids(self) -> list[str | int]:
        """Danh sách các mã lô vi phạm (loại bỏ trùng lặp)."""
        seen: set[str | int] = set()
        result: list[str | int] = []
        for error in self.errors:
            if error.violating_batch_id is not None and error.violating_batch_id not in seen:
                seen.add(error.violating_batch_id)
                result.append(error.violating_batch_id)
        return result

    @property
    def error_messages(self) -> list[str]:
        """Danh sách các thông báo lỗi."""
        return [err.message for err in self.errors]


# ------------------------------------------------------------- Exceptions ---
class BatchMergeValidationError(Exception):
    """Exception cơ sở cho các lỗi kiểm tra đầu vào khi gộp lô."""

    def __init__(
        self,
        message: str,
        violating_batch_id: str | int | None = None,
        code: MergeValidationErrorCode = MergeValidationErrorCode.INSUFFICIENT_BATCH_COUNT,
        details: dict[str, Any] | None = None,
        all_errors: list[ValidationErrorItem] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.violating_batch_id = violating_batch_id
        self.code = code
        self.details = details or {}
        self.all_errors = all_errors or [
            ValidationErrorItem(
                code=code,
                violating_batch_id=violating_batch_id,
                message=message,
                details=self.details,
            )
        ]


class DifferentProductError(BatchMergeValidationError):
    """Lỗi: Lô có mã sản phẩm (product_id) không khớp với các lô khác trong danh sách gộp."""

    def __init__(
        self,
        violating_batch_id: str | int,
        actual_product_id: str | int,
        expected_product_id: str | int,
    ) -> None:
        message = (
            f"Từ chối gộp: Lô vi phạm '{violating_batch_id}' có sản phẩm "
            f"'{actual_product_id}' không đồng nhất với sản phẩm '{expected_product_id}' của lô trước đó."
        )
        super().__init__(
            message=message,
            violating_batch_id=violating_batch_id,
            code=MergeValidationErrorCode.DIFFERENT_PRODUCT,
            details={
                "violating_batch_id": violating_batch_id,
                "actual_product_id": actual_product_id,
                "expected_product_id": expected_product_id,
            },
        )


class OrgMismatchError(BatchMergeValidationError):
    """Lỗi: Lô không do tổ chức hiện tại nắm giữ."""

    def __init__(
        self,
        violating_batch_id: str | int,
        batch_org_id: str | int,
        current_org_id: str | int,
    ) -> None:
        message = (
            f"Từ chối gộp: Lô vi phạm '{violating_batch_id}' không do tổ chức hiện tại "
            f"('{current_org_id}') nắm giữ (đang thuộc tổ chức '{batch_org_id}')."
        )
        super().__init__(
            message=message,
            violating_batch_id=violating_batch_id,
            code=MergeValidationErrorCode.NOT_HELD_BY_ORG,
            details={
                "violating_batch_id": violating_batch_id,
                "batch_org_id": batch_org_id,
                "current_org_id": current_org_id,
            },
        )


class InvalidQuantityError(BatchMergeValidationError):
    """Lỗi: Khối lượng lấy không dương (<= 0)."""

    def __init__(
        self,
        violating_batch_id: str | int,
        take_quantity: float,
    ) -> None:
        message = (
            f"Từ chối gộp: Khối lượng lấy từ lô vi phạm '{violating_batch_id}' "
            f"phải lớn hơn 0 (khối lượng yêu cầu: {take_quantity} kg)."
        )
        super().__init__(
            message=message,
            violating_batch_id=violating_batch_id,
            code=MergeValidationErrorCode.INVALID_QUANTITY_NON_POSITIVE,
            details={
                "violating_batch_id": violating_batch_id,
                "take_quantity": take_quantity,
            },
        )


class InsufficientQuantityError(BatchMergeValidationError):
    """Lỗi: Khối lượng lấy vượt quá số lượng còn lại khả dụng."""

    def __init__(
        self,
        violating_batch_id: str | int,
        take_quantity: float,
        remaining_quantity: float,
    ) -> None:
        message = (
            f"Từ chối gộp: Khối lượng lấy ({take_quantity} kg) từ lô vi phạm '{violating_batch_id}' "
            f"vượt quá số lượng còn lại khả dụng ({remaining_quantity} kg)."
        )
        super().__init__(
            message=message,
            violating_batch_id=violating_batch_id,
            code=MergeValidationErrorCode.EXCEEDS_REMAINING_QUANTITY,
            details={
                "violating_batch_id": violating_batch_id,
                "take_quantity": take_quantity,
                "remaining_quantity": remaining_quantity,
            },
        )


# ----------------------------------------------------------- Core Validator ---
def validate_batch_merge_input(
    items: list[BatchMergeInputItem],
    batches_map: dict[str | int, BatchRecord],
    current_org_id: str | int,
    raise_exception: bool = False,
) -> ValidationResult:
    """Kiểm tra tính hợp lệ của danh sách lô nguồn trong yêu cầu gộp lô.

    Quy tắc kiểm tra:
    1. Danh sách gộp phải có ít nhất 2 lô và không bị trùng mã lô.
    2. Tất cả các lô phải tồn tại trong hệ thống.
    3. **Ca 1 (Cùng product_id)**: Tất cả các lô phải có cùng mã `product_id`.
    4. **Ca 2 (Cùng tổ chức)**: Tất cả các lô phải do tổ chức hiện tại (`current_org_id`) nắm giữ.
    5. **Ca 3 (Khối lượng hợp lệ)**: Khối lượng lấy > 0 và <= `remaining_quantity` của từng lô.

    Mọi lỗi đều nêu rõ `violating_batch_id` để client/người dùng biết chính xác lô cần xử lý.

    Args:
        items: Danh sách các lô nguồn và khối lượng yêu cầu lấy.
        batches_map: Bản đồ tra cứu thông tin các lô từ database (`{batch_id: BatchRecord}`).
        current_org_id: Mã tổ chức của người dùng hiện tại đang thực hiện thao tác.
        raise_exception: Nếu `True`, raise `BatchMergeValidationError` ngay khi gặp lỗi đầu tiên.
            Nếu `False`, gom toàn bộ lỗi vào `ValidationResult`.

    Returns:
        ValidationResult: Kết quả kiểm tra chứa `is_valid` và danh sách chi tiết các lỗi `errors`.

    Raises:
        BatchMergeValidationError: Nếu `raise_exception=True` và có lỗi vi phạm.
    """
    errors: list[ValidationErrorItem] = []

    # Kiểm tra số lượng lô tối thiểu
    if not items or len(items) < 2:
        err = ValidationErrorItem(
            code=MergeValidationErrorCode.INSUFFICIENT_BATCH_COUNT,
            violating_batch_id=None,
            message="Yêu cầu gộp lô phải chứa ít nhất 2 lô nguồn.",
            details={"provided_count": len(items) if items else 0},
        )
        if raise_exception:
            raise BatchMergeValidationError(
                message=err.message,
                violating_batch_id=None,
                code=err.code,
                details=err.details,
            )
        errors.append(err)
        return ValidationResult(is_valid=False, errors=errors)

    # Kiểm tra trùng lặp mã lô trong request
    seen_ids: set[str | int] = set()
    for item in items:
        if item.batch_id in seen_ids:
            err = ValidationErrorItem(
                code=MergeValidationErrorCode.DUPLICATE_SOURCE_BATCH,
                violating_batch_id=item.batch_id,
                message=f"Lô vi phạm '{item.batch_id}' bị khai báo trùng lặp trong yêu cầu gộp.",
                details={"batch_id": item.batch_id},
            )
            if raise_exception:
                raise BatchMergeValidationError(
                    message=err.message,
                    violating_batch_id=item.batch_id,
                    code=err.code,
                    details=err.details,
                )
            errors.append(err)
        seen_ids.add(item.batch_id)

    # Kiểm tra tồn tại trong database & thu thập thông tin lô hợp lệ
    expected_product_id: str | int | None = None
    first_valid_batch_id: str | int | None = None

    for item in items:
        batch_record = batches_map.get(item.batch_id)

        # 0. Kiểm tra tồn tại
        if batch_record is None:
            err = ValidationErrorItem(
                code=MergeValidationErrorCode.BATCH_NOT_FOUND,
                violating_batch_id=item.batch_id,
                message=f"Không tìm thấy thông tin lô vi phạm '{item.batch_id}' trong hệ thống.",
                details={"batch_id": item.batch_id},
            )
            if raise_exception:
                raise BatchMergeValidationError(
                    message=err.message,
                    violating_batch_id=item.batch_id,
                    code=err.code,
                    details=err.details,
                )
            errors.append(err)
            continue

        # 1. Ca 1: Kiểm tra cùng product_id
        if expected_product_id is None:
            expected_product_id = batch_record.product_id
            first_valid_batch_id = batch_record.batch_id
        elif batch_record.product_id != expected_product_id:
            exc = DifferentProductError(
                violating_batch_id=item.batch_id,
                actual_product_id=batch_record.product_id,
                expected_product_id=expected_product_id,
            )
            if raise_exception:
                raise exc
            errors.append(
                ValidationErrorItem(
                    code=exc.code,
                    violating_batch_id=item.batch_id,
                    message=exc.message,
                    details=exc.details,
                )
            )

        # 2. Ca 2: Kiểm tra cùng tổ chức hiện tại nắm giữ
        if str(batch_record.holder_org_id) != str(current_org_id):
            exc = OrgMismatchError(
                violating_batch_id=item.batch_id,
                batch_org_id=batch_record.holder_org_id,
                current_org_id=current_org_id,
            )
            if raise_exception:
                raise exc
            errors.append(
                ValidationErrorItem(
                    code=exc.code,
                    violating_batch_id=item.batch_id,
                    message=exc.message,
                    details=exc.details,
                )
            )

        # 3. Ca 3: Kiểm tra khối lượng lấy dương (> 0) và không vượt quá số còn lại
        if item.take_quantity <= 0:
            exc = InvalidQuantityError(
                violating_batch_id=item.batch_id,
                take_quantity=item.take_quantity,
            )
            if raise_exception:
                raise exc
            errors.append(
                ValidationErrorItem(
                    code=exc.code,
                    violating_batch_id=item.batch_id,
                    message=exc.message,
                    details=exc.details,
                )
            )
        elif item.take_quantity > batch_record.remaining_quantity:
            exc = InsufficientQuantityError(
                violating_batch_id=item.batch_id,
                take_quantity=item.take_quantity,
                remaining_quantity=batch_record.remaining_quantity,
            )
            if raise_exception:
                raise exc
            errors.append(
                ValidationErrorItem(
                    code=exc.code,
                    violating_batch_id=item.batch_id,
                    message=exc.message,
                    details=exc.details,
                )
            )

    is_valid = len(errors) == 0
    return ValidationResult(is_valid=is_valid, errors=errors)
