"""Module giám sát chuỗi lạnh và phát hiện vi phạm nhiệt độ (S-41).

Phục vụ phân tích dữ liệu cảm biến IoT nhiệt độ theo thời gian (time-series).
Quy chuẩn chuỗi lạnh (Cold Chain):
- Ngưỡng nhiệt độ an toàn: [min_temp, max_temp] (mặc định cho rau quả/nông sản tươi: 2.0°C đến 8.0°C).
- Ngưỡng thời gian vi phạm tối thiểu (tolerance window): ví dụ nhiệt độ vượt quá ngưỡng liên tục
  từ `threshold_minutes` (mặc định 15 phút hoặc 30 phút, hoặc cấu hình linh hoạt) mới tính là vi phạm,
  hoặc tính theo từng phiên vượt ngưỡng.
- Xử lý mượt mà dữ liệu xuyên ngày (vượt qua nửa đêm 23:59 -> 00:01).
- Tính toán khoảng cách giữa các lần vi phạm (khoảng hồi phục / gap).
"""

from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


class TemperatureReading(BaseModel):
    """Một điểm đo nhiệt độ tại thời điểm cụ thể."""

    timestamp: str = Field(..., description="Thời điểm ghi nhận (ISO 8601 hoặc 'YYYY-MM-DD HH:MM:SS').")
    temperature: float = Field(..., description="Nhiệt độ đo được (°C).")


class ExcursionDetail(BaseModel):
    """Chi tiết một lần vi phạm ngưỡng nhiệt độ chuỗi lạnh."""

    start_time: str
    end_time: str
    duration_minutes: float
    max_temperature: float
    min_temperature: float
    violation_type: str  # "TOO_HOT" hoặc "TOO_COLD"
    points_count: int


class ColdChainAnalysisRequest(BaseModel):
    """Dữ liệu đầu vào để phân tích chuỗi nhiệt độ."""

    readings: list[TemperatureReading] = Field(..., description="Chuỗi thời điểm và nhiệt độ.")
    min_temp: float = Field(default=2.0, description="Ngưỡng nhiệt độ tối thiểu (°C).")
    max_temp: float = Field(default=8.0, description="Ngưỡng nhiệt độ tối đa (°C).")
    min_duration_minutes: float = Field(
        default=0.0,
        description="Số phút vượt ngưỡng tối thiểu để tính là 1 vi phạm (0: mọi lần vượt đều tính).",
    )


class ColdChainAnalysisResponse(BaseModel):
    """Kết quả phân tích vi phạm nhiệt độ chuỗi lạnh (S-41)."""

    total_readings: int
    is_compliant: bool
    total_violations: int
    excursions: list[ExcursionDetail]
    summary_message: str


def parse_iso_datetime(ts_str: str) -> datetime:
    """Chuyển chuỗi thời gian sang đối tượng datetime chuẩn UTC."""
    ts_str = ts_str.strip()
    # Hỗ trợ định dạng ISO và định dạng thông dụng 'YYYY-MM-DD HH:MM:SS'
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]:
        try:
            dt = datetime.strptime(ts_str.replace("Z", "+0000") if "Z" in ts_str and "%z" in fmt else ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    # Fallback isoformat của datetime
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception as exc:
        raise ValueError(f"Không thể phân tích thời điểm: '{ts_str}'") from exc


def analyze_cold_chain(
    readings: list[dict[str, Any] | TemperatureReading],
    min_temp: float = 2.0,
    max_temp: float = 8.0,
    min_duration_minutes: float = 0.0,
) -> dict[str, Any]:
    """Thuật toán phân tích chuỗi thời điểm và nhiệt độ để phát hiện vi phạm chuỗi lạnh.

    Xử lý:
    - Sắp xếp tăng dần theo thời gian.
    - Nhóm các điểm vượt ngưỡng liên tục thành từng phiên (excursion).
    - Tính thời lượng (duration_minutes) = (end_time - start_time).
    - Xử lý xuyên qua nửa đêm (across midnight) dựa trên timestamp thực tế.
    - Hai lần vượt cách nhau thì được tách thành 2 vi phạm riêng biệt.
    """
    if not readings:
        return {
            "total_readings": 0,
            "is_compliant": True,
            "total_violations": 0,
            "excursions": [],
            "summary_message": "Không có dữ liệu đo nhiệt độ.",
        }

    # Chuẩn hoá và sắp xếp dữ liệu theo thời gian
    parsed_points = []
    for r in readings:
        if isinstance(r, TemperatureReading):
            t_str = r.timestamp
            temp = r.temperature
        else:
            t_str = r["timestamp"]
            temp = float(r["temperature"])
        dt = parse_iso_datetime(t_str)
        parsed_points.append((dt, temp, t_str))

    parsed_points.sort(key=lambda x: x[0])

    excursions: list[dict[str, Any]] = []
    current_excursion: list[tuple[datetime, float, str]] = []
    current_type: str | None = None

    def close_current_excursion():
        nonlocal current_excursion, current_type
        if not current_excursion or not current_type:
            current_excursion = []
            current_type = None
            return

        start_dt, _, start_str = current_excursion[0]
        end_dt, _, end_str = current_excursion[-1]
        duration_min = round((end_dt - start_dt).total_seconds() / 60.0, 2)
        temps = [pt[1] for pt in current_excursion]

        if duration_min >= min_duration_minutes:
            excursions.append({
                "start_time": start_str,
                "end_time": end_str,
                "duration_minutes": duration_min,
                "max_temperature": max(temps),
                "min_temperature": min(temps),
                "violation_type": current_type,
                "points_count": len(current_excursion),
            })
        current_excursion = []
        current_type = None

    for dt, temp, original_str in parsed_points:
        if temp > max_temp:
            v_type = "TOO_HOT"
        elif temp < min_temp:
            v_type = "TOO_COLD"
        else:
            v_type = None

        if v_type is not None:
            if current_type is None:
                current_type = v_type
                current_excursion = [(dt, temp, original_str)]
            elif current_type == v_type:
                current_excursion.append((dt, temp, original_str))
            else:
                # Đổi từ nóng sang lạnh hoặc ngược lại
                close_current_excursion()
                current_type = v_type
                current_excursion = [(dt, temp, original_str)]
        else:
            if current_excursion:
                close_current_excursion()

    if current_excursion:
        close_current_excursion()

    total_violations = len(excursions)
    is_compliant = total_violations == 0

    return {
        "total_readings": len(parsed_points),
        "is_compliant": is_compliant,
        "total_violations": total_violations,
        "excursions": excursions,
        "summary_message": (
            "Duy trì chuỗi lạnh đạt chuẩn, 0 vi phạm."
            if is_compliant
            else f"Phát hiện {total_violations} lần vi phạm ngưỡng nhiệt độ chuỗi lạnh."
        ),
    }
