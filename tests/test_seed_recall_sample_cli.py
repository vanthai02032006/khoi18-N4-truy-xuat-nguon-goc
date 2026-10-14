"""Kiểm thử **đường dòng lệnh** của script dựng kịch bản thu hồi mẫu (S-22).

Bổ sung cho `tests/test_recall_enumeration_benchmark.py`: bộ test kia gọi
``seed_recall_sample(session)`` **trong tiến trình** với SQLite in-memory, nên chưa chạm
tới đúng thứ mà tiêu chí nghiệm thu số 1 nói tới — **chạy script**:

1. ``python scripts/seed_recall_sample.py`` dựng được đồ thị **bốn tầng qua ba tổ chức**
   với lô **gộp từ nhiều nguồn** và lô **đã tiêu thụ**, rồi in phạm vi thu hồi của từng
   lô gốc để người đọc đối chiếu tệp đáp án đếm tay.
2. Chạy lại lần hai cho **cùng kết quả** (idempotent & deterministic).
3. Script **từ chối chạy** khi môi trường là dữ liệu thật (``APP_ENV``/``IS_PRODUCTION``)
   và không để lại file CSDL nào.

Các ca này chạy script như **tiến trình riêng** và trỏ CSDL sang file tạm bằng biến môi
trường ``TTCS_DATABASE_URL``, nên không đụng tới ``backend/ttcs.db``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_SCRIPT = REPO_ROOT / "backend" / "scripts" / "seed_recall_sample.py"
ANSWER_KEY_PATH = REPO_ROOT / "backend" / "data" / "recall_benchmark.json"

#: Prefix định danh mọi lô của kịch bản mẫu.
SAMPLE_BATCH_PREFIX = "LOT-S22-"

#: Dòng lô gốc: "● Lô gốc LOT-S22-A1 còn 250.0 kg → 8 hậu duệ qua 3 tầng, ...".
ROOT_LINE = re.compile(
    r"Lô gốc (?P<code>[\w-]+) còn (?P<remaining>[\d.]+) kg → (?P<count>\d+) hậu duệ "
    r"qua (?P<max_depth>\d+) tầng, còn thu hồi được (?P<descendant_remaining>[\d.]+) kg "
    r"\(cả lô gốc: (?P<total_remaining>[\d.]+) kg, đã ra thị trường: (?P<consumed>[\d.]+) kg\)"
)

#: Dòng hậu duệ: "- tầng 1 | LOT-S22-B11 | Cong Ty Che Bien Mekong | còn 150.0 kg | đã bán 0.0 kg".
ENTRY_LINE = re.compile(
    r"- tầng (?P<depth>\d+) \| (?P<code>[\w-]+) \| (?P<organization>.+?) \| "
    r"còn (?P<remaining>[\d.]+) kg \| đã bán (?P<consumed>[\d.]+) kg"
)


@pytest.fixture(scope="module")
def answer_key() -> dict[str, Any]:
    """Tệp đáp án đếm tay đi kèm kịch bản (hai người đếm độc lập)."""
    with open(ANSWER_KEY_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _run_seed_script(db_file: Path, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    """Chạy ``seed_recall_sample.py`` như tiến trình riêng, CSDL trỏ về file tạm."""
    env = dict(os.environ)
    env["TTCS_DATABASE_URL"] = f"sqlite:///{db_file.as_posix()}"
    env["APP_ENV"] = "development"
    env["PYTHONUTF8"] = "1"
    for key in ("IS_PRODUCTION", "ENV", "ENVIRONMENT"):
        env.pop(key, None)
    env.update(env_overrides)

    return subprocess.run(
        [sys.executable, str(SEED_SCRIPT)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=180,
    )


def _read_sample_batches(db_file: Path) -> dict[str, dict[str, Any]]:
    """Đọc thẳng bảng ``batches`` của CSDL vừa seed, không qua tầng ORM."""
    with sqlite3.connect(db_file) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT batch_code, quantity, consumed_quantity, status, current_holder_org "
            "FROM batches WHERE batch_code LIKE ? ORDER BY batch_code",
            (f"{SAMPLE_BATCH_PREFIX}%",),
        ).fetchall()
    return {row["batch_code"]: dict(row) for row in rows}


def _read_merge_sources(db_file: Path) -> dict[str, list[str]]:
    """Lô gộp -> danh sách mã lô nguồn, đọc từ bảng ``batch_relations``."""
    with sqlite3.connect(db_file) as connection:
        rows = connection.execute(
            "SELECT child.batch_code AS child_code, parent.batch_code AS parent_code "
            "FROM batch_relations AS relation "
            "JOIN batches AS child ON child.id = relation.child_batch_id "
            "JOIN batches AS parent ON parent.id = relation.parent_batch_id "
            "WHERE child.batch_code LIKE ?",
            (f"{SAMPLE_BATCH_PREFIX}%",),
        ).fetchall()

    sources: dict[str, list[str]] = {}
    for child_code, parent_code in rows:
        sources.setdefault(child_code, []).append(parent_code)
    return {code: sorted(parents) for code, parents in sources.items()}


def _parse_recall_scope(stdout: str) -> dict[str, dict[str, Any]]:
    """Bóc phần "PHẠM VI THU HỒI" do script in ra thành cấu trúc để đối chiếu đáp án."""
    scopes: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None

    for line in stdout.splitlines():
        root_match = ROOT_LINE.search(line)
        if root_match:
            current = {
                "root_code": root_match["code"],
                "root_remaining_quantity": float(root_match["remaining"]),
                "descendant_count": int(root_match["count"]),
                "max_depth": int(root_match["max_depth"]),
                "descendant_remaining_quantity": float(root_match["descendant_remaining"]),
                "total_remaining_quantity": float(root_match["total_remaining"]),
                "consumed_quantity": float(root_match["consumed"]),
                "descendants": [],
            }
            scopes[root_match["code"]] = current
            continue

        entry_match = ENTRY_LINE.search(line)
        if entry_match and current is not None:
            current["descendants"].append(
                {
                    "batch_code": entry_match["code"],
                    "depth": int(entry_match["depth"]),
                    "organization": entry_match["organization"],
                    "remaining_quantity": float(entry_match["remaining"]),
                    "consumed_quantity": float(entry_match["consumed"]),
                }
            )

    return scopes


# --------------------------------------------------------------------------- AC 1 ---
def test_seed_script_cli_builds_four_tier_graph(
    tmp_path: Path, answer_key: dict[str, Any]
) -> None:
    """AC 1: Chạy script qua dòng lệnh là CSDL có đồ thị 4 tầng qua 3 tổ chức khớp đáp án."""
    db_file = tmp_path / "seed_cli.db"
    completed = _run_seed_script(db_file)

    assert completed.returncode == 0, completed.stderr
    assert db_file.exists(), "Script phải tạo CSDL tại đường dẫn của TTCS_DATABASE_URL"
    assert (
        "4 tầng - 3 tổ chức - 18 lô, 6 quan hệ gộp nhiều nguồn, 1 lô đã tiêu thụ"
        in completed.stdout
    )

    graph = answer_key["graph_structure"]
    batches = _read_sample_batches(db_file)
    assert len(batches) == graph["totals"]["batch_count"] == 18
    assert len(graph["tiers"]) == 4
    assert {row["current_holder_org"] for row in batches.values()} == set(graph["organizations"])

    for tier in graph["tiers"]:
        for expected in tier["batches"]:
            row = batches[expected["batch_code"]]
            assert row["quantity"] == pytest.approx(expected["quantity"])
            assert (row["consumed_quantity"] or 0.0) == pytest.approx(
                expected["consumed_quantity"]
            )
            assert row["status"] == expected["status"]
            assert row["current_holder_org"] == tier["organization"]

    # Lô gộp từ nhiều nguồn: khớp đúng cha trong bảng quan hệ và có từ hai nguồn trở lên.
    merges = _read_merge_sources(db_file)
    for merged_code, sources in graph["merge_sources"].items():
        assert len(sources) >= 2, f"Lô gộp {merged_code} phải nhận từ ít nhất hai nguồn"
        assert merges[merged_code] == sorted(sources)

    # Lô đã tiêu thụ: bán hết nên còn 0 kg và mang trạng thái consumed.
    sold_out = batches["LOT-S22-F12"]
    assert sold_out["status"] == "consumed"
    assert sold_out["quantity"] == pytest.approx(0.0)
    assert sold_out["consumed_quantity"] == pytest.approx(150.0)


# --------------------------------------------------------------------------- AC 2 ---
def test_seed_script_cli_prints_recall_scope_matching_hand_count(
    tmp_path: Path, answer_key: dict[str, Any]
) -> None:
    """AC 2: Phần script in ra phải khớp 100% đáp án đếm tay của cả ba lô gốc."""
    db_file = tmp_path / "seed_cli.db"
    completed = _run_seed_script(db_file)
    assert completed.returncode == 0, completed.stderr

    scopes = _parse_recall_scope(completed.stdout)
    ground_truth: dict[str, dict[str, Any]] = answer_key["ground_truth_recall_scope"]
    assert sorted(scopes) == sorted(ground_truth), "Script phải in phạm vi của mọi lô gốc"

    for root_code, expected in ground_truth.items():
        printed = scopes[root_code]
        assert printed["root_code"] == root_code
        assert printed["root_remaining_quantity"] == pytest.approx(
            expected["root"]["remaining_quantity"]
        )
        # Dòng của lô gốc in tổng "đã ra thị trường" của CẢ phạm vi (lô gốc + hậu duệ),
        # đúng khoá `consumed_quantity` ở cấp phạm vi trong tệp đáp án.
        assert printed["consumed_quantity"] == pytest.approx(expected["consumed_quantity"])
        assert printed["descendant_count"] == expected["descendant_count"]
        assert printed["descendant_remaining_quantity"] == pytest.approx(
            expected["descendant_remaining_quantity"]
        )
        assert printed["total_remaining_quantity"] == pytest.approx(
            expected["total_remaining_quantity"]
        )
        assert printed["max_depth"] == max(
            entry["depth"] for entry in expected["descendants"]
        )

        assert [entry["batch_code"] for entry in printed["descendants"]] == [
            entry["batch_code"] for entry in expected["descendants"]
        ], f"Danh sách hậu duệ (và thứ tự theo tầng) của {root_code} phải khớp đáp án"

        for printed_entry, expected_entry in zip(
            printed["descendants"], expected["descendants"]
        ):
            assert printed_entry["depth"] == expected_entry["depth"]
            assert printed_entry["organization"] == expected_entry["organization"]
            assert printed_entry["remaining_quantity"] == pytest.approx(
                expected_entry["remaining_quantity"]
            )
            assert printed_entry["consumed_quantity"] == pytest.approx(
                expected_entry["consumed_quantity"]
            )

    assert "[đã hết hàng]" in completed.stdout, "Lô bán hết phải được đánh dấu trong phần in"


def test_seed_script_cli_rerun_keeps_same_result(tmp_path: Path) -> None:
    """Chạy lại script cho cùng kết quả, không nhân đôi dữ liệu mẫu."""
    db_file = tmp_path / "seed_cli.db"
    first = _run_seed_script(db_file)
    second = _run_seed_script(db_file)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "Đã dọn dẹp 18 lô mẫu cũ" in second.stdout
    assert len(_read_sample_batches(db_file)) == 18
    assert _parse_recall_scope(first.stdout) == _parse_recall_scope(second.stdout), (
        "Hai lần chạy phải in ra phạm vi thu hồi giống hệt nhau"
    )


# ----------------------------------------------------------------- Chặn production ---
@pytest.mark.parametrize(
    ("env_overrides", "expected_detail"),
    [
        ({"APP_ENV": "production"}, "APP_ENV='production'"),
        ({"APP_ENV": "prod"}, "APP_ENV='prod'"),
        ({"APP_ENV": "live"}, "APP_ENV='live'"),
        ({"APP_ENV": "development", "IS_PRODUCTION": "true"}, "IS_PRODUCTION=true"),
    ],
)
def test_seed_script_cli_refuses_real_data_environment(
    tmp_path: Path, env_overrides: dict[str, str], expected_detail: str
) -> None:
    """Script tuyệt đối không chạy trên môi trường dữ liệu thật và không tạo CSDL."""
    db_file = tmp_path / "must_not_be_created.db"
    completed = _run_seed_script(db_file, **env_overrides)

    assert completed.returncode == 1
    assert "NGUY HIỂM" in completed.stderr
    assert expected_detail in completed.stderr
    assert not db_file.exists(), "Script bị chặn thì không được tạo file CSDL"
