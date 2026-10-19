"""Router tiếp nhận dữ liệu cảm biến IoT chuỗi lạnh (Telemetry).

Hỗ trợ:
- POST /telemetry/temperature: Tiếp nhận dữ liệu đo nhiệt độ từ thiết bị IoT.
  Tự động lọc trùng lặp & dữ liệu gửi trễ/cũ hơn mốc thời gian gần nhất (trả về status 'ignored').
  Ghi nhận dữ liệu mới hợp lệ (trả về status 'recorded').
  Bảo đảm cách ly Multi-tenant giữa các tổ chức.
- GET /telemetry/temperature: Xem lịch sử nhiệt độ theo chuyến xe hoặc thùng lạnh.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TemperatureLog, Trip, User
from app.schemas import TelemetryTemperatureCreate, TelemetryTemperatureResponse
from app.security import get_current_user
from app.tenant import require_tenant_context

router = APIRouter(
    prefix="/telemetry",
    tags=["Vận Chuyển & Giám Sát Chuỗi Lạnh"],
)


@router.post(
    "/temperature",
    response_model=TelemetryTemperatureResponse,
    status_code=status.HTTP_200_OK,
    summary="Tiếp nhận telemetry nhiệt độ từ cảm biến IoT",
)
def record_temperature(
    data: TelemetryTemperatureCreate,
    current_user: User = Depends(get_current_user),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Ghi nhận nhiệt độ từ cảm biến IoT:

    1. Kiểm tra quyền Multi-tenant: Tổ chức không được gửi telemetry cho chuyến xe của tổ chức khác.
    2. Kiểm tra mốc thời gian: Nếu trùng hoặc cũ hơn mốc thời gian đã ghi nhận gần nhất -> status='ignored'.
    3. Nếu mốc thời gian mới hơn -> ghi nhận vào database và trả về status='recorded'.
    """
    trip: Optional[Trip] = None
    if data.trip_id:
        trip = db.get(Trip, data.trip_id)
        if not trip:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy chuyến xe có ID #{data.trip_id}.",
            )
    elif data.trip_code:
        trip = db.scalar(select(Trip).where(Trip.code == data.trip_code.strip()))

    # Kiểm tra phân quyền Multi-tenant
    if trip and current_org and current_org != "HTX Nông Nghiệp Số 4" and current_user.role != "admin":
        if trip.organization != current_org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền gửi telemetry cho chuyến xe của tổ chức khác.",
            )

    # Chuẩn hoá timestamp sang naive UTC để lưu vào database & so sánh
    ts = data.timestamp
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)

    # Tìm mốc thời gian đo gần nhất của chuyến xe / thùng lạnh này
    stmt = select(TemperatureLog)
    if trip:
        stmt = stmt.where(TemperatureLog.trip_id == trip.id)
    elif data.container_code:
        stmt = stmt.where(TemperatureLog.container_code == data.container_code.strip())
    elif data.trip_code:
        stmt = stmt.where(TemperatureLog.trip_code == data.trip_code.strip())

    stmt = stmt.order_by(TemperatureLog.timestamp.desc()).limit(1)
    latest_log = db.scalar(stmt)

    # Kiểm tra trùng hoặc cũ hơn mốc đã có (Deduplication / Out-of-order check)
    if latest_log is not None:
        latest_ts = latest_log.timestamp
        if latest_ts.tzinfo is not None:
            latest_ts = latest_ts.astimezone(timezone.utc).replace(tzinfo=None)
        if ts <= latest_ts:
            return {
                "status": "ignored",
                "message": "Mốc thời gian gửi trùng hoặc cũ hơn dữ liệu đã ghi nhận trong hệ thống.",
                "log_id": None,
                "recorded_temperature": data.temperature,
                "timestamp": ts.isoformat(),
            }

    # Lưu bản ghi nhiệt độ mới
    log = TemperatureLog(
        trip_id=trip.id if trip else data.trip_id,
        trip_code=trip.code if trip else data.trip_code,
        container_code=trip.container_code if (trip and trip.container_code) else data.container_code,
        temperature=data.temperature,
        timestamp=ts,
        location=data.location,
        organization=trip.organization if trip else current_org,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    return {
        "status": "recorded",
        "message": "Ghi nhận nhiệt độ thành công.",
        "log_id": log.id,
        "recorded_temperature": log.temperature,
        "timestamp": log.timestamp.isoformat(),
    }


@router.get(
    "/temperature",
    summary="Xem lịch sử đo nhiệt độ",
)
def get_temperature_logs(
    trip_id: Optional[int] = Query(None, description="Lọc theo ID chuyến xe"),
    container_code: Optional[str] = Query(None, description="Lọc theo mã thùng lạnh"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Lấy danh sách các mốc nhiệt độ đã đo."""
    stmt = select(TemperatureLog).order_by(TemperatureLog.timestamp.asc())
    if trip_id:
        stmt = stmt.where(TemperatureLog.trip_id == trip_id)
    if container_code:
        stmt = stmt.where(TemperatureLog.container_code == container_code.strip())

    logs = list(db.scalars(stmt).all())
    return [
        {
            "id": l.id,
            "trip_id": l.trip_id,
            "trip_code": l.trip_code,
            "container_code": l.container_code,
            "temperature": l.temperature,
            "timestamp": l.timestamp.isoformat() if l.timestamp else None,
            "location": l.location,
            "organization": l.organization,
        }
        for l in logs
    ]
