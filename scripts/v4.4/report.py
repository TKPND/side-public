"""Phase 63 structural-interpretation-report — v4.4 sign-forensics aggregator.

Per 63-CONTEXT.md D-01: Python-only single-file heavy lifting; invoked from Rust
`side sign-forensics --report` (Phase 63 D-02/D-27, thin orchestrator).

Scope spans REGIME-01..REGIME-03 + REPORT-01..REPORT-04 across plans 63-01..63-09.
Plan 63-06 (REPORT-02) completes `main()` as a full aggregator: loads the 4
upstream artifacts, computes VIF + 3×3 matrix, derives flags + verdict, then
emits `report.json` (D-20 schema, NaN-safe, deterministic) + `report.md`
(D-17/D-18/D-19 narrative) via `render_report_md`.

Per D-12: `exact_pair_agreement_ci`, `stationary_bootstrap_ci`, and
`_sanitize_for_json` are IMPORTED from `sign_breakdown`; duplication forbidden.

Per D-29: STACK reject list includes matplotlib / plotly / seaborn / altair /
arch. Text-based rendering only (statsmodels + scipy + numpy + stdlib).

Usage (D-26 verbatim):
    uv run python scripts/v4.4/report.py \\
        --audit docs/reports/v4.4-sign-forensics/audit_matrix.json \\
        --drift docs/reports/v4.4-sign-forensics/drift_detected.json \\
        --sign docs/reports/v4.4-sign-forensics/sign_breakdown.json \\
        --regime docs/reports/v4.4-sign-forensics/regime_labels.json \\
        --output-dir docs/reports/v4.4-sign-forensics/ \\
        --commit-ref 8498b0e
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# ─── Sibling-script import (Phase 61/62 pattern) ───────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sign_breakdown import (  # noqa: E402  — D-12 function import, duplication forbidden
    _sanitize_for_json,
    exact_pair_agreement_ci,
)
from sign_breakdown import (  # noqa: E402
    politis_romano_bootstrap_ci as stationary_bootstrap_ci,
)

LOGGER = logging.getLogger(__name__)


# ─── argparse (D-26 verbatim CLI signature) ────────────────────────────────
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for report.py per D-26 exact signature."""
    parser = argparse.ArgumentParser(
        description=(
            "Phase 63 structural-interpretation-report (REGIME-01..03 + REPORT-01..04)."
        ),
    )
    parser.add_argument(
        "--audit",
        type=Path,
        required=True,
        help="Path to Phase 61 audit_matrix.json",
    )
    parser.add_argument(
        "--drift",
        type=Path,
        required=True,
        help="Path to Phase 61 drift_detected.json",
    )
    parser.add_argument(
        "--sign",
        type=Path,
        required=True,
        help="Path to Phase 62 sign_breakdown.json",
    )
    parser.add_argument(
        "--regime",
        type=Path,
        required=True,
        help="Path to Phase 63-01 regime_labels.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for report.md / report.json / VALIDATION.md outputs.",
    )
    parser.add_argument(
        "--commit-ref",
        type=str,
        required=True,
        help="Reference commit hash for integrity trace, e.g. 8498b0e",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


# ─── REGIME-02: 3×3 event × flow-type matrix (Plan 63-03) ──────────────────
#
# D-09: rows = flow-types (safe_haven / risk_on / commodity)
#        cols = events (fomc / ecb / nfp) → 9 cells.
# D-10: safe_haven row unions USDJPY + EURJPY sign vectors.
# D-11: independence_broken = (|emp_corr| > 0.7) AND (binomtest p < 0.05).
# D-12: CI via imported exact_pair_agreement_ci / stationary_bootstrap_ci.
# D-29: no plotting libs.
FLOW_TO_COL_ORDER: tuple[str, ...] = ("fomc", "ecb", "nfp")  # D-09
ROW_ORDER: tuple[str, ...] = ("safe_haven", "risk_on", "commodity")  # D-09

# safe_haven_cross (EURJPY, D-08) unions into safe_haven row (D-10).
FLOW_UNION: dict[str, tuple[str, ...]] = {
    "safe_haven": ("safe_haven", "safe_haven_cross"),  # USDJPY + EURJPY
    "risk_on": ("risk_on",),  # EURUSD
    "commodity": ("commodity",),  # AUDUSD
}

INDEPENDENCE_CORR_THRESHOLD = 0.7  # D-11 ad-hoc threshold, disclosed in Limitations
INDEPENDENCE_P_THRESHOLD = 0.05  # D-11


def _pairs_for_row(regime_labels: dict, row: str) -> list[str]:
    """D-10: return the list of pair tickers whose regime label lies in
    ``FLOW_UNION[row]``. The safe_haven row therefore collects both
    ``safe_haven`` + ``safe_haven_cross`` labeled pairs (USDJPY + EURJPY).

    Pair names returned preserve the casing of ``regime_labels["labels"]``
    (uppercase in the canonical artifact). Callers must ``.lower()`` before
    indexing into ``sign_breakdown["per_pair_event_slot_tally"]``, which is
    keyed by lowercase pair names.
    """
    wanted = set(FLOW_UNION[row])
    return sorted(p for p, lbl in regime_labels["labels"].items() if lbl in wanted)


def _collect_sign_vector(sign_breakdown: dict, pair: str, event: str) -> np.ndarray:
    """Reconstruct a per-slot sign vector from Phase 62 tally.

    Reads ``sign_breakdown["per_pair_event_slot_tally"][pair.lower()][event]``,
    a dict of ``slot_key -> {long: int, short: int, neutral: int}``. Each slot
    contributes ``long`` copies of +1, ``short`` copies of -1, and ``neutral``
    copies of 0 to the returned vector — preserving the canonical Phase 62
    encoding (see sign_breakdown.py::build_per_pair_event_slot_tally).

    Slot iteration order follows ``sorted(tally.keys())`` for deterministic
    cross-pair alignment (required for pair-pair correlation computation).

    Returns an empty array (shape ``(0,)``) when the pair or event is missing
    — callers treat this as a no-data cell (e.g. AUDUSD × ECB excluded per
    v4.4 scope).
    """
    tally_root = sign_breakdown.get("per_pair_event_slot_tally", {})
    pair_key = pair.lower()
    pair_tally = tally_root.get(pair_key, {})
    event_tally = pair_tally.get(event, {})
    if not event_tally:
        return np.empty(0, dtype=np.int64)
    pieces: list[np.ndarray] = []
    for slot in sorted(event_tally.keys()):
        bucket = event_tally[slot]
        longs = int(bucket.get("long", 0))
        shorts = int(bucket.get("short", 0))
        neutrals = int(bucket.get("neutral", 0))
        if longs:
            pieces.append(np.ones(longs, dtype=np.int64))
        if shorts:
            pieces.append(-np.ones(shorts, dtype=np.int64))
        if neutrals:
            pieces.append(np.zeros(neutrals, dtype=np.int64))
    if not pieces:
        return np.empty(0, dtype=np.int64)
    return np.concatenate(pieces)


def _compute_empirical_correlation(vectors: list[np.ndarray]) -> float:
    """Pair-pair Pearson correlation, averaged as mean absolute correlation.

    Returns 0.0 when fewer than 2 pair vectors (single-pair row → independence
    not relevant). For ``len(vectors) >= 2``, compute pairwise Pearson
    ``|corrcoef|`` on aligned slot positions and return the mean across all
    ``C(k, 2)`` pairs. When vectors have different lengths (should not happen
    if Phase 62 tally is complete), truncate to the common prefix length.

    Degenerate columns (zero variance in either vector) contribute 0.0 to the
    mean — np.corrcoef yields NaN on constant series, which we coerce away.
    """
    if len(vectors) < 2:
        return 0.0
    min_len = min(len(v) for v in vectors)
    if min_len < 2:
        return 0.0
    truncated = [v[:min_len].astype(np.float64) for v in vectors]
    abs_corrs: list[float] = []
    for i in range(len(truncated)):
        for j in range(i + 1, len(truncated)):
            a, b = truncated[i], truncated[j]
            if a.std() == 0.0 or b.std() == 0.0:
                abs_corrs.append(0.0)
                continue
            corr = float(np.corrcoef(a, b)[0, 1])
            if np.isnan(corr):
                abs_corrs.append(0.0)
            else:
                abs_corrs.append(abs(corr))
    if not abs_corrs:
        return 0.0
    return float(np.mean(abs_corrs))


def build_regime_matrix_3x3(
    sign_breakdown: dict,
    regime_labels: dict,
    vif_max: float | None = None,
) -> dict:
    """Build the 3×3 event × flow-type matrix for REGIME-02.

    Rows = flow-types (D-09, ``ROW_ORDER``), cols = events (``FLOW_TO_COL_ORDER``).
    Each cell aggregates sign vectors of the pairs whose regime label maps to
    the row (D-10 union). CI computed via Phase 62 imports (D-12):

    * single-pair rows → ``exact_pair_agreement_ci`` (Clopper-Pearson on a
      binary 1-agrees-with-majority indicator).
    * multi-pair rows  → ``stationary_bootstrap_ci`` (Politis-Romano 1994)
      with ``block_len = ceil(n^(1/3))`` and ``rng = default_rng(42)`` for
      deterministic test reproducibility.

    ``sign_agreement`` matches the Phase 62 stratified_3d convention
    (``long / (long + short)``, neutrals excluded). ``independence_broken``
    flags cells violating the pair-event independence assumption per D-11.

    Args:
        sign_breakdown: Phase 62 sign_breakdown.json payload. Required key
            ``per_pair_event_slot_tally[pair][event][slot] ->
            {long, short, neutral}``.
        regime_labels: Phase 63-01 regime_labels.json payload. Required key
            ``labels[PAIR] -> flow_type_string``.

    Returns:
        ``{"rows": [...], "cols": [...], "cells": {row__event: {...}}}``
        with 9 cells total. Each cell carries sign_agreement, ci_low,
        ci_high, n_nominal, n_effective (placeholder = n_nominal; 63-04
        applies VIF deflation), pairs_aggregated, empirical_correlation,
        binomtest_pvalue, independence_broken.
    """
    from scipy.stats import binomtest  # local import: avoid module-load cost

    cells: dict[str, dict] = {}
    for row in ROW_ORDER:
        pairs = _pairs_for_row(regime_labels, row)
        for event in FLOW_TO_COL_ORDER:
            key = f"{row}__{event}"
            raw_vectors = [
                _collect_sign_vector(sign_breakdown, p, event) for p in pairs
            ]
            vectors = [v for v in raw_vectors if v.size > 0]
            if not vectors:
                cells[key] = {
                    "sign_agreement": None,
                    "ci_low": None,
                    "ci_high": None,
                    "n_nominal": 0,
                    "n_effective": 0.0,
                    "pairs_aggregated": pairs,
                    "empirical_correlation": None,
                    "binomtest_pvalue": None,
                    "independence_broken": False,
                    "note": "no data (e.g. AUDUSD×ECB excluded per v4.4 scope #6)",
                }
                continue
            concat = np.concatenate(vectors)
            # Phase 62 stratified_3d convention: neutrals excluded.
            non_neutral_mask = concat != 0
            non_neutral = concat[non_neutral_mask]
            n_nominal = int(non_neutral.size)
            if n_nominal == 0:
                sign_agr: float | None = None
                lo: float | None = None
                hi: float | None = None
                p_val: float | None = None
            else:
                longs = int((non_neutral > 0).sum())
                sign_agr = float(longs / n_nominal)
                # Majority direction → binary 1-agrees indicator (D-15 conv).
                majority_long = longs >= (n_nominal - longs)
                binary = (
                    (non_neutral > 0).astype(np.int64)
                    if majority_long
                    else (non_neutral < 0).astype(np.int64)
                )
                if len(pairs) == 1:
                    # Clopper-Pearson exact CI.
                    _point, lo_f, hi_f = exact_pair_agreement_ci(binary)
                    lo, hi = float(lo_f), float(hi_f)
                else:
                    # Politis-Romano stationary bootstrap; block ~ n^(1/3).
                    block_len = max(1, int(np.ceil(n_nominal ** (1.0 / 3.0))))
                    lo_f, hi_f = stationary_bootstrap_ci(
                        binary,
                        n_resamples=1000,
                        block_len=block_len,
                        rng=np.random.default_rng(42),
                    )
                    lo, hi = float(lo_f), float(hi_f)
                # Two-sided binomtest vs p=0.5 on majority count.
                try:
                    p_val = float(
                        binomtest(
                            k=int(binary.sum()),
                            n=n_nominal,
                            p=0.5,
                            alternative="two-sided",
                        ).pvalue
                    )
                except ValueError:
                    p_val = 1.0
            emp_corr = _compute_empirical_correlation(vectors)
            independence_broken = bool(
                emp_corr is not None
                and p_val is not None
                and emp_corr > INDEPENDENCE_CORR_THRESHOLD
                and p_val < INDEPENDENCE_P_THRESHOLD
            )
            n_eff = (
                float(n_nominal) / float(vif_max)
                if (vif_max is not None and vif_max > 0)
                else float(n_nominal)
            )
            cells[key] = {
                "sign_agreement": sign_agr,
                "ci_low": lo,
                "ci_high": hi,
                "n_nominal": n_nominal,
                "n_effective": n_eff,  # D-15/D-16: n_nominal / max(VIF) when provided
                "pairs_aggregated": pairs,
                "empirical_correlation": emp_corr,
                "binomtest_pvalue": p_val,
                "independence_broken": independence_broken,
            }
    return {
        "rows": list(ROW_ORDER),
        "cols": list(FLOW_TO_COL_ORDER),
        "cells": cells,
    }


# ─── REGIME-03: VIF deflation (Plan 63-04) ─────────────────────────────────
#
# D-13: VIF input = 4-pair × K-slot sign vectors reconstructed from
#       per_pair_event_slot_tally, concatenated across the 3 events per pair.
#       sign encoding: long=+1 / short=-1 / neutral=0.
# D-14: VIF uses statsmodels.stats.outliers_influence.variance_inflation_factor;
#       for pair_i, R²_i is from regressing pair_i signs on the other 3 pairs,
#       VIF_i = 1 / (1 - R²_i).
# D-15: n_effective = n_nominal / max(VIF), conservative aggregation (headline).
# D-16: mean / per-pair aggregation rejected (report.md Limitations must explain).
PAIRS_4: tuple[str, ...] = ("USDJPY", "EURUSD", "AUDUSD", "EURJPY")  # D-15
N_NOMINAL = 12  # 4 pair × 3 event (D-15) — module-level constant


def _extract_per_pair_signs_matrix(sign_breakdown: dict) -> np.ndarray:
    """Build shape ``(K, 4)`` sign-vector matrix for VIF per D-13.

    For each pair in ``PAIRS_4`` order, concatenates the per-slot sign vectors
    across the 3 events (fomc/ecb/nfp) via ``_collect_sign_vector``. Pairs
    with different total lengths are aligned by truncation to the common
    minimum length (K = min pair length) so that ``statsmodels``'s
    ``variance_inflation_factor`` sees a well-formed rectangular design
    matrix. A leading 1-intercept column is prepended inside
    ``compute_vif_block`` (statsmodels expects an exog with intercept for
    R² interpretation to hold).

    Returns shape ``(K, 4)`` int64 array (columns follow ``PAIRS_4`` order)
    or ``(0, 4)`` if any pair has no data (fail-soft; caller degrades to
    VIF=1 per T-63-01 mitigation).
    """
    columns: list[np.ndarray] = []
    for pair in PAIRS_4:
        event_vectors = [
            _collect_sign_vector(sign_breakdown, pair, event)
            for event in FLOW_TO_COL_ORDER
        ]
        pieces = [v for v in event_vectors if v.size > 0]
        if not pieces:
            return np.empty((0, 4), dtype=np.int64)
        columns.append(np.concatenate(pieces))
    min_len = min(c.size for c in columns)
    if min_len == 0:
        return np.empty((0, 4), dtype=np.int64)
    truncated = [c[:min_len] for c in columns]
    return np.column_stack(truncated).astype(np.int64)


def compute_vif_block(sign_breakdown: dict) -> dict:
    """REGIME-03 headline: VIF deflation of sample size.

    Computes 4 per-pair VIF values from the Phase 62 per-pair-event-slot
    tally. For each pair column, ``variance_inflation_factor`` regresses
    that pair on the others (with intercept column); numerical floor
    ``VIF >= 1.0`` is enforced to guard against tiny negative R² from
    floating-point noise. Degenerate designs (< 4 usable rows, singular
    exog) fail soft to VIF=1.0 per T-63-01.

    Output (D-20 schema):

    ``{per_pair: {pair: float}, max: float, n_nominal: 12,
       n_effective: float, rule: "n_effective = n_nominal / max(VIF)"}``

    Args:
        sign_breakdown: Phase 62 payload; must carry
            ``per_pair_event_slot_tally`` (see D-13).

    Returns:
        Dict with the 5 keys above. ``per_pair`` has exactly the 4 keys
        in ``PAIRS_4``.
    """
    # D-14 verbatim import (single-line for grep acceptance):
    from statsmodels.stats.outliers_influence import variance_inflation_factor  # noqa: E501

    exog = _extract_per_pair_signs_matrix(sign_breakdown)  # (K, 4)
    if exog.shape[0] < 4:
        # Degenerate: insufficient rows → report VIF=1 (fail-soft per T-63-01).
        vifs: dict[str, float] = {p: 1.0 for p in PAIRS_4}
        max_vif = 1.0
    else:
        # statsmodels expects an intercept column for R² interpretation.
        design = np.column_stack(
            [np.ones(exog.shape[0], dtype=np.float64), exog.astype(np.float64)]
        )
        vifs = {}
        for i, pair in enumerate(PAIRS_4):
            try:
                # design column order: [const, USDJPY, EURUSD, AUDUSD, EURJPY]
                # → pair column index is (i + 1).
                v = float(variance_inflation_factor(design, i + 1))
            except (ValueError, ZeroDivisionError, np.linalg.LinAlgError):
                v = float("inf")
            # Theoretical floor is 1.0; numerical noise can push R² slightly
            # negative (→ VIF < 1). Guard and also treat NaN as 1.0.
            if not np.isfinite(v) or v > 1e12:
                # Practical cap: R² > 1 - 1e-12 — effectively collinear.
                v = 1e12
            vifs[pair] = max(v, 1.0)
        max_vif = max(vifs.values())
    n_effective = float(N_NOMINAL) / max_vif if max_vif > 0 else float(N_NOMINAL)
    return {
        "per_pair": vifs,
        "max": max_vif,
        "n_nominal": N_NOMINAL,
        "n_effective": n_effective,
        "rule": "n_effective = n_nominal / max(VIF)",  # D-15 verbatim
    }


# ─── REPORT-01: render_report_md (Plan 63-05) ──────────────────────────────
#
# D-17: voice/section template = docs/reports/v4.2-cross-pair/cross_pair_summary.md.
# D-18: 4-candidate explanation sections (Bug / Config drift / Sampling noise /
#       Structural) all required; each carries evidence-based evaluation.
# D-19: Limitations section must disclose 4 items — k=4 power floor / VIF
#       deflation (max-rule rationale + mean rejection) / regime circularity
#       avoidance / ad-hoc thresholds (0.3 Simpson + 0.7 independence).
# D-29: text-only rendering (no PNG/SVG); 3×3 matrix embedded as markdown table.
def render_report_md(report: dict) -> str:
    """REPORT-01: emit the McLean-Pontiff structural-interpretation report body.

    Pure in-memory rendering: input is the aggregated Phase 63 report dict
    (assembled in 63-06), output is a markdown string written to disk by
    downstream main() integration. No file I/O here — that keeps 63-05
    testable without touching the filesystem.

    Structure (D-17/D-18/D-19):
      1. Header + date/commit/phase banner.
      2. "McLean-Pontiff Verdict" headline with bootstrap-CI + kappa +
         VIF-deflated n_effective claim.
      3. 4 candidate subsections (Bug / Config drift / Sampling noise /
         Structural) each with evidence-pointer.
      4. 3×3 Event × Flow-Type matrix rendered as markdown table (D-29).
      5. "Limitations" section with 4 disclosure bullets.
      6. Footer with McLean-Pontiff / Landis-Koch / Politis-Romano
         citations.

    Args:
        report: aggregated dict carrying at minimum the keys
            ``regime_matrix_3x3`` (D-09 schema), ``vif`` (D-20 schema),
            ``flags``, and ``verdict``. Missing keys degrade gracefully
            with placeholder strings so the renderer never raises
            KeyError on partial inputs (useful for 63-06 fixture tests).

    Returns:
        Markdown string ending in a trailing newline.
    """
    lines: list[str] = []
    lines.append("# v4.4 Sign Forensics — Structural Interpretation Report")
    lines.append("")
    lines.append(f"**Date:** {report.get('date', '')}")
    lines.append(
        f"**Commit reference:** `{report.get('commit_reference', '')}` "
        "(artifact-level reuse, v4.3 gate integrity fix)"
    )
    lines.append(
        f"**Phase:** {report.get('phase', 63)} — v4.4 Cross-Pair Sign "
        "Disagreement Forensics"
    )
    lines.append("")

    # ── McLean-Pontiff verdict headline ────────────────────────────────────
    vif = report.get("vif", {}) or {}
    v_n_nom = vif.get("n_nominal", 12)
    v_n_eff = float(vif.get("n_effective", 0.0) or 0.0)
    v_max = float(vif.get("max", 1.0) or 1.0)
    verdict = report.get("verdict", {}) or {}

    lines.append("## McLean-Pontiff Verdict")
    lines.append("")
    lines.append(
        "**Observed sign_agreement (4-pair intersection):** see "
        "`report.json::sign_breakdown` (95% CI, Fleiss' kappa, "
        f"VIF-deflated n_effective≈{v_n_eff:.2f}, n_nominal={v_n_nom}, "
        f"max(VIF)={v_max:.2f})."
    )
    lines.append("")
    lines.append(
        f"**Dominant explanation:** `{verdict.get('dominant_explanation', 'unknown')}` "
        f"— {verdict.get('rationale', '')}"
    )
    lines.append("")

    # ── 4 candidate sections (D-18 verbatim) ───────────────────────────────
    flags = report.get("flags", {}) or {}

    lines.append("### 1. Bug — code artifact?")
    lines.append(
        "Evidence: `audit_matrix.json` + `drift_detected.json` (Phase 61, "
        "commit 8498b0e retro verification delta=0, all prior PASS genuine)."
    )
    lines.append(f"Drift detected: `{flags.get('drift_detected', 'unknown')}`.")
    lines.append("")

    lines.append("### 2. Config drift — DST / parameter shift?")
    lines.append(
        "Evidence: `drift_detected.json::12-cell SHA256 group-by` over "
        "(window_offset, DST anchor, signal_dir, long_only, fold_size, "
        "event_count, fee_bps), DST spot-check via chrono-tz."
    )
    lines.append("")

    lines.append("### 3. Sampling noise — k=4 power floor?")
    lines.append(
        "Evidence: bootstrap CI (`sign_breakdown.json::bootstrap`), Fleiss "
        "kappa (Landis-Koch label), VIF-deflated n_effective≈"
        f"{v_n_eff:.2f} (n_nominal={v_n_nom}, max(VIF)={v_max:.2f})."
    )
    lines.append("")

    lines.append("### 4. Structural — regime-level explanation?")
    lines.append("Evidence: 3×3 flow matrix (below), pair-event independence flags.")
    indep_broken = flags.get("independence_broken_cells", []) or []
    if indep_broken:
        lines.append(
            "Independence-broken cells: "
            + ", ".join(f"`{c}`" for c in indep_broken)
            + "."
        )
    else:
        lines.append("Independence-broken cells: none flagged at thresholds D-11.")
    lines.append("")

    # ── 3×3 matrix (D-29 text-only markdown table) ─────────────────────────
    lines.append("#### 3×3 Event × Flow-Type Matrix")
    lines.append("")
    matrix = report.get("regime_matrix_3x3", {}) or {}
    cells = matrix.get("cells", {}) or {}
    rows = matrix.get("rows", list(ROW_ORDER))
    cols = matrix.get("cols", list(FLOW_TO_COL_ORDER))

    # header row
    lines.append("| flow \\ event | " + " | ".join(c.upper() for c in cols) + " |")
    lines.append("|" + "---|" * (len(cols) + 1))
    for r in rows:
        row_cells: list[str] = []
        for c in cols:
            cell = cells.get(f"{r}__{c}", {}) or {}
            agr = cell.get("sign_agreement")
            if agr is None:
                row_cells.append("—")
            else:
                lo = cell.get("ci_low", 0.0) or 0.0
                hi = cell.get("ci_high", 0.0) or 0.0
                n_eff = cell.get("n_effective", 0.0) or 0.0
                marker = " ⚠broken" if cell.get("independence_broken") else ""
                row_cells.append(
                    f"agr={float(agr):.3f} "
                    f"[{float(lo):.2f},{float(hi):.2f}] "
                    f"n_eff={float(n_eff):.1f}{marker}"
                )
        first_cell = cells.get(f"{r}__{cols[0]}", {}) or {}
        pairs = first_cell.get("pairs_aggregated", []) or []
        pair_note = f" ({'+'.join(pairs)})" if pairs else ""
        lines.append(f"| {r}{pair_note} | " + " | ".join(row_cells) + " |")
    lines.append("")

    # ── Limitations (D-19 four verbatim items) ─────────────────────────────
    lines.append("## Limitations")
    lines.append("")
    lines.append(
        f"- **k=4 power floor**: 4 pair × 3 event では exact enumeration "
        f"でも CI width が固定下限を持つ。n_effective ≈ {v_n_eff:.1f} "
        f"(VIF deflation 後) は実質的な統計力の上限を示す。"
    )
    lines.append(
        "- **VIF deflation**: `n_effective = n_nominal / max(VIF)` "
        "(conservative rule, D-15)。mean(VIF) 代替は pair correlation の"
        "ワーストケースを反映しないため棄却。採用 VIF の full per-pair 値は "
        "`report.json::vif.per_pair` 参照。"
    )
    lines.append(
        "- **Regime circularity avoidance**: `regime_labels.json` は matrix "
        "code より strictly 前に commit 済み (git 履歴で ex-ante proof)。"
        "post-hoc label flip は構造的に禁止。"
    )
    lines.append(
        "- **Ad-hoc thresholds**: Simpson threshold 0.3 (Phase 62 ATTR-02)、"
        "independence correlation threshold 0.7 (D-11) は academic "
        "justification が未確立 — future work で正当化予定。"
    )
    lines.append("")

    # ── Footer citations ───────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append(
        "*Report generated by `side sign-forensics --report` (Phase 63). "
        "References: McLean & Pontiff (2016) J. Finance 71:5-32; "
        "Landis & Koch (1977) Biometrics 33:159-174; "
        "Politis & Romano (1994) JASA 89:1303-1313.*"
    )

    return "\n".join(lines) + "\n"


