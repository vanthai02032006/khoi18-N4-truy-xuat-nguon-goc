"""Lõi thuật toán phân tích và phát hiện vi phạm chuỗi lạnh (Cold-Chain Analytics Engine).

Đáp ứng yêu cầu User Story S-42 (Tier Later - 5 SP):
- Là cán bộ kiểm tra, tôi muốn chỉ những lần vượt ngưỡng kéo dài mới bị tính là vi phạm
  để hệ thống không báo động liên tục vì mấy phút mở cửa xe.
- Quy tắc nghiệp vụ:
  1. Cửa sổ vi phạm kéo dài (Breach Window, mặc định 30 phút):
     Nhiệt độ vượt ngưỡng liên tục quá 30 phút thì sinh MỘT vi phạm ghi rõ:
     - Thời điểm bắt đầu (started_at)
     - Thời điểm kết thúc (ended_at)
     - Nhiệt độ cực trị (peak_temperature: max đối với vượt trên, min đối với vượt dưới)
     - Thời gian kéo dài (duration_minutes)
  2. Khử nhiễu vượt ngắn (Transient Excursion Filter):
     Vượt ngắn rồi trở lại trong dải an toàn (< 30 phút, ví dụ mở cửa xe bốc hàng 10-20 phút)
     thì KHÔNG cấu thành vi phạm (0 vi phạm).
  3. Cơ chế Debounce Gap 5 phút:
     - Hai lần vượt cách nhau dưới hoặc bằng 5 phút (<= 5 phút hạ nhiệt tạm thời):
       Hệ thống gộp liền mạch thành MỘT đợt vượt duy nhất.
       Nếu tổng thời gian từ đầu đợt 1 đến cuối đợt 2 >= 30 phút -> tính là 1 vi phạm.
     - Hai lần vượt cách xa hơn 5 phút (> 5 phút trở lại bình thường):
       Tính là HAI đợt riêng biệt. Nếu mỗi đợt đều >= 30 phút -> tính là 2 vi phạm độc lập.
  4. Xử lý chuẩn xác chuyển giao ngày / qua nửa đêm (Midnight crossing):
     Tính toán delta thời gian chính xác khi mốc thời gian vắt qua mốc 00:00 giữa 2 ngày.
  5. Khử trùng lặp (Deduplication) và sắp xếp tuần tự theo thời gian (Chronological Sorting).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def parse_timestamp(val: str | datetime) -> datetime:
    """Chuyển đổi chuỗi thời gian hoặc datetime sang UTC timezone-aware datetime.
    
    Hỗ trợ chuẩn ISO 8601 (có 'Z', lệch múi giờ '+07:00', hoặc naive datetime).
    """
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)

    clean_str = str(val).strip()
    if clean_str.endswith("Z"):
        clean_str = clean_str[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        # Fallback cho các định dạng ngày giờ phổ biến khác
        for fmt in (
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(clean_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                continue
        raise ValueError(f"Không thể phân tích định dạng mốc thời gian: '{val}'")


def deduplicate_and_sort_telemetry(
    readings: list[dict[str, Any] | Any],
    min_timestamp: str | datetime | None = None,
    max_timestamp: str | datetime | None = None,
) -> list[dict[str, Any]]:
    """Lọc mốc cũ, khử trùng lặp và sắp xếp theo trình tự thời gian tăng dần."""
    if not readings:
        return []

    min_dt = parse_timestamp(min_timestamp) if min_timestamp else None
    max_dt = parse_timestamp(max_timestamp) if max_timestamp else None

    seen_keys: set[tuple[str, str]] = set()
    cleaned_list: list[dict[str, Any]] = []

    for r in readings:
        if isinstance(r, dict):
            device_id = str(r.get("device_id", "SENSOR_DEFAULT")).strip()
            raw_recorded_at = r.get("timestamp") or r.get("recorded_at")
            temp = r.get("temperature")
            humidity = r.get("humidity")
            location = r.get("location")
            trip_code = r.get("trip_code")
        else:
            device_id = str(getattr(r, "device_id", "SENSOR_DEFAULT")).strip()
            raw_recorded_at = getattr(r, "timestamp", None) or getattr(r, "recorded_at", None)
            temp = getattr(r, "temperature", None)
            humidity = getattr(r, "humidity", None)
            location = getattr(r, "location", None)
            trip_code = getattr(r, "trip_code", None)

        if raw_recorded_at is None or temp is None:
            continue

        try:
            recorded_dt = parse_timestamp(raw_recorded_at)
        except Exception:
            continue

        # 1. Lọc mốc thời gian cũ hơn min_timestamp hoặc tương lai xa
        if min_dt and recorded_dt < min_dt:
            continue
        if max_dt and recorded_dt > max_dt:
            continue

        # 2. Khử trùng lặp (Deduplication theo device_id + timestamp ISO)
        iso_str = recorded_dt.isoformat()
        dedup_key = (device_id, iso_str)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        cleaned_list.append({
            "device_id": device_id,
            "temperature": float(temp),
            "humidity": float(humidity) if humidity is not None else None,
            "location": location,
            "recorded_at": iso_str,
            "trip_code": trip_code,
            "_dt": recorded_dt,
        })

    # 3. Sắp xếp tuần tự thời gian tăng dần
    cleaned_list.sort(key=lambda x: x["_dt"])

    for item in cleaned_list:
        item.pop("_dt", None)

    return cleaned_list


def evaluate_cold_chain_violations(
    readings: list[dict[str, Any] | Any],
    temp_min: float,
    temp_max: float,
    breach_window_minutes: float = 30.0,
    debounce_gap_minutes: float = 5.0,
    max_continuous_gap_minutes: float = 20.0,
) -> list[dict[str, Any]]:
    """Lõi phát hiện vi phạm chuỗi lạnh theo chuẩn S-42 (Tier Later - 5 SP).

    Quy tắc nghiệp vụ:
    - Vượt ngưỡng trên: temperature > temp_max
    - Vượt ngưỡng dưới: temperature < temp_min
    - Cửa sổ vi phạm (breach_window_minutes = 30 phút):
      Chỉ khi thời gian vượt ngưỡng liên tục >= 30 phút thì MỚI KẾT LUẬN VI PHẠM.
      Các dao động thoáng qua < 30 phút (mở cửa xe) KHÔNG cấu thành vi phạm.
    - Debounce Gap (debounce_gap_minutes = 5 phút):
      Nếu nhiệt độ tạm thời về trong dải an toàn nhưng quay lại vượt ngưỡng trong <= 5 phút,
      toàn bộ khoảng thời gian này được gộp liền mạch thành một đợt vượt ngưỡng duy nhất.
    - Ghi nhận chi tiết vi phạm:
      Ghi rõ mốc bắt đầu (started_at), kết thúc (ended_at), thời gian kéo dài (duration_minutes),
      nhiệt độ đỉnh cực trị (peak_temperature) và mức độ nghiêm trọng (severity).

    Returns:
        list[dict[str, Any]]: Danh sách các vi phạm phát hiện được.
    """
    if not readings:
        return []

    sorted_readings = deduplicate_and_sort_telemetry(readings)
    if not sorted_readings:
        return []

    violations: list[dict[str, Any]] = []
    active_breach: Optional[dict[str, Any]] = None

    def _close_and_record_if_violated(breach_state: dict[str, Any]) -> None:
        duration_mins = (breach_state["last_breach_dt"] - breach_state["started_dt"]).total_seconds() / 60.0
        if duration_mins >= breach_window_minutes:
            v_type = breach_state["type"]
            peak = round(breach_state["peak_temp"], 2)
            thresh = breach_state["threshold_temp"]
            duration_rounded = round(duration_mins, 1)
            diff = abs(peak - thresh)

            severity = "CRITICAL" if (duration_mins >= 60.0 or diff >= 5.0) else "WARNING"

            if v_type == "HIGH_TEMP_BREACH":
                reason = (
                    f"CẢNH BÁO NHIỆT ĐỘ CAO: Nhiệt độ chạm đỉnh {peak}°C vượt ngưỡng trên "
                    f"({thresh}°C) kéo dài {duration_rounded} phút (vượt quá giới hạn cho phép {breach_window_minutes} phút)."
                )
            else:
                reason = (
                    f"CẢNH BÁO NHIỆT ĐỘ THẤP: Nhiệt độ chạm đáy {peak}°C dưới ngưỡng dưới "
                    f"({thresh}°C) kéo dài {duration_rounded} phút (vượt quá giới hạn cho phép {breach_window_minutes} phút)."
                )

            violations.append({
                "violation_type": v_type,
                "severity": severity,
                "started_at": breach_state["started_dt"].isoformat(),
                "ended_at": breach_state["last_breach_dt"].isoformat(),
                "duration_minutes": duration_rounded,
                "peak_temperature": peak,
                "threshold_temp": thresh,
                "reason": reason,
            })

    for item in sorted_readings:
        curr_dt = parse_timestamp(item["recorded_at"])
        curr_temp = float(item["temperature"])

        if curr_temp > temp_max:
            curr_breach_type = "HIGH_TEMP_BREACH"
            curr_threshold = temp_max
        elif curr_temp < temp_min:
            curr_breach_type = "LOW_TEMP_BREACH"
            curr_threshold = temp_min
        else:
            curr_breach_type = None
            curr_threshold = None

        if curr_breach_type is not None:
            if active_breach is None:
                # Bắt đầu đợt vượt ngưỡng mới
                active_breach = {
                    "type": curr_breach_type,
                    "started_dt": curr_dt,
                    "last_breach_dt": curr_dt,
                    "peak_temp": curr_temp,
                    "threshold_temp": curr_threshold,
                }
            else:
                if active_breach["type"] == curr_breach_type:
                    # Cùng loại vượt ngưỡng: kiểm tra khoảng cách so với mốc vi phạm liền trước
                    gap_from_last = (curr_dt - active_breach["last_breach_dt"]).total_seconds() / 60.0
                    if gap_from_last <= max_continuous_gap_minutes:
                        # Khoảng cách giữa các lần đo hợp lý (<= 20 phút) hoặc vừa từ bình thường quay lại qua debounce gap
                        active_breach["last_breach_dt"] = curr_dt
                        if curr_breach_type == "HIGH_TEMP_BREACH":
                            active_breach["peak_temp"] = max(active_breach["peak_temp"], curr_temp)
                        else:
                            active_breach["peak_temp"] = min(active_breach["peak_temp"], curr_temp)
                    else:
                        # Mất tín hiệu quá lâu (> 20 phút): chốt đợt cũ, mở đợt mới
                        _close_and_record_if_violated(active_breach)
                        active_breach = {
                            "type": curr_breach_type,
                            "started_dt": curr_dt,
                            "last_breach_dt": curr_dt,
                            "peak_temp": curr_temp,
                            "threshold_temp": curr_threshold,
                        }
                else:
                    # Đổi từ vượt ngưỡng trên sang vượt ngưỡng dưới hoặc ngược lại
                    _close_and_record_if_violated(active_breach)
                    active_breach = {
                        "type": curr_breach_type,
                        "started_dt": curr_dt,
                        "last_breach_dt": curr_dt,
                        "peak_temp": curr_temp,
                        "threshold_temp": curr_threshold,
                    }
        else:
            # Nhiệt độ quay về dải an toàn bình thường
            if active_breach is not None:
                gap_from_last = (curr_dt - active_breach["last_breach_dt"]).total_seconds() / 60.0
                if gap_from_last > debounce_gap_minutes:
                    # Nhiệt độ bình thường kéo dài vượt quá Debounce Gap (> 5 phút): kết thúc đợt vi phạm
                    _close_and_record_if_violated(active_breach)
                    active_breach = None

    # Đóng đợt vi phạm còn mở ở cuối hành trình (nếu có)
    if active_breach is not None:
        _close_and_record_if_violated(active_breach)

    return violations
