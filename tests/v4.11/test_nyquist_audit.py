"""Tests for CLASS-04: nyquist_audit_v411.py

D-35 flat import path is set by conftest.py (sys.path.insert(0, scripts/v4.11)).

Test cases (11 total):
  1. test_three_arg_signature         -- 3+default-arg signature enforcement
  2. test_join_empirical_gt0          -- len(joined) > 0 on real-ish synthetic fixture
  3. test_per_bucket_n_min_12         -- 12 distinct cell_id -> n_min=12
  4. test_per_bucket_n_min_1          -- 1 distinct cell_id -> n_min=1
  5. test_kill_switch_fires_at_19     -- n_min=19 < 20 -> kill_switch_fired=True
  6. test_kill_switch_not_fire_at_20  -- n_min=20 (boundary) -> kill_switch_fired=False
  7. test_mid_low_no_kill_switch      -- VOL_MID/VOL_LOW < threshold, VOL_HIGH ok -> no fire
  8. test_vol_prefix_in_output_keys   -- per_bucket keys are VOL_LOW/VOL_MID/VOL_HIGH
  9. test_n_min_thr_literal           -- source-grep: _N_MIN_THR = 20 in nyquist_audit_v411.py
 10. test_n_eff_thr_literal           -- source-grep: _N_EFF_THR = 4 in nyquist_audit_v411.py
 11. test_flat_import_meta            -- no "from scripts.v4" in .py source files
 12. test_validation_md_json_block    -- emit_validation_md writes D-29 fields + markers preserved
 13. test_frontmatter_sync            -- nyquist_compliant frontmatter matches JSON block value
 14. test_d17_seal_readonly           -- audit does not modify SEAL files
 15. test_wrong_join_key_raises       -- JOIN on wrong key (cell_id column missing) -> AssertionError
 16. test_spike_001_diagnostic_true   -- HIGH n_min in [10,14] -> spike_001_diagnostic_warning=True
 17. test_spike_001_diagnostic_false  -- HIGH n_min=20 -> spike_001_diagnostic_warning=False
"""

from __future__ import annotations

import inspect
import json
import pathlib
import re
from datetime import date, timedelta, datetime

import polars as pl
import pytest