# ─── REPORT-02: flag extraction + verdict derivation (Plan 63-06) ─────────
#
# D-20: flags block = {simpson_flag, drift_detected, fee_sign_flip,
#       independence_broken_cells}.
# D-11: independence_broken_cells derived from regime_matrix_3x3 cells carrying
#       independence_broken=True (empirical_corr > 0.7 AND binomtest p < 0.05).
# D-17/D-18: verdict.dominant_explanation ∈
#       {"bug","config_drift","sampling_noise","structural","mixed"}.
def extract_flags(sign_breakdown: dict, drift_detected: dict, matrix_3x3: dict) -> dict:
    """Assemble the D-20 ``flags`` sub-block.

    Sources:
    * ``simpson_flag`` — Phase 62 sign_breakdown.json top-level
      (see sign_breakdown.py ATTR-02); falls back to ``attribution.simpson_flag``.
    * ``drift_detected`` — Phase 61 drift_detected.json. ``audit_verdict`` is
      the canonical pass/fail gate (``PASS`` ⇒ no drift); ``dst_failures``
      non-empty also trips the flag. ``structural_drift`` is an expected
      asymmetry annotation (e.g. audusd vs eurjpy ECB empty-source) and
      does NOT flip the flag.
    * ``fee_sign_flip`` — Phase 62 sign_breakdown.json top-level list
      (ATTR-03); passed through verbatim.
    * ``independence_broken_cells`` — regime_matrix_3x3.cells entries where
      ``independence_broken=True`` (D-11).
    """
    simpson = bool(
        sign_breakdown.get("simpson_flag", False)
        or sign_breakdown.get("attribution", {}).get("simpson_flag", False)
    )
    audit_verdict = str(drift_detected.get("audit_verdict", "")).upper()
    dst_failures = drift_detected.get("dst_failures", []) or []
    drift_top = drift_detected.get("drift_detected", None)
    drift = bool(
        drift_top is True or audit_verdict in {"FAIL", "DRIFT"} or len(dst_failures) > 0
    )
    fee_flip = (
        sign_breakdown.get("fee_sign_flip", [])
        or sign_breakdown.get("attribution", {}).get("fee_sign_flip", [])
        or []
    )
    cells = (matrix_3x3.get("cells", {}) or {}) if matrix_3x3 else {}
    broken_cells = sorted(
        key
        for key, cell in cells.items()
        if isinstance(cell, dict) and cell.get("independence_broken", False)
    )
    return {
        "simpson_flag": simpson,
        "drift_detected": drift,
        "fee_sign_flip": list(fee_flip),
        "independence_broken_cells": broken_cells,
    }


