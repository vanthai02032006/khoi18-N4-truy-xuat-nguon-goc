"""Router Giám sát Chuỗi Lạnh & Dữ liệu Cảm biến IoT (Cold Chain & Telemetry).

Phân hệ Cold Chain chuẩn S-42 (5 User Stories / 17 SP):
1. S-42.1: Nhận luồng dữ liệu cảm biến IoT, khử trùng lặp, lọc mốc thời gian cũ (POST /cold-chain/telemetry).
2. S-42.2: Quản lý vòng đời chuyến xe, tự động sinh sự kiện ON-BOARD / OFF-BOARD cho các lô hàng
   (POST /cold-chain/trips & PUT /cold-chain/trips/{code}/complete).
3. S-42.3: Lõi đánh giá vi phạm chuỗi lạnh theo cửa sổ 30 phút, debounce gap 5 phút và peak temperature.
4. S-42.4: Cảnh báo đỏ chuỗi lạnh khi bàn giao lô hàng (GET /cold-chain/batches/{batch_id}/handover-warning).
5. S-42.5: Báo cáo đánh giá hiệu suất nhà xe theo kỳ (GET /cold-chain/reports/carriers).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.cold_chain_engine import (
    deduplicate_and_sort_telemetry,
    evaluate_cold_chain_violations,
    parse_timestamp,
)
from app.database import get_db
from app.events import record_event
from app.models import (
    Batch,
    BatchEvent,
    ColdChainThreshold,
    ColdChainViolation,
    TelemetryReading,
    Trip,
    TripConsignment,
    TripViolation,
    User,
)
from app.schemas import (
    CarrierReportItem,
    CarrierReportResponse,
    ColdChainThresholdCreate,
    ColdChainThresholdResponse,
    ColdChainThresholdUpdate,
    ColdChainViolationResponse,
    HandoverWarningResponse,
    MultiProductEffectiveThresholdRequest,
    MultiProductEffectiveThresholdResponse,
    TelemetryBatchIngestRequest,
    TelemetryCheckRequest,
    TelemetryCheckResponse,
    TelemetryIngestResponse,
    TelemetryItemSchema,
    TripCompleteResponse,
    TripConsignmentResponse,
    TripCreateRequest,
    TripResponse,
    TripViolationDetail,
)
from app.security import get_current_user, require_admin, require_farmer

router = APIRouter(
    prefix="/cold-chain",
    tags=["Giám Sát Chuỗi Lạnh (Cold Chain & Telemetry)"],
)


# ==============================================================================
# S-42.1: TIẾP NHẬN DỮ LIỆU CẢM BIẾN IOT (TELEMETRY INGESTION)
# ==============================================================================
@router.post(
    "/telemetry",
    response_model=TelemetryIngestResponse,
    status_code=status.HTTP_200_OK,
    summary="Tiếp nhận dòng dữ liệu cảm biến IoT từ xe lạnh (S-42.1)",
)
def ingest_telemetry(
    payload: Union[TelemetryBatchIngestRequest, list[TelemetryItemSchema], TelemetryItemSchema],
    db: Session = Depends(get_db),
) -> TelemetryIngestResponse:
    """Tiếp nhận dữ liệu cảm biến IoT, xử lý triệt để:
    
    1. Chuẩn hoá gói tin gửi lên (hỗ trợ cả batch object, raw list hoặc single item).
    2. Lọc mốc thời gian cũ: loại bỏ các bản ghi có timestamp trước khi chuyến xe bắt đầu.
    3. Khử trùng lặp (Deduplication): loại bỏ các gói tin bị gửi lặp do mất sóng/gửi bù.
    4. Sắp xếp theo trình tự thời gian (Sort chronologically).
    5. Tự động kích hoạt Core Engine S-42 đánh giá vi phạm (cửa sổ 30 phút, debounce 5 phút, peak temp).
    """
    # 1. Trích xuất danh sách readings
    if isinstance(payload, TelemetryBatchIngestRequest):
        raw_items = payload.readings
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = [payload]

    received_count = len(raw_items)
    if received_count == 0:
        return TelemetryIngestResponse(
            received_count=0,
            accepted_count=0,
            filtered_count=0,
            violations_detected=0,
            message="Không có bản ghi telemetry nào được gửi lên.",
        )

    # 2. Gom nhóm readings theo trip_code
    grouped_by_trip: dict[str, list[TelemetryItemSchema]] = {}
    for item in raw_items:
        clean_code = item.trip_code.strip()
        grouped_by_trip.setdefault(clean_code, []).append(item)

    accepted_total = 0
    filtered_total = 0
    violations_detected_total = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for trip_code, items in grouped_by_trip.items():
        trip = db.scalar(
            select(Trip)
            .where(Trip.trip_code == trip_code)
            .options(
                selectinload(Trip.telemetry_readings),
                selectinload(Trip.violations),
            )
        )
        if not trip:
            # Nếu không tìm thấy chuyến xe tương ứng, tính vào filtered
            filtered_total += len(items)
            continue

        # Lọc dữ liệu cũ hơn thời điểm chuyến xe bắt đầu và khử trùng lặp nội bộ payload
        cleaned_payload = deduplicate_and_sort_telemetry(
            [it.model_dump() for it in items],
            min_timestamp=trip.started_at,
        )

        filtered_in_payload = len(items) - len(cleaned_payload)
        filtered_total += filtered_in_payload

        # Kiểm tra trùng lặp với các bản ghi đã lưu trong CSDL
        existing_keys = {
            (r.device_id, r.recorded_at)
            for r in trip.telemetry_readings
        }

        new_db_readings: list[TelemetryReading] = []
        for c in cleaned_payload:
            key = (c["device_id"], c["recorded_at"])
            if key in existing_keys:
                filtered_total += 1
                continue
            existing_keys.add(key)

            reading_row = TelemetryReading(
                trip_id=trip.id,
                device_id=c["device_id"],
                temperature=c["temperature"],
                humidity=c.get("humidity"),
                latitude=c.get("latitude"),
                longitude=c.get("longitude"),
                recorded_at=c["recorded_at"],
                received_at=now_iso,
            )
            db.add(reading_row)
            new_db_readings.append(reading_row)

        accepted_total += len(new_db_readings)

        # 3. Kích hoạt Core Engine S-42 trên toàn bộ dữ liệu telemetry của chuyến xe
        all_readings = list(trip.telemetry_readings) + new_db_readings
        detected_violations = evaluate_cold_chain_violations(
            readings=all_readings,
            temp_min=trip.temp_min_threshold,
            temp_max=trip.temp_max_threshold,
            breach_window_minutes=float(trip.max_allowed_breach_minutes),
            debounce_gap_minutes=5.0,
        )

        # Cập nhật vi phạm vào bảng trip_violations nếu chưa tồn tại
        existing_violations = {(v.started_at, v.violation_type) for v in trip.violations}
        for dv in detected_violations:
            v_key = (dv["started_at"], dv["violation_type"])
            if v_key not in existing_violations:
                new_v = TripViolation(
                    trip_id=trip.id,
                    violation_type=dv["violation_type"],
                    severity=dv["severity"],
                    started_at=dv["started_at"],
                    ended_at=dv["ended_at"],
                    duration_minutes=dv["duration_minutes"],
                    peak_temperature=dv["peak_temperature"],
                    threshold_temp=dv["threshold_temp"],
                    reason=dv["reason"],
                    created_at=now_iso,
                )
                db.add(new_v)
                existing_violations.add(v_key)
                violations_detected_total += 1

    db.commit()

    return TelemetryIngestResponse(
        received_count=received_count,
        accepted_count=accepted_total,
        filtered_count=filtered_total,
        violations_detected=violations_detected_total,
        message=(
            f"Đã xử lý {received_count} bản ghi telemetry: "
            f"Lưu thành công {accepted_total}, loại bỏ {filtered_total} bản ghi (cũ/trùng), "
            f"phát hiện {violations_detected_total} vi phạm chuỗi lạnh mới."
        ),
    )


# ==============================================================================
# S-42.2: QUẢN LÝ VÒNG ĐỜI CHUYẾN XE & TỰ SINH SỰ KIỆN LÔ HÀNG (ON-BOARD / OFF-BOARD)
# ==============================================================================
@router.post(
    "/trips",
    response_model=TripResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Khởi tạo chuyến xe lạnh và tự sinh sự kiện ON-BOARD cho lô hàng (S-42.2)",
)
def create_trip(
    data: TripCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TripResponse:
    """Tạo chuyến xe vận chuyển chuỗi lạnh.
    
    Quy tắc nghiệp vụ:
    - Kiểm tra mã chuyến xe duy nhất.
    - Kiểm tra danh sách các lô hàng hợp lệ (batch_ids).
    - Tạo các bản ghi TripConsignment cho từng lô hàng trên xe.
    - TỰ ĐỘNG GHI SỰ KIỆN ON-BOARD vào chuỗi băm bất biến BatchEvent cho từng lô hàng.
    """
    clean_code = data.trip_code.strip()
    exists = db.scalar(select(Trip).where(Trip.trip_code == clean_code))
    if exists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mã chuyến xe '{clean_code}' đã tồn tại trong hệ thống.",
        )

    # Kiểm tra các lô hàng
    unique_batch_ids = list(dict.fromkeys(data.batch_ids))
    batches = list(db.scalars(select(Batch).where(Batch.id.in_(unique_batch_ids))).all())
    found_ids = {b.id for b in batches}
    missing_ids = [bid for bid in unique_batch_ids if bid not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy các lô nông sản có ID: {missing_ids}.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    trip_started_at = parse_timestamp(data.started_at).isoformat() if data.started_at else now_iso
    trip = Trip(
        trip_code=clean_code,
        carrier_name=data.carrier_name.strip(),
        vehicle_plate=data.vehicle_plate.strip(),
        driver_name=data.driver_name.strip(),
        driver_phone=data.driver_phone.strip() if data.driver_phone else None,
        start_location=data.start_location.strip(),
        end_location=data.end_location.strip(),
        temp_min_threshold=data.temp_min_threshold,
        temp_max_threshold=data.temp_max_threshold,
        max_allowed_breach_minutes=data.max_allowed_breach_minutes,
        started_at=trip_started_at,
        status="IN_TRANSIT",
    )
    db.add(trip)
    db.flush()

    consignments_res: list[TripConsignmentResponse] = []
    for b in batches:
        cons = TripConsignment(
            trip_id=trip.id,
            batch_id=b.id,
            quantity=b.quantity,
            loaded_at=now_iso,
            notes=f"Xếp lên chuyến {trip.trip_code}",
        )
        db.add(cons)

        # TỰ ĐỘNG SINH SỰ KIỆN ON-BOARD
        record_event(
            db,
            batch_id=b.id,
            event_type="ON_BOARD",
            actor=current_user.username,
            organization=trip.carrier_name,
            payload={
                "trip_code": trip.trip_code,
                "vehicle_plate": trip.vehicle_plate,
                "carrier_name": trip.carrier_name,
                "driver_name": trip.driver_name,
                "start_location": trip.start_location,
                "end_location": trip.end_location,
                "loaded_at": now_iso,
                "message": f"Lô hàng {b.batch_code or b.id} đã xếp lên xe {trip.vehicle_plate} của nhà xe {trip.carrier_name}",
            },
        )

        consignments_res.append(
            TripConsignmentResponse(
                batch_id=b.id,
                batch_code=b.batch_code,
                product_name=b.product_name,
                quantity=b.quantity,
                loaded_at=now_iso,
            )
        )

    db.commit()
    db.refresh(trip)

    return TripResponse(
        id=trip.id,
        trip_code=trip.trip_code,
        carrier_name=trip.carrier_name,
        vehicle_plate=trip.vehicle_plate,
        driver_name=trip.driver_name,
        driver_phone=trip.driver_phone,
        start_location=trip.start_location,
        end_location=trip.end_location,
        temp_min_threshold=trip.temp_min_threshold,
        temp_max_threshold=trip.temp_max_threshold,
        max_allowed_breach_minutes=trip.max_allowed_breach_minutes,
        started_at=trip.started_at,
        completed_at=trip.completed_at,
        status=trip.status,
        consignments=consignments_res,
        violations=[],
        violations_count=0,
    )


@router.put(
    "/trips/{code}/complete",
    response_model=TripCompleteResponse,
    summary="Hoàn tất chuyến xe và tự sinh sự kiện OFF-BOARD cho các lô hàng (S-42.2)",
)
def complete_trip(
    code: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TripCompleteResponse:
    """Hoàn tất hành trình chuyến xe lạnh:
    
    1. Đổi trạng thái chuyến xe sang COMPLETED.
    2. Chạy đánh giá vi phạm lần cuối để chốt các đợt vượt ngưỡng đang mở.
    3. TỰ ĐỘNG GHI SỰ KIỆN OFF-BOARD vào chuỗi băm bất biến BatchEvent cho từng lô hàng trên xe.
    """
    clean_code = code.strip()
    trip = db.scalar(
        select(Trip)
        .where(Trip.trip_code == clean_code)
        .options(
            selectinload(Trip.consignments).selectinload(TripConsignment.batch),
            selectinload(Trip.telemetry_readings),
            selectinload(Trip.violations),
        )
    )
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy chuyến xe với mã '{clean_code}'.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    trip.status = "COMPLETED"
    trip.completed_at = now_iso

    # Đánh giá lại toàn bộ dữ liệu telemetry để chốt vi phạm lần cuối
    if trip.telemetry_readings:
        final_violations = evaluate_cold_chain_violations(
            readings=trip.telemetry_readings,
            temp_min=trip.temp_min_threshold,
            temp_max=trip.temp_max_threshold,
            breach_window_minutes=float(trip.max_allowed_breach_minutes),
            debounce_gap_minutes=5.0,
        )
        existing_v = {(v.started_at, v.violation_type) for v in trip.violations}
        for fv in final_violations:
            if (fv["started_at"], fv["violation_type"]) not in existing_v:
                db.add(
                    TripViolation(
                        trip_id=trip.id,
                        violation_type=fv["violation_type"],
                        severity=fv["severity"],
                        started_at=fv["started_at"],
                        ended_at=fv["ended_at"],
                        duration_minutes=fv["duration_minutes"],
                        peak_temperature=fv["peak_temperature"],
                        threshold_temp=fv["threshold_temp"],
                        reason=fv["reason"],
                        created_at=now_iso,
                    )
                )
        db.flush()

    # TỰ ĐỘNG GHI SỰ KIỆN OFF-BOARD CHO TỪNG LÔ HÀNG
    offboarded_ids: list[int] = []
    violations_count = len(trip.violations)
    cold_chain_status = "VIOLATED" if violations_count > 0 else "COMPLIANT"

    for cons in trip.consignments:
        b = cons.batch
        offboarded_ids.append(b.id)
        record_event(
            db,
            batch_id=b.id,
            event_type="OFF_BOARD",
            actor=current_user.username,
            organization=trip.carrier_name,
            payload={
                "trip_code": trip.trip_code,
                "vehicle_plate": trip.vehicle_plate,
                "carrier_name": trip.carrier_name,
                "status": "COMPLETED",
                "completed_at": now_iso,
                "violations_count": violations_count,
                "cold_chain_status": cold_chain_status,
                "message": (
                    f"Lô hàng {b.batch_code or b.id} đã hoàn tất vận chuyển trên xe "
                    f"{trip.vehicle_plate} và dỡ hàng (OFF-BOARD). Trạng thái chuỗi lạnh: {cold_chain_status}."
                ),
            },
        )

    db.commit()

    summary_text = (
        f"Chuyến xe {trip.trip_code} đã hoàn tất. Đã dỡ {len(offboarded_ids)} lô hàng (OFF-BOARD). "
        f"Ghi nhận {violations_count} vi phạm chuỗi lạnh."
    )

    return TripCompleteResponse(
        trip_code=trip.trip_code,
        status=trip.status,
        completed_at=now_iso,
        batches_offboarded=offboarded_ids,
        total_violations=violations_count,
        summary=summary_text,
    )


# ==============================================================================
# S-42.4: CẢNH BÁO ĐỎ CHUỖI LẠNH KHI BÀN GIAO LÔ HÀNG (HANDOVER WARNING)
# ==============================================================================
@router.get(
    "/batches/{batch_id}/handover-warning",
    response_model=HandoverWarningResponse,
    summary="Kiểm tra lịch sử vi phạm chuỗi lạnh để phát cảnh báo đỏ khi bàn giao (S-42.4)",
)
def check_handover_warning(
    batch_id: int,
    db: Session = Depends(get_db),
) -> HandoverWarningResponse:
    """Kiểm tra toàn bộ lịch sử các chuyến xe đã chở lô hàng này:
    
    - Nếu có vi phạm chuỗi lạnh (TripViolation): Trả về CẢNH BÁO ĐỎ (RED) kèm chi tiết vi phạm,
      nhiệt độ cực trị, thời gian kéo dài và khuyến cáo kiểm định/từ chối nhận hàng.
    - Nếu chuỗi lạnh an toàn 100%: Trả về CẢNH BÁO XANH (GREEN) xác nhận đủ điều kiện nghiệm thu.
    """
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô nông sản với ID #{batch_id}.",
        )

    # Truy vấn các chuyến xe liên quan tới lô hàng này
    consignments = list(
        db.scalars(
            select(TripConsignment)
            .where(TripConsignment.batch_id == batch_id)
            .options(
                selectinload(TripConsignment.trip).selectinload(Trip.violations)
            )
        ).all()
    )

    all_violations: list[TripViolation] = []
    carrier_set: set[str] = set()
    plate_set: set[str] = set()

    for c in consignments:
        if c.trip:
            carrier_set.add(c.trip.carrier_name)
            plate_set.add(c.trip.vehicle_plate)
            all_violations.extend(c.trip.violations)

    has_violations = len(all_violations) > 0
    violations_detail: list[TripViolationDetail] = []
    max_duration = 0.0
    worst_peak: Optional[float] = None

    if has_violations:
        warning_level = "RED"
        max_duration = max(v.duration_minutes for v in all_violations)

        # Tìm peak lệch chuẩn nhiều nhất
        high_peaks = [v.peak_temperature for v in all_violations if v.violation_type == "HIGH_TEMP_BREACH"]
        low_peaks = [v.peak_temperature for v in all_violations if v.violation_type == "LOW_TEMP_BREACH"]
        if high_peaks:
            worst_peak = max(high_peaks)
        elif low_peaks:
            worst_peak = min(low_peaks)
        else:
            worst_peak = all_violations[0].peak_temperature

        for v in all_violations:
            violations_detail.append(
                TripViolationDetail(
                    id=v.id,
                    violation_type=v.violation_type,
                    severity=v.severity,
                    started_at=v.started_at,
                    ended_at=v.ended_at,
                    duration_minutes=v.duration_minutes,
                    peak_temperature=v.peak_temperature,
                    threshold_temp=v.threshold_temp,
                    reason=v.reason,
                )
            )

        recommendation = (
            f"CẢNH BÁO ĐỎ BÀN GIAO: Lô hàng đã ghi nhận {len(all_violations)} vi phạm chuỗi lạnh "
            f"trong quá trình vận chuyển (nhiệt độ chạm ngưỡng {worst_peak}°C, vi phạm dài nhất {max_duration} phút). "
            f"Đề nghị bộ phận tiếp nhận cách ly kiểm định phẩm chất hoặc lập biên bản từ chối nhận hàng theo tiêu chuẩn ISO/HACCP."
        )
    else:
        warning_level = "GREEN"
        recommendation = (
            "AN TOÀN BÀN GIAO: Toàn bộ quá trình vận chuyển chuỗi lạnh đạt chuẩn 100% (0 vi phạm). "
            "Đủ điều kiện nghiệm thu và tiếp nhận lô hàng."
        )

    return HandoverWarningResponse(
        batch_id=batch.id,
        batch_code=batch.batch_code,
        product_name=batch.product_name,
        warning_level=warning_level,
        has_violations=has_violations,
        total_violations=len(all_violations),
        max_violation_duration_minutes=max_duration,
        worst_peak_temperature=worst_peak,
        carrier_names=sorted(carrier_set),
        vehicle_plates=sorted(plate_set),
        violations_detail=violations_detail,
        recommendation=recommendation,
    )


# ==============================================================================
# S-42.5: BÁO CÁO ĐÁNH GIÁ HIỆU SUẤT NHÀ XE THEO KỲ (CARRIER REPORT)
# ==============================================================================
@router.get(
    "/reports/carriers",
    response_model=CarrierReportResponse,
    summary="Báo cáo đánh giá hiệu suất tuân thủ chuỗi lạnh của các nhà xe theo kỳ (S-42.5)",
)
def get_carrier_reports(
    start_date: Optional[str] = Query(None, description="Thời điểm bắt đầu kỳ báo cáo (ISO format)"),
    end_date: Optional[str] = Query(None, description="Thời điểm kết thúc kỳ báo cáo (ISO format)"),
    carrier_name: Optional[str] = Query(None, description="Lọc theo tên nhà xe cụ thể"),
    db: Session = Depends(get_db),
) -> CarrierReportResponse:
    """Thống kê chi tiết chất lượng vận chuyển chuỗi lạnh của từng nhà xe:
    
    - Tổng chuyến xe, số chuyến hoàn tất, số chuyến đạt chuẩn 100%, số chuyến có vi phạm.
    - Tỷ lệ tuân thủ (%) = (chuyến đạt chuẩn / tổng chuyến) * 100.
    - Tổng số vụ vi phạm và thời gian vi phạm trung bình mỗi vụ.
    - Xếp hạng hiệu suất: XUẤT SẮC (>=95%), ĐẠT CHUẨN (>=80%), CẢNH CÁO (>=60%), ĐÌNH CHỈ HỢP ĐỒNG (<60%).
    """
    stmt = select(Trip).options(selectinload(Trip.violations))

    if carrier_name:
        stmt = stmt.where(Trip.carrier_name == carrier_name.strip())

    trips = list(db.scalars(stmt).all())

    # Lọc theo khoảng ngày nếu có
    if start_date:
        s_dt = parse_timestamp(start_date)
        trips = [t for t in trips if parse_timestamp(t.started_at) >= s_dt]
    if end_date:
        e_dt = parse_timestamp(end_date)
        trips = [t for t in trips if parse_timestamp(t.started_at) <= e_dt]

    # Gom nhóm theo carrier_name
    grouped: dict[str, list[Trip]] = {}
    for t in trips:
        grouped.setdefault(t.carrier_name, []).append(t)

    carrier_items: list[CarrierReportItem] = []
    for c_name, c_trips in grouped.items():
        total_trips = len(c_trips)
        completed_trips = sum(1 for t in c_trips if t.status == "COMPLETED")
        violated_trips = sum(1 for t in c_trips if len(t.violations) > 0)
        compliant_trips = total_trips - violated_trips
        compliance_rate = round((compliant_trips / total_trips * 100.0), 2) if total_trips > 0 else 100.0

        all_v = [v for t in c_trips for v in t.violations]
        total_v = len(all_v)
        avg_duration = round(sum(v.duration_minutes for v in all_v) / total_v, 1) if total_v > 0 else 0.0

        # Xếp loại nhà xe
        if compliance_rate >= 95.0:
            rating = "XUẤT SẮC"
        elif compliance_rate >= 80.0:
            rating = "ĐẠT CHUẨN"
        elif compliance_rate >= 60.0:
            rating = "CẢNH CÁO"
        else:
            rating = "ĐÌNH CHỈ HỢP ĐỒNG"

        carrier_items.append(
            CarrierReportItem(
                carrier_name=c_name,
                total_trips=total_trips,
                completed_trips=completed_trips,
                compliant_trips=compliant_trips,
                violated_trips=violated_trips,
                compliance_rate=compliance_rate,
                total_violations=total_v,
                avg_violation_duration_minutes=avg_duration,
                rating=rating,
            )
        )

    carrier_items.sort(key=lambda x: x.compliance_rate, reverse=True)

    return CarrierReportResponse(
        start_date=start_date,
        end_date=end_date,
        total_carriers=len(carrier_items),
        carriers=carrier_items,
    )


# ==============================================================================
# HELPER APIS CHO CHUYẾN XE (TRIPS LIST & DETAIL)
# ==============================================================================
@router.get(
    "/trips",
    response_model=list[TripResponse],
    summary="Danh sách tất cả các chuyến xe vận chuyển chuỗi lạnh",
)
def list_trips(
    carrier_name: Optional[str] = Query(None, description="Lọc theo nhà xe"),
    status_filter: Optional[str] = Query(None, alias="status", description="Lọc theo trạng thái (IN_TRANSIT, COMPLETED)"),
    db: Session = Depends(get_db),
) -> list[TripResponse]:
    stmt = (
        select(Trip)
        .options(
            selectinload(Trip.consignments).selectinload(TripConsignment.batch),
            selectinload(Trip.violations),
        )
        .order_by(Trip.id.desc())
    )
    if carrier_name:
        stmt = stmt.where(Trip.carrier_name == carrier_name.strip())
    if status_filter:
        stmt = stmt.where(Trip.status == status_filter.strip())

    trips = list(db.scalars(stmt).all())
    result: list[TripResponse] = []
    for t in trips:
        cons_list = [
            TripConsignmentResponse(
                batch_id=c.batch_id,
                batch_code=c.batch.batch_code if c.batch else None,
                product_name=c.batch.product_name if c.batch else None,
                quantity=c.quantity,
                loaded_at=c.loaded_at,
            )
            for c in t.consignments
        ]
        viols_list = [
            TripViolationDetail(
                id=v.id,
                violation_type=v.violation_type,
                severity=v.severity,
                started_at=v.started_at,
                ended_at=v.ended_at,
                duration_minutes=v.duration_minutes,
                peak_temperature=v.peak_temperature,
                threshold_temp=v.threshold_temp,
                reason=v.reason,
            )
            for v in t.violations
        ]
        result.append(
            TripResponse(
                id=t.id,
                trip_code=t.trip_code,
                carrier_name=t.carrier_name,
                vehicle_plate=t.vehicle_plate,
                driver_name=t.driver_name,
                driver_phone=t.driver_phone,
                start_location=t.start_location,
                end_location=t.end_location,
                temp_min_threshold=t.temp_min_threshold,
                temp_max_threshold=t.temp_max_threshold,
                max_allowed_breach_minutes=t.max_allowed_breach_minutes,
                started_at=t.started_at,
                completed_at=t.completed_at,
                status=t.status,
                consignments=cons_list,
                violations=viols_list,
                violations_count=len(viols_list),
            )
        )
    return result


@router.get(
    "/trips/{code}",
    response_model=TripResponse,
    summary="Chi tiết một chuyến xe vận chuyển chuỗi lạnh kèm vi phạm",
)
def get_trip_detail(
    code: str,
    db: Session = Depends(get_db),
) -> TripResponse:
    trip = db.scalar(
        select(Trip)
        .where(Trip.trip_code == code.strip())
        .options(
            selectinload(Trip.consignments).selectinload(TripConsignment.batch),
            selectinload(Trip.violations),
        )
    )
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy chuyến xe với mã '{code}'.",
        )

    cons_list = [
        TripConsignmentResponse(
            batch_id=c.batch_id,
            batch_code=c.batch.batch_code if c.batch else None,
            product_name=c.batch.product_name if c.batch else None,
            quantity=c.quantity,
            loaded_at=c.loaded_at,
        )
        for c in trip.consignments
    ]
    viols_list = [
        TripViolationDetail(
            id=v.id,
            violation_type=v.violation_type,
            severity=v.severity,
            started_at=v.started_at,
            ended_at=v.ended_at,
            duration_minutes=v.duration_minutes,
            peak_temperature=v.peak_temperature,
            threshold_temp=v.threshold_temp,
            reason=v.reason,
        )
        for v in trip.violations
    ]
    return TripResponse(
        id=trip.id,
        trip_code=trip.trip_code,
        carrier_name=trip.carrier_name,
        vehicle_plate=trip.vehicle_plate,
        driver_name=trip.driver_name,
        driver_phone=trip.driver_phone,
        start_location=trip.start_location,
        end_location=trip.end_location,
        temp_min_threshold=trip.temp_min_threshold,
        temp_max_threshold=trip.temp_max_threshold,
        max_allowed_breach_minutes=trip.max_allowed_breach_minutes,
        started_at=trip.started_at,
        completed_at=trip.completed_at,
        status=trip.status,
        consignments=cons_list,
        violations=viols_list,
        violations_count=len(viols_list),
    )


# ==============================================================================
# QUẢN LÝ CẤU HÌNH NGƯỠNG & ĐỘ TRỄ RIÊNG TỪNG LOẠI SẢN PHẨM (BACKWARD COMPATIBLE)
# ==============================================================================
@router.get(
    "/thresholds",
    response_model=list[ColdChainThresholdResponse],
    summary="Lấy danh sách cấu hình ngưỡng cho từng loại sản phẩm",
)
def list_thresholds(
    db: Session = Depends(get_db),
) -> list[ColdChainThreshold]:
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
    clean_types = [t.strip() for t in payload.product_types if t.strip()]
    if not clean_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Danh sách loại sản phẩm không được để trống.",
        )

    stmt = select(ColdChainThreshold).where(ColdChainThreshold.product_type.in_(clean_types))
    configs = list(db.scalars(stmt).all())
    found_types = {c.product_type for c in configs}

    missing = [t for t in clean_types if t not in found_types]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chưa có cấu hình ngưỡng cho các loại sản phẩm: {', '.join(missing)}. Vui lòng thiết lập trước.",
        )

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
    summary="Kiểm tra telemetry nhiệt độ chuyến xe và ghi nhận vi phạm thủ công nếu vượt ngưỡng",
)
def check_telemetry(
    data: TelemetryCheckRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TelemetryCheckResponse:
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

    eff_min = max(c.temp_min for c in configs)
    eff_max = min(c.temp_max for c in configs)
    eff_delay = min(c.delay_minutes for c in configs)

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
