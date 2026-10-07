"""Router quản lý cấu hình ngưỡng nhiệt độ và độ trễ chuỗi lạnh cho từng loại sản phẩm.

Đáp ứng yêu cầu:
1. Đặt ngưỡng trên, ngưỡng dưới và độ trễ riêng cho từng loại sản phẩm
   (Rau lá và thịt đông lạnh không dùng chung một con số).
2. Chuyến chở nhiều sản phẩm áp ngưỡng chặt nhất:
   - temp_min_effective = max(temp_min)
   - temp_max_effective = min(temp_max)
   - delay_effective = min(delay_minutes)
3. Đổi cấu hình không tính lại vi phạm đã ghi:
   - Các vi phạm đã ghi lưu snapshot các ngưỡng tại thời điểm xảy ra.
   - Khi admin sửa ngưỡng, danh sách vi phạm cũ vẫn giữ nguyên.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ColdChainThreshold, ColdChainViolation, User
from app.schemas import (
    ColdChainThresholdCreate,
    ColdChainThresholdResponse,
    ColdChainThresholdUpdate,
    ColdChainViolationResponse,
    MultiProductEffectiveThresholdRequest,
    MultiProductEffectiveThresholdResponse,
    TelemetryCheckRequest,
    TelemetryCheckResponse,
)
from app.security import get_current_user, require_admin

router = APIRouter(
    prefix="/cold-chain",
    tags=["Giám Sát Chuỗi Lạnh (Cold Chain & Telemetry)"],
)


@router.get(
    "/thresholds",
    response_model=list[ColdChainThresholdResponse],
    summary="Lấy danh sách cấu hình ngưỡng cho từng loại sản phẩm",
)
def list_thresholds(
    db: Session = Depends(get_db),
) -> list[ColdChainThreshold]:
    """Danh sách cấu hình ngưỡng nhiệt độ và độ trễ cho từng loại sản phẩm."""
    return list(db.scalars(select(ColdChainThreshold).order_by(ColdChainThreshold.id.asc())).all())


@router.post(
    "/thresholds",
    response_model=ColdChainThresholdResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Thêm cấu hình ngưỡng và độ trễ cho loại sản phẩm (Chỉ Admin)",
)
def create_threshold(
    data: ColdChainThresholdCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ColdChainThreshold:
    """Tạo cấu hình ngưỡng mới. Chỉ quản trị viên hệ thống có quyền cấu hình."""
    clean_type = data.product_type.strip()
    exists = db.scalar(select(ColdChainThreshold).where(ColdChainThreshold.product_type == clean_type))
    if exists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Loại sản phẩm '{clean_type}' đã được thiết lập cấu hình ngưỡng.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    item = ColdChainThreshold(
        product_type=clean_type,
        temp_min=data.temp_min,
        temp_max=data.temp_max,
        delay_minutes=data.delay_minutes,
        description=data.description or "",
        created_at=now_iso,
        updated_at=now_iso,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put(
    "/thresholds/{threshold_id}",
    response_model=ColdChainThresholdResponse,
    summary="Cập nhật ngưỡng và độ trễ của loại sản phẩm (Chỉ Admin)",
)
def update_threshold(
    threshold_id: int,
    data: ColdChainThresholdUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ColdChainThreshold:
    """Cập nhật cấu hình ngưỡng.

    LƯU Ý QUAN TRỌNG:
    Việc cập nhật ngưỡng chỉ áp dụng cho các hành trình/chuyến xe và dữ liệu kiểm tra
    về sau. Các vi phạm đã được ghi nhận trước đó KHÔNG bị tính lại (đảm bảo tính bất biến).
    """
    item = db.get(ColdChainThreshold, threshold_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy cấu hình ngưỡng với ID #{threshold_id}.",
        )

    new_min = data.temp_min if data.temp_min is not None else item.temp_min
    new_max = data.temp_max if data.temp_max is not None else item.temp_max

    if new_max <= new_min:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Ngưỡng trên (temp_max) phải lớn hơn ngưỡng dưới (temp_min).",
        )

    if data.temp_min is not None:
        item.temp_min = data.temp_min
    if data.temp_max is not None:
        item.temp_max = data.temp_max
    if data.delay_minutes is not None:
        item.delay_minutes = data.delay_minutes
    if data.description is not None:
        item.description = data.description

    item.updated_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    db.refresh(item)
    return item


@router.delete(
    "/thresholds/{threshold_id}",
    status_code=status.HTTP_200_OK,
    summary="Xoá cấu hình ngưỡng của loại sản phẩm (Chỉ Admin)",
)
def delete_threshold(
    threshold_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    item = db.get(ColdChainThreshold, threshold_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy cấu hình ngưỡng với ID #{threshold_id}.",
        )
    p_type = item.product_type
    db.delete(item)
    db.commit()
    return {"message": f"Đã xoá cấu hình ngưỡng cho loại sản phẩm '{p_type}'."}


@router.post(
    "/effective-threshold",
    response_model=MultiProductEffectiveThresholdResponse,
    summary="Tính toán ngưỡng áp dụng chặt nhất cho chuyến hàng chở nhiều sản phẩm",
)
def calculate_effective_threshold(
    payload: MultiProductEffectiveThresholdRequest,
    db: Session = Depends(get_db),
) -> MultiProductEffectiveThresholdResponse:
    """Tính toán ngưỡng hiệu dụng cho chuyến xe chở hỗn hợp nhiều loại sản phẩm.

    Quy tắc: Chuyến chở nhiều sản phẩm áp ngưỡng chặt nhất:
    - temp_min_effective = max(temp_min các sản phẩm)
    - temp_max_effective = min(temp_max các sản phẩm)
    - delay_effective = min(delay_minutes các sản phẩm)
    """
    clean_types = [t.strip() for t in payload.product_types if t.strip()]
    if not clean_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Danh sách loại sản phẩm không được để trống.",
        )

    # Tìm các cấu hình đã lưu
    stmt = select(ColdChainThreshold).where(ColdChainThreshold.product_type.in_(clean_types))
    configs = list(db.scalars(stmt).all())
    found_types = {c.product_type for c in configs}

    missing = [t for t in clean_types if t not in found_types]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chưa có cấu hình ngưỡng cho các loại sản phẩm: {', '.join(missing)}. Vui lòng thiết lập trước.",
        )

    # Áp dụng quy tắc chặt nhất:
    # temp_min_effective = max(c.temp_min)
    # temp_max_effective = min(c.temp_max)
    # delay_effective = min(c.delay_minutes)
    effective_min = max(c.temp_min for c in configs)
    effective_max = min(c.temp_max for c in configs)
    effective_delay = min(c.delay_minutes for c in configs)

    is_compatible = effective_min <= effective_max
    warning = None
    if not is_compatible:
        warning = (
            f"CẢNH BÁO XUNG ĐỘT BẢO QUẢN: Không nên vận chuyển chung! "
            f"Dải nhiệt độ giao nhau rỗng ([{effective_min}°C, {effective_max}°C]). "
            f"Ví dụ Rau lá (2°C đến 8°C) và Thịt đông lạnh (-22°C đến -18°C) không thể chở chung cùng một khoang nhiệt độ."
        )

    summary = (
        f"Chuyến chở {len(configs)} loại ({', '.join(clean_types)}): "
        f"Áp dụng ngưỡng chặt nhất [{effective_min}°C, {effective_max}°C], độ trễ cảnh báo {effective_delay} phút."
    )

    return MultiProductEffectiveThresholdResponse(
        product_types=clean_types,
        effective_temp_min=effective_min,
        effective_temp_max=effective_max,
        effective_delay_minutes=effective_delay,
        strictest_rule_summary=summary,
        is_compatible=is_compatible,
        compatibility_warning=warning,
    )


@router.post(
    "/check-telemetry",
    response_model=TelemetryCheckResponse,
    summary="Kiểm tra telemetry nhiệt độ chuyến xe và ghi nhận vi phạm nếu vượt ngưỡng",
)
def check_telemetry(
    data: TelemetryCheckRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TelemetryCheckResponse:
    """Đánh giá bản ghi nhiệt độ theo ngưỡng hiệu dụng chặt nhất của các sản phẩm.

    Nếu vi phạm (vượt quá ngưỡng trên hoặc dưới và thời gian duy trì > độ trễ cho phép),
    hệ thống sẽ lưu bản ghi ColdChainViolation với snapshot ngưỡng tại thời điểm đó.
    """
    clean_types = [t.strip() for t in data.product_types if t.strip()]
    stmt = select(ColdChainThreshold).where(ColdChainThreshold.product_type.in_(clean_types))
    configs = list(db.scalars(stmt).all())
    found_types = {c.product_type for c in configs}

    missing = [t for t in clean_types if t not in found_types]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chưa có cấu hình ngưỡng cho loại sản phẩm: {', '.join(missing)}.",
        )

    # Tính ngưỡng chặt nhất tại thời điểm hiện tại
    eff_min = max(c.temp_min for c in configs)
    eff_max = min(c.temp_max for c in configs)
    eff_delay = min(c.delay_minutes for c in configs)

    # Đánh giá vi phạm
    is_violated = False
    reason = None
    violation_id = None

    temp = data.recorded_temperature
    duration = data.duration_minutes

    if temp > eff_max and duration >= eff_delay:
        is_violated = True
        reason = f"Nhiệt độ {temp}°C vượt quá ngưỡng trên ({eff_max}°C) trong {duration} phút (vượt độ trễ {eff_delay} phút)."
    elif temp < eff_min and duration >= eff_delay:
        is_violated = True
        reason = f"Nhiệt độ {temp}°C thấp hơn ngưỡng dưới ({eff_min}°C) trong {duration} phút (vượt độ trễ {eff_delay} phút)."

    if is_violated:
        now_iso = datetime.now(timezone.utc).isoformat()
        violation = ColdChainViolation(
            shipment_code=data.shipment_code,
            product_types_json=json.dumps(clean_types, ensure_ascii=False),
            recorded_temperature=temp,
            duration_minutes=duration,
            applied_temp_min=eff_min,
            applied_temp_max=eff_max,
            applied_delay_minutes=eff_delay,
            violation_reason=reason,
            timestamp=now_iso,
            location=data.location or "Xe lạnh chuyên dụng",
        )
        db.add(violation)
        db.commit()
        db.refresh(violation)
        violation_id = violation.id

    return TelemetryCheckResponse(
        shipment_code=data.shipment_code,
        product_types=clean_types,
        recorded_temperature=temp,
        duration_minutes=duration,
        applied_temp_min=eff_min,
        applied_temp_max=eff_max,
        applied_delay_minutes=eff_delay,
        is_violated=is_violated,
        violation_reason=reason,
        violation_id=violation_id,
    )


@router.get(
    "/violations",
    response_model=list[ColdChainViolationResponse],
    summary="Xem danh sách các vi phạm chuỗi lạnh đã ghi nhận",
)
def list_violations(
    shipment_code: Optional[str] = Query(None, description="Lọc theo mã chuyến xe / mã vận đơn"),
    db: Session = Depends(get_db),
) -> list[ColdChainViolationResponse]:
    """Danh sách vi phạm chuỗi lạnh.

    Các vi phạm này mang tính bất biến: kể cả khi người quản trị cập nhật lại cấu hình ngưỡng
    sau này, các vi phạm này vẫn giữ nguyên các giá trị applied_temp_min, applied_temp_max,
    applied_delay_minutes và lý do đã ghi nhận.
    """
    stmt = select(ColdChainViolation).order_by(ColdChainViolation.id.desc())
    if shipment_code:
        stmt = stmt.where(ColdChainViolation.shipment_code == shipment_code.strip())

    records = list(db.scalars(stmt).all())
    result = []
    for r in records:
        try:
            ptypes = json.loads(r.product_types_json)
        except Exception:
            ptypes = [r.product_types_json]
        result.append(
            ColdChainViolationResponse(
                id=r.id,
                shipment_code=r.shipment_code,
                product_types_json=r.product_types_json,
                product_types=ptypes,
                recorded_temperature=r.recorded_temperature,
                duration_minutes=r.duration_minutes,
                applied_temp_min=r.applied_temp_min,
                applied_temp_max=r.applied_temp_max,
                applied_delay_minutes=r.applied_delay_minutes,
                violation_reason=r.violation_reason,
                timestamp=r.timestamp,
                location=r.location,
            )
        )
    return result
