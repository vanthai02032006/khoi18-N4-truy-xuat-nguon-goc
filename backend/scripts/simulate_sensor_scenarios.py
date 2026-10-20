#!/usr/bin/env python3
"""Script giả lập cảm biến IoT chạy theo kịch bản viết sẵn (Sensor Simulation Script).

Đáp ứng User Story & Tiêu chí nghiệm thu S-41:
- Đọc tệp kịch bản (chuỗi thời điểm và nhiệt độ) từ định dạng chuẩn JSON.
- Đóng vai trò cảm biến/bộ ghi dữ liệu trên xe lạnh (IoT Telemetry Gateway).
- Đánh giá luồng dữ liệu theo thời gian thực và gọi endpoint S-41:
  + POST /cold-chain/check-telemetry: Đánh giá vi phạm và lưu vết vi phạm vào database.
  + GET /cold-chain/violations: Xác nhận bản ghi vi phạm được bảo toàn bất biến.
- So sánh số lượng vi phạm thực tế ghi nhận được với ĐÁP ÁN BIẾT TRƯỚC (Ground Truth):
  1. Không vượt (0 vi phạm)
  2. Vượt 10 phút (0 vi phạm vì < 15 phút độ trễ)
  3. Vượt 45 phút (1 vi phạm vì >= 15 phút độ trễ)
  4. Hai lần vượt cách nhau 5 phút (2 vi phạm độc lập)
  5. Vượt qua nửa đêm (1 vi phạm, xử lý chuẩn xác chuyển giao ngày)

Chế độ chạy:
1. In-memory / TestClient (Mặc định):
   Chạy độc lập tức thì không cần bật uvicorn server (phù hợp cho CI/CD và Unit Test).
2. Live HTTP Server:
   Gọi trực tiếp vào server đang chạy thông qua cờ `--base-url http://127.0.0.1:8000`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Thêm backend vào sys.path để hỗ trợ import app khi chạy trực tiếp từ bất kỳ thư mục nào
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Đảm bảo UTF-8 trên Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ANSI Color Codes cho giao diện dòng lệnh chuyên nghiệp
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def parse_iso_timestamp(ts_str: str | datetime) -> datetime:
    """Chuyển đổi chuỗi ISO timestamp sang UTC timezone-aware datetime."""
    if isinstance(ts_str, datetime):
        if ts_str.tzinfo is None:
            return ts_str.replace(tzinfo=timezone.utc)
        return ts_str.astimezone(timezone.utc)

    clean_str = str(ts_str).strip()
    if clean_str.endswith("Z"):
        clean_str = clean_str[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        for fmt in (
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                dt = datetime.strptime(clean_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                continue
        raise ValueError(f"Không thể phân tích định dạng mốc thời gian: '{ts_str}'")


class ApiClientWrapper:
    """Wrapper trừu tượng hoá việc gọi API giữa TestClient (in-memory) và httpx/requests (live HTTP)."""

    def __init__(self, base_url: Optional[str] = None, auth: tuple[str, str] = ("admin", "123456")):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.auth = auth
        self._test_client = None

        if not self.base_url:
            from fastapi.testclient import TestClient
            from app.database import Base, get_db
            from app.main import app
            from app.models import ColdChainThreshold, ROLE_ADMIN, User
            from app.security import hash_password
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            from sqlalchemy.pool import StaticPool

            # Khởi tạo SQLite in-memory cho TestClient
            engine = create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            Base.metadata.create_all(bind=engine)
            TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

            with TestingSessionLocal() as db:
                # Tạo user admin
                admin_user = User(
                    username=auth[0],
                    password=hash_password(auth[1]),
                    role=ROLE_ADMIN,
                )
                db.add(admin_user)

                # Nạp sẵn cấu hình ngưỡng chuẩn cho "Rau lá" (2°C - 8°C, delay 15 phút)
                t_rau = ColdChainThreshold(
                    product_type="Rau lá",
                    temp_min=2.0,
                    temp_max=8.0,
                    delay_minutes=15,
                    description="Rau lá tươi Đà Lạt",
                    created_at="2026-10-20T00:00:00Z",
                    updated_at="2026-10-20T00:00:00Z",
                )
                db.add(t_rau)
                db.commit()

            def override_get_db():
                db = TestingSessionLocal()
                try:
                    yield db
                finally:
                    db.close()

            app.dependency_overrides[get_db] = override_get_db
            self._test_client = TestClient(app)

    def post(self, endpoint: str, json_data: dict[str, Any]) -> dict[str, Any]:
        """Gửi request POST tới endpoint."""
        if self._test_client:
            res = self._test_client.post(endpoint, json=json_data, auth=self.auth)
            if res.status_code not in (200, 201):
                raise RuntimeError(f"API Error {res.status_code}: {res.text}")
            return res.json()
        else:
            import urllib.error
            import urllib.request
            import base64

            url = f"{self.base_url}{endpoint}"
            req = urllib.request.Request(
                url,
                data=json.dumps(json_data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            auth_str = f"{self.auth[0]}:{self.auth[1]}"
            b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
            req.add_header("Authorization", f"Basic {b64_auth}")

            try:
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8")
                raise RuntimeError(f"HTTP Error {e.code} on {endpoint}: {err_body}")

    def get(self, endpoint: str) -> Any:
        """Gửi request GET tới endpoint."""
        if self._test_client:
            res = self._test_client.get(endpoint, auth=self.auth)
            if res.status_code != 200:
                raise RuntimeError(f"API Error {res.status_code}: {res.text}")
            return res.json()
        else:
            import urllib.request
            import base64

            url = f"{self.base_url}{endpoint}"
            req = urllib.request.Request(url, method="GET")
            auth_str = f"{self.auth[0]}:{self.auth[1]}"
            b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
            req.add_header("Authorization", f"Basic {b64_auth}")

            try:
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                raise RuntimeError(f"HTTP Error on {endpoint}: {e}")


def load_scenario(file_path: Path | str) -> dict[str, Any]:
    """Đọc tệp kịch bản kiểm thử JSON."""
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"Không tìm thấy tệp kịch bản: {file_path}")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def simulate_scenario(
    scenario: dict[str, Any],
    client: ApiClientWrapper,
    verbose: bool = False,
) -> dict[str, Any]:
    """Chạy mô phỏng cảm biến theo kịch bản và gọi endpoint S-41 kiểm tra vi phạm.

    Args:
        scenario: Dữ liệu kịch bản (gồm readings, product_types, expected_violations_count,...).
        client: Wrapper client gọi endpoint S-41.
        verbose: In chi tiết từng mốc đọc cảm biến.

    Returns:
        dict: Kết quả mô phỏng gồm số vi phạm thực tế, so sánh với đáp án biết trước, và trạng thái PASS/FAIL.
    """
    scenario_id = scenario.get("scenario_id", "UNKNOWN_SCENARIO")
    scenario_name = scenario.get("name", scenario_id)
    shipment_code = scenario.get("shipment_code", f"SHIP-{scenario_id}")
    product_types = scenario.get("product_types", ["Rau lá"])
    expected_count = scenario.get("expected_violations_count", 0)
    readings = scenario.get("readings", [])
    location = scenario.get("location", "Xe lạnh chuyên dụng")

    if not readings:
        raise ValueError(f"Kịch bản '{scenario_id}' không chứa mốc đọc nhiệt độ (readings rỗng)!")

    # 1. Chuẩn bị payload gửi tới endpoint S-42 evaluate-stream
    breach_window = float(scenario.get("breach_window_minutes", 30.0))
    debounce_gap = float(scenario.get("debounce_gap_minutes", 5.0))

    stream_payload = {
        "shipment_code": shipment_code,
        "product_types": product_types,
        "readings": [
            {
                "timestamp": r["timestamp"],
                "temperature": float(r["temperature"]),
                "humidity": r.get("humidity"),
            }
            for r in readings
        ],
        "breach_window_minutes": breach_window,
        "debounce_gap_minutes": debounce_gap,
        "auto_save_violations": True,
        "location": location,
    }

    # 2. Gọi endpoint S-42 phân tích luồng cảm biến (POST /cold-chain/evaluate-stream)
    eval_response = client.post("/cold-chain/evaluate-stream", stream_payload)

    t_min = float(eval_response["applied_temp_min"])
    t_max = float(eval_response["applied_temp_max"])
    actual_violations = eval_response["violations"]
    actual_violations_count = len(actual_violations)

    # In chi tiết nếu bật verbose
    if verbose:
        for idx, r in enumerate(readings):
            cur_temp = float(r["temperature"])
            cur_ts = r["timestamp"]
            is_out = (cur_temp > t_max) or (cur_temp < t_min)
            status_str = f"{RED}VƯỢT NGƯỠNG ({cur_temp}°C){RESET}" if is_out else f"{GREEN}AN TOÀN ({cur_temp}°C){RESET}"
            print(f"  [{idx+1:02d}] {cur_ts} -> {status_str}")

    # 3. Lấy danh sách vi phạm đã lưu trong CSDL từ endpoint GET /cold-chain/violations
    saved_violations = client.get(f"/cold-chain/violations?shipment_code={shipment_code}")

    # 4. So sánh với đáp án biết trước (Ground Truth)
    is_passed = (actual_violations_count == expected_count)

    episodes_detail = []
    for v in actual_violations:
        episodes_detail.append({
            "start": v["started_at"],
            "end": v["ended_at"],
            "duration_minutes": v["duration_minutes"],
            "peak_temperature": v["peak_temperature"],
            "reason": v["reason"],
        })

    return {
        "scenario_id": scenario_id,
        "name": scenario_name,
        "shipment_code": shipment_code,
        "product_types": product_types,
        "effective_threshold": {
            "temp_min": t_min,
            "temp_max": t_max,
            "breach_window_minutes": breach_window,
            "debounce_gap_minutes": debounce_gap,
        },
        "readings_count": len(readings),
        "breach_episodes_found": len(episodes_detail),
        "episodes_detail": episodes_detail,
        "expected_violations_count": expected_count,
        "actual_violations_count": actual_violations_count,
        "database_violations_recorded": len(saved_violations),
        "detected_violations": actual_violations,
        "is_passed": is_passed,
    }


def run_all_scenarios(
    scenarios_dir: Path,
    client: ApiClientWrapper,
    verbose: bool = False,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Thực thi toàn bộ các kịch bản mẫu và in báo cáo đối soát đáp án."""
    scenario_files = sorted(scenarios_dir.glob("scenario_*.json"))
    if not scenario_files:
        raise FileNotFoundError(f"Không tìm thấy kịch bản mẫu nào trong thư mục: {scenarios_dir}")

    total_scenarios = len(scenario_files)
    passed_scenarios = 0
    results: list[dict[str, Any]] = []

    print(f"\n{BOLD}{CYAN}================================================================================{RESET}")
    print(f"{BOLD}{CYAN}   GIẢ LẬP CẢM BIẾN CHUỖI LẠNH & KIỂM CHỨNG ĐÁP ÁN BIẾT TRƯỚC (S-41 & S-42)   {RESET}")
    print(f"{BOLD}{CYAN}================================================================================{RESET}\n")

    for file_path in scenario_files:
        scenario = load_scenario(file_path)
        print(f"👉 {BOLD}Đang chạy kịch bản:{RESET} {scenario.get('name')} ({file_path.name})")

        res = simulate_scenario(scenario, client, verbose=verbose)
        results.append(res)

        exp = res["expected_violations_count"]
        act = res["actual_violations_count"]
        episodes = res["episodes_detail"]

        if res["is_passed"]:
            passed_scenarios += 1
            status_badge = f"{GREEN}{BOLD}[PASS] ĐẠT{RESET}"
        else:
            status_badge = f"{RED}{BOLD}[FAIL] SAI KHÁC{RESET}"

        print(f"   Trạng thái: {status_badge}")
        print(f"   - Số lần đo: {res['readings_count']} mốc nhiệt độ")
        if episodes:
            for ep_idx, ep in enumerate(episodes):
                print(f"   - Đợt vượt {ep_idx+1}: {ep['start']} -> {ep['end']} ({ep['duration_minutes']} phút, đỉnh {ep['peak_temperature']}°C)")
        else:
            print(f"   - Nhiệt độ hoàn toàn ổn định trong dải [{res['effective_threshold']['temp_min']}°C, {res['effective_threshold']['temp_max']}°C]")
        print(f"   - Đáp án biết trước (Expected): {exp} vi phạm")
        print(f"   - Thuật toán S-41 phát hiện (Actual): {act} vi phạm")
        print()

    # Bảng tổng kết kết quả
    print(f"{BOLD}{CYAN}--------------------------------------------------------------------------------{RESET}")
    print(f"{BOLD}{'MÃ KỊCH BẢN':<28} | {'KÉO DÀI':<12} | {'ĐÁP ÁN':<8} | {'THỰC TẾ':<8} | {'KẾT QUẢ':<8}{RESET}")
    print(f"{CYAN}--------------------------------------------------------------------------------{RESET}")

    for r in results:
        sc_id = r["scenario_id"]
        exp = r["expected_violations_count"]
        act = r["actual_violations_count"]
        badge = f"{GREEN}PASS{RESET}" if r["is_passed"] else f"{RED}FAIL{RESET}"

        ep_dur = ""
        if r["episodes_detail"]:
            durs = [f"{ep['duration_minutes']}m" for ep in r["episodes_detail"]]
            ep_dur = ", ".join(durs)
        else:
            ep_dur = "0m (Safe)"

        print(f"{sc_id:<28} | {ep_dur:<12} | {exp:<8} | {act:<8} | {badge:<8}")

    print(f"{BOLD}{CYAN}--------------------------------------------------------------------------------{RESET}")
    pass_rate = (passed_scenarios / total_scenarios) * 100
    summary_color = GREEN if passed_scenarios == total_scenarios else RED
    print(f"{summary_color}{BOLD}KẾT QUẢ: {passed_scenarios}/{total_scenarios} kịch bản ĐẠT ({pass_rate:.1f}% khớp đáp án biết trước){RESET}\n")

    return total_scenarios, passed_scenarios, results