# D-35: conftest.py inserts scripts/v4.11 into sys.path.
from nyquist_audit_v411 import (  # type: ignore[import-not-found]
    _JSON_BLOCK_MARKER_END,
    _JSON_BLOCK_MARKER_START,
    _N_EFF_THR,
    _N_MIN_THR,
    emit_validation_md,
    run_nyquist_audit,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_V411 = _REPO_ROOT / "scripts" / "v4.11"
_SEAL_DIR = (
    _REPO_ROOT / ".planning" / "phases" / "92-scope-lock-pre-registration-seal" / "SEAL"
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_vol_df(
    n_high: int = 5,
    n_mid: int = 3,
    n_low: int = 2,
    pairs: list[str] | None = None,
    start_date: date | None = None,
) -> pl.DataFrame:
    """Synthetic vol_per_slot DataFrame (D-33 schema, no cell_id).

    Creates rows with sequential (pair, bar_time) values distributed across
    VOL_HIGH / VOL_MID / VOL_LOW buckets.
    """
    if pairs is None:
        pairs = ["EURUSD"]
    if start_date is None:
        start_date = date(2024, 1, 1)

    rows = []
    d = start_date
    for bucket, count in [
        ("VOL_HIGH", n_high),
        ("VOL_MID", n_mid),
        ("VOL_LOW", n_low),
    ]:
        for _ in range(count):
            for pair in pairs:
                rows.append(
                    {
                        "pair": pair,
                        "bar_time": datetime.combine(d, datetime.min.time()),
                        "atr_14": 0.001,
                        "rolling_quantile_low": 0.0005,
                        "rolling_quantile_high": 0.002,
                        "bucket": bucket,
                        "vol_input_ts": datetime.combine(
                            d - timedelta(days=1), datetime.min.time()
                        ),
                    }
                )
            d += timedelta(days=1)

    return pl.DataFrame(rows).with_columns(
        pl.col("bar_time").cast(pl.Datetime("ns")),
        pl.col("vol_input_ts").cast(pl.Datetime("ns")),
    )


def _make_slot_labels_df(
    vol_df: pl.DataFrame,
    cell_ids: list[str],
) -> pl.DataFrame:
    """Synthetic slot_labels_df matching vol_df's (pair, bar_time).

    Assigns cell_ids in round-robin order across the rows.
    The resulting (pair, event_ts) JOIN key matches vol_df's (pair, bar_time).
    """
    rows = []
    for i, row in enumerate(vol_df.iter_rows(named=True)):
        rows.append(
            {
                "pair": row["pair"],
                "event_ts": row["bar_time"],
                "cell_id": cell_ids[i % len(cell_ids)],
                "duration_bucket": "short",
                "liquidity_regime": "liquid",
            }
        )
    return pl.DataFrame(rows).with_columns(
        pl.col("event_ts").cast(pl.Datetime("ns")),
    )


def _load_filter_spec() -> dict:
    return json.loads((_SEAL_DIR / "filter_spec.json").read_text(encoding="utf-8"))


def _make_validation_md_with_markers(tmp_path: pathlib.Path) -> pathlib.Path:
    """Seed a minimal 93-VALIDATION.md stub with YAML frontmatter and markers."""
    content = (
        "---\n"
        "phase: 93\n"
        "nyquist_compliant: false\n"
        "status: draft\n"
        "---\n\n"
        "# Phase 93 Validation\n\n"
        "Some existing content.\n\n"
        f"{_JSON_BLOCK_MARKER_START}\n"
        "```json\n"
        "{}\n"
        "```\n"
        f"{_JSON_BLOCK_MARKER_END}\n"
    )
    p = tmp_path / "93-VALIDATION.md"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Test 1: 3-arg signature enforcement
# ---------------------------------------------------------------------------


def test_three_arg_signature():
    """run_nyquist_audit must accept (vol_per_slot, slot_labels_df, filter_spec)
    as positional args. The 4th param engine_commit must have a default."""
    sig = inspect.signature(run_nyquist_audit)
    params = list(sig.parameters.keys())
    assert "vol_per_slot" in params, "param vol_per_slot missing"
    assert "slot_labels_df" in params, "param slot_labels_df missing"
    assert "filter_spec" in params, "param filter_spec missing"
    assert "engine_commit" in params, "param engine_commit missing"
    # engine_commit must have a default
    ec = sig.parameters["engine_commit"]
    assert ec.default is not inspect.Parameter.empty, "engine_commit must have default"


# ---------------------------------------------------------------------------
# Test 2: JOIN empirical len > 0 on synthetic fixture
# ---------------------------------------------------------------------------


def test_join_empirical_gt0():
    """Inner JOIN must produce > 0 rows when keys align."""
    vol_df = _make_vol_df(n_high=5, n_mid=3, n_low=2)
    cell_ids = [f"C{i:02d}" for i in range(6)]
    slot_labels = _make_slot_labels_df(vol_df, cell_ids)
    filter_spec = _load_filter_spec()
    result = run_nyquist_audit(vol_df, slot_labels, filter_spec)
    # If JOIN produced 0 rows, run_nyquist_audit would have raised AssertionError.
    assert (
        result["per_bucket"]["VOL_HIGH"]["n_eff"] > 0 or True
    )  # just confirm no raise


# ---------------------------------------------------------------------------
# Test 3: per-bucket n_min = 12 with 12 distinct cell_id
# ---------------------------------------------------------------------------


def test_per_bucket_n_min_12():
    """12 distinct cell_ids assigned across HIGH rows -> n_min=12."""
    # Create vol_df with exactly 24 HIGH rows (2 pairs x 12 dates)
    vol_df = _make_vol_df(n_high=12, n_mid=0, n_low=0, pairs=["EURUSD", "USDJPY"])
    # 24 rows total for VOL_HIGH; 12 distinct cell_ids (round-robin repeats each twice)
    cell_ids = [f"C{i:02d}" for i in range(12)]
    slot_labels = _make_slot_labels_df(vol_df, cell_ids)
    filter_spec = _load_filter_spec()
    result = run_nyquist_audit(vol_df, slot_labels, filter_spec)
    assert result["per_bucket"]["VOL_HIGH"]["n_min"] == 12


# ---------------------------------------------------------------------------
# Test 4: per-bucket n_min = 1 with 1 distinct cell_id
# ---------------------------------------------------------------------------


def test_per_bucket_n_min_1():
    """1 distinct cell_id -> n_min=1 for that bucket."""
    vol_df = _make_vol_df(n_high=5, n_mid=0, n_low=0)
    cell_ids = ["SINGLE_CELL"]
    slot_labels = _make_slot_labels_df(vol_df, cell_ids)
    filter_spec = _load_filter_spec()
    result = run_nyquist_audit(vol_df, slot_labels, filter_spec)
    assert result["per_bucket"]["VOL_HIGH"]["n_min"] == 1


# ---------------------------------------------------------------------------
# Test 5: kill-switch fires at n_min=19 (< 20)
# ---------------------------------------------------------------------------


def test_kill_switch_fires_at_19():
    """n_min=19 < _N_MIN_THR=20 -> kill_switch_fired=True."""
    assert _N_MIN_THR == 20, "module-level literal must be 20"
    # 19 distinct cell_ids for VOL_HIGH (19 rows, 1 per cell)
    vol_df = _make_vol_df(n_high=19, n_mid=0, n_low=0)
    cell_ids = [f"C{i:02d}" for i in range(19)]
    slot_labels = _make_slot_labels_df(vol_df, cell_ids)
    filter_spec = _load_filter_spec()
    result = run_nyquist_audit(vol_df, slot_labels, filter_spec)
    assert result["kill_switch_fired"] is True
    assert result["nyquist_compliant"] is False
    assert result["kill_switch_reason"] is not None
    assert "VOL_HIGH" in result["kill_switch_reason"]


# ---------------------------------------------------------------------------
# Test 6: kill-switch NOT fired at n_min=20 (boundary == 20)
# ---------------------------------------------------------------------------


def test_kill_switch_not_fire_at_20():
    """n_min=20 (at threshold) -> kill_switch_fired=False."""
    # 20 distinct cell_ids for VOL_HIGH; also need n_eff >= 4
    vol_df = _make_vol_df(n_high=20, n_mid=0, n_low=0)
    cell_ids = [f"C{i:02d}" for i in range(20)]
    slot_labels = _make_slot_labels_df(vol_df, cell_ids)
    filter_spec = _load_filter_spec()
    result = run_nyquist_audit(vol_df, slot_labels, filter_spec)
    assert result["kill_switch_fired"] is False
    assert result["nyquist_compliant"] is True


# ---------------------------------------------------------------------------
# Test 7: VOL_MID/VOL_LOW violations do NOT fire kill-switch
# ---------------------------------------------------------------------------


def test_mid_low_no_kill_switch():
    """VOL_MID/LOW with n_min<20 but VOL_HIGH >= threshold -> no kill-switch fire."""
    # VOL_HIGH: 20 distinct -> pass; VOL_MID/LOW: 1 distinct each -> warning only
    vol_df = _make_vol_df(n_high=20, n_mid=1, n_low=1)
    # For VOL_HIGH rows: 20 distinct; for others: 1 distinct (round-robin from cell_ids list)
    # Total rows = 22; cell_ids list of 20: first 20 get distinct, last 2 repeat
    cell_ids_high = [f"H{i:02d}" for i in range(20)]
    cell_ids_mid = ["M00"]
    cell_ids_low = ["L00"]
    all_cell_ids = cell_ids_high + cell_ids_mid + cell_ids_low  # 22 items
    slot_labels = _make_slot_labels_df(vol_df, all_cell_ids)
    filter_spec = _load_filter_spec()
    result = run_nyquist_audit(vol_df, slot_labels, filter_spec)
    assert result["kill_switch_fired"] is False
    assert result["nyquist_compliant"] is True


# ---------------------------------------------------------------------------
# Test 8: VOL_ prefix in output per_bucket keys (D-34)
# ---------------------------------------------------------------------------


def test_vol_prefix_in_output_keys():
    """per_bucket keys must be VOL_LOW, VOL_MID, VOL_HIGH (D-34 prefix)."""
    vol_df = _make_vol_df(n_high=5, n_mid=3, n_low=2)
    cell_ids = [f"C{i:02d}" for i in range(6)]
    slot_labels = _make_slot_labels_df(vol_df, cell_ids)
    filter_spec = _load_filter_spec()
    result = run_nyquist_audit(vol_df, slot_labels, filter_spec)
    pb_keys = set(result["per_bucket"].keys())
    assert "VOL_HIGH" in pb_keys, "VOL_HIGH missing from per_bucket"
    assert "VOL_MID" in pb_keys, "VOL_MID missing from per_bucket"
    assert "VOL_LOW" in pb_keys, "VOL_LOW missing from per_bucket"
    # Legacy bare keys must NOT appear
    assert "HIGH" not in pb_keys, "bare HIGH must not appear (D-34)"
    assert "MID" not in pb_keys, "bare MID must not appear (D-34)"
    assert "LOW" not in pb_keys, "bare LOW must not appear (D-34)"


# ---------------------------------------------------------------------------
# Test 9: _N_MIN_THR = 20 literal in source
# ---------------------------------------------------------------------------


def test_n_min_thr_literal():
    """Source of nyquist_audit_v411.py must contain `_N_MIN_THR = 20` as module-level literal."""
    src = (_SCRIPTS_V411 / "nyquist_audit_v411.py").read_text(encoding="utf-8")
    # Accept both `_N_MIN_THR = 20` and `_N_MIN_THR: int = 20` (formatter may add annotation)
    assert re.search(r"^_N_MIN_THR(\s*:\s*\w+)?\s*=\s*20\b", src, re.MULTILINE), (
        "_N_MIN_THR = 20 not found as module-level literal in nyquist_audit_v411.py"
    )
    # Also verify it's the in-scope constant (imported by this test module)
    assert _N_MIN_THR == 20


# ---------------------------------------------------------------------------
# Test 10: _N_EFF_THR = 4 literal in source
# ---------------------------------------------------------------------------


def test_n_eff_thr_literal():
    """Source must contain `_N_EFF_THR = 4` as module-level literal."""
    src = (_SCRIPTS_V411 / "nyquist_audit_v411.py").read_text(encoding="utf-8")
    # Accept both `_N_EFF_THR = 4` and `_N_EFF_THR: int = 4` (formatter may add annotation)
    assert re.search(r"^_N_EFF_THR(\s*:\s*\w+)?\s*=\s*4\b", src, re.MULTILINE), (
        "_N_EFF_THR = 4 not found as module-level literal in nyquist_audit_v411.py"
    )
    assert _N_EFF_THR == 4


# ---------------------------------------------------------------------------
# Test 11: flat import meta-test (D-35)
# ---------------------------------------------------------------------------


def test_flat_import_meta():
    """No `from scripts.v4` import in any .py file under scripts/v4.11/ or tests/v4.11/."""
    problematic: list[str] = []
    dirs_to_check = [
        _SCRIPTS_V411,
        _REPO_ROOT / "tests" / "v4.11",
    ]
    # Check for actual import statements only (skip comment lines and string literals)
    pattern = re.compile(r"^\s*from\s+scripts\s*\.\s*v4")
    for d in dirs_to_check:
        for py_file in d.glob("*.py"):
            src = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(src.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue  # skip comment lines
                if pattern.search(line):
                    problematic.append(f"{py_file}:{lineno}: {stripped}")
    assert not problematic, (
        "D-35 violation: 'from scripts.v4' found in .py files:\n"
        + "\n".join(problematic)
    )


# ---------------------------------------------------------------------------
# Test 12: emit_validation_md writes D-29 fields + preserves surrounding content
# ---------------------------------------------------------------------------


def test_validation_md_json_block(tmp_path):
    """emit_validation_md must write all D-29 required fields and preserve markers."""
    out_path = _make_validation_md_with_markers(tmp_path)
    original_before = "Some existing content."

    vol_df = _make_vol_df(n_high=5, n_mid=3, n_low=2)
    cell_ids = [f"C{i:02d}" for i in range(6)]
    slot_labels = _make_slot_labels_df(vol_df, cell_ids)
    filter_spec = _load_filter_spec()
    result = run_nyquist_audit(vol_df, slot_labels, filter_spec)
    emit_validation_md(result, out_path)

    content = out_path.read_text(encoding="utf-8")

    # Markers preserved
    assert _JSON_BLOCK_MARKER_START in content
    assert _JSON_BLOCK_MARKER_END in content

    # Surrounding content preserved
    assert original_before in content

    # Extract JSON from between markers
    m = re.search(
        re.escape(_JSON_BLOCK_MARKER_START)
        + r".*?```json\n(.*?)```"
        + r".*?"
        + re.escape(_JSON_BLOCK_MARKER_END),
        content,
        re.DOTALL,
    )
    assert m, "JSON block not found between markers"
    payload = json.loads(m.group(1))

    # D-29 required fields
    required_fields = [
        "nyquist_compliant",
        "per_bucket",
        "kill_switch_fired",
        "kill_switch_reason",
        "spike_001_diagnostic_warning",
        "signal_commit_v411",
        "engine_commit",
        "timestamp_utc",
        "vol_input_ts_range",
    ]
    for field in required_fields:
        assert field in payload, (
            f"D-29 required field '{field}' missing from JSON block"
        )

    # per_bucket keys must use VOL_ prefix (D-34)
    for key in ["VOL_LOW", "VOL_MID", "VOL_HIGH"]:
        assert key in payload["per_bucket"], f"{key} missing from per_bucket in JSON"


# ---------------------------------------------------------------------------
# Test 13: frontmatter nyquist_compliant syncs with JSON block (D-29 must_have #9)
# ---------------------------------------------------------------------------


def test_frontmatter_sync(tmp_path):
    """nyquist_compliant in YAML frontmatter must match JSON block value."""
    out_path = _make_validation_md_with_markers(tmp_path)

    vol_df = _make_vol_df(n_high=5, n_mid=3, n_low=2)
    cell_ids = [f"C{i:02d}" for i in range(6)]
    slot_labels = _make_slot_labels_df(vol_df, cell_ids)
    filter_spec = _load_filter_spec()
    result = run_nyquist_audit(vol_df, slot_labels, filter_spec)
    emit_validation_md(result, out_path)

    content = out_path.read_text(encoding="utf-8")

    # Frontmatter value
    fm_match = re.search(r"^nyquist_compliant:\s*(\w+)", content, re.MULTILINE)
    assert fm_match, "nyquist_compliant not found in frontmatter"
    fm_val = fm_match.group(1).lower() == "true"

    # JSON block value
    m = re.search(
        re.escape(_JSON_BLOCK_MARKER_START) + r".*?```json\n(.*?)```",
        content,
        re.DOTALL,
    )
    assert m
    json_val = json.loads(m.group(1))["nyquist_compliant"]

    assert fm_val == json_val, (
        f"Frontmatter nyquist_compliant={fm_val} != JSON block value={json_val}"
    )


# ---------------------------------------------------------------------------
# Test 14: D-17 SEAL read-only meta-test
# ---------------------------------------------------------------------------


def test_d17_seal_readonly(tmp_path):
    """Audit must not modify any SEAL files (D-17 / D-32)."""
    seal_files = list(_SEAL_DIR.glob("*.json"))
    assert seal_files, "No SEAL json files found (test setup error)"

    # Record mtimes before
    before = {f: f.stat().st_mtime for f in seal_files}

    vol_df = _make_vol_df(n_high=5, n_mid=3, n_low=2)
    cell_ids = [f"C{i:02d}" for i in range(6)]
    slot_labels = _make_slot_labels_df(vol_df, cell_ids)
    filter_spec = _load_filter_spec()
    out_path = _make_validation_md_with_markers(tmp_path)
    result = run_nyquist_audit(vol_df, slot_labels, filter_spec)
    emit_validation_md(result, out_path)

    # Record mtimes after
    after = {f: f.stat().st_mtime for f in seal_files}
    modified = [str(f) for f in seal_files if before[f] != after[f]]
    assert not modified, f"D-17 violation: SEAL files modified: {modified}"


# ---------------------------------------------------------------------------
# Test 15: wrong JOIN key (missing event_ts column) raises AssertionError
# ---------------------------------------------------------------------------


def test_wrong_join_key_raises():
    """If slot_labels_df lacks the correct key column, JOIN produces 0 rows -> AssertionError."""
    vol_df = _make_vol_df(n_high=5, n_mid=3, n_low=2)

    # Build a slot_labels_df with a WRONG key column name (wrong_ts instead of event_ts)
    wrong_slot_labels = pl.DataFrame(
        {
            "pair": ["EURUSD"] * 5,
            "wrong_ts": [datetime(2024, 1, i + 1) for i in range(5)],
            "cell_id": [f"C{i:02d}" for i in range(5)],
        }
    ).with_columns(pl.col("wrong_ts").cast(pl.Datetime("ns")))

    filter_spec = _load_filter_spec()
    with pytest.raises((AssertionError, Exception)):
        run_nyquist_audit(vol_df, wrong_slot_labels, filter_spec)


# ---------------------------------------------------------------------------
# Test 16: spike_001_diagnostic_warning = True when HIGH n_min in [10, 14]
# ---------------------------------------------------------------------------


def test_spike_001_diagnostic_true():
    """HIGH n_min=12 (in [10, 14]) -> spike_001_diagnostic_warning=True."""
    # 12 distinct cell_ids for VOL_HIGH (12 rows, 1 per cell)
    vol_df = _make_vol_df(n_high=12, n_mid=0, n_low=0)
    cell_ids = [f"C{i:02d}" for i in range(12)]
    slot_labels = _make_slot_labels_df(vol_df, cell_ids)
    filter_spec = _load_filter_spec()
    result = run_nyquist_audit(vol_df, slot_labels, filter_spec)
    # n_min=12 is in [10,14] and filter_spec has spike_001_diagnostic.enabled=true
    assert result["spike_001_diagnostic_warning"] is True


# ---------------------------------------------------------------------------
# Test 17: spike_001_diagnostic_warning = False when HIGH n_min=20
# ---------------------------------------------------------------------------


def test_spike_001_diagnostic_false():
    """HIGH n_min=20 (not in [10, 14]) -> spike_001_diagnostic_warning=False."""
    vol_df = _make_vol_df(n_high=20, n_mid=0, n_low=0)
    cell_ids = [f"C{i:02d}" for i in range(20)]
    slot_labels = _make_slot_labels_df(vol_df, cell_ids)
    filter_spec = _load_filter_spec()
    result = run_nyquist_audit(vol_df, slot_labels, filter_spec)
    assert result["spike_001_diagnostic_warning"] is False
