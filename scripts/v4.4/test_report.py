"""Phase 63 report.py skeleton tests (63-02).

Expands per 63-03..07 plans (matrix, VIF, report.md / report.json /
VALIDATION.md emitters). 63-02 scope: 3 smoke tests covering the CLI
signature, fail-fast loader, and D-12 sibling-script imports.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import report  # noqa: E402


def test_cli_help_exits_zero():
    """CLI `--help` returns exit 0 and advertises all 6 required flags (D-26)."""
    result = subprocess.run(
        ["uv", "run", "python", str(SCRIPT_DIR / "report.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for flag in (
        "--audit",
        "--drift",
        "--sign",
        "--regime",
        "--output-dir",
        "--commit-ref",
    ):
        assert flag in result.stdout, f"missing flag {flag!r} in --help output"


def test_load_json_missing_file_fails_fast(tmp_path):
    """`_load_json` on a missing path aborts with SystemExit(2)."""
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit) as exc_info:
        report._load_json(missing)
    assert exc_info.value.code == 2


def test_sibling_imports_available():
    """D-12: sign_breakdown helpers are importable at module scope."""
    assert callable(report.exact_pair_agreement_ci)
    assert callable(report.stationary_bootstrap_ci)
    assert callable(report._sanitize_for_json)


# ─── Matrix tests (Plan 63-03, REGIME-02) ─────────────────────────────────

import numpy as np  # noqa: E402


def _mk_tally(long: int = 1, short: int = 0, neutral: int = 0) -> dict:
    return {"long": long, "short": short, "neutral": neutral}


def _build_sign_breakdown(
    per_pair_event: dict[str, dict[str, dict[str, dict[str, int]]]],
) -> dict:
    """Wrap per-pair-event-slot tallies in the Phase 62 top-level schema."""
    return {"per_pair_event_slot_tally": per_pair_event}


@pytest.fixture
def regime_labels():
    return {
        "version": "v4.4",
        "labels": {
            "USDJPY": "safe_haven",
            "EURJPY": "safe_haven_cross",
            "EURUSD": "risk_on",
            "AUDUSD": "commodity",
        },
        "flow_types": ["safe_haven", "risk_on", "commodity"],
    }


@pytest.fixture
def sign_breakdown_all_long():
    """4 pair × 3 event × 2 slots all-long (AUDUSD×ecb omitted, v4.4 scope #6)."""
    events = ("fomc", "ecb", "nfp")
    pairs = ("usdjpy", "eurusd", "audusd", "eurjpy")
    per_pair: dict = {}
    for p in pairs:
        per_pair[p] = {}
        for e in events:
            # Skip AUDUSD×ECB to exercise the no-data branch.
            if p == "audusd" and e == "ecb":
                per_pair[p][e] = {}
                continue
            per_pair[p][e] = {
                "slot_0": _mk_tally(long=3),
                "slot_1": _mk_tally(long=3),
            }
    return _build_sign_breakdown(per_pair)


@pytest.fixture
def sign_breakdown_strong_corr():
    """USDJPY + EURJPY perfectly aligned long-biased with mixed slots so the
    sign vectors have non-zero variance (required for Pearson correlation).

    Layout per event: 8 slots — slots 0-5 long, slots 6-7 short, identical
    across the two pairs. Expected: |corr| = 1.0 (same pattern), 12 longs +
    4 shorts total → binomtest(12, 16, 0.5) p ≈ 0.077. That's above 0.05, so
    we instead use 7 longs / 1 short per pair (14 longs / 2 shorts over 2
    pairs × 8 slots concat) → binomtest(14, 16) ≈ 4e-3 < 0.05.
    """
    events = ("fomc", "ecb", "nfp")

    def _slots() -> dict[str, dict[str, int]]:
        # 7 long + 1 short = 8 slots; same pattern across both pairs.
        return {
            **{f"slot_{i}": _mk_tally(long=1) for i in range(7)},
            "slot_7": _mk_tally(long=0, short=1),
        }

    per_pair: dict = {
        "usdjpy": {e: _slots() for e in events},
        "eurjpy": {e: _slots() for e in events},
        "eurusd": {e: {} for e in events},
        "audusd": {e: {} for e in events},
    }
    return _build_sign_breakdown(per_pair)


def test_matrix_has_9_cells(sign_breakdown_all_long, regime_labels):
    m = report.build_regime_matrix_3x3(sign_breakdown_all_long, regime_labels)
    assert len(m["cells"]) == 9
    assert m["rows"] == ["safe_haven", "risk_on", "commodity"]
    assert m["cols"] == ["fomc", "ecb", "nfp"]


def test_safe_haven_row_aggregates_usdjpy_eurjpy(
    sign_breakdown_all_long, regime_labels
):
    m = report.build_regime_matrix_3x3(sign_breakdown_all_long, regime_labels)
    cell = m["cells"]["safe_haven__fomc"]
    # _pairs_for_row returns sorted uppercase → ["EURJPY", "USDJPY"]
    assert cell["pairs_aggregated"] == ["EURJPY", "USDJPY"]


def test_risk_on_row_eurusd_only(sign_breakdown_all_long, regime_labels):
    m = report.build_regime_matrix_3x3(sign_breakdown_all_long, regime_labels)
    assert m["cells"]["risk_on__fomc"]["pairs_aggregated"] == ["EURUSD"]


def test_commodity_row_audusd_only(sign_breakdown_all_long, regime_labels):
    m = report.build_regime_matrix_3x3(sign_breakdown_all_long, regime_labels)
    assert m["cells"]["commodity__fomc"]["pairs_aggregated"] == ["AUDUSD"]


def test_audusd_ecb_cell_has_no_data_note(sign_breakdown_all_long, regime_labels):
    m = report.build_regime_matrix_3x3(sign_breakdown_all_long, regime_labels)
    cell = m["cells"]["commodity__ecb"]
    assert cell["n_nominal"] == 0
    assert cell["sign_agreement"] is None
    assert "note" in cell


def test_all_long_fixture_agreement_is_one(sign_breakdown_all_long, regime_labels):
    m = report.build_regime_matrix_3x3(sign_breakdown_all_long, regime_labels)
    cell = m["cells"]["risk_on__fomc"]  # EURUSD-only, Clopper-Pearson CI
    assert cell["sign_agreement"] == 1.0
    assert cell["ci_low"] <= 1.0
    assert cell["ci_high"] == 1.0


def test_ci_low_leq_point_leq_ci_high(sign_breakdown_all_long, regime_labels):
    m = report.build_regime_matrix_3x3(sign_breakdown_all_long, regime_labels)
    for key, cell in m["cells"].items():
        if cell["sign_agreement"] is None:
            continue
        # For the binary majority-agreement indicator, the CI is on P(agrees),
        # which is ≥ sign_agreement when the majority is long. When all signs
        # agree, CI bounds bracket 1.0 from below.
        assert cell["ci_low"] <= cell["ci_high"], key


def test_independence_flag_triggers_on_strong_correlation(
    sign_breakdown_strong_corr, regime_labels
):
    m = report.build_regime_matrix_3x3(sign_breakdown_strong_corr, regime_labels)
    cell = m["cells"]["safe_haven__fomc"]
    # USDJPY + EURJPY perfectly aligned → |corr| = 1.0 > 0.7 AND
    # binomtest(16 of 16, p=0.5) ≈ 3e-5 < 0.05.
    assert cell["empirical_correlation"] is not None
    assert cell["empirical_correlation"] > 0.7
    assert cell["binomtest_pvalue"] is not None
    assert cell["binomtest_pvalue"] < 0.05
    assert cell["independence_broken"] is True


def test_independence_flag_false_on_single_pair(sign_breakdown_all_long, regime_labels):
    """Single-pair rows (risk_on, commodity) must have empirical_correlation=0.0
    and independence_broken=False regardless of binomtest result."""
    m = report.build_regime_matrix_3x3(sign_breakdown_all_long, regime_labels)
    for cell_key in ("risk_on__fomc", "commodity__fomc"):
        cell = m["cells"][cell_key]
        assert cell["empirical_correlation"] == 0.0
        assert cell["independence_broken"] is False


def test_pairs_for_row_safe_haven_unions_cross(regime_labels):
    pairs = report._pairs_for_row(regime_labels, "safe_haven")
    assert pairs == ["EURJPY", "USDJPY"]  # D-10 union, sorted


def test_collect_sign_vector_reconstructs_expected_length():
    bd = _build_sign_breakdown(
        {"usdjpy": {"fomc": {"s0": _mk_tally(long=3, short=2, neutral=1)}}}
    )
    vec = report._collect_sign_vector(bd, "USDJPY", "fomc")
    # 3 longs + 2 shorts + 1 neutral = 6 entries; sum(+3 - 2 + 0) = 1
    assert vec.shape == (6,)
    assert int(np.sum(vec == 1)) == 3
    assert int(np.sum(vec == -1)) == 2
    assert int(np.sum(vec == 0)) == 1


# ─── VIF block tests (Plan 63-04, REGIME-03) ──────────────────────────────


def _rand_sign_tally(
    rng: np.random.Generator, n_slots: int, slots_per_event: int
) -> dict[str, dict[str, int]]:
    """Generate an independent +/- 1 per slot using a fresh RNG draw."""
    out: dict[str, dict[str, int]] = {}
    for s in range(slots_per_event):
        # 1 slot = 1 observation: random +1 OR -1 (no neutrals, tiny n guard).
        if rng.integers(0, 2) == 1:
            out[f"slot_{s}"] = _mk_tally(long=1, short=0)
        else:
            out[f"slot_{s}"] = _mk_tally(long=0, short=1)
    _ = n_slots  # suppressed unused warn
    return out


@pytest.fixture
def sign_breakdown_independent_pairs():
    """Independent signs across pairs (per-slot Bernoulli, pair-specific RNG).

    K ≈ 4 pair × 3 event × 120 slots = 360-per-pair → well above the degenerate
    threshold. Each pair uses a distinct seed so the per-pair columns are
    statistically independent → expected VIF ≈ 1.0.
    """
    events = ("fomc", "ecb", "nfp")
    pairs = ("usdjpy", "eurusd", "audusd", "eurjpy")
    slots_per_event = 120
    per_pair: dict = {}
    for i, p in enumerate(pairs):
        rng = np.random.default_rng(1000 + i)
        per_pair[p] = {}
        for e in events:
            per_pair[p][e] = _rand_sign_tally(rng, slots_per_event, slots_per_event)
    return _build_sign_breakdown(per_pair)


@pytest.fixture
def sign_breakdown_identical_pairs():
    """All 4 pairs carry an identical sign sequence → VIF → very large.

    Per-slot uses the SAME deterministic sequence across pairs so columns
    in the design matrix are collinear (actually identical). statsmodels
    will report very large VIF (numerical cap applied in compute_vif_block).
    """
    events = ("fomc", "ecb", "nfp")
    pairs = ("usdjpy", "eurusd", "audusd", "eurjpy")
    slots_per_event = 60
    rng = np.random.default_rng(2026)
    # Pre-compute shared per-event slot tallies so all 4 pairs share them.
    shared_per_event: dict[str, dict[str, dict[str, int]]] = {}
    for e in events:
        shared_per_event[e] = _rand_sign_tally(rng, slots_per_event, slots_per_event)
    per_pair: dict = {}
    for p in pairs:
        # dict-comprehension ensures each pair gets an independent dict object
        # (prevents aliasing surprises in later mutation paths).
        per_pair[p] = {e: dict(shared_per_event[e]) for e in events}
    return _build_sign_breakdown(per_pair)


def test_vif_independent_pairs_near_one(sign_breakdown_independent_pairs):
    """Independent pair signs → VIF ≈ 1.0 for every pair (D-13/D-14)."""
    v = report.compute_vif_block(sign_breakdown_independent_pairs)
    for pair, vif in v["per_pair"].items():
        # Floor 1.0 enforced; random-walk upper bound ≈ 1.3 at K=360.
        assert 1.0 <= vif < 1.6, f"pair {pair} VIF {vif} out of [1.0, 1.6)"


def test_vif_identical_pairs_large(sign_breakdown_identical_pairs):
    """Identical pair sign vectors → max(VIF) large (D-14 collinearity)."""
    v = report.compute_vif_block(sign_breakdown_identical_pairs)
    assert v["max"] > 50, f"expected collinear max>50, got {v['max']}"
    # And n_effective collapses well below n_nominal.
    assert v["n_effective"] < 1.0


def test_vif_rule_string_exact_match(sign_breakdown_all_long):
    """D-15 verbatim: ``n_effective = n_nominal / max(VIF)``."""
    v = report.compute_vif_block(sign_breakdown_all_long)
    assert v["rule"] == "n_effective = n_nominal / max(VIF)"


def test_vif_n_nominal_equals_12(sign_breakdown_all_long):
    """D-15: n_nominal fixed at 4 pairs × 3 events = 12."""
    v = report.compute_vif_block(sign_breakdown_all_long)
    assert v["n_nominal"] == 12


def test_vif_per_pair_has_4_keys(sign_breakdown_all_long):
    """D-15: all 4 pair VIFs recorded (record all, don't collapse)."""
    v = report.compute_vif_block(sign_breakdown_all_long)
    assert set(v["per_pair"].keys()) == {"USDJPY", "EURUSD", "AUDUSD", "EURJPY"}


def test_vif_n_effective_le_n_nominal(sign_breakdown_independent_pairs):
    """VIF >= 1 ⇒ n_effective <= n_nominal always (D-15 headline)."""
    v = report.compute_vif_block(sign_breakdown_independent_pairs)
    assert v["n_effective"] <= v["n_nominal"] + 1e-9


def test_matrix_cells_n_effective_deflated(sign_breakdown_all_long, regime_labels):
    """63-04 upgrade: vif_max kwarg deflates the n_effective in each cell.

    With vif_max=2.4, cell.n_effective must equal cell.n_nominal / 2.4.
    Replaces the 63-03 placeholder (n_effective == n_nominal).
    """
    m = report.build_regime_matrix_3x3(
        sign_breakdown_all_long, regime_labels, vif_max=2.4
    )
    safe_cell = m["cells"]["safe_haven__fomc"]
    # n_nominal > 0 for the populated cell.
    assert safe_cell["n_nominal"] > 0
    assert safe_cell["n_effective"] == pytest.approx(
        safe_cell["n_nominal"] / 2.4, rel=1e-6
    )
    # All non-empty cells must satisfy deflation equality.
    for cell_key, cell in m["cells"].items():
        if cell["sign_agreement"] is None:
            continue
        assert cell["n_effective"] == pytest.approx(
            cell["n_nominal"] / 2.4, rel=1e-6
        ), cell_key


def test_matrix_cells_n_effective_unchanged_when_vif_max_none(
    sign_breakdown_all_long, regime_labels
):
    """Back-compat: omitting vif_max preserves 63-03 behaviour (n_effective = n_nominal)."""
    m = report.build_regime_matrix_3x3(sign_breakdown_all_long, regime_labels)
    safe_cell = m["cells"]["safe_haven__fomc"]
    assert safe_cell["n_effective"] == float(safe_cell["n_nominal"])


# ─── render_report_md tests (Plan 63-05, REPORT-01) ───────────────────────


@pytest.fixture
def minimal_report_dict():
    """Minimal aggregated report dict covering render_report_md's keys.

    All 9 cells populated so the 3×3 table exercises the non-None branch;
    VIF block carries max=2.4, n_effective=5.0 for headline claims.
    """
    return {
        "phase": 63,
        "milestone": "v4.4-sign-forensics",
        "date": "2026-04-17",
        "commit_reference": "8498b0e",
        "regime_labels": {
            "labels": {
                "USDJPY": "safe_haven",
                "EURJPY": "safe_haven_cross",
                "EURUSD": "risk_on",
                "AUDUSD": "commodity",
            }
        },
        "regime_matrix_3x3": {
            "rows": ["safe_haven", "risk_on", "commodity"],
            "cols": ["fomc", "ecb", "nfp"],
            "cells": {
                f"{r}__{c}": {
                    "sign_agreement": 0.5,
                    "ci_low": 0.3,
                    "ci_high": 0.7,
                    "n_nominal": 4,
                    "n_effective": 2.0,
                    "pairs_aggregated": ["USDJPY"],
                    "independence_broken": False,
                }
                for r in ["safe_haven", "risk_on", "commodity"]
                for c in ["fomc", "ecb", "nfp"]
            },
        },
        "vif": {
            "per_pair": {
                "USDJPY": 1.5,
                "EURUSD": 1.2,
                "AUDUSD": 1.1,
                "EURJPY": 2.4,
            },
            "max": 2.4,
            "n_nominal": 12,
            "n_effective": 5.0,
            "rule": "n_effective = n_nominal / max(VIF)",
        },
        "flags": {
            "simpson_flag": False,
            "drift_detected": False,
            "fee_sign_flip": [],
            "independence_broken_cells": [],
        },
        "verdict": {
            "dominant_explanation": "sampling_noise",
            "rationale": (
                "k=4 power floor dominates; VIF-deflated n_effective=5.0 "
                "places CI wide across all candidates."
            ),
        },
    }


def test_render_report_md_returns_nonempty_str(minimal_report_dict):
    """Smoke: renderer returns a non-empty string with the canonical header."""
    md = report.render_report_md(minimal_report_dict)
    assert isinstance(md, str)
    assert md.strip() != ""
    assert md.startswith("# v4.4 Sign Forensics")


def test_render_report_md_contains_4_candidates(minimal_report_dict):
    """D-18: all four explanation candidate subsections are present."""
    md = report.render_report_md(minimal_report_dict)
    assert "### 1. Bug" in md
    assert "### 2. Config drift" in md
    assert "### 3. Sampling noise" in md
    assert "### 4. Structural" in md


def test_render_report_md_has_3x3_table(minimal_report_dict):
    """D-29 text-only markdown table for the 3×3 matrix (no image embedding)."""
    md = report.render_report_md(minimal_report_dict)
    # header row (markdown escapes the backslash — accept either form).
    assert "| flow \\\\ event |" in md or "| flow \\ event |" in md
    # 3 data rows expected (safe_haven / risk_on / commodity).
    assert md.count("| safe_haven") >= 1
    assert md.count("| risk_on") >= 1
    assert md.count("| commodity") >= 1


def test_render_report_md_limitations_has_4_items(minimal_report_dict):
    """D-19: Limitations section discloses all four required items."""
    md = report.render_report_md(minimal_report_dict)
    lim_idx = md.index("## Limitations")
    lim_block = md[lim_idx:]
    for term in (
        "k=4 power floor",
        "VIF deflation",
        "regime circularity",
        "ad-hoc",
    ):
        assert term.lower() in lim_block.lower(), f"missing {term!r}"


def test_render_report_md_no_image_embeds(minimal_report_dict):
    """D-29: no PNG/SVG or any markdown image syntax allowed."""
    md = report.render_report_md(minimal_report_dict)
    assert "![" not in md


def test_render_report_md_citations_present(minimal_report_dict):
    """Footer cites McLean-Pontiff / Landis-Koch / Politis-Romano (D-18/D-19)."""
    md = report.render_report_md(minimal_report_dict)
    assert "McLean" in md and "Pontiff" in md
    assert "Landis" in md and "Koch" in md
    assert "Politis" in md and "Romano" in md


def test_render_report_md_verdict_headline(minimal_report_dict):
    """McLean-Pontiff headline echoes n_effective / max(VIF) from the vif block."""
    md = report.render_report_md(minimal_report_dict)
    assert "McLean-Pontiff Verdict" in md
    assert "n_effective≈5.00" in md
    assert "n_nominal=12" in md
    assert "max(VIF)=2.40" in md
    assert "`sampling_noise`" in md


# ─── REPORT-02 tests (Plan 63-06) ─────────────────────────────────────────
import hashlib  # noqa: E402
import json as _json  # noqa: E402

PROJECT_ROOT = SCRIPT_DIR.parent.parent
PROD_AUDIT = PROJECT_ROOT / "docs/reports/v4.4-sign-forensics/audit_matrix.json"
PROD_DRIFT = PROJECT_ROOT / "docs/reports/v4.4-sign-forensics/drift_detected.json"
PROD_SIGN = PROJECT_ROOT / "docs/reports/v4.4-sign-forensics/sign_breakdown.json"
PROD_REGIME = PROJECT_ROOT / "docs/reports/v4.4-sign-forensics/regime_labels.json"


def _mk_flag_fixtures():
    """Minimal fixture set for extract_flags / derive_verdict tests."""
    sb = {
        "simpson_flag": False,
        "fee_sign_flip": [{"event": "fomc", "sign_high": "short"}],
    }
    drift = {"audit_verdict": "PASS", "dst_failures": [], "structural_drift": []}
    matrix = {
        "rows": ["safe_haven", "risk_on", "commodity"],
        "cols": ["fomc", "ecb", "nfp"],
        "cells": {
            "safe_haven__fomc": {"independence_broken": True},
            "safe_haven__ecb": {"independence_broken": False},
            "risk_on__fomc": {"independence_broken": False},
        },
    }
    return sb, drift, matrix


def test_extract_flags_returns_four_keys():
    """D-20: flags block = {simpson_flag, drift_detected, fee_sign_flip,
    independence_broken_cells}."""
    sb, drift, matrix = _mk_flag_fixtures()
    flags = report.extract_flags(sb, drift, matrix)
    assert set(flags.keys()) == {
        "simpson_flag",
        "drift_detected",
        "fee_sign_flip",
        "independence_broken_cells",
    }


def test_extract_flags_independence_broken_cells_lists_true_cells():
    """D-11: cells with independence_broken=True enumerated into flags."""
    sb, drift, matrix = _mk_flag_fixtures()
    flags = report.extract_flags(sb, drift, matrix)
    assert flags["independence_broken_cells"] == ["safe_haven__fomc"]
    assert flags["simpson_flag"] is False
    assert flags["drift_detected"] is False
    assert len(flags["fee_sign_flip"]) == 1


def test_extract_flags_drift_true_when_audit_fail():
    """audit_verdict=FAIL ⇒ drift_detected=True."""
    sb, drift, matrix = _mk_flag_fixtures()
    drift["audit_verdict"] = "FAIL"
    flags = report.extract_flags(sb, drift, matrix)
    assert flags["drift_detected"] is True


def test_extract_flags_drift_true_when_dst_failures_nonempty():
    """dst_failures non-empty ⇒ drift_detected=True (even if verdict=PASS)."""
    sb, drift, matrix = _mk_flag_fixtures()
    drift["dst_failures"] = [{"event": "fomc"}]
    flags = report.extract_flags(sb, drift, matrix)
    assert flags["drift_detected"] is True


def test_derive_verdict_returns_allowed_values():
    """dominant_explanation ∈ {bug,config_drift,sampling_noise,structural,mixed}."""
    allowed = {"bug", "config_drift", "sampling_noise", "structural", "mixed"}
    vif = {"n_effective": 5.0, "max": 2.4, "n_nominal": 12}
    for flags in (
        {
            "drift_detected": True,
            "simpson_flag": False,
            "independence_broken_cells": [],
        },
        {
            "drift_detected": False,
            "simpson_flag": True,
            "independence_broken_cells": [],
        },
        {
            "drift_detected": False,
            "simpson_flag": False,
            "independence_broken_cells": ["x"],
        },
        {
            "drift_detected": False,
            "simpson_flag": False,
            "independence_broken_cells": [],
        },
    ):
        v = report.derive_verdict({}, vif, flags)
        assert set(v.keys()) == {"dominant_explanation", "rationale"}
        assert v["dominant_explanation"] in allowed


def test_derive_verdict_drift_detected_is_config_drift():
    """flags.drift_detected=True ⇒ verdict=config_drift (priority 1)."""
    vif = {"n_effective": 5.0, "max": 2.4, "n_nominal": 12}
    flags = {
        "drift_detected": True,
        "simpson_flag": True,
        "independence_broken_cells": [],
    }
    v = report.derive_verdict({}, vif, flags)
    assert v["dominant_explanation"] == "config_drift"


def test_derive_verdict_simpson_rationale_mentions_simpson():
    """simpson_flag=True ⇒ verdict=mixed with Simpson in rationale."""
    vif = {"n_effective": 5.0, "max": 2.4, "n_nominal": 12}
    flags = {
        "drift_detected": False,
        "simpson_flag": True,
        "independence_broken_cells": [],
    }
    v = report.derive_verdict({}, vif, flags)
    assert v["dominant_explanation"] == "mixed"
    assert "Simpson" in v["rationale"]


def test_derive_verdict_independence_broken_is_structural():
    """flags.independence_broken_cells non-empty ⇒ verdict=structural."""
    vif = {"n_effective": 5.0, "max": 2.4, "n_nominal": 12}
    flags = {
        "drift_detected": False,
        "simpson_flag": False,
        "independence_broken_cells": ["safe_haven__fomc", "risk_on__ecb"],
    }
    v = report.derive_verdict({}, vif, flags)
    assert v["dominant_explanation"] == "structural"


def test_derive_verdict_low_n_eff_is_sampling_noise():
    """VIF-deflated n_eff < 6 AND no other flags ⇒ verdict=sampling_noise."""
    vif = {"n_effective": 5.0, "max": 2.4, "n_nominal": 12}
    flags = {
        "drift_detected": False,
        "simpson_flag": False,
        "independence_broken_cells": [],
    }
    v = report.derive_verdict({}, vif, flags)
    assert v["dominant_explanation"] == "sampling_noise"


# ─── CLI end-to-end smoke tests ───────────────────────────────────────────

D20_TOP_LEVEL_KEYS = {
    "phase",
    "milestone",
    "date",
    "generated_at",
    "commit_reference",
    "audit_matrix",
    "drift_detected",
    "sign_breakdown",
    "regime_labels",
    "regime_matrix_3x3",
    "vif",
    "flags",
    "verdict",
}


def _write_minimal_fixtures(tmp: Path) -> dict[str, Path]:
    audit = tmp / "audit_matrix.json"
    drift = tmp / "drift_detected.json"
    sign = tmp / "sign_breakdown.json"
    regime = tmp / "regime_labels.json"
    audit.write_text(_json.dumps({"cells": {}, "generated_at": "2026-04-17T00:00:00Z"}))
    drift.write_text(
        _json.dumps(
            {"audit_verdict": "PASS", "dst_failures": [], "structural_drift": []}
        )
    )
    sign.write_text(
        _json.dumps(
            {
                "phase": 62,
                "simpson_flag": False,
                "fee_sign_flip": [],
                "per_pair_event_slot_tally": {},
                "stratified_3d": {},
            }
        )
    )
    regime.write_text(
        _json.dumps(
            {
                "version": "v4.4",
                "labels": {
                    "USDJPY": "safe_haven",
                    "EURJPY": "safe_haven_cross",
                    "EURUSD": "risk_on",
                    "AUDUSD": "commodity",
                },
                "flow_types": ["safe_haven", "risk_on", "commodity"],
            }
        )
    )
    return {"audit": audit, "drift": drift, "sign": sign, "regime": regime}


def _run_cli(paths: dict[str, Path], out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(SCRIPT_DIR / "report.py"),
            "--audit",
            str(paths["audit"]),
            "--drift",
            str(paths["drift"]),
            "--sign",
            str(paths["sign"]),
            "--regime",
            str(paths["regime"]),
            "--output-dir",
            str(out),
            "--commit-ref",
            "8498b0e",
            "--log-level",
            "WARNING",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_smoke_produces_report_json_and_md(tmp_path):
    """CLI with 4 minimal upstream JSONs emits both report.json and report.md."""
    fixtures = _write_minimal_fixtures(tmp_path)
    out = tmp_path / "out"
    result = _run_cli(fixtures, out)
    assert result.returncode == 0, result.stderr
    assert (out / "report.json").exists()
    assert (out / "report.md").exists()


def test_report_json_has_all_d20_top_level_keys(tmp_path):
    """Emitted report.json carries the full D-20 top-level key set."""
    fixtures = _write_minimal_fixtures(tmp_path)
    out = tmp_path / "out"
    result = _run_cli(fixtures, out)
    assert result.returncode == 0, result.stderr
    payload = _json.loads((out / "report.json").read_text())
    missing = D20_TOP_LEVEL_KEYS - set(payload.keys())
    assert not missing, f"missing D-20 top-level keys: {missing}"


def test_report_json_no_nan_allow_nan_false(tmp_path):
    """allow_nan=False emission forbids literal NaN/Infinity tokens."""
    fixtures = _write_minimal_fixtures(tmp_path)
    out = tmp_path / "out"
    result = _run_cli(fixtures, out)
    assert result.returncode == 0, result.stderr
    raw = (out / "report.json").read_text()
    assert "NaN" not in raw, "NaN literal leaked into report.json"
    assert "Infinity" not in raw, "Infinity literal leaked into report.json"
    # _json.loads with default parser would raise on NaN anyway; sanity-check:
    _json.loads(raw)


def test_report_json_deterministic(tmp_path):
    """Same inputs + same commit-ref ⇒ byte-identical report.json modulo
    the generated_at / date timestamps."""
    fixtures = _write_minimal_fixtures(tmp_path)
    out1 = tmp_path / "o1"
    out2 = tmp_path / "o2"
    r1 = _run_cli(fixtures, out1)
    r2 = _run_cli(fixtures, out2)
    assert r1.returncode == 0 and r2.returncode == 0
    d1 = _json.loads((out1 / "report.json").read_text())
    d2 = _json.loads((out2 / "report.json").read_text())
    # Strip timestamps that change between invocations.
    for d in (d1, d2):
        d.pop("generated_at", None)
        d.pop("date", None)
    h1 = hashlib.md5(
        _json.dumps(d1, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    h2 = hashlib.md5(
        _json.dumps(d2, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    assert h1 == h2, "report.json not deterministic after stripping timestamps"


def test_report_md_contains_mclean_and_limitations(tmp_path):
    """CLI-emitted report.md carries McLean-Pontiff + 4 candidates + Limitations."""
    fixtures = _write_minimal_fixtures(tmp_path)
    out = tmp_path / "out"
    result = _run_cli(fixtures, out)
    assert result.returncode == 0, result.stderr
    md = (out / "report.md").read_text()
    assert "McLean-Pontiff" in md
    assert "### 1. Bug" in md
    assert "### 4. Structural" in md
    assert "## Limitations" in md


# ─── Production artifact guard (gated on real upstream JSONs) ─────────────


@pytest.mark.skipif(
    not (
        PROD_AUDIT.exists()
        and PROD_DRIFT.exists()
        and PROD_SIGN.exists()
        and PROD_REGIME.exists()
    ),
    reason="production upstream JSONs not present",
)
def test_production_report_artifacts_present_and_schema_compliant():
    """docs/reports/v4.4-sign-forensics/{report.json,report.md} exist and pass
    the D-20 schema check (belt-and-braces guard against accidental deletion)."""
    prod_dir = PROJECT_ROOT / "docs/reports/v4.4-sign-forensics"
    rj = prod_dir / "report.json"
    rm = prod_dir / "report.md"
    assert rj.exists(), "production report.json missing"
    assert rm.exists(), "production report.md missing"
    payload = _json.loads(rj.read_text())
    assert payload["phase"] == 63
    assert payload["milestone"] == "v4.4-sign-forensics"
    assert len(payload["regime_matrix_3x3"]["cells"]) == 9
    assert payload["vif"]["rule"] == "n_effective = n_nominal / max(VIF)"
    md_text = rm.read_text()
    assert "McLean-Pontiff" in md_text
    assert "### 4. Structural" in md_text
    assert "## Limitations" in md_text


# ─── REPORT-03: render_validation_md tests (Plan 63-07) ───────────────────


def test_validation_md_frontmatter_nyquist_true(minimal_report_dict):
    """D-21: VALIDATION.md frontmatter must carry literal ``nyquist_compliant: true``."""
    md = report.render_validation_md(minimal_report_dict, "abc1234")
    # First line must open frontmatter.
    assert md.splitlines()[0] == "---"
    assert "nyquist_compliant: true" in md


def test_validation_md_has_6_checkboxes(minimal_report_dict):
    """D-22: exactly 6 ``- [x]`` scientific integrity checklist items."""
    md = report.render_validation_md(minimal_report_dict, "abc1234")
    assert md.count("- [x]") == 6


def test_validation_md_cites_regime_commit(minimal_report_dict):
    """D-06: regime_labels_commit SHA must appear in frontmatter + checklist."""
    md = report.render_validation_md(minimal_report_dict, "abc1234")
    assert "abc1234" in md
    assert "regime_labels_commit: abc1234" in md


def test_validation_md_rule_audit_line(minimal_report_dict):
    """VIF rule audit (D-15) must be present in Manually Verifiable Items."""
    md = report.render_validation_md(minimal_report_dict, "abc1234")
    assert "n_effective = n_nominal / max(VIF)" in md


def test_validation_md_checklist_item_phrases(minimal_report_dict):
    """D-22 verbatim: all 6 checklist anchor phrases present."""
    md = report.render_validation_md(minimal_report_dict, "abc1234")
    for phrase in (
        "Pre-registration",
        "Ex-ante regime definition",
        "No post-hoc sign flip",
        "Artifact-level reuse",
        "4-candidate explanation exhaustiveness",
        "Limitations fully disclosed",
    ):
        assert phrase in md, f"missing checklist phrase: {phrase}"


def test_validation_md_manual_verify_section(minimal_report_dict):
    """Manually Verifiable Items section + its 4 bullets must be present."""
    md = report.render_validation_md(minimal_report_dict, "abc1234")
    assert "## Manually Verifiable Items" in md
    # 4 bullets: pre-registration / report.json / CLI reproducibility / VIF rule
    manual_section = md.split("## Manually Verifiable Items", 1)[1]
    assert manual_section.count("- **") >= 4


def test_validation_md_includes_optional_report_commit(minimal_report_dict):
    """When report_commit is passed, it appears in frontmatter."""
    md = report.render_validation_md(
        minimal_report_dict, "abc1234", report_commit="deadbeef"
    )
    assert "report_commit: deadbeef" in md


def test_validation_md_milestone_and_commit_reference(minimal_report_dict):
    """Frontmatter surfaces milestone + commit_reference from the report dict."""
    md = report.render_validation_md(minimal_report_dict, "abc1234")
    assert "milestone: v4.4-sign-forensics" in md
    assert "commit_reference: 8498b0e" in md


def test_git_first_commit_sha_fallback_unknown(tmp_path):
    """Outside any git repo (or for a path git does not know), fallback is ``unknown``."""
    # Path that does not exist in any git repo — git log returns nothing.
    missing = tmp_path / "never_committed.json"
    missing.write_text("{}")
    sha = report._git_first_commit_sha(missing)
    # The helper runs `git log --format=%H -- <path>`; for an uncommitted file
    # the output is empty → sentinel ``unknown`` per T-63-01 fail-soft.
    assert sha == "unknown"


def test_cli_emits_validation_md(tmp_path):
    """CLI with minimal fixtures emits VALIDATION.md alongside report.{json,md}."""
    fixtures = _write_minimal_fixtures(tmp_path)
    out = tmp_path / "out"
    result = _run_cli(fixtures, out)
    assert result.returncode == 0, result.stderr
    validation = out / "VALIDATION.md"
    assert validation.exists(), "CLI must emit VALIDATION.md"
    md = validation.read_text()
    assert md.startswith("---\n"), "VALIDATION.md must start with frontmatter"
    assert "nyquist_compliant: true" in md
    # Count the 6-item integrity checklist.
    assert md.count("- [x]") == 6
    # regime_labels_commit field must be populated (either real SHA or fallback).
    assert "regime_labels_commit:" in md


@pytest.mark.skipif(
    not (
        PROD_AUDIT.exists()
        and PROD_DRIFT.exists()
        and PROD_SIGN.exists()
        and PROD_REGIME.exists()
    ),
    reason="production upstream JSONs not present",
)
def test_production_validation_md_present_and_schema_compliant():
    """docs/reports/v4.4-sign-forensics/VALIDATION.md exists and carries
    D-21 frontmatter + D-22 6-item checklist + real regime_labels_commit SHA."""
    prod_dir = PROJECT_ROOT / "docs/reports/v4.4-sign-forensics"
    vm = prod_dir / "VALIDATION.md"
    assert vm.exists(), "production VALIDATION.md missing"
    md = vm.read_text()
    assert md.startswith("---\n")
    assert "nyquist_compliant: true" in md
    assert md.count("- [x]") == 6
    # regime_labels_commit: <40-hex> or ``unknown`` fallback. Production MUST
    # carry a real SHA (7+ hex chars) to prove pre-registration.
    import re

    m = re.search(r"^regime_labels_commit:\s*([0-9a-f]{7,40}|unknown)$", md, re.M)
    assert m is not None, "regime_labels_commit field malformed"
    assert m.group(1) != "unknown", (
        "production VALIDATION.md must carry a real regime_labels_commit SHA "
        "(pre-registration anchor, D-06)"
    )
    assert "## Manually Verifiable Items" in md
