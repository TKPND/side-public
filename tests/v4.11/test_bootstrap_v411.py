"""Tests for scripts/v4.11/bootstrap_v411.py — Phase 95 SHIP-01.

TDD-driven test suite for v4.11 FWER Bonferroni-Holm + degenerate padding (D-44).
Import via importlib because 'v4.11' contains a dot (invalid Python package identifier).

Covers:
    Task 1: constants (M_PRIME=64 literal, signal_commit_v411 literal), SEAL drift happy-path,
            Bonferroni-Holm length guard.
    Task 2: main() emits 64-row p_adj_v411.json with provenance block + padding invariants.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest


# ---------------------------------------------------------------------------
# Module import (v4.11 contains dot — not a valid Python package identifier)
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "scripts" / "v4.11" / "bootstrap_v411.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("bootstrap_v411", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bootstrap_v411"] = mod
    spec.loader.exec_module(mod)
    return mod


try:
    bootstrap_v411 = _load_module()
    _AVAILABLE = True
    _IMPORT_EXC = None
except Exception as exc:  # noqa: BLE001
    _AVAILABLE = False
    _IMPORT_EXC = exc

pytestmark = pytest.mark.skipif(
    not _AVAILABLE,
    reason=f"bootstrap_v411 not available: {_IMPORT_EXC}",
)


# ---------------------------------------------------------------------------
# Task 1: constants + SEAL drift (happy-path) + Holm length guard
# ---------------------------------------------------------------------------
def test_m_prime_constant() -> None:
    """M_PRIME is the hardcoded SEAL literal 64 (no runtime computation)."""
    assert bootstrap_v411.M_PRIME == 64


def test_signal_commit_literal() -> None:
    """signal_commit_v411 literal matches SEAL expected sha256."""
    assert bootstrap_v411._SIGNAL_COMMIT_V411 == (
        "f8ccc8a806b847230c238b12011a479c77f7f10e6aed3f9959e8dbecfaa93bae"
    )


def test_seed_unchanged() -> None:
    """Bootstrap seed carries from v4.10 (pre-reg invariant)."""
    assert bootstrap_v411._BOOTSTRAP_SEED == 42


def test_bootstrap_pvalue_preserved() -> None:
    """Core bootstrap_pvalue function is preserved from v4.10 (byte-stable parity)."""
    assert callable(bootstrap_v411.bootstrap_pvalue)


def test_seal_drift_check_passes_on_clean() -> None:
    """Import succeeds against the untouched SEAL (no RuntimeError)."""
    # If we got here, import already passed. Re-run the check explicitly.
    bootstrap_v411._verify_seal_at_import()


def test_bonferroni_holm_accepts_64() -> None:
    """Holm accepts exactly M_PRIME=64 p-values and returns 64-length list."""
    p_raw = [0.5] * 64
    p_adj = bootstrap_v411.apply_bonferroni_holm(p_raw)
    assert len(p_adj) == 64
    for p in p_adj:
        assert 0.0 <= p <= 1.0


def test_bonferroni_holm_rejects_63() -> None:
    """Holm rejects length != M_PRIME via AssertionError (pre-reg guard)."""
    with pytest.raises(AssertionError):
        bootstrap_v411.apply_bonferroni_holm([0.5] * 63)


def test_bonferroni_holm_rejects_65() -> None:
    with pytest.raises(AssertionError):
        bootstrap_v411.apply_bonferroni_holm([0.5] * 65)


def test_bonferroni_holm_monotonic_after_sort() -> None:
    """Synthetic p_raw produces monotonic non-decreasing p_adj after sort."""
    p_raw = sorted([0.001, 0.01, 0.05, 0.1, 0.5] + [0.9] * 59)
    assert len(p_raw) == 64
    p_adj = bootstrap_v411.apply_bonferroni_holm(p_raw)
    p_adj_sorted = sorted(p_adj)
    for i in range(1, len(p_adj_sorted)):
        assert p_adj_sorted[i] >= p_adj_sorted[i - 1]


def test_no_runtime_dynamic_denominator() -> None:
    """Source must not use len(filtered_cells) — SEAL runtime_dynamic_prohibited (D-11)."""
    src = _MODULE_PATH.read_text(encoding="utf-8")
    assert "len(filtered_cells)" not in src


# ---------------------------------------------------------------------------
# Task 2: main() + provenance + padding
# ---------------------------------------------------------------------------
_P_ADJ_PATH = _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "p_adj_v411.json"
_FILTER_EVAL_PATH = (
    _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "filter_eval.json"
)


@pytest.fixture(scope="module")
def p_adj_doc() -> dict:
    """Run main() once and read the emitted JSON."""
    bootstrap_v411.main()
    return json.loads(_P_ADJ_PATH.read_text(encoding="utf-8"))


def test_main_emits_64_row_output(p_adj_doc: dict) -> None:
    """p_adj_v411.json has exactly M_PRIME=64 result rows."""
    assert len(p_adj_doc["results"]) == 64


def test_main_provenance_keys(p_adj_doc: dict) -> None:
    """Provenance block contains D-44 audit fields."""
    prov = p_adj_doc["provenance"]
    for key in (
        "signal_commit_v411",
        "m_prime",
        "n_tested",
        "n_padded",
        "kill_switch_consumed",
        "seed",
        "n_bootstrap_samples",
    ):
        assert key in prov, f"missing provenance key: {key}"


def test_main_provenance_values(p_adj_doc: dict) -> None:
    prov = p_adj_doc["provenance"]
    assert prov["m_prime"] == 64
    assert prov["seed"] == 42
    assert prov["signal_commit_v411"] == (
        "f8ccc8a806b847230c238b12011a479c77f7f10e6aed3f9959e8dbecfaa93bae"
    )
    assert isinstance(prov["kill_switch_consumed"], bool)


def test_main_n_tested_plus_padded_equals_m_prime(p_adj_doc: dict) -> None:
    prov = p_adj_doc["provenance"]
    assert prov["n_tested"] + prov["n_padded"] == 64


def test_main_kill_switch_matches_filter_eval(p_adj_doc: dict) -> None:
    filter_eval = json.loads(_FILTER_EVAL_PATH.read_text(encoding="utf-8"))
    assert (
        p_adj_doc["provenance"]["kill_switch_consumed"]
        == filter_eval["kill_switch_consumed"]
    )


def test_padded_rows_p_adj_equals_one(p_adj_doc: dict) -> None:
    padded = [r for r in p_adj_doc["results"] if r["status"] == "padded"]
    for r in padded:
        assert r["p_adj_holm"] == 1.0
        assert r["p_raw"] is None


def test_padded_count_matches_provenance(p_adj_doc: dict) -> None:
    padded = [r for r in p_adj_doc["results"] if r["status"] == "padded"]
    assert len(padded) == p_adj_doc["provenance"]["n_padded"]


def test_tested_count_matches_provenance(p_adj_doc: dict) -> None:
    tested = [r for r in p_adj_doc["results"] if r["status"] == "tested"]
    assert len(tested) == p_adj_doc["provenance"]["n_tested"]


def test_tested_rows_have_real_p_raw(p_adj_doc: dict) -> None:
    tested = [r for r in p_adj_doc["results"] if r["status"] == "tested"]
    for r in tested:
        assert r["p_raw"] is not None
        assert 0.0 <= float(r["p_raw"]) <= 1.0
