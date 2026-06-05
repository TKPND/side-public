"""test_nyquist_audit_v412.py — Phase 101 Wave 2 Plan 101-05 Task 2 (5 GREEN).

D-72 nyquist audit unit tests. Uses synthetic polars fixtures where possible
so tests don't need the real D-71 parquet, plus 1 schema test against the
generated reports/v4.12/nyquist_audit_v412.json.

Citations:
    CONTEXT.md L98-110 — D-72 schema
    101-05-PLAN.md Task 2 — 5 behavior tests
    CLASS-V412-03 — kill_switch enforcement
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import importlib.util
import sys

import polars as pl
import pytest

# scripts/v4.12 has a dot in the dir name → load nyquist_audit_v412 by path.
_HERE = Path(__file__).resolve().parent
_AUDIT_PATH = _HERE.parent / "nyquist_audit_v412.py"
_audit_spec = importlib.util.spec_from_file_location("nyquist_audit_v412", _AUDIT_PATH)
assert _audit_spec is not None and _audit_spec.loader is not None
audit = importlib.util.module_from_spec(_audit_spec)
sys.modules["nyquist_audit_v412"] = audit
_audit_spec.loader.exec_module(audit)


# ── synthetic fixtures ───────────────────────────────────────────────────────


def _make_stance_df(rows: list[tuple[str, str, str]]) -> pl.DataFrame:
    """rows: list of (event_ts ISO, pair, stance)."""
    return pl.DataFrame(
        {
            "event_ts": [r[0] for r in rows],
            "pair": [r[1] for r in rows],
            "stance": [r[2] for r in rows],
        }
    ).with_columns(pl.col("event_ts").str.to_datetime(time_unit="ns", time_zone="UTC"))


def _make_vol_df(rows: list[tuple[str, str, str]]) -> pl.DataFrame:
    """rows: list of (bar_time naive ISO, pair, bucket)."""
    return pl.DataFrame(
        {
            "bar_time": [r[0] for r in rows],
            "pair": [r[1] for r in rows],
            "bucket": [r[2] for r in rows],
        }
    ).with_columns(pl.col("bar_time").str.to_datetime(time_unit="ns"))


# ── tests ────────────────────────────────────────────────────────────────────


def test_n_per_cell_counts_compound_cells() -> None:
    """compute_compound_cells groups by (vol_bucket × stance) — at most 9."""
    stance = _make_stance_df(
        [
            ("2024-01-25T00:00:00+00:00", "USDJPY", "HAWK"),
            ("2024-01-25T00:00:00+00:00", "EURUSD", "HAWK"),
            ("2024-01-31T00:00:00+00:00", "USDJPY", "DOV"),
            ("2024-03-07T00:00:00+00:00", "EURUSD", "HAWK"),
        ]
    )
    vol = _make_vol_df(
        [
            ("2024-01-25T00:00:00", "USDJPY", "VOL_HIGH"),
            ("2024-01-25T00:00:00", "EURUSD", "VOL_LOW"),
            ("2024-01-31T00:00:00", "USDJPY", "VOL_HIGH"),
            ("2024-03-07T00:00:00", "EURUSD", "VOL_MID"),
        ]
    )
    cells = audit.compute_compound_cells(stance, vol)
    assert cells.height <= 9
    # Each cell has the four expected columns.
    assert set(cells.columns) >= {"vol_bucket", "stance", "n", "sufficient"}
    # Sum of n equals number of joinable rows.
    assert cells["n"].sum() == 4


def test_kill_switch_fires_when_any_cell_below_n_min(tmp_path: Path) -> None:
    """When the largest cell has n < n_min(=20), kill_switch_fired = true."""
    stance = _make_stance_df(
        [
            ("2024-01-25T00:00:00+00:00", "USDJPY", "HAWK"),
            ("2024-01-31T00:00:00+00:00", "USDJPY", "HAWK"),
        ]
    )
    vol = _make_vol_df(
        [
            ("2024-01-25T00:00:00", "USDJPY", "VOL_HIGH"),
            ("2024-01-31T00:00:00", "USDJPY", "VOL_HIGH"),
        ]
    )
    spec = tmp_path / "spec.json"
    spec.write_text("{}")
    labels = tmp_path / "labels.json"
    labels.write_text("{}")
    report = audit.build_audit_report(
        stance,
        vol,
        macro_classifier_spec_path=spec,
        labels_metadata_path=labels,
        vol_per_slot_path=tmp_path / "vol.parquet",
    )
    assert report["kill_switch_fired"] is True
    assert report["n_min_threshold"] == 20
    assert report["n_cells_total"] >= 1
    assert report["n_cells_insufficient"] >= 1


def test_kill_switch_silent_when_all_cells_sufficient(tmp_path: Path) -> None:
    """All compound cells with n ≥ 20 → kill_switch_fired = false."""
    n = 25
    stance = _make_stance_df([("2024-01-25T00:00:00+00:00", "USDJPY", "HAWK")] * n)
    vol = _make_vol_df([("2024-01-25T00:00:00", "USDJPY", "VOL_HIGH")] * n)
    spec = tmp_path / "spec.json"
    spec.write_text("{}")
    labels = tmp_path / "labels.json"
    labels.write_text("{}")
    report = audit.build_audit_report(
        stance,
        vol,
        macro_classifier_spec_path=spec,
        labels_metadata_path=labels,
        vol_per_slot_path=tmp_path / "vol.parquet",
    )
    assert report["kill_switch_fired"] is False
    assert report["n_cells_sufficient"] == report["n_cells_total"]
    assert all(c["sufficient"] for c in report["compound_cells"])


def test_audit_json_schema_d72() -> None:
    """Generated reports/v4.12/nyquist_audit_v412.json contains all D-72 fields."""
    json_path = Path("reports/v4.12/nyquist_audit_v412.json")
    if not json_path.exists():
        pytest.skip(f"audit artifact missing: {json_path}")
    obj = json.loads(json_path.read_text())
    required = {
        "audit_at",
        "n_min_threshold",
        "compound_cells",
        "n_total_events",
        "n_cells_total",
        "n_cells_sufficient",
        "n_cells_insufficient",
        "kill_switch_fired",
        "kill_switch_reason",
        "macro_classifier_spec_sha256",
        "labels_metadata_sha256",
        "vol_per_slot_path",
    }
    missing = required - set(obj.keys())
    assert not missing, f"missing D-72 fields: {sorted(missing)}"
    # audit_at must parse as ISO-8601 with tzinfo (UTC).
    parsed = datetime.fromisoformat(obj["audit_at"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    # compound_cells entries must each carry the 4 fields.
    for cell in obj["compound_cells"]:
        assert set(cell.keys()) == {"vol_bucket", "stance", "n", "sufficient"}
        assert isinstance(cell["n"], int)
        assert isinstance(cell["sufficient"], bool)
    # Wave-2 expected outcome (CONTEXT-authoritative): kill_switch fires.
    assert obj["kill_switch_fired"] is True
    assert obj["n_total_events"] == 16


def test_sha256_pins_recorded() -> None:
    """macro_classifier_spec_sha256 + labels_metadata_sha256 are 64-char hex."""
    json_path = Path("reports/v4.12/nyquist_audit_v412.json")
    if not json_path.exists():
        pytest.skip(f"audit artifact missing: {json_path}")
    obj = json.loads(json_path.read_text())
    hex64 = re.compile(r"^[0-9a-f]{64}$")
    assert hex64.match(obj["macro_classifier_spec_sha256"]), (
        f"invalid spec sha256: {obj['macro_classifier_spec_sha256']}"
    )
    assert hex64.match(obj["labels_metadata_sha256"]), (
        f"invalid labels sha256: {obj['labels_metadata_sha256']}"
    )
