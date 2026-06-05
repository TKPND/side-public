"""Phase 61 Plan 05 unit tests for audit.py drift/intersection/DST/verdict logic.

Covers the 8 behaviors in 61-05-PLAN.md Task 1 <behavior>:
  1. detect_drift with identical cells → empty drift list.
  2. detect_drift with differing cells at same (pair, event) → epoch_drift.
  3. compute_intersection_dates returns intersection set across pairs.
  4. events_calendar_check flags dates for which event_dir_at returns None.
  5. All-clean → audit_verdict=PASS.
  6. drift_cells non-empty → audit_verdict=FAIL; exit code 2 without --force.
  7. run_dst_check parses pytest-json-report output correctly.
  8. DST failures with empty drift/missing still → audit_verdict=FAIL (wiring guarantee).

Run:
    uv run pytest scripts/v4.4/test_audit.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audit  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _cell(
    pair: str = "usdjpy",
    event: str = "fomc",
    epoch: str = "v4.1",
    *,
    window_offset=(1, 2, 3),
    dst_utc_hour=(18, 19),
    signal_dir=((2022, 3, 16, 1),),
    long_only=True,
    fold_size=16,
    event_count=16,
    fee_bps=(0.0, 2.0, 5.0),
    event_dates=((2022, 3, 16),),
    status: str = "populated",
) -> dict:
    """Build a minimal cell dict with the shape Plan 04 emits."""
    return {
        "pair": pair,
        "event": event,
        "epoch": epoch,
        "status": status,
        "source_path": f"docs/reports/{pair}/report.json",
        "dimensions": {
            "window_offset": list(window_offset),
            "dst_utc_hour": list(dst_utc_hour),
            "signal_dir": [list(sd) for sd in signal_dir],
            "long_only": long_only,
            "fold_size": fold_size,
            "event_count": event_count,
            "fee_bps": list(fee_bps),
        },
        "slot_grid": {"exit_type": ["none"], "hold_bars": [1], "n_slots": 96},
        "event_dates": [list(d) for d in event_dates],
    }


# ---------------------------------------------------------------------------
# Behaviors 1-2: detect_drift
# ---------------------------------------------------------------------------


def test_detect_drift_identical_cells_returns_empty():
    """Test 1: identical-dimension cells → no drift."""
    a = _cell(pair="usdjpy", event="fomc", epoch="v4.1")
    b = _cell(pair="usdjpy", event="fomc", epoch="v4.1")
    assert audit.detect_drift([a, b]) == []


def test_detect_drift_differing_dimensions_flags_epoch_drift():
    """Test 2: same (pair, event) with differing window_offset → drift entry."""
    a = _cell(pair="usdjpy", event="fomc", epoch="v4.1", window_offset=(1, 2, 3))
    b = _cell(pair="usdjpy", event="fomc", epoch="v4.2", window_offset=(1, 2, 3, 4))
    drift = audit.detect_drift([a, b])
    assert len(drift) >= 1
    kinds = {entry.get("drift_kind") for entry in drift}
    assert "epoch_drift" in kinds, f"expected epoch_drift entry, got {drift}"
    epoch_entry = next(e for e in drift if e.get("drift_kind") == "epoch_drift")
    assert epoch_entry["pair"] == "usdjpy"
    assert epoch_entry["event"] == "fomc"
    assert len(epoch_entry["hashes"]) == 2


def test_detect_drift_same_event_different_pairs_flags_pair_drift():
    """Additional coverage: pair_drift at same (event, epoch)."""
    a = _cell(pair="usdjpy", event="fomc", epoch="v4.2", window_offset=(1, 2))
    b = _cell(pair="audusd", event="fomc", epoch="v4.2", window_offset=(1, 2, 3))
    drift = audit.detect_drift([a, b])
    kinds = {entry.get("drift_kind") for entry in drift}
    assert "pair_drift" in kinds, f"expected pair_drift entry, got {drift}"


# ---------------------------------------------------------------------------
# Plan 61-07: split_structural_drift — CONFIG-03 intent asymmetry separation
# ---------------------------------------------------------------------------


def test_split_structural_drift_audusd_eurjpy_ecb_v42_moves_to_structural():
    """AUDUSD×EURJPY×ECB×v4.2 empty_source/populated pair_drift is structural."""
    audusd_cell = _cell(
        pair="audusd",
        event="ecb",
        epoch="v4.2",
        window_offset=(),
        signal_dir=(),
        event_dates=(),
        fold_size=0,
        event_count=0,
        status="empty_source",
    )
    eurjpy_cell = _cell(
        pair="eurjpy",
        event="ecb",
        epoch="v4.2",
        window_offset=(1, 2, 3),
        status="populated",
    )
    drift = audit.detect_drift([audusd_cell, eurjpy_cell])
    assert any(e.get("drift_kind") == "pair_drift" for e in drift), drift

    remaining, structural = audit.split_structural_drift(
        drift, [audusd_cell, eurjpy_cell]
    )
    assert remaining == []
    assert len(structural) == 1
    entry = structural[0]
    assert entry["epoch"] == "v4.2"
    assert entry["event"] == "ecb"
    assert entry["reason"] == "pair_drift_expected_structural_asymmetry"
    assert sorted(entry["pairs"]) == ["audusd", "eurjpy"]
    assert "empty_source" in entry["detail"] and "populated" in entry["detail"]


def test_split_structural_drift_verdict_pass_when_only_structural(tmp_path):
    """PASS if drift_cells=[] and dst_failures=[], even with 1 structural entry."""
    structural = [
        {
            "epoch": "v4.2",
            "event": "ecb",
            "reason": "pair_drift_expected_structural_asymmetry",
            "pairs": ["audusd", "eurjpy"],
            "detail": "audusd=empty_source vs eurjpy=populated (CONFIG-03 intent)",
        }
    ]
    result = audit.emit_drift_detected(
        tmp_path,
        drift_cells=[],
        intersections={},
        missing=[],
        dst_failures=[],
        structural_drift=structural,
    )
    assert result["audit_verdict"] == "PASS"
    assert result["structural_drift"] == structural
    assert result["drift_cells"] == []
    on_disk = json.loads((tmp_path / "drift_detected.json").read_text())
    assert on_disk["audit_verdict"] == "PASS"
    assert on_disk["structural_drift"] == structural


def test_split_structural_drift_genuine_pair_drift_still_fails(tmp_path):
    """Non-structural pair_drift (e.g. usdjpy vs audusd on fomc) → verdict=FAIL."""
    genuine = [
        {
            "event": "fomc",
            "epoch": "v4.2",
            "drift_kind": "pair_drift",
            "hash_groups": {"h1": ["usdjpy"], "h2": ["audusd"]},
        }
    ]
    result = audit.emit_drift_detected(
        tmp_path,
        drift_cells=genuine,
        intersections={},
        missing=[],
        dst_failures=[],
        structural_drift=[],
    )
    assert result["audit_verdict"] == "FAIL"
    assert result["phase_62_blocker"] is True


# ---------------------------------------------------------------------------
# Behaviors 3-4: intersection + calendar check
# ---------------------------------------------------------------------------


def test_compute_intersection_dates_returns_common_set_per_epoch():
    """Test 3: intersection across pairs per (event, epoch)."""
    cells = [
        _cell(
            pair="usdjpy",
            event="fomc",
            epoch="v4.1",
            event_dates=((2022, 3, 16), (2022, 5, 4), (2022, 6, 15)),
        ),
        _cell(
            pair="audusd",
            event="fomc",
            epoch="v4.1",
            event_dates=((2022, 3, 16), (2022, 5, 4)),
        ),
    ]
    result = audit.compute_intersection_dates(cells)
    # For (fomc, v4.1) both pairs should map to the intersection set
    key = ("fomc", "v4.1")
    assert key in result, f"expected {key} in intersections, got {list(result)}"
    per_pair = result[key]
    assert "usdjpy" in per_pair and "audusd" in per_pair
    # intersection = {(2022,3,16), (2022,5,4)}
    usdjpy_dates = {tuple(d) for d in per_pair["usdjpy"]["dates"]}
    assert (2022, 3, 16) in usdjpy_dates
    assert (2022, 5, 4) in usdjpy_dates
    assert (
        2022,
        6,
        15,
    ) not in usdjpy_dates  # not in audusd → excluded from intersection


def test_events_calendar_check_flags_missing_dates():
    """Test 4: intersection date with no events.rs entry → missing list populated."""
    # Inject a date not in events.rs calendar for FOMC
    intersections = {
        ("fomc", "v4.1"): {
            "usdjpy": {
                "dates": [(2099, 12, 31)],  # definitely not in events.rs
                "n_intersection": 1,
            },
        },
    }
    missing = audit.events_calendar_check(intersections)
    assert len(missing) == 1
    assert missing[0]["event"] == "fomc"
    assert missing[0]["date"] == [2099, 12, 31]


def test_events_calendar_check_known_date_returns_empty():
    """Positive path: real events.rs date → not missing."""
    intersections = {
        ("fomc", "v4.1"): {
            "usdjpy": {
                "dates": [(2022, 3, 16)],  # real FOMC date
                "n_intersection": 1,
            },
        },
    }
    missing = audit.events_calendar_check(intersections)
    assert missing == []


# ---------------------------------------------------------------------------
# Behaviors 5-6, 8: verdict + exit code + DST gate guarantee
# ---------------------------------------------------------------------------


def test_emit_drift_detected_all_clean_is_pass(tmp_path):
    """Test 5: drift=[], missing=[], dst_failures=[] → PASS."""
    result = audit.emit_drift_detected(
        output=tmp_path,
        drift_cells=[],
        intersections={},
        missing=[],
        dst_failures=[],
    )
    assert result["audit_verdict"] == "PASS"
    assert result["phase_62_blocker"] is False
    assert (tmp_path / "drift_detected.json").exists()


def test_emit_drift_detected_drift_non_empty_is_fail(tmp_path):
    """Test 6a: drift_cells non-empty → FAIL."""
    drift = [
        {
            "pair": "usdjpy",
            "event": "fomc",
            "drift_kind": "epoch_drift",
            "hashes": ["a", "b"],
            "epochs_diff": ["v4.1", "v4.2"],
        }
    ]
    result = audit.emit_drift_detected(
        output=tmp_path,
        drift_cells=drift,
        intersections={},
        missing=[],
        dst_failures=[],
    )
    assert result["audit_verdict"] == "FAIL"
    assert result["phase_62_blocker"] is True


def test_emit_drift_detected_dst_failures_non_empty_is_fail(tmp_path):
    """Test 8: dst_failures non-empty alone → FAIL (wiring guarantee)."""
    dst_failures = [
        {
            "nodeid": "scripts/v4.4/test_dst.py::test_x",
            "outcome": "failed",
            "longrepr": "AssertionError",
        }
    ]
    result = audit.emit_drift_detected(
        output=tmp_path,
        drift_cells=[],
        intersections={},
        missing=[],
        dst_failures=dst_failures,
    )
    assert result["audit_verdict"] == "FAIL", (
        "DST failures must flip verdict to FAIL even if drift/missing empty"
    )
    assert result["dst_failures"] == dst_failures


def test_main_fail_without_force_exits_2(tmp_path, monkeypatch):
    """Test 6b: main() exits with code 2 when FAIL and --force not set."""
    # Build a minimal drift-inducing scenario via direct args; patch build_audit_matrix
    # to return two conflicting cells at same (pair, event).
    cell_a = _cell(pair="usdjpy", event="fomc", epoch="v4.1", window_offset=(1,))
    cell_b = _cell(pair="usdjpy", event="fomc", epoch="v4.2", window_offset=(1, 2))
    fake_matrix = {
        "generated_at": "2026-01-01T00:00:00Z",
        "eurusd_compat": False,
        "eurusd_gap_note": "",
        "cells": [cell_a, cell_b],
    }
    monkeypatch.setattr(
        audit, "build_audit_matrix", lambda a, b, c=(): fake_matrix
    )
    # Stub DST subprocess — no failures
    monkeypatch.setattr(audit, "run_dst_check", lambda: [])

    argv = ["--output", str(tmp_path)]
    rc = audit.main(argv)
    assert rc == 2
    data = json.loads((tmp_path / "drift_detected.json").read_text())
    assert data["audit_verdict"] == "FAIL"


def test_main_fail_with_force_exits_0(tmp_path, monkeypatch):
    """Additional: --force overrides FAIL verdict → exit 0."""
    cell_a = _cell(pair="usdjpy", event="fomc", epoch="v4.1", window_offset=(1,))
    cell_b = _cell(pair="usdjpy", event="fomc", epoch="v4.2", window_offset=(1, 2))
    fake_matrix = {
        "generated_at": "2026-01-01T00:00:00Z",
        "eurusd_compat": False,
        "eurusd_gap_note": "",
        "cells": [cell_a, cell_b],
    }
    monkeypatch.setattr(
        audit, "build_audit_matrix", lambda a, b, c=(): fake_matrix
    )
    monkeypatch.setattr(audit, "run_dst_check", lambda: [])

    rc = audit.main(["--output", str(tmp_path), "--force"])
    assert rc == 0


# ---------------------------------------------------------------------------
# Behavior 7: run_dst_check parses pytest-json-report output
# ---------------------------------------------------------------------------


def test_run_dst_check_parses_failures_from_json_report(tmp_path, monkeypatch):
    """Test 7: fixture JSON with 2 failed tests → returned list length 2."""
    fixture = {
        "summary": {"total": 5, "passed": 3, "failed": 2},
        "tests": [
            {
                "nodeid": "test_dst.py::test_pass_a",
                "outcome": "passed",
                "call": {"longrepr": ""},
            },
            {
                "nodeid": "test_dst.py::test_pass_b",
                "outcome": "passed",
                "call": {"longrepr": ""},
            },
            {
                "nodeid": "test_dst.py::test_pass_c",
                "outcome": "passed",
                "call": {"longrepr": ""},
            },
            {
                "nodeid": "test_dst.py::test_fail_d",
                "outcome": "failed",
                "call": {"longrepr": "AssertionError: FOMC 2024-03-20 drift"},
            },
            {
                "nodeid": "test_dst.py::test_fail_e",
                "outcome": "failed",
                "call": {"longrepr": "AssertionError: ECB 2024-03-31 drift"},
            },
        ],
    }

    class _FakeResult:
        returncode = 1
        stdout = "fake pytest output"
        stderr = ""

    def _fake_subprocess_run(cmd, **kwargs):
        # Simulate pytest writing the JSON report to --json-report-file
        for arg in cmd:
            if isinstance(arg, str) and arg.startswith("--json-report-file="):
                target = Path(arg.split("=", 1)[1])
                target.write_text(json.dumps(fixture))
        return _FakeResult()

    monkeypatch.setattr(audit.subprocess, "run", _fake_subprocess_run)

    failures = audit.run_dst_check(test_module=Path("scripts/v4.4/test_dst.py"))
    assert len(failures) == 2
    node_ids = {f["nodeid"] for f in failures}
    assert node_ids == {"test_dst.py::test_fail_d", "test_dst.py::test_fail_e"}
    for f in failures:
        assert f["outcome"] == "failed"
        assert "AssertionError" in f["longrepr"]


def test_run_dst_check_all_passed_returns_empty(monkeypatch):
    """Positive: all tests pass → empty list."""
    fixture = {
        "summary": {"total": 3, "passed": 3, "failed": 0},
        "tests": [
            {"nodeid": f"test_pass_{i}", "outcome": "passed", "call": {"longrepr": ""}}
            for i in range(3)
        ],
    }

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_subprocess_run(cmd, **kwargs):
        for arg in cmd:
            if isinstance(arg, str) and arg.startswith("--json-report-file="):
                Path(arg.split("=", 1)[1]).write_text(json.dumps(fixture))
        return _FakeResult()

    monkeypatch.setattr(audit.subprocess, "run", _fake_subprocess_run)
    assert audit.run_dst_check() == []


def test_run_dst_check_timeout_returns_synthetic_error(monkeypatch):
    """Fail-safe: subprocess timeout → synthetic error entry (not empty)."""

    def _raise_timeout(cmd, **kwargs):
        raise audit.subprocess.TimeoutExpired(cmd, timeout=120)

    monkeypatch.setattr(audit.subprocess, "run", _raise_timeout)
    failures = audit.run_dst_check()
    assert len(failures) == 1
    assert failures[0]["outcome"] == "error"
    assert "timed out" in failures[0]["longrepr"].lower()


# ---------------------------------------------------------------------------
# Phase 65-02: infer_pair v4.2-<pair>/report.json parent-dir parse regression
# ---------------------------------------------------------------------------


def test_infer_pair_handles_v4_2_pairdir_layout(tmp_path):
    """Regression guard for Phase 65-01 parent-dir parse patch (CONTEXT.md D-20/D-21).

    Covers:
      - 4 pair (usdjpy/eurusd/audusd/eurjpy) ``v4.2-<pair>/report.json`` layout
        — parent-dir parse branch added in Phase 65-01.
      - Backcompat with existing ``PAIR_FROM_PATH_HINT`` dict entries
        (``v4.1-n-expansion`` → usdjpy, ``v3.9-cross-pair`` → eurusd).
      - ``cross_pair_summary.json`` None-return (guard against spurious matches).
      - ``v4.2_usdjpy_subset.json`` (underscore fixture naming owned by
        ``sign_breakdown._infer_pair``, not ``audit.infer_pair``) → None, to
        confirm the parent-dir branch does not misinterpret underscored stems.
    """
    # 1. Build v4.2-<pair>/report.json layouts for all 4 canonical pairs.
    expected = {
        "usdjpy": tmp_path / "v4.2-usdjpy" / "report.json",
        "eurusd": tmp_path / "v4.2-eurusd" / "report.json",
        "audusd": tmp_path / "v4.2-audusd" / "report.json",
        "eurjpy": tmp_path / "v4.2-eurjpy" / "report.json",
    }
    for pair, path in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
        assert audit.infer_pair(path) == pair, (
            f"parent-dir parse failed for {path}: expected {pair}"
        )

    # 2. Backcompat: real PAIR_FROM_PATH_HINT entries continue to resolve.
    hint_cases = {
        "v4.1-n-expansion": "usdjpy",
        "v3.9-cross-pair": "eurusd",
    }
    for hint, pair in hint_cases.items():
        hint_path = tmp_path / hint / "report.json"
        hint_path.parent.mkdir(parents=True, exist_ok=True)
        hint_path.write_text("{}")
        assert audit.infer_pair(hint_path) == pair, (
            f"PAIR_FROM_PATH_HINT regression: {hint} → expected {pair}"
        )

    # 3. cross_pair_summary.json → None (guard path).
    cross_path = tmp_path / "cross_pair_summary.json"
    cross_path.write_text("{}")
    assert audit.infer_pair(cross_path) is None

    # 4. Negative guard: underscored sign_breakdown-fixture name stays None.
    #    (audit.infer_pair must not misread "v4.2_usdjpy_subset.json" as v4.2-<pair>.)
    subset_path = tmp_path / "v4.2_usdjpy_subset.json"
    subset_path.write_text("{}")
    assert audit.infer_pair(subset_path) is None


# ---------------------------------------------------------------------------
# Phase 71-02: --v46-report flag + v4.6 branch (12-cell coverage incl. EURUSD)
# ---------------------------------------------------------------------------
#
# Plan 71-02 adds:
#   - `--v46-report` CLI flag (repeatable) feeding the fresh WFD per-pair/per-event
#     report.json layout (Phase 70 output, commit 2efa119).
#   - `v4.6` epoch branch in `build_audit_matrix` that disables the eurusd-skip
#     hardcode (eurusd_compat=FALSE) so that 4 pair × 3 event = 12 cells are emitted.
#   - `EPOCH_EVENT_DATES["v4.6"]` reusing the 2022-2023 calendars (D-CONTEXT
#     Phase 70 CSV truncation forced v4.6 data scope to 2022-2023).
#   - `infer_pair` extension to handle `.../per-pair/<pair>/<event>/report.json`
#     grandparent layout.
#
# RED tests below MUST FAIL on the current unmodified audit.py.
# ---------------------------------------------------------------------------


_FRESH_V46_PATHS: tuple[Path, ...] = tuple(
    Path("docs/reports/v4.6-verdict-resolution/per-pair") / pair / event / "report.json"
    for pair in ("audusd", "eurjpy", "eurusd", "usdjpy")
    for event in ("fomc", "ecb", "nfp")
)


def test_audit_v46_cli_flag_registered():
    """Test D: parse_args accepts --v46-report flag and stores Path values.

    On unmodified audit.py this raises SystemExit (argparse: unrecognized argument).
    """
    args = audit.parse_args(
        ["--v46-report", "x.json", "--v46-report", "y.json", "--output", "/tmp/out"]
    )
    assert hasattr(args, "v46_report"), "expected args.v46_report to exist"
    assert args.v46_report == [Path("x.json"), Path("y.json")]


def test_audit_v46_mode_includes_eurusd():
    """Test A: build_audit_matrix(v46_paths=...) emits at least one EURUSD cell.

    Currently the skip guard (audit.py L457-464) drops every eurusd path
    regardless of epoch. With v4.6 branch, eurusd MUST appear in cells.
    """
    eurusd_paths = [
        p for p in _FRESH_V46_PATHS if "eurusd" in str(p) and p.exists()
    ]
    assert len(eurusd_paths) == 3, (
        f"expected 3 eurusd fresh reports on disk, got {len(eurusd_paths)}: "
        "Phase 70 commit 2efa119 should provide them"
    )
    matrix = audit.build_audit_matrix([], [], eurusd_paths)
    eurusd_cells = [c for c in matrix["cells"] if c["pair"] == "eurusd"]
    assert len(eurusd_cells) >= 3, (
        f"expected ≥3 eurusd cells (3 events × 1 path each), got {len(eurusd_cells)}"
    )


def test_audit_v46_mode_epoch_label():
    """Test B: every cell emitted from --v46-report inputs has epoch=='v4.6'."""
    eurusd_paths = [
        p for p in _FRESH_V46_PATHS if "eurusd" in str(p) and p.exists()
    ]
    matrix = audit.build_audit_matrix([], [], eurusd_paths)
    assert matrix["cells"], "expected cells to be emitted from v46 paths"
    epochs = {c["epoch"] for c in matrix["cells"]}
    assert epochs == {"v4.6"}, f"expected only v4.6 epoch, got {epochs}"


def test_audit_v46_mode_twelve_cells():
    """Test C: full 12-path input → 12 cells covering 4 pair × 3 event.

    Validates the SIGN-04 acceptance gate: 4 pair × 3 event = 12 populated cells,
    EURUSD included (Phase 61 'EURUSD deferred' caveat closed in v4.6).
    """
    fresh = [p for p in _FRESH_V46_PATHS if p.exists()]
    assert len(fresh) == 12, (
        f"expected 12 fresh reports on disk, got {len(fresh)}; "
        "rerun Phase 70 plan 05 to regenerate"
    )
    matrix = audit.build_audit_matrix([], [], fresh)
    populated_cells = [c for c in matrix["cells"] if c.get("status") == "populated"]
    assert len(populated_cells) == 12, (
        f"expected 12 populated cells, got {len(populated_cells)}: "
        f"{[(c['pair'], c['event']) for c in populated_cells]}"
    )
    coverage = {(c["pair"], c["event"]) for c in populated_cells}
    expected = {
        (p, e)
        for p in ("audusd", "eurjpy", "eurusd", "usdjpy")
        for e in ("fomc", "ecb", "nfp")
    }
    assert coverage == expected, (
        f"missing cells: {expected - coverage}; extra: {coverage - expected}"
    )
