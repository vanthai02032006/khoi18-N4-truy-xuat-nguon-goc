"""Kiểm thử tự động bộ kịch bản giả lập cảm biến chuỗi lạnh S-41 (Pytest).

Kiểm tra thuật toán phát hiện vi phạm chuỗi lạnh theo các kịch bản viết sẵn
với đáp án biết trước (Ground Truth).
"""

from __future__ import annotations

from pathlib import Path
import pytest

try:
    from scripts.simulate_sensor_scenarios import (
        ApiClientWrapper,
        load_scenario,
        simulate_scenario,
    )
except ImportError:
    from backend.scripts.simulate_sensor_scenarios import (
        ApiClientWrapper,
        load_scenario,
        simulate_scenario,
    )

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "backend" / "data" / "cold_chain_scenarios"


@pytest.fixture
def sim_client():
    """Tạo client in-memory với CSDL SQLite và user admin."""
    return ApiClientWrapper(base_url=None, auth=("admin", "123456"))


def test_scenario_01_no_violation(sim_client):
    """Kịch bản 1: Không vượt ngưỡng -> Đáp án biết trước: 0 vi phạm."""
    scenario_file = SCENARIOS_DIR / "scenario_01_no_violation.json"
    scenario = load_scenario(scenario_file)
    result = simulate_scenario(scenario, sim_client)

    assert result["is_passed"] is True
    assert result["actual_violations_count"] == 0
    assert result["expected_violations_count"] == 0
    assert result["breach_episodes_found"] == 0


def test_scenario_02_breach_10m(sim_client):
    """Kịch bản 2: Vượt 10 phút (< 30 phút cửa sổ vi phạm) -> Đáp án biết trước: 0 vi phạm."""
    scenario_file = SCENARIOS_DIR / "scenario_02_breach_10m.json"
    scenario = load_scenario(scenario_file)
    result = simulate_scenario(scenario, sim_client)

    assert result["is_passed"] is True
    assert result["actual_violations_count"] == 0
    assert result["expected_violations_count"] == 0
    assert result["breach_episodes_found"] == 0


def test_scenario_03_breach_45m(sim_client):
    """Kịch bản 3: Vượt 45 phút (>= 30 phút ngưỡng vi phạm) -> Đáp án biết trước: 1 vi phạm."""
    scenario_file = SCENARIOS_DIR / "scenario_03_breach_45m.json"
    scenario = load_scenario(scenario_file)
    result = simulate_scenario(scenario, sim_client)

    assert result["is_passed"] is True
    assert result["actual_violations_count"] == 1
    assert result["expected_violations_count"] == 1
    assert result["breach_episodes_found"] == 1
    assert result["episodes_detail"][0]["duration_minutes"] == 45.0
    assert result["episodes_detail"][0]["peak_temperature"] == 14.8
    assert result["episodes_detail"][0]["start"] == "2026-10-20T10:15:00+00:00"
    assert result["episodes_detail"][0]["end"] == "2026-10-20T11:00:00+00:00"


def test_scenario_04_two_spikes_under_5m_gap(sim_client):
    """Kịch bản 4: Hai lần vượt cách nhau dưới 5 phút (3 phút) -> Tính là 1 vi phạm gộp (40 phút)."""
    scenario_file = SCENARIOS_DIR / "scenario_04_two_spikes_5m_gap.json"
    scenario = load_scenario(scenario_file)
    result = simulate_scenario(scenario, sim_client)

    assert result["is_passed"] is True
    assert result["actual_violations_count"] == 1
    assert result["expected_violations_count"] == 1
    assert result["breach_episodes_found"] == 1
    assert result["episodes_detail"][0]["duration_minutes"] == 40.0
    assert result["episodes_detail"][0]["peak_temperature"] == 12.8


def test_scenario_04b_separate_spikes_over_5m_gap(sim_client):
    """Kịch bản 4B: Hai lần vượt cách nhau trên 5 phút (15 phút) -> Tính là 2 vi phạm độc lập."""
    scenario_file = SCENARIOS_DIR / "scenario_04b_separate_spikes_over_5m.json"
    scenario = load_scenario(scenario_file)
    result = simulate_scenario(scenario, sim_client)

    assert result["is_passed"] is True
    assert result["actual_violations_count"] == 2
    assert result["expected_violations_count"] == 2
    assert result["breach_episodes_found"] == 2
    # Đợt 1 kéo dài 35 phút
    assert result["episodes_detail"][0]["duration_minutes"] == 35.0
    assert result["episodes_detail"][0]["peak_temperature"] == 13.5
    # Đợt 2 kéo dài 35 phút
    assert result["episodes_detail"][1]["duration_minutes"] == 35.0
    assert result["episodes_detail"][1]["peak_temperature"] == 14.0


def test_scenario_05_midnight_crossing(sim_client):
    """Kịch bản 5: Vượt qua nửa đêm (23:45 -> 00:30) -> Đáp án biết trước: 1 vi phạm."""
    scenario_file = SCENARIOS_DIR / "scenario_05_midnight_crossing.json"
    scenario = load_scenario(scenario_file)
    result = simulate_scenario(scenario, sim_client)

    assert result["is_passed"] is True
    assert result["actual_violations_count"] == 1
    assert result["expected_violations_count"] == 1
    assert result["breach_episodes_found"] == 1
    assert result["episodes_detail"][0]["duration_minutes"] == 45.0
    assert result["episodes_detail"][0]["peak_temperature"] == 14.2


def test_all_scenarios_in_catalog(sim_client):
    """Kiểm tra toàn bộ danh mục kịch bản chuẩn trong catalog."""
    catalog_file = SCENARIOS_DIR / "scenarios_catalog.json"
    assert catalog_file.is_file()

    scenario_files = sorted(SCENARIOS_DIR.glob("scenario_*.json"))
    assert len(scenario_files) >= 5

    for sf in scenario_files:
        scenario = load_scenario(sf)
        result = simulate_scenario(scenario, sim_client)
        assert result["is_passed"] is True, f"Kịch bản {sf.name} không khớp đáp án biết trước!"
