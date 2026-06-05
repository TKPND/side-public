"""
test_emit_kill_switch_v412.py — Phase 103 Plan 04 (Wave 1).

D-03 standalone kill_switch_v412.json emitter tests.

- Test 1: emit_kill_switch_v412.main() emits data/v4.12/kill_switch_v412.json with
  required fields (kill_switch_fired, n_cells_total, n_cells_active, etc.)
- Test 2: Phase 102 cells_post_compound_filter.parquet sha256 INTACT pre/post emit
  (D-03 read-only invariant, T-103-04 mitigate).
- Test 3: aggregation logic — mock parquet with one stratum n_active < 20 →
  kill_switch_fired == True (Phase 101 nyquist threshold = 20).

Citations: 103-04-PLAN.md Task 1, D-03 (standalone emit), T-103-04 (Phase 102 read-only).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import polars as pl

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EMITTER = _REPO_ROOT / "scripts" / "v4.12" / "emit_kill_switch_v412.py"
_OUTPUT = _REPO_ROOT / "data" / "v4.12" / "kill_switch_v412.json"
_SOURCE = _REPO_ROOT / "data" / "v4.12" / "cells_post_compound_filter.parquet"
_EXPECTED_SOURCE_SHA = (
    "1f4b31c953a7ca183b46953f6852d7849b49a66d1e7fb40e1edda035f6206b79"
)


def _import_emitter():
    """Import emit_kill_switch_v412 from absolute path (scripts/v4.12 is not a package)."""
    spec = importlib.util.spec_from_file_location("emit_kill_switch_v412", _EMITTER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_emit_kill_switch_v412_emits_json():
    """D-03: emit_kill_switch_v412.main() emits data/v4.12/kill_switch_v412.json
    as standalone step (not embedded in bootstrap or permutation pipeline)."""
    mod = _import_emitter()
    mod.main()
    assert _OUTPUT.exists(), f"Output JSON not emitted at {_OUTPUT}"
    doc = json.loads(_OUTPUT.read_text(encoding="utf-8"))
    required = [
        "schema_version",
        "kill_switch_fired",
        "n_cells_total",
        "n_cells_kill_set",
        "n_cells_active",
        "data_provenance",
        "emitted_at",
        "source_sha256",
        "nyquist_threshold",
    ]
    for key in required:
        assert key in doc, f"Missing key: {key}"
    assert doc["schema_version"] == "v4.12"
    assert isinstance(doc["kill_switch_fired"], bool)
    assert doc["nyquist_threshold"] == 20


def test_emit_kill_switch_v412_does_not_modify_phase_102_parquet():
    """D-03 / T-103-04: Phase 102 cells_post_compound_filter.parquet sha256 INTACT
    pre and post emit run (read-only invariant)."""
    pre_sha = hashlib.sha256(_SOURCE.read_bytes()).hexdigest()
    assert pre_sha == _EXPECTED_SOURCE_SHA, f"Pre-emit SHA drift: {pre_sha}"
    mod = _import_emitter()
    mod.main()
    post_sha = hashlib.sha256(_SOURCE.read_bytes()).hexdigest()
    assert post_sha == _EXPECTED_SOURCE_SHA, f"Post-emit SHA drift: {post_sha}"


def test_emit_kill_switch_v412_aggregation_fires_when_understaffed(
    tmp_path, monkeypatch
):
    """SHIP-V412-05: aggregation logic — n_active < 20 stratum 存在で
    kill_switch_fired=True (Phase 101 nyquist_audit_v412 整合)."""
    # mock parquet: HAWK 10 active rows + DOV 30 active rows + NEUT 5 kill_set rows
    df = pl.DataFrame(
        {
            "stance": ["HAWK"] * 10 + ["DOV"] * 30 + ["NEUT"] * 5,
            "kill_set": [False] * 40 + [True] * 5,
            "vol_bucket": ["LOW"] * 45,
            "pass_flag": [True] * 45,
        }
    )
    mock_path = tmp_path / "mock_cells.parquet"
    df.write_parquet(mock_path)
    mock_sha = hashlib.sha256(mock_path.read_bytes()).hexdigest()

    mod = _import_emitter()
    monkeypatch.setattr(mod, "_CELLS_POST_FILTER", mock_path)
    monkeypatch.setattr(mod, "_OUTPUT", tmp_path / "mock_kill_switch.json")
    monkeypatch.setattr(mod, "_EXPECTED_SOURCE_SHA256", mock_sha)

    doc = mod.emit_kill_switch_decision()
    assert doc["kill_switch_fired"] is True, (
        f"Expected kill_switch_fired=True (HAWK n_active=10 < 20), got {doc}"
    )
    assert doc["min_n_active_per_stratum"] == 10
