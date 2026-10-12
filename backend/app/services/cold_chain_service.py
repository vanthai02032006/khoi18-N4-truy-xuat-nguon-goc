"""Service giám sát nhiệt độ chuỗi lạnh & tổng hợp dữ liệu cũ hơn 30 ngày (SCRUM-126).

Yêu cầu kỹ thuật:
1. Tích hợp cấu hình ngưỡng bảo quản lạnh theo từng loại nông sản (TemperatureThresholdConfig),
   không áp một ngưỡng chung cho tất cả nông sản. Fallback về cấu hình DEFAULT nếu không khớp.
2. Job hằng đêm quét các số đo nhiệt độ thô (TemperatureReading) cũ hơn 30 ngày (recorded_at < cutoff_time).
3. Nhóm theo từng giờ (hour_bucket), theo từng lô (batch_id) và cảm biến (sensor_id).
4. Tính toán MIN, MAX, AVG (làm tròn 2 chữ số thập phân), số lượng mẫu và số vi phạm.
5. Idempotent: Chạy lại nhiều lần không tạo dữ liệu trùng lặp trong HourlyTemperatureSummary.
6. CHÍNH SÁCH BẢO LƯU SỐ ĐO THÔ:
   - CHỈ xoá các số đo thô ĐÃ TỔNG HỢP VÀ KHÔNG VI PHẠM (is_violation == False).
   - BẮT BUỘC GIỮ LẠI NGUYÊN VẸN 100% các số đo thô tại các khoảng vi phạm ngưỡng (is_violation == True)
     để phục vụ thanh tra, truy vết sự cố chuỗi lạnh và kiểm định chất lượng.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from app.models import Batch, HourlyTemperatureSummary, TemperatureReading, TemperatureThresholdConfig


def get_threshold_for_batch(db: Session, batch_id: int | None) -> tuple[float, float, str]:
    """Lấy ngưỡng nhiệt độ chuẩn (min_temp, max_temp, product_name) cho lô nông sản.

    Nếu batch không tồn tại hoặc không khớp cấu hình riêng, sử dụng cấu hình 'DEFAULT' (2°C - 8°C).
    """
    default_min, default_max = 2.0, 8.0
    product_name = "DEFAULT"

    if batch_id is not None:
        batch = db.get(Batch, batch_id)
        if batch and batch.product_name:
            product_name = batch.product_name
            # Tìm khớp chính xác hoặc khớp từ khoá trong product_name
            cfg = db.scalar(
                select(TemperatureThresholdConfig)
                .where(TemperatureThresholdConfig.product_name == product_name)
            )
            if not cfg:
                # Tìm partial match (ví dụ: product_name = "Xoài Cát Chu Loại 1" khớp với "Xoài Cát Chu")
                all_cfgs = list(db.scalars(select(TemperatureThresholdConfig)).all())
                for c in all_cfgs:
                    if c.product_name.lower() in product_name.lower() and c.product_name != "DEFAULT":
                        cfg = c
                        break

            if cfg:
                return cfg.min_temp, cfg.max_temp, product_name

    # Fallback cấu hình DEFAULT
    default_cfg = db.scalar(
        select(TemperatureThresholdConfig)
        .where(TemperatureThresholdConfig.product_name == "DEFAULT")
    )
    if default_cfg:
        return default_cfg.min_temp, default_cfg.max_temp, "DEFAULT"

    return default_min, default_max, "DEFAULT"


def record_temperature(
    db: Session,
    sensor_id: str,
    temperature: float,
    batch_id: int | None = None,
    recorded_at: datetime | None = None,
) -> TemperatureReading:
    """Ghi nhận một số đo nhiệt độ mới từ cảm biến chuỗi lạnh và tự động kiểm tra vi phạm."""
    rec_time = recorded_at or datetime.now()
    min_temp, max_temp, prod = get_threshold_for_batch(db, batch_id)

    is_violation = False
    violation_reason = None

    if temperature < min_temp:
        is_violation = True
        violation_reason = f"Dưới ngưỡng tối thiểu {min_temp}°C (đo được: {temperature}°C) cho sản phẩm {prod}."
    elif temperature > max_temp:
        is_violation = True
        violation_reason = f"Vượt ngưỡng tối đa {max_temp}°C (đo được: {temperature}°C) cho sản phẩm {prod}."

    reading = TemperatureReading(
        batch_id=batch_id,
        sensor_id=sensor_id.strip(),
        temperature=round(temperature, 2),
        recorded_at=rec_time,
        is_violation=is_violation,
        is_aggregated=False,
        violation_reason=violation_reason,
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)
    return reading


def aggregate_old_temperatures_job(
    db: Session,
    cutoff_time: datetime | None = None,
    days_threshold: int = 30,
) -> dict[str, Any]:
    """Thực thi job tổng hợp số đo nhiệt độ cũ hơn 30 ngày.

    Quy trình:
    1. Xác định mốc cutoff_time (mặc định = now - 30 ngày).
    2. Tìm tất cả số đo thô có recorded_at < cutoff_time.
    3. Phân loại theo (batch_id, sensor_id, hour_bucket).
    4. Tính MIN, MAX, AVG, sample_count, violation_count.
    5. Cập nhật hoặc chèn vào HourlyTemperatureSummary (idempotent).
    6. Xóa các số đo thô bình thường đã tổng hợp (is_violation == False).
    7. GIỮ NGUYÊN các số đo thô vi phạm (is_violation == True).
    """
    if cutoff_time is None:
        cutoff_time = datetime.now() - timedelta(days=days_threshold)

    # Đọc các số đo thô cũ hơn cutoff_time
    raw_readings = list(
        db.scalars(
            select(TemperatureReading)
            .where(TemperatureReading.recorded_at < cutoff_time)
            .order_by(TemperatureReading.recorded_at)
        ).all()
    )

    if not raw_readings:
        return {
            "message": "Không có số đo nhiệt độ cũ hơn mốc quy định để tổng hợp.",
            "cutoff_time": cutoff_time,
            "aggregated_buckets": 0,
            "purged_raw_readings": 0,
            "retained_violation_readings": 0,
        }

    # Gom nhóm theo (batch_id, sensor_id, hour_bucket)
    buckets: dict[tuple[int | None, str, datetime], list[TemperatureReading]] = {}
    for r in raw_readings:
        # Làm tròn về đầu giờ: 2026-08-01 14:23:45 -> 2026-08-01 14:00:00
        hour_b = r.recorded_at.replace(minute=0, second=0, microsecond=0)
        key = (r.batch_id, r.sensor_id, hour_b)
        buckets.setdefault(key, []).append(r)

    aggregated_count = 0
    safe_reading_ids_to_purge: list[int] = []
    retained_violation_count = 0

    for (b_id, s_id, hour_bucket), readings_list in buckets.items():
        temps = [item.temperature for item in readings_list]
        violations_in_bucket = [item for item in readings_list if item.is_violation]
        safe_in_bucket = [item for item in readings_list if not item.is_violation]

        min_val = round(min(temps), 2)
        max_val = round(max(temps), 2)
        avg_val = round(sum(temps) / len(temps), 2)
        sample_count = len(temps)
        violation_count = len(violations_in_bucket)

        # Kiểm tra xem bản ghi summary cho (b_id, s_id, hour_bucket) đã tồn tại chưa (Idempotent)
        existing_summary = db.scalar(
            select(HourlyTemperatureSummary).where(
                HourlyTemperatureSummary.batch_id == b_id,
                HourlyTemperatureSummary.sensor_id == s_id,
                HourlyTemperatureSummary.hour_bucket == hour_bucket,
            )
        )

        if existing_summary:
            existing_summary.min_temp = min_val
            existing_summary.max_temp = max_val
            existing_summary.avg_temp = avg_val
            existing_summary.sample_count = sample_count
            existing_summary.violation_count = violation_count
        else:
            new_summary = HourlyTemperatureSummary(
                batch_id=b_id,
                sensor_id=s_id,
                hour_bucket=hour_bucket,
                min_temp=min_val,
                max_temp=max_val,
                avg_temp=avg_val,
                sample_count=sample_count,
                violation_count=violation_count,
            )
            db.add(new_summary)

        # Đánh dấu các số đo an toàn cần xoá sau khi tổng hợp
        safe_reading_ids_to_purge.extend([item.id for item in safe_in_bucket])
        # Đếm các số đo vi phạm được bảo lưu
        retained_violation_count += len(violations_in_bucket)
        aggregated_count += 1

    # Commit dữ liệu tổng hợp
    db.commit()

    # Dọn dẹp số đo thô AN TOÀN (chỉ xoá khi bản ghi không vi phạm)
    purged_count = 0
    if safe_reading_ids_to_purge:
        # SQLite hỗ trợ xoá theo danh sách ID
        # Chia thành từng batch 500 ID để tránh SQLite parameter limit
        for i in range(0, len(safe_reading_ids_to_purge), 500):
            batch_ids = safe_reading_ids_to_purge[i : i + 500]
            db.execute(
                delete(TemperatureReading).where(TemperatureReading.id.in_(batch_ids))
            )
        db.commit()
        purged_count = len(safe_reading_ids_to_purge)

    return {
        "message": f"Tổng hợp thành công {aggregated_count} khung giờ.",
        "cutoff_time": cutoff_time,
        "aggregated_buckets": aggregated_count,
        "purged_raw_readings": purged_count,
        "retained_violation_readings": retained_violation_count,
    }


def list_temperature_summaries(
    db: Session,
    sensor_id: str | None = None,
    batch_id: int | None = None,
    limit: int = 100,
) -> list[HourlyTemperatureSummary]:
    """Tra cứu các bản ghi tổng hợp nhiệt độ theo giờ."""
    query = select(HourlyTemperatureSummary).order_by(desc(HourlyTemperatureSummary.hour_bucket))
    if sensor_id:
        query = query.where(HourlyTemperatureSummary.sensor_id == sensor_id)
    if batch_id:
        query = query.where(HourlyTemperatureSummary.batch_id == batch_id)
    return list(db.scalars(query.limit(limit)).all())


def list_raw_violations(
    db: Session,
    limit: int = 100,
) -> list[TemperatureReading]:
    """Lấy danh sách các số đo vi phạm nhiệt độ đang được bảo lưu nguyên vẹn."""
    return list(
        db.scalars(
            select(TemperatureReading)
            .where(TemperatureReading.is_violation == True)  # noqa: E712
            .order_by(desc(TemperatureReading.recorded_at))
            .limit(limit)
        ).all()
    )
