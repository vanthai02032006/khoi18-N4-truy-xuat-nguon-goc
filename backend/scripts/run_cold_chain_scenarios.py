"""Script đọc tệp kịch bản chuỗi lạnh (chuỗi thời điểm và nhiệt độ) rồi gọi endpoint ở S-41.

Bao gồm ít nhất 5 kịch bản mẫu có đáp án số vi phạm:
1. Không vượt: 0 vi phạm (is_compliant = True).
2. Vượt 10 phút: 1 vi phạm, thời lượng 10 phút.
3. Vượt 45 phút: 1 vi phạm, thời lượng 45 phút.
4. Hai lần vượt cách nhau 5 phút: 2 vi phạm độc lập.
5. Vượt qua nửa đêm (across midnight): 1 vi phạm kéo dài từ 23:45 sang 00:30 hôm sau (45 phút).

Chưa refine chi tiết — Tier Later, ước lượng thô 3 SP.
"""

import json
import sys
from pathlib import Path
from typing import Any

# Thêm thư mục backend vào sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from app.cold_chain import (
    ColdChainAnalysisRequest,
    TemperatureReading,
    analyze_cold_chain,
)
from app.routers.cold_chain import analyze_temperature_readings


def load_scenarios_from_file(file_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Đọc tệp kịch bản chuỗi lạnh (JSON)."""
    if file_path is None:
        file_path = BASE_DIR / "data" / "cold_chain_scenarios.json"

    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Không tìm thấy tệp kịch bản: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("scenarios", [])


def run_cold_chain_scenarios_script(file_path: Path | str | None = None) -> None:
    """Đọc tệp kịch bản và gọi endpoint xử lý S-41, đối chiếu với đáp án mong đợi."""
    print("=" * 75)
    print("CHẠY SCRIPT ĐỌC TỆP KỊCH BẢN CHUỖI LẠNH & GỌI ENDPOINT S-41 (3 SP - TIER LATER)")
    print("=" * 75)

    scenarios = load_scenarios_from_file(file_path)
    print(f"-> Đã nạp thành công {len(scenarios)} kịch bản mẫu từ tệp cấu hình JSON.\n")

    passed_count = 0

    for i, sc in enumerate(scenarios, 1):
        sc_id = sc["id"]
        sc_name = sc["name"]
        expected_violations = sc["expected_violations"]
        expected_compliant = sc["expected_is_compliant"]
        readings_raw = sc["readings"]

        print(f"[{i}] {sc_name} (ID: {sc_id}):")
        print(f"    - Mô tả: {sc.get('description')}")
        print(f"    - Số điểm đo: {len(readings_raw)} điểm")

        # Chuẩn bị payload tương tự khi client gửi lên POST /cold-chain/analyze (S-41)
        req_readings = [
            TemperatureReading(timestamp=r["timestamp"], temperature=r["temperature"])
            for r in readings_raw
        ]
        payload = ColdChainAnalysisRequest(
            readings=req_readings,
            min_temp=sc.get("min_temp", 2.0),
            max_temp=sc.get("max_temp", 8.0),
            min_duration_minutes=sc.get("min_duration_minutes", 0.0),
        )

        # Gọi trực tiếp qua hàm router của endpoint S-41
        response = analyze_temperature_readings(payload)

        print(f"    - Kết quả API: Số vi phạm = {response.total_violations}, Đạt chuẩn = {response.is_compliant}")
        print(f"    - Thông điệp: '{response.summary_message}'")

        # Đối chiếu số vi phạm với đáp án mong đợi
        assert response.total_violations == expected_violations, (
            f"Lỗi: Số vi phạm thực tế {response.total_violations} != Mong đợi {expected_violations}"
        )
        assert response.is_compliant == expected_compliant

        # Đối chiếu chi tiết thời lượng nếu kịch bản có yêu cầu
        if "expected_duration_minutes" in sc:
            assert len(response.excursions) > 0
            actual_duration = response.excursions[0].duration_minutes
            exp_duration = sc["expected_duration_minutes"]
            assert actual_duration == exp_duration, (
                f"Lỗi thời lượng: Thực tế {actual_duration} phút != Mong đợi {exp_duration} phút"
            )
            print(f"    - Chi tiết vi phạm: Kéo dài {actual_duration} phút ({response.excursions[0].start_time} -> {response.excursions[0].end_time}), Max = {response.excursions[0].max_temperature}°C")

        # Với kịch bản 4: hai lần vượt cách nhau 5 phút
        if sc_id == "scenario_4_two_excursions_5min_gap":
            assert len(response.excursions) == 2
            exc1 = response.excursions[0]
            exc2 = response.excursions[1]
            print(f"    - Vi phạm 1: {exc1.start_time} -> {exc1.end_time} ({exc1.duration_minutes} phút)")
            print(f"    - Vi phạm 2: {exc2.start_time} -> {exc2.end_time} ({exc2.duration_minutes} phút)")

        # Với kịch bản 5: vượt qua nửa đêm
        if sc_id == "scenario_5_across_midnight":
            assert len(response.excursions) == 1
            exc = response.excursions[0]
            assert "2026-03-20" in exc.start_time and "2026-03-21" in exc.end_time
            print(f"    - Xuyên ngày thành công: Bắt đầu lúc {exc.start_time} -> Kết thúc lúc {exc.end_time} ({exc.duration_minutes} phút)")

        print("    -> ĐẠT (PASSED)\n")
        passed_count += 1

    print("=" * 75)
    print(f"KẾT QUẢ: ĐÃ THỰC THI {passed_count}/{len(scenarios)} KỊCH BẢN MẪU - 100% KHỚP ĐÁP ÁN!")
    print("=" * 75)


if __name__ == "__main__":
    run_cold_chain_scenarios_script()