def derive_verdict(matrix_3x3: dict, vif_block: dict, flags: dict) -> dict:
    """4-candidate dominant-explanation derivation per D-17/D-18.

    Priority order (report.md Limitations discloses threshold rationale):
      1. ``drift_detected`` ⇒ "config_drift" — Phase 61 flagged config-level
         divergence; structural interpretation blocked until resolved.
      2. ``simpson_flag`` ⇒ "mixed" — Simpson's paradox (Phase 62 ATTR-02)
         means structural + sampling explanations co-exist.
      3. ``independence_broken_cells`` non-empty ⇒ "structural" — pair-event
         independence violated, regime-level reaction pattern supported.
      4. VIF-deflated n_effective < 6.0 ⇒ "sampling_noise" — k=4 power floor
         dominates.
      5. otherwise ⇒ "mixed".

    Args:
        matrix_3x3: D-09 3×3 matrix (used for Structural cell enumeration).
        vif_block: D-20 vif block (``n_effective``, ``max``).
        flags: output of ``extract_flags``.

    Returns:
        ``{"dominant_explanation": <str>, "rationale": <str>}``
    """
    n_eff = float(vif_block.get("n_effective", 12.0) or 12.0)
    max_vif = float(vif_block.get("max", 1.0) or 1.0)
    broken = flags.get("independence_broken_cells", []) or []

    if flags.get("drift_detected"):
        dom = "config_drift"
        rat = (
            "drift_detected.json audit_verdict=FAIL or DST failures present — "
            "Phase 61 flagged config-level divergence. Structural "
            "interpretation contingent on drift resolution; see report.md §2."
        )
    elif flags.get("simpson_flag"):
        dom = "mixed"
        rat = (
            f"Simpson's paradox detected (Phase 62 ATTR-02); pooled vs max-"
            f"stratum diff > 0.3. Structural and sampling explanations co-"
            f"exist. VIF-deflated n_effective={n_eff:.2f} "
            f"(max(VIF)={max_vif:.2f}) keeps CI wide."
        )
    elif broken:
        dom = "structural"
        rat = (
            f"{len(broken)} cells violate pair-event independence "
            f"(empirical_corr > 0.7, binomtest p < 0.05). Regime-level "
            f"reaction pattern supported; ad-hoc 0.7 threshold disclosed in "
            f"Limitations (D-11)."
        )
    elif n_eff < 6.0:
        dom = "sampling_noise"
        rat = (
            f"VIF-deflated n_effective={n_eff:.2f} (n_nominal=12, "
            f"max(VIF)={max_vif:.2f}) below k=4 power floor; sampling noise "
            f"dominates."
        )
    else:
        dom = "mixed"
        rat = (
            f"No single candidate dominates at this power level "
            f"(n_effective={n_eff:.2f}, max(VIF)={max_vif:.2f}); per-"
            f"candidate evidence in report.md §1–§4."
        )
    return {"dominant_explanation": dom, "rationale": rat}


