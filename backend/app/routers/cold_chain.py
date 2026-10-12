"""Router API giám sát nhiệt độ chuỗi lạnh & tổng hợp theo giờ (SCRUM-126)."""

from __future__ import annotations

from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TemperatureThresholdConfig, User
from app.schemas import (
    ColdChainAggregateRunResponse,
    HourlyTemperatureSummaryResponse,
    TemperatureReadingCreate,
    TemperatureReadingResponse,
    TemperatureThresholdResponse,
)
from app.security import require_admin, require_farmer
from app.services.cold_chain_service import (
    aggregate_old_temperatures_job,
    list_raw_violations,
    list_temperature_summaries,
    record_temperature,
)

router = APIRouter(
    prefix="/cold-chain",
    tags=["Cold Chain Telemetry & Aggregation (SCRUM-126)"],
)


@router.post(
    "/readings",
    response_model=TemperatureReadingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ghi nhận số đo nhiệt độ cảm biến chuỗi lạnh",
    description="Lưu số đo thô từ cảm biến IoT và tự động đối chiếu với ngưỡng bảo quản nông sản.",
)
def add_temperature_reading(
    payload: TemperatureReadingCreate,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> TemperatureReadingResponse:
    _ = current_user
    reading = record_temperature(
        db,
        sensor_id=payload.sensor_id,
        temperature=payload.temperature,
        batch_id=payload.batch_id,
        recorded_at=payload.recorded_at,
    )
    return reading


@router.get(
    "/readings/violations",
    response_model=list[TemperatureReadingResponse],
    status_code=status.HTTP_200_OK,
    summary="Danh sách các số đo vi phạm nhiệt độ được bảo lưu nguyên vẹn",
    description=(
        "Trả về toàn bộ số đo thô đã vi phạm ngưỡng bảo quản lạnh. "
        "Các số đo này được bảo vệ 100%, không bị xoá bởi job tổng hợp hằng đêm "
        "để phục vụ điều tra sự cố chuỗi lạnh."
    ),
)
def get_violations_list(
    limit: int = 100,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> list[TemperatureReadingResponse]:
    _ = current_user
    return list_raw_violations(db, limit=limit)


@router.get(
    "/summaries",
    response_model=list[HourlyTemperatureSummaryResponse],
    status_code=status.HTTP_200_OK,
    summary="Xem dữ liệu nhiệt độ tổng hợp theo từng giờ (MIN, MAX, AVG)",
)
def get_summaries_list(
    sensor_id: str | None = None,
    batch_id: int | None = None,
    limit: int = 100,
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> list[HourlyTemperatureSummaryResponse]:
    _ = current_user
    return list_temperature_summaries(db, sensor_id=sensor_id, batch_id=batch_id, limit=limit)


@router.get(
    "/thresholds",
    response_model=list[TemperatureThresholdResponse],
    status_code=status.HTTP_200_OK,
    summary="Danh sách ngưỡng nhiệt độ bảo quản chuẩn theo từng nông sản",
)
def get_thresholds_list(
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> list[TemperatureThresholdResponse]:
    _ = current_user
    return list(db.scalars(select(TemperatureThresholdConfig)).all())


@router.post(
    "/aggregate",
    response_model=ColdChainAggregateRunResponse,
    status_code=status.HTTP_200_OK,
    summary="Kích hoạt job tổng hợp số đo cũ hơn 30 ngày (chỉ admin)",
    description=(
        "Gom các số đo nhiệt độ thô cũ hơn 30 ngày thành MIN, MAX, AVG theo từng giờ. "
        "Chỉ xoá số đo an toàn đã tổng hợp; BẮT BUỘC giữ lại nguyên vẹn các số đo thô vi phạm ngưỡng."
    ),
)
def trigger_aggregation_endpoint(
    days_threshold: int = Query(default=30, ge=1, le=365, description="Số ngày tính từ hiện tại để tổng hợp"),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ColdChainAggregateRunResponse:
    _ = current_user
    cutoff = datetime.now() - timedelta(days=days_threshold)
    result = aggregate_old_temperatures_job(db, cutoff_time=cutoff, days_threshold=days_threshold)
    return ColdChainAggregateRunResponse(
        message=result["message"],
        cutoff_time=result["cutoff_time"],
        aggregated_buckets=result["aggregated_buckets"],
        purged_raw_readings=result["purged_raw_readings"],
        retained_violation_readings=result["retained_violation_readings"],
    )