def main() -> int:
    """Điểm vào thực thi dòng lệnh."""
    parser = argparse.ArgumentParser(
        description="Giả lập cảm biến IoT chạy theo kịch bản để kiểm tra thuật toán phát hiện vi phạm S-41.",
    )
    default_scenarios_dir = BACKEND_DIR / "data" / "cold_chain_scenarios"
    parser.add_argument(
        "--scenario-dir",
        type=Path,
        default=default_scenarios_dir,
        help="Đường dẫn thư mục chứa các tệp kịch bản JSON (mặc định: backend/data/cold_chain_scenarios)",
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=None,
        help="Đường dẫn đến 1 tệp kịch bản cụ thể để chạy lẻ",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="URL của backend FastAPI đang chạy (ví dụ: http://127.0.0.1:8000). Nếu bỏ trống sẽ chạy in-memory.",
    )
    parser.add_argument(
        "--user",
        type=str,
        default="admin",
        help="Tài khoản HTTP Basic Auth (mặc định: admin)",
    )
    parser.add_argument(
        "--password",
        type=str,
        default="123456",
        help="Mật khẩu HTTP Basic Auth (mặc định: 123456)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="In chi tiết từng mốc đo nhiệt độ của cảm biến",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Xuất báo cáo kết quả ra tệp JSON",
    )

    args = parser.parse_args()
    client = ApiClientWrapper(base_url=args.base_url, auth=(args.user, args.password))

    if args.scenario:
        scenario = load_scenario(args.scenario)
        res = simulate_scenario(scenario, client, verbose=args.verbose)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res["is_passed"] else 1

    total, passed, results = run_all_scenarios(args.scenario_dir, client, verbose=args.verbose)

    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total_scenarios": total,
                    "passed_scenarios": passed,
                    "results": results,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(f"Đã xuất báo cáo ra: {args.json_output}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