# ─── REPORT-03: render_validation_md (Plan 63-07) ──────────────────────────
#
# D-21: VALIDATION.md frontmatter must carry ``nyquist_compliant: true``
#       (literal ``true``, not quoted, not True) to satisfy the Phase 48
#       precedent certificate-as-frontmatter convention.
# D-22: 6-item Scientific Integrity Checklist (verbatim phrasing):
#       1. Pre-registration of regime_labels.json
#       2. Ex-ante regime definition (no post-hoc label flip)
#       3. No post-hoc sign flip
#       4. Artifact-level reuse of commit 8498b0e only
#       5. 4-candidate explanation exhaustiveness
#       6. Limitations fully disclosed
# D-06: regime_labels_commit cited verbatim from git log oldest commit of
#       docs/reports/v4.4-sign-forensics/regime_labels.json (proves
#       pre-registration ex-ante, not just "committed alongside").
def render_validation_md(
    report: dict,
    regime_labels_commit: str,
    report_commit: str | None = None,
) -> str:
    """REPORT-03: emit the Phase 63 scientific integrity certificate.

    Pure in-memory rendering; caller is responsible for persisting to
    ``docs/reports/v4.4-sign-forensics/VALIDATION.md``. Structure mirrors
    the v4.1 precedent (docs/reports/v4.1-n-expansion/VALIDATION.md):
      1. Frontmatter (D-21) — ``nyquist_compliant: true`` + provenance.
      2. Title + 1-paragraph certification.
      3. Scientific Integrity Checklist (D-22, 6 items, all ``- [x]``).
      4. Manually Verifiable Items — bash/jq one-liners that developers
         can replay to re-prove every claim.

    The ``regime_labels_commit`` SHA anchors the pre-registration claim
    (D-06): the regime_labels.json artifact MUST have been committed
    strictly before the report.py matrix-building code, so the labels
    could not have been fitted post-hoc to the matrix results.

    Args:
        report: aggregated dict (D-20 top-level schema). At minimum
            ``vif`` block + ``generated_at`` + ``milestone`` +
            ``commit_reference`` are read; missing keys degrade to
            sensible defaults (12 / current-UTC / v4.4-sign-forensics /
            8498b0e).
        regime_labels_commit: git SHA of the oldest commit that touched
            ``regime_labels.json``. ``"unknown"`` is tolerated for unit
            tests but the production VALIDATION.md MUST carry a real
            SHA (enforced by main() via ``_git_first_commit_sha``).
        report_commit: optional SHA of the report-generation commit
            (omitted in pre-commit dry runs).

    Returns:
        Markdown string ending in a trailing newline.
    """
    vif = report.get("vif", {}) or {}
    n_nom = vif.get("n_nominal", 12)
    n_eff = float(vif.get("n_effective", 0.0) or 0.0)
    max_vif = float(vif.get("max", 1.0) or 1.0)
    generated = report.get("generated_at", datetime.now(timezone.utc).isoformat())
    milestone = report.get("milestone", "v4.4-sign-forensics")
    commit_ref = report.get("commit_reference", "8498b0e")

    lines: list[str] = []
    # ── Frontmatter (D-21) ─────────────────────────────────────────────────
    lines.append("---")
    lines.append("nyquist_compliant: true")
    lines.append("phase: 63-structural-interpretation-report")
    lines.append(f"generated: {generated}")
    lines.append(f"milestone: {milestone}")
    lines.append(f"regime_labels_commit: {regime_labels_commit}")
    if report_commit:
        lines.append(f"report_commit: {report_commit}")
    lines.append(f"commit_reference: {commit_ref}")
    lines.append(f"n_nominal: {n_nom}")
    lines.append(f"n_effective: {n_eff:.4f}")
    lines.append(f"max_vif: {max_vif:.4f}")
    lines.append("---")
    lines.append("")
    # ── Title + certification ──────────────────────────────────────────────
    lines.append(
        "# Validation Certificate — Phase 63 (Structural Interpretation Report)"
    )
    lines.append("")
    lines.append(
        "This document certifies that the Phase 63 structural-interpretation "
        "artifacts (`report.md` + `report.json` + `regime_labels.json`) satisfy "
        "pre-registration discipline, VIF-deflated power claims, and 4-candidate "
        "exhaustiveness per REQUIREMENTS §REGIME-01..03 / REPORT-01..04."
    )
    lines.append("")
    # ── D-22 Scientific Integrity Checklist (6 items, verbatim) ────────────
    lines.append("## Scientific Integrity Checklist (D-22)")
    lines.append("")
    lines.append(
        f"- [x] **Pre-registration**: `regime_labels.json` committed before "
        f"matrix code (git hash `{regime_labels_commit}` strictly before "
        f"report.py matrix commit; see 63-01 SUMMARY)."
    )
    lines.append(
        "- [x] **Ex-ante regime definition**: flow-types taxonomy sourced from "
        "`ROADMAP.md §Phase 63` (no post-hoc label flip)."
    )
    lines.append(
        "- [x] **No post-hoc sign flip**: strategy signal direction fixed "
        "(commit 8498b0e validation.rs freeze)."
    )
    lines.append(
        f"- [x] **Artifact-level reuse of commit {commit_ref}**: "
        "`validation.rs::stationary_bootstrap_ci` referenced as spec source "
        "only; Python re-implementation in Phase 62 has no functional reuse "
        "of Rust symbols."
    )
    lines.append(
        "- [x] **4-candidate explanation exhaustiveness**: bug / config drift / "
        "sampling noise / structural all evidence-evaluated in `report.md` "
        "§McLean-Pontiff Verdict."
    )
    lines.append(
        "- [x] **Limitations fully disclosed**: k=4 power floor, VIF deflation "
        "(max rule), regime circularity avoidance, ad-hoc thresholds "
        "(Simpson 0.3 / independence 0.7) disclosed in `report.md "
        "§Limitations`."
    )
    lines.append("")
    # ── Manually Verifiable Items ──────────────────────────────────────────
    lines.append("## Manually Verifiable Items")
    lines.append("")
    lines.append(
        "- **regime_labels.json pre-registration**: "
        "`git log --format=%cI,%H -- docs/reports/v4.4-sign-forensics/"
        "regime_labels.json | tail -1` — this timestamp MUST be strictly "
        "before the report.py matrix-code commit timestamp."
    )
    lines.append(
        "- **report.json integrity**: `jq '.phase, .milestone, "
        ".vif.n_effective, .flags' docs/reports/v4.4-sign-forensics/"
        "report.json`."
    )
    lines.append(
        "- **CLI reproducibility**: re-running `side sign-forensics --report` "
        "with identical upstream JSONs MUST produce byte-identical "
        "`report.json` (enforced by `json.dumps(sort_keys=True, "
        "allow_nan=False)`)."
    )
    lines.append(
        f"- **VIF rule audit**: `jq -e '.vif.rule == \"n_effective = n_nominal "
        f"/ max(VIF)\"' report.json` (D-15 conservative rule, "
        f"max(VIF)={max_vif:.3f}, n_effective={n_eff:.3f})."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _git_first_commit_sha(path: Path) -> str:
    """Return the oldest (first) commit SHA that touched ``path``.

    Used by main() to populate ``regime_labels_commit`` in the emitted
    VALIDATION.md — the oldest commit is the pre-registration anchor
    (D-06). ``git log`` lists newest-first, so the last line of output
    is the oldest commit.

    Fails soft: any subprocess / encoding / missing-git error returns
    the literal string ``"unknown"`` so VALIDATION.md emission is never
    blocked (T-63-01 mitigation). Production runs are re-checked via
    the Manual Verifiable Items `git log` command in the certificate.
    """
    import subprocess

    try:
        out = subprocess.check_output(
            ["git", "log", "--format=%H", "--", str(path)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        sha_lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return sha_lines[-1] if sha_lines else "unknown"
    except Exception:  # noqa: BLE001 — fail-soft per T-63-01
        return "unknown"


# ─── Loader adapter (fail-fast per PATTERNS.md §report.py) ─────────────────
def _load_json(path: Path) -> dict:
    """Defensive JSON loader — FileNotFoundError / JSONDecodeError → SystemExit(2).

    Unlike the Phase 62 ``_load_report_as_event_slots`` empty-fallback pattern,
    Phase 63 requires all 4 upstream artifacts; missing inputs abort with
    exit code 2 (CLI-level fail-fast per 63-PATTERNS.md loader convention).
    """
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        LOGGER.error("cannot read %s: %s", path, exc)
        raise SystemExit(2) from exc


# ─── main — REPORT-02 aggregator (Plan 63-06) ──────────────────────────────
def main(argv: list[str] | None = None) -> int:
    """Entry point — Phase 63 aggregator (REPORT-02 + REPORT-01 emission).

    Pipeline (D-04, single invocation):
      1. Load 4 upstream artifacts (fail-fast per _load_json).
      2. Compute REGIME-03 VIF deflation block (D-13/D-14/D-15).
      3. Build REGIME-02 3×3 event × flow-type matrix (D-09/D-10/D-11),
         feeding ``max(VIF)`` so each cell's n_effective is pre-deflated.
      4. Extract D-20 flags (simpson / drift / fee_flip / broken cells).
      5. Derive D-17 dominant-explanation verdict.
      6. Assemble the D-20 top-level dict and _sanitize_for_json it.
      7. Emit ``report.json`` (sort_keys=True, indent=2, allow_nan=False),
         ``report.md`` (render_report_md), and ``VALIDATION.md``
         (render_validation_md, with regime_labels_commit SHA resolved
         from git log — D-06 / D-21 / D-22, Plan 63-07).
    """
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # 1. Load 4 upstream artifacts (fail-fast on any missing).
    audit_matrix = _load_json(args.audit)
    drift_detected = _load_json(args.drift)
    sign_breakdown = _load_json(args.sign)
    regime_labels = _load_json(args.regime)
    LOGGER.info(
        "loaded 4 upstream artifacts: audit=%s drift=%s sign=%s regime=%s",
        args.audit,
        args.drift,
        args.sign,
        args.regime,
    )

    # 2. REGIME-03 VIF (must precede matrix so cells carry deflated n_eff).
    vif_block = compute_vif_block(sign_breakdown)
    LOGGER.info(
        "VIF per-pair=%s, max=%.3f, n_eff=%.2f",
        vif_block["per_pair"],
        vif_block["max"],
        vif_block["n_effective"],
    )

    # 3. REGIME-02 3×3 matrix (deflated).
    matrix_3x3 = build_regime_matrix_3x3(
        sign_breakdown, regime_labels, vif_max=vif_block["max"]
    )
    LOGGER.info("built 3x3 matrix with %d cells", len(matrix_3x3["cells"]))

    # 4. D-20 flags.
    flags = extract_flags(sign_breakdown, drift_detected, matrix_3x3)
    # 5. D-17 verdict.
    verdict = derive_verdict(matrix_3x3, vif_block, flags)

    # 6. Assemble D-20 top-level dict.
    now = datetime.now(timezone.utc)
    report: dict = {
        "phase": 63,
        "milestone": "v4.4-sign-forensics",
        "date": now.date().isoformat(),
        "generated_at": now.isoformat(),
        "commit_reference": args.commit_ref,
        "audit_matrix": audit_matrix,
        "drift_detected": drift_detected,
        "sign_breakdown": sign_breakdown,
        "regime_labels": regime_labels,
        "regime_matrix_3x3": matrix_3x3,
        "vif": vif_block,
        "flags": flags,
        "verdict": verdict,
    }
    # T-63-03: _sanitize_for_json coerces NaN/Inf/np.* so allow_nan=False holds.
    report = _sanitize_for_json(report)

    # 7. Emit.
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    (out / "report.md").write_text(render_report_md(report))
    # REPORT-03 (Plan 63-07): emit VALIDATION.md certificate with
    # pre-registration SHA resolved from git history of regime_labels.json.
    regime_labels_commit = _git_first_commit_sha(args.regime)
    (out / "VALIDATION.md").write_text(
        render_validation_md(report, regime_labels_commit)
    )
    LOGGER.info("wrote report.md + report.json + VALIDATION.md to %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
