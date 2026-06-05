"""Phase 62 sign_forensics breakdown — per-pair per-event per-slot direction tally.

Per CONTEXT.md D-01: Python-only; invoked from Rust `side sign-forensics --breakdown`.
Per CONTEXT.md D-04/D-05: slot grid via audit.extract_slot_dimensions (Phase 61 frozen).
Per CONTEXT.md D-09/D-10/D-11: Politis-Romano stationary bootstrap (implemented in Plan 04).
Per CONTEXT.md D-12: --seed CLI flag required; np.random.default_rng.
Per CONTEXT.md D-19/D-20: single JSON output, schema fixed.

Usage:
    uv run python scripts/v4.4/sign_breakdown.py \\
        --input scripts/v4.4/fixtures/v4.2_usdjpy_subset.json \\
        --input scripts/v4.4/fixtures/v4.2_eurusd_subset.json \\
        --input scripts/v4.4/fixtures/v4.2_audusd_subset.json \\
        --input scripts/v4.4/fixtures/v4.2_eurjpy_subset.json \\
        --output /tmp/sign_breakdown.json \\
        --seed 42

Plan 02 scope: argparse + loader adapter + BREAK-01 raw tally + BREAK-02 pass-conditional tally.
Plans 03-06 fill bootstrap/kappa/stratified/Simpson/fee-flip stubs.
Plan 07 orchestrates full D-20 schema assembly in main().

Key decisions (per 62-02-SUMMARY.md §Decisions):
  - Loader: `_load_report_as_event_slots(path)` adapter unwraps Phase 62
    event-subdir `{fomc: {pair, slots: [...]}}` into Phase 61 flat
    `{fomc: [...]}` shape. `audit.load_pair_report` remains frozen.
  - `slot_key = (window_offset, hold_bars, exit_type)` (fee_bps is NOT part
    of the key). Fixture has 96 unique (wo,hb,et) × 5 fee_results — test
    `test_per_slot_tally_shape_is_4x3x96` binds len==96, forcing this
    interpretation. Plan `<interfaces>` tuple signature is a doc bug.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.stats import binomtest  # noqa: F401  — used in exact_pair_agreement_ci
from statsmodels.stats.inter_rater import (  # noqa: F401  — used in kappa wrappers
    cohens_kappa as _sm_cohens_kappa,
)
from statsmodels.stats.inter_rater import (  # noqa: F401
    fleiss_kappa as _sm_fleiss_kappa,
)

LOGGER = logging.getLogger(__name__)

# ─── Sibling-script import (Phase 61 pattern) ──────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import audit  # noqa: E402

# ─── Constants (per D-04, D-07, D-18) ──────────────────────────────────────
EVENTS: tuple[str, ...] = ("fomc", "ecb", "nfp")
PAIRS: tuple[str, ...] = ("usdjpy", "eurusd", "audusd", "eurjpy")
FEE_LEVELS_BPS: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 5.0)
EXPECTED_SLOTS_PER_EVENT: int = 96  # D-04


# ─── argparse (Phase 61 audit.py lines 230-264 pattern) ────────────────────
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for sign_breakdown."""
    parser = argparse.ArgumentParser(
        description="Phase 62 sign_forensics breakdown (BREAK-01..05 + ATTR-01..03).",
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        type=Path,
        help="Path to v4.1/v4.2 report.json. Repeatable (once per pair).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output path for sign_breakdown.json (file, not directory).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        required=True,
        help="RNG seed for Politis-Romano bootstrap reproducibility (D-12).",
    )
    parser.add_argument(
        "--n-resamples",
        type=int,
        default=10000,
        help="Bootstrap resamples (default 10000 per D-11).",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


# ─── Loader adapter (Phase 62 event-subdir → Phase 61 flat) ────────────────
def _load_report_as_event_slots(path: Path) -> dict[str, list[dict]]:
    """Load a report file as a dict[event -> list[slot]]. See CONTEXT.md D-03/D-04.

    Supports three input shapes:
      1. Fixture layout: top-level dict keyed by EVENTS, each value is list[slot]
         with fee_results[] already carrying 'sign' (existing behavior, unchanged).
      2. Real v4.2 layout: top-level dict with 'slots' key (flat list of slots);
         event is unknown from the file itself, so slots are bucketed into
         the first EVENTS entry ("fomc") for the pair (pair-level aggregation
         consumer).  fee_results[] lack 'sign' and are adapted via
         _adapt_real_slots.
      3. v3.9-cross-pair/eurusd layout: path points to a *directory* that
         contains {fomc,ecb,nfp}/report.json; dispatch to _load_eurusd_3subdir.

    Missing files / unreadable JSON yield empty event lists (defensive; D-17).
    """
    resolved = Path(path)

    # Shape 3: directory pointing at v3.9-cross-pair/eurusd layout
    if resolved.is_dir():
        return _load_eurusd_3subdir(resolved)

    try:
        raw = json.loads(resolved.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        LOGGER.error("report not readable: %s: %s", resolved, exc)
        return {event: [] for event in EVENTS}
    if not isinstance(raw, dict):
        LOGGER.warning("unexpected top-level type in %s: %s", resolved, type(raw))
        return {event: [] for event in EVENTS}

    # Shape 1: fixture layout — top-level keys are EVENTS
    if any(event in raw for event in EVENTS):
        out: dict[str, list[dict]] = {}
        for event in EVENTS:
            value = raw.get(event)
            if isinstance(value, list):
                out[event] = value
            elif isinstance(value, dict):
                slots = value.get("slots", [])
                out[event] = slots if isinstance(slots, list) else []
            else:
                out[event] = []
        return out

    # Shape 4: fresh WFD per-pair/per-event layout (Phase 70 WfdRerunReport).
    # Discriminator: in-file `event` key + `slots` list; in-file key wins over
    # any path inference (71-CONTEXT.md D-04 priority 1). Valid event values
    # are EVENTS tuple. MUST come before Shape 2 to avoid bucketing per-event
    # ECB/NFP reports into the EVENTS[0]="fomc" bucket.
    if (
        "event" in raw
        and raw.get("event") in EVENTS
        and "slots" in raw
        and isinstance(raw["slots"], list)
    ):
        event_name = raw["event"]
        adapted = _adapt_real_slots(raw["slots"])
        out = {event: [] for event in EVENTS}
        out[event_name] = adapted
        LOGGER.info(
            "Shape 4 (fresh WFD per-event) loaded from %s: %d slots → bucket '%s'",
            resolved,
            len(adapted),
            event_name,
        )
        return out

    # Shape 2: real v4.2 layout — top-level 'slots' list, no event partition in file
    if "slots" in raw and isinstance(raw["slots"], list):
        adapted = _adapt_real_slots(raw["slots"])
        out = {event: [] for event in EVENTS}
        # Bucket into 'fomc' as pair-level aggregate; downstream pair tally
        # treats events as strata (D-18 tolerant — empty events are {+1:0,-1:0,0:0})
        out[EVENTS[0]] = adapted
        LOGGER.info(
            "real v4.2 layout loaded from %s: %d slots bucketed into %s",
            resolved,
            len(adapted),
            EVENTS[0],
        )
        return out

    LOGGER.warning(
        "unrecognized report shape in %s (keys=%s)", resolved, list(raw.keys())[:10]
    )
    return {event: [] for event in EVENTS}


def _infer_pair(path: Path) -> str | None:
    """Derive pair identity from filename stem (Phase 62 fixture convention).

    Fixture filename: ``v4.2_<pair>_subset.json`` → returns ``<pair>``.
    Phase 71 fresh WFD layout: ``.../per-pair/<pair>/<event>/report.json`` →
    returns ``<pair>`` (used only when in-file `pair` key is absent; in-file
    key takes priority per 71-CONTEXT.md D-04).
    Falls back to ``audit.infer_pair`` for non-fixture paths (real reports).
    """
    stem = Path(path).stem  # "v4.2_usdjpy_subset"
    parts = stem.split("_")
    for part in parts:
        lower = part.lower()
        if lower in PAIRS:
            return lower
    # Phase 71: fresh WFD layout `.../per-pair/<pair>/<event>/report.json` —
    # filename stem is just "report", so inspect the grandparent directory.
    p = Path(path)
    if p.name == "report.json" and p.parent.parent.name.lower() in PAIRS:
        return p.parent.parent.name.lower()
    # Fallback to Phase 61 inference for production report paths.
    return audit.infer_pair(Path(path))


# ─── Slot-key normalization ────────────────────────────────────────────────
def _slot_key(window_offset: int, hold_bars: int, exit_type: str) -> str:
    """Serialize slot dimensions as a stable string for dict key + JSON.

    Note: fee_bps is intentionally excluded — the 96-slot grid per D-04 is
    addressed by (window_offset, hold_bars, exit_type) only. Multiple
    fee_result rows within a slot contribute to the same slot bucket.
    """
    return f"{window_offset}/{hold_bars}/{exit_type}"


def _sign_to_bucket(sign_value: int) -> str:
    """Map signed integer to direction bucket label."""
    if sign_value > 0:
        return "long"
    if sign_value < 0:
        return "short"
    return "neutral"


# ─── Phase 66 Wave 1: real-schema sign derivation helpers (D-03/D-04/D-05) ──
def _derive_sign(fee_entry: dict) -> int:
    """Derive sign from real fee_entry (trades/pf). See CONTEXT.md D-05.

    Rule:
        combined_oos_trades == 0  -> 0 (neutral)
        combined_oos_pf is None   -> 0 + LOGGER.warning
        combined_oos_pf >= 1.0    -> +1 (long)
        combined_oos_pf < 1.0     -> -1 (short)
    """
    trades = fee_entry.get("combined_oos_trades", 0)
    if trades == 0:
        return 0
    pf = fee_entry.get("combined_oos_pf")
    if pf is None:
        LOGGER.warning("pf missing in fee_entry; sign=0 fallback: %s", fee_entry)
        return 0
    return 1 if float(pf) >= 1.0 else -1


def _adapt_real_slots(slots: list) -> list[dict]:
    """Inject derived 'sign' into each fee_entry of real-schema slots.

    D-04/D-06: preserves existing 'sign' (fixture defensive), otherwise derives
    from (pf, trades). Output shape matches the contract of _iter_slot_fee_entries.
    """
    adapted: list[dict] = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        fee_results = slot.get("fee_results", [])
        if not isinstance(fee_results, list):
            fee_results = []
        new_entries: list[dict] = []
        for entry in fee_results:
            if not isinstance(entry, dict):
                continue
            if "sign" not in entry:
                entry = dict(entry)  # shallow copy to avoid mutating input
                entry["sign"] = _derive_sign(entry)
            new_entries.append(entry)
        adapted_slot = dict(slot)
        adapted_slot["fee_results"] = new_entries
        adapted.append(adapted_slot)
    return adapted


def _load_eurusd_3subdir(pair_dir: Path) -> dict[str, list[dict]]:
    """Load v3.9-cross-pair/eurusd/{fomc,ecb,nfp}/report.json and aggregate. See CONTEXT.md D-03.

    Each subdir's slots[] is adapted via _adapt_real_slots so fee_results[] entries
    carry integer 'sign'. Missing subdirs yield empty event lists (D-17 tolerant).

    Supports two on-disk shapes for per-event report.json:
      - Real v3.9 layout: top-level JSON is a *list* of slot dicts
        (each ``{window_offset, hold_bars, exit_type, fee_results}``).
      - Legacy/mock layout: top-level JSON is a dict with a ``slots`` key.
    """
    pair_dir = Path(pair_dir)
    out: dict[str, list[dict]] = {event: [] for event in EVENTS}
    for event in EVENTS:
        report_path = pair_dir / event / "report.json"
        if not report_path.exists():
            LOGGER.warning("eurusd 3-subdir: missing %s", report_path)
            continue
        try:
            raw = json.loads(report_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            LOGGER.error("eurusd 3-subdir: unreadable %s: %s", report_path, exc)
            continue
        # Real v3.9 shape: top-level is list[slot]. Mock/legacy shape: dict with 'slots' key.
        if isinstance(raw, list):
            slots: list = raw
        elif isinstance(raw, dict):
            candidate = raw.get("slots", [])
            slots = candidate if isinstance(candidate, list) else []
        else:
            LOGGER.warning(
                "eurusd 3-subdir: unexpected top-level type %s in %s",
                type(raw).__name__,
                report_path,
            )
            slots = []
        out[event] = _adapt_real_slots(slots)
    return out


def _iter_slot_fee_entries(
    pair_report: dict[str, list[dict]],
) -> Iterable[tuple[str, int, int, str, float, int, str]]:
    """Yield ``(event, window_offset, hold_bars, exit_type, fee_bps, sign, verdict)``."""
    for event in EVENTS:
        slots = pair_report.get(event, [])
        if not isinstance(slots, list):
            continue
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            wo = int(slot.get("window_offset", 0))
            hb = int(slot.get("hold_bars", 1))
            et = str(slot.get("exit_type", "none"))
            for fee_entry in slot.get("fee_results", []) or []:
                if not isinstance(fee_entry, dict):
                    continue
                fee_bps = float(fee_entry.get("fee_bps", 0.0))
                sign = int(fee_entry.get("sign", 0))
                verdict = str(fee_entry.get("verdict", ""))
                yield (event, wo, hb, et, fee_bps, sign, verdict)


# ─── BREAK-01: Raw per-pair per-event per-slot tally ──────────────────────
def build_per_pair_event_slot_tally(inputs: list[Path]) -> dict:
    """Build nested dict: ``pair -> event -> slot_key -> {long, short, neutral}``.

    Slot key format: ``"{window_offset}/{hold_bars}/{exit_type}"``.
    The 96-slot grid per event per D-04 means
    ``len(result[pair][event]) == 96`` for v4.2 fixtures.
    """
    result: dict[str, dict[str, dict[str, dict[str, int]]]] = {
        pair: {event: {} for event in EVENTS} for pair in PAIRS
    }
    for path in inputs:
        resolved = Path(path).resolve()
        LOGGER.info("load report: %s", resolved)
        pair = _infer_pair(resolved)
        if pair not in PAIRS:
            LOGGER.warning("skipping input %s: pair=%r not in PAIRS", resolved, pair)
            continue
        report = _load_report_as_event_slots(resolved)
        for event, wo, hb, et, _fee_bps, sign, _verdict in _iter_slot_fee_entries(
            report
        ):
            key = _slot_key(wo, hb, et)
            bucket = result[pair][event].setdefault(
                key, {"long": 0, "short": 0, "neutral": 0}
            )
            bucket[_sign_to_bucket(sign)] += 1
    return result


# ─── BREAK-02: Pass-conditional tally (verdict=Pass only) ─────────────────
def build_pass_conditional_tally(inputs: list[Path]) -> dict:
    """Same shape as :func:`build_per_pair_event_slot_tally`, filters verdict=Pass.

    Per D-06: only ``fee_result.verdict == "Pass"`` contributes to the bucket.
    """
    result: dict[str, dict[str, dict[str, dict[str, int]]]] = {
        pair: {event: {} for event in EVENTS} for pair in PAIRS
    }
    for path in inputs:
        resolved = Path(path).resolve()
        pair = _infer_pair(resolved)
        if pair not in PAIRS:
            continue
        report = _load_report_as_event_slots(resolved)
        for event, wo, hb, et, _fee_bps, sign, verdict in _iter_slot_fee_entries(
            report
        ):
            if verdict != "Pass":
                continue  # D-06 filter
            key = _slot_key(wo, hb, et)
            bucket = result[pair][event].setdefault(
                key, {"long": 0, "short": 0, "neutral": 0}
            )
            bucket[_sign_to_bucket(sign)] += 1
    return result


# ─── Stubs for Plans 03-06 (NotImplementedError) ──────────────────────────
def exact_pair_agreement_ci(
    observed_signs: np.ndarray,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Pair-level exact Clopper-Pearson CI for sign agreement proportion (BREAK-03, D-15).

    For n=4 pairs at a single slot, bootstrap variance dominates; exact binomial CI
    (Clopper-Pearson) is the canonical tool. Delegates to
    ``scipy.stats.binomtest(...).proportion_ci(method='exact')`` per RESEARCH
    "Don't Hand-Roll" — scipy already handles the beta.ppf math.

    Agreement convention (D-15): caller is expected to pre-filter to a binary
    {0, 1} indicator (e.g. ``observed_signs == majority_sign`` over non-zero
    signs). ``k = sum(observed_signs)`` is the number of pairs agreeing with
    the majority; ``n = len(observed_signs)`` is the sample size (typically 4).
    Neutral (sign=0) entries should be excluded by the caller before calling.

    Args:
        observed_signs: 1D array of {0, 1} per pair, typically length 4.
        alpha: significance level (default 0.05 → 95% CI).

    Returns:
        (point_estimate, ci_low, ci_high) as Python floats.
        For n=0, returns (0.0, 0.0, 0.0) with a LOGGER warning.
    """
    n = int(len(observed_signs))
    if n == 0:
        LOGGER.warning("exact_pair_agreement_ci called with empty array")
        return 0.0, 0.0, 0.0
    k = int(np.sum(observed_signs))
    point = k / n
    result = binomtest(k, n, p=0.5, alternative="two-sided")
    ci = result.proportion_ci(method="exact", confidence_level=1.0 - alpha)
    return float(point), float(ci.low), float(ci.high)


def block_len_heuristic(n: int) -> int:
    """Politis-Romano block length heuristic (D-10 authoritative).

    Returns ``max(1, ceil(n^(1/3)))``. NOTE: Rust ``validation.rs`` uses
    ``sqrt(N)`` for PnL bootstrap — that is a DIFFERENT heuristic for a
    different statistic; D-03 forbids reusing the Rust symbol. Python uses
    cube-root per BREAK-03 and CONTEXT D-10.

    Examples: 27→3, 64→4, 20→3, 1→1.
    """
    if n <= 0:
        return 1
    return max(1, int(np.ceil(n ** (1.0 / 3.0))))


def politis_romano_bootstrap_ci(
    series: np.ndarray,
    n_resamples: int = 10000,
    block_len: int | None = None,
    rng: np.random.Generator | None = None,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Politis & Romano (1994) stationary bootstrap with circular wrap.

    Returns ``(ci_low, ci_high)`` for the mean of ``series``, a 1D binary
    indicator (0/1 sign-agreement). Geometric block length
    ~ Geometric(1/block_len), circular wrap on the index.

    Algorithm (Politis-Romano 1994, JASA 89:1303-1313):
        For r in 1..n_resamples:
            Sample ``starts[t] ~ Uniform(0, n)`` for t in 1..n
            Sample ``new_block[t] ~ Bernoulli(1/block_len)`` for t in 1..n
            ``idx[0] = starts[0]``
            ``idx[t] = starts[t] if new_block[t] else (idx[t-1] + 1) mod n``
            ``means[r] = mean(series[idx])``
        return ``quantile(means, [alpha/2, 1-alpha/2])``

    Args:
        series: 1D numpy array (int or float) — typically a 0/1 indicator.
        n_resamples: number of bootstrap resamples (D-11 default 10000).
        block_len: mean geometric block length; defaults to
            ``block_len_heuristic(len(series))`` per D-10.
        rng: ``np.random.Generator`` (D-12); defaults to
            ``np.random.default_rng()`` if ``None``.
        alpha: significance level (default 0.05 → 95% percentile CI).

    Returns:
        ``(ci_low, ci_high)`` as Python floats. For ``n == 0`` or
        ``n_resamples == 0``, returns ``(0.0, 0.0)``.
    """
    n = int(len(series))
    if n == 0 or n_resamples == 0:
        return 0.0, 0.0
    if rng is None:
        rng = np.random.default_rng()
    if block_len is None:
        block_len = block_len_heuristic(n)
    p_restart = 1.0 / block_len  # Bernoulli restart probability

    means = np.empty(n_resamples, dtype=np.float64)
    for r in range(n_resamples):
        starts = rng.integers(0, n, size=n)
        new_block = rng.random(n) < p_restart
        idx = np.empty(n, dtype=np.int64)
        idx[0] = starts[0]
        for t in range(1, n):
            idx[t] = starts[t] if new_block[t] else (idx[t - 1] + 1) % n
        means[r] = series[idx].mean()
    lo = np.quantile(means, alpha / 2.0)
    hi = np.quantile(means, 1.0 - alpha / 2.0)
    return float(lo), float(hi)


def pairwise_agreement_pvalue(k: int, n: int) -> float:
    """BREAK-04 (D-13): two-sided exact binomial p-value vs null p=0.5.

    For pair-pair (i, j) agreement: k = # slots where signs match,
    n = # common slots. Delegates to ``scipy.stats.binomtest`` (RESEARCH
    "Don't Hand-Roll") and casts the numpy scalar to ``float`` for
    JSON-safe downstream serialization (pitfall #6, #14).

    Edge case: ``n == 0`` → returns ``nan``. Plan 07 JSON emitter replaces
    NaN with ``None`` (pitfall #11, #15).
    """
    if n == 0:
        return float("nan")
    result = binomtest(int(k), int(n), p=0.5, alternative="two-sided")
    return float(result.pvalue)


def fleiss_kappa_wrapper(table: np.ndarray) -> float:
    """BREAK-05a (D-14): Fleiss' kappa via statsmodels.

    ``table`` shape ``(N_subjects, k_categories)``; each row sums to the fixed
    number of raters (4 pairs in Phase 62). Neutral pairs must be excluded
    upstream per D-14. Delegates to
    ``statsmodels.stats.inter_rater.fleiss_kappa(table, method='fleiss')``.

    Returns NaN for empty / degenerate inputs (all raters agree on a single
    category → statsmodels yields NaN, RESEARCH pitfall #7). The NumPy scalar
    is cast to ``float`` for JSON safety (pitfall #6).
    """
    arr = np.asarray(table, dtype=np.int64)
    if arr.size == 0 or arr.shape[0] == 0:
        return float("nan")
    kappa = _sm_fleiss_kappa(arr, method="fleiss")
    return float(kappa)


def cohen_kappa_wrapper(ct: np.ndarray) -> float:
    """BREAK-05b (D-15): Cohen's kappa for a k×k contingency matrix.

    ``ct`` is a square count matrix (rater_A × rater_B cross-tab). In Phase 62
    each pairwise (i, j) comparison builds a 2×2 table (long/short × long/short)
    after dropping neutral observations. Delegates to
    ``statsmodels.stats.inter_rater.cohens_kappa`` and extracts ``.kappa`` from
    the returned ``KappaResults`` object (pitfall #6 — ``numpy.float64`` → float).

    Returns NaN for empty matrices or zero-total contingency tables.
    """
    arr = np.asarray(ct, dtype=np.int64)
    if arr.size == 0 or arr.sum() == 0:
        return float("nan")
    res = _sm_cohens_kappa(arr, return_results=True)
    return float(res.kappa)


def interpret_kappa(k: float) -> str:
    """BREAK-05c (D-16): Landis-Koch (1977) cutoffs.

    Cite: Landis, J. R. & Koch, G. G. (1977). "The Measurement of Observer
    Agreement for Categorical Data." Biometrics 33(1): 159-174. JSTOR 2529310.

    Boundaries (right-closed per RESEARCH §Landis-Koch note):
        κ < 0.00         → poor
        0.00 ≤ κ ≤ 0.20  → slight
        0.20 <  κ ≤ 0.40 → fair
        0.40 <  κ ≤ 0.60 → moderate
        0.60 <  κ ≤ 0.80 → substantial
        0.80 <  κ ≤ 1.00 → almost_perfect

    NaN input → ``"undefined"`` so degenerate-kappa cases remain labelable
    in the downstream JSON artifact.
    """
    if k != k:  # NaN check (NaN != NaN)
        return "undefined"
    if k < 0.0:
        return "poor"
    if k <= 0.20:
        return "slight"
    if k <= 0.40:
        return "fair"
    if k <= 0.60:
        return "moderate"
    if k <= 0.80:
        return "substantial"
    return "almost_perfect"


def build_stratified_3d(inputs: list[Path]) -> dict:
    """ATTR-01 (D-07): 3D stratified sign agreement matrix.

    Axis order: event × horizon (hold_bars) × fee (fee_bps) × pair.
    Agreement at each cell = long_count / (long_count + short_count);
    neutral excluded (D-14 convention). Cells with zero denominator → None.

    Returns:
        Nested dict: event (str) → horizon (int) → fee (float) → pair (str)
        → agreement (float | None).
    """
    # Nested counter: [event][horizon][fee][pair] → {"long": int, "short": int}
    counts: dict = {event: {} for event in EVENTS}
    for path in inputs:
        resolved = Path(path).resolve()
        pair = _infer_pair(resolved)
        if pair not in PAIRS:
            continue
        report = _load_report_as_event_slots(resolved)
        for event, _wo, hb, _et, fee_bps, sign, _verdict in _iter_slot_fee_entries(
            report
        ):
            horizon = int(hb)
            fee = float(fee_bps)
            horizon_layer = counts[event].setdefault(horizon, {})
            fee_layer = horizon_layer.setdefault(fee, {})
            pair_layer = fee_layer.setdefault(pair, {"long": 0, "short": 0})
            if sign > 0:
                pair_layer["long"] += 1
            elif sign < 0:
                pair_layer["short"] += 1
            # neutral (sign == 0) excluded per D-14

    # Convert counts → agreement values
    result: dict = {}
    for event, horizons in counts.items():
        result[event] = {}
        for horizon, fees in horizons.items():
            result[event][horizon] = {}
            for fee, pairs in fees.items():
                result[event][horizon][fee] = {}
                for pair, c in pairs.items():
                    total = c["long"] + c["short"]
                    result[event][horizon][fee][pair] = (
                        c["long"] / total if total > 0 else None
                    )
    return result


def detect_simpson(
    pooled_agreement: float,
    stratified: dict,
    threshold: float = 0.3,
) -> tuple[bool, float]:
    """ATTR-02 (D-17): Simpson-style magnitude divergence detector.

    Triggers when ``abs(pooled - max(stratified))`` exceeds ``threshold``.
    Threshold 0.3 is a CONTEXT magic number (D-17); its justification is
    deferred to Phase 63 narrative (CONTEXT §Deferred Ideas).

    Args:
        pooled_agreement: overall sign-agreement statistic (scalar).
        stratified: dict of stratum label → agreement value. ``None`` values
            (degenerate cells) are skipped before ``max()``.
        threshold: ad-hoc 0.3 per D-17; configurable but default locked.

    Returns:
        ``(flag, diff)``. ``diff = abs(pooled - max_stratum)``.
        Empty stratified → ``(False, 0.0)``.
    """
    values = [v for v in stratified.values() if v is not None]
    if not values:
        return False, 0.0
    max_stratum = max(values)
    diff = abs(pooled_agreement - max_stratum)
    return diff > threshold, diff


def detect_fee_sign_flip(inputs: list[Path]) -> list[dict]:
    """ATTR-03 (D-18): per-slot monotonicity check of sign direction across fee.

    For each ``(event, window_offset, hold_bars, exit_type)`` slot, pool the
    signs across the 4 pairs at each fee level. Reduce the pooled sum to a
    majority direction ("long" / "short" / "neutral"), walk the fee grid
    ``{0, 1, 2, 3, 5}`` bps and emit an entry for every consecutive-fee pair
    whose direction flips between opposite non-neutral signs (long↔short).

    Pooling at the per-slot granularity (not per-event) mirrors the fixture
    construction: fomc slot 0 embeds fee=3→long, fee=5→short — that flip is
    invisible if sums are collapsed across all 96 slots of an event. Pooled
    per-event aggregation obscures per-slot reversals via cross-slot
    cancellation (Rule 1 — matches test fixture intent).

    Returns:
        List of dicts: ``[{event, fee_low, fee_high, sign_low, sign_high}, ...]``.
        Empty list if all slots are monotonic.
    """
    # [(event, wo, hb, et)][fee_bps] → pooled sign sum across pairs.
    per_slot_fee_sum: dict[tuple, dict[float, int]] = {}
    for path in inputs:
        resolved = Path(path).resolve()
        pair = _infer_pair(resolved)
        if pair not in PAIRS:
            continue
        report = _load_report_as_event_slots(resolved)
        for event, wo, hb, et, fee_bps, sign, _verdict in _iter_slot_fee_entries(
            report
        ):
            if fee_bps not in FEE_LEVELS_BPS:
                continue
            key = (event, wo, hb, et)
            slot_map = per_slot_fee_sum.setdefault(
                key, {fee: 0 for fee in FEE_LEVELS_BPS}
            )
            slot_map[fee_bps] += int(sign)

    def _direction(x: int) -> str:
        if x > 0:
            return "long"
        if x < 0:
            return "short"
        return "neutral"

    flips: list[dict] = []
    fees_sorted = sorted(FEE_LEVELS_BPS)
    for (event, _wo, _hb, _et), fee_map in per_slot_fee_sum.items():
        directions = [(fee, _direction(fee_map[fee])) for fee in fees_sorted]
        for i in range(1, len(directions)):
            prev_fee, prev_dir = directions[i - 1]
            cur_fee, cur_dir = directions[i]
            # Only report a flip between opposite non-neutral directions.
            if prev_dir != cur_dir and "neutral" not in (prev_dir, cur_dir):
                flips.append(
                    {
                        "event": event,
                        "fee_low": float(prev_fee),
                        "fee_high": float(cur_fee),
                        "sign_low": prev_dir,
                        "sign_high": cur_dir,
                    }
                )
    return flips


# ─── Plan 07 orchestrator helpers ─────────────────────────────────────────
def _indicator_vs_mode(signs: np.ndarray) -> np.ndarray:
    """Return {0,1} indicator of whether each sign equals the mode of the non-zero signs.

    If all signs are 0 (no non-zero), returns an empty array. Used by
    bootstrap pair_level pooled k/n derivation where neutrals are excluded.
    """
    non_zero = signs[signs != 0]
    if non_zero.size == 0:
        return np.array([], dtype=int)
    mode = 1 if (non_zero > 0).sum() >= (non_zero < 0).sum() else -1
    return (signs == mode).astype(int)


def _collect_per_slot_pair_signs(
    inputs: list[Path],
) -> dict[tuple[str, str], dict[str, int]]:
    """Build ``{(event, slot_key): {pair: sign_int}}`` across all 4 pair reports.

    Neutral (sign=0) pairs are kept here and filtered by callers where needed
    (Fleiss strict: drop slots with any neutral pair; pairwise: drop neutral
    from each pair-pair view independently). Multiple fee_result rows within a
    slot are aggregated via sum (sign direction is dominated by majority).
    """
    # Sum signs across fee_results within each slot per pair.
    raw: dict[tuple[str, str], dict[str, int]] = {}
    for path in inputs:
        resolved = Path(path).resolve()
        pair = _infer_pair(resolved)
        if pair not in PAIRS:
            continue
        report = _load_report_as_event_slots(resolved)
        for event, wo, hb, et, _fee_bps, sign, _verdict in _iter_slot_fee_entries(
            report
        ):
            key = (event, _slot_key(wo, hb, et))
            raw.setdefault(key, {}).setdefault(pair, 0)
            raw[key][pair] += int(sign)
    # Collapse summed sign to {-1, 0, +1} for downstream agreement counting.
    out: dict[tuple[str, str], dict[str, int]] = {}
    for key, pair_sums in raw.items():
        reduced = {}
        for pair, s in pair_sums.items():
            if s > 0:
                reduced[pair] = 1
            elif s < 0:
                reduced[pair] = -1
            else:
                reduced[pair] = 0
        out[key] = reduced
    return out


def build_sign_matrix_4x4(
    per_slot_signs: dict[tuple[str, str], dict[str, int]],
) -> dict[str, dict[str, float | None]]:
    """BREAK-04 numerator: 4×4 pair × pair sign-agreement proportion.

    Agreement = # common non-neutral slots where sign_i == sign_j / # common
    non-neutral slots. Self-cell = 1.0. Zero common slots → None.
    """
    matrix: dict[str, dict[str, float | None]] = {p: {} for p in PAIRS}
    for i, pair_i in enumerate(PAIRS):
        for j, pair_j in enumerate(PAIRS):
            if i == j:
                matrix[pair_i][pair_j] = 1.0
                continue
            agree = total = 0
            for _key, signs in per_slot_signs.items():
                si = signs.get(pair_i, 0)
                sj = signs.get(pair_j, 0)
                if si == 0 or sj == 0:
                    continue  # exclude neutral
                total += 1
                if si == sj:
                    agree += 1
            matrix[pair_i][pair_j] = (agree / total) if total > 0 else None
    return matrix


def build_pairwise_pvalues(
    per_slot_signs: dict[tuple[str, str], dict[str, int]],
) -> dict[str, dict[str, float | None]]:
    """BREAK-04: 4×4 binomtest two-sided p-values vs null p=0.5."""
    pv: dict[str, dict[str, float | None]] = {p: {} for p in PAIRS}
    for i, pair_i in enumerate(PAIRS):
        for j, pair_j in enumerate(PAIRS):
            if i == j:
                pv[pair_i][pair_j] = 0.0  # trivially significant self-agreement
                continue
            agree = total = 0
            for _key, signs in per_slot_signs.items():
                si = signs.get(pair_i, 0)
                sj = signs.get(pair_j, 0)
                if si == 0 or sj == 0:
                    continue
                total += 1
                if si == sj:
                    agree += 1
            p = pairwise_agreement_pvalue(agree, total)
            pv[pair_i][pair_j] = None if p != p else float(p)  # NaN → None
    return pv


def build_kappa_block(
    per_slot_signs: dict[tuple[str, str], dict[str, int]],
) -> dict:
    """BREAK-05: ``{"fleiss": {value, interpretation}, "cohen_pairwise": {...}}``.

    Fleiss input: rows where all 4 pairs are non-neutral (RESEARCH pitfall #5),
    columns = [long_count, short_count], row sum = 4.
    Cohen: 2×2 per pair-pair on non-neutral slots.
    """
    # Fleiss: drop rows with any neutral pair
    fleiss_rows: list[list[int]] = []
    n_dropped = 0
    for _key, signs in per_slot_signs.items():
        if any(signs.get(p, 0) == 0 for p in PAIRS):
            n_dropped += 1
            continue
        long_count = sum(1 for p in PAIRS if signs.get(p, 0) > 0)
        short_count = sum(1 for p in PAIRS if signs.get(p, 0) < 0)
        fleiss_rows.append([long_count, short_count])

    if fleiss_rows:
        table = np.asarray(fleiss_rows, dtype=np.int64)
        fleiss_val = fleiss_kappa_wrapper(table)
    else:
        fleiss_val = float("nan")
    fleiss_interp = interpret_kappa(fleiss_val)

    cohen: dict[str, dict[str, dict | None]] = {p: {} for p in PAIRS}
    for i, pair_i in enumerate(PAIRS):
        for j, pair_j in enumerate(PAIRS):
            if i == j:
                cohen[pair_i][pair_j] = {
                    "kappa": 1.0,
                    "interpretation": "almost_perfect",
                }
                continue
            # rows = pair_i {long, short}, cols = pair_j {long, short}
            ct = np.zeros((2, 2), dtype=np.int64)
            for _key, signs in per_slot_signs.items():
                si = signs.get(pair_i, 0)
                sj = signs.get(pair_j, 0)
                if si == 0 or sj == 0:
                    continue
                row = 0 if si > 0 else 1
                col = 0 if sj > 0 else 1
                ct[row, col] += 1
            if ct.sum() == 0:
                cohen[pair_i][pair_j] = None
                continue
            k = cohen_kappa_wrapper(ct)
            cohen[pair_i][pair_j] = {
                "kappa": None if k != k else float(k),
                "interpretation": interpret_kappa(k),
            }

    return {
        "fleiss": {
            "value": None if fleiss_val != fleiss_val else float(fleiss_val),
            "interpretation": fleiss_interp,
            "n_rows_used": len(fleiss_rows),
            "n_rows_dropped": n_dropped,
        },
        "cohen_pairwise": cohen,
    }


def build_bootstrap_ci_block(
    per_slot_signs: dict[tuple[str, str], dict[str, int]],
    rng: np.random.Generator,
    n_resamples: int,
) -> dict:
    """BREAK-03: ``{"pair_level": {...}, "event_level": {...}}``.

    pair_level: pooled 4-pair full-agreement indicator across all slots via
    Clopper-Pearson exact CI (D-15).
    event_level: per event, binary series = 1 iff all 4 pairs agree at that
    slot → Politis-Romano stationary bootstrap (D-09/D-10/D-11).
    """
    # Pair level: overall agreement across all slots (4-pair unanimous indicator).
    overall_agreements: list[int] = []
    for _key, signs in per_slot_signs.items():
        vals = [signs.get(p, 0) for p in PAIRS]
        if any(v == 0 for v in vals):
            continue  # require all 4 non-neutral
        overall_agreements.append(
            1 if (all(v > 0 for v in vals) or all(v < 0 for v in vals)) else 0
        )
    if overall_agreements:
        arr = np.asarray(overall_agreements, dtype=np.int64)
        point, lo, hi = exact_pair_agreement_ci(arr, alpha=0.05)
    else:
        point, lo, hi = 0.0, 0.0, 0.0

    pair_level = {
        "method": "exact_enumeration",  # Clopper-Pearson per D-15
        "point_estimate": float(point),
        "ci_low": float(lo),
        "ci_high": float(hi),
        "n_slots": len(overall_agreements),
    }

    # Event level: per event, build binary series = 1 iff all 4 pairs agree.
    event_level: dict = {}
    for event in EVENTS:
        series: list[int] = []
        for (ev, _skey), signs in per_slot_signs.items():
            if ev != event:
                continue
            vals = [signs.get(p, 0) for p in PAIRS]
            if any(v == 0 for v in vals):
                continue
            series.append(
                1 if (all(v > 0 for v in vals) or all(v < 0 for v in vals)) else 0
            )
        if series:
            s = np.asarray(series, dtype=np.int64)
            bl = block_len_heuristic(len(s))
            lo_e, hi_e = politis_romano_bootstrap_ci(
                s,
                n_resamples=n_resamples,
                block_len=bl,
                rng=rng,
            )
            event_level[event] = {
                "method": "politis_romano_stationary",
                "block_len": int(bl),
                "n_resamples": int(n_resamples),
                "n_slots": int(len(s)),
                "point_estimate": float(s.mean()),
                "ci_low": float(lo_e),
                "ci_high": float(hi_e),
            }
        else:
            event_level[event] = {
                "method": "politis_romano_stationary",
                "block_len": None,
                "n_resamples": int(n_resamples),
                "n_slots": 0,
                "point_estimate": None,
                "ci_low": None,
                "ci_high": None,
            }

    return {"pair_level": pair_level, "event_level": event_level}


# ─── NaN guard (RESEARCH pitfall #15) ──────────────────────────────────────
def _sanitize_for_json(obj):
    """Recursively replace ``float('nan')`` with None so ``json.dumps(allow_nan=False)`` succeeds.

    Also coerces numpy scalars to Python primitives (pitfall #6).
    """
    # numpy scalars → Python primitives (check before float to handle np.floating).
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        f = float(obj)
        return None if f != f else f
    if isinstance(obj, float):
        return None if obj != obj else obj  # NaN → None
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj


# ─── main — full D-20 orchestration (Plan 07) ─────────────────────────────
def main(argv: list[str] | None = None) -> int:
    """Full D-20 orchestrator: assembles all 13 required top-level keys."""
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    LOGGER.info("loading %d input report(s)", len(args.input))
    rng = np.random.default_rng(args.seed)

    # Wave 1: tallies (Plan 02)
    tally = build_per_pair_event_slot_tally(args.input)
    pass_tally = build_pass_conditional_tally(args.input)

    # Shared per-slot pair-sign index for matrix / pvalue / kappa / bootstrap.
    per_slot_signs = _collect_per_slot_pair_signs(args.input)

    # BREAK-04 + BREAK-05 + BREAK-03
    sign_matrix = build_sign_matrix_4x4(per_slot_signs)
    pvalues = build_pairwise_pvalues(per_slot_signs)
    kappa = build_kappa_block(per_slot_signs)
    bootstrap_ci = build_bootstrap_ci_block(per_slot_signs, rng, args.n_resamples)

    # ATTR-01 / ATTR-03
    stratified = build_stratified_3d(args.input)
    fee_flip = detect_fee_sign_flip(args.input)

    # Pooled agreement (mean of all stratum agreement values) for Simpson input.
    all_vals: list[float] = []
    for _ev, horizons in stratified.items():
        for _h, fees in horizons.items():
            for _f, pairs in fees.items():
                for _p, v in pairs.items():
                    if v is not None:
                        all_vals.append(v)
    pooled_agreement = float(np.mean(all_vals)) if all_vals else 0.0

    # Flatten stratified cell values (per event/horizon/fee) → mean across pairs.
    flat_strat: dict[str, float] = {}
    for ev, horizons in stratified.items():
        for h, fees in horizons.items():
            for f, pairs in fees.items():
                vals = [v for v in pairs.values() if v is not None]
                if vals:
                    flat_strat[f"{ev}/{h}/{f}"] = float(np.mean(vals))
    simpson_flag, simpson_diff = detect_simpson(
        pooled_agreement, flat_strat, threshold=0.3
    )

    # Phase 71 D-12: propagate fresh WFD provenance stamp into sign_breakdown meta.
    input_provenance_stamps: list[str] = []
    for p in args.input:
        try:
            raw = json.loads(Path(p).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            stamp = raw.get("data_provenance")
            if isinstance(stamp, str):
                input_provenance_stamps.append(stamp)
    unique_stamps = sorted(set(input_provenance_stamps))
    # Single stamp when all 12 fresh reports share provenance (expected normal case).
    # Preserve list form for audit traceability when mixed (e.g. partial reruns).
    meta = {
        "input_paths": [str(Path(p)) for p in args.input],
        "input_count": len(args.input),
        "input_provenance_stamp": unique_stamps[0] if len(unique_stamps) == 1 else unique_stamps,
        "seed": int(args.seed),
        "n_resamples": int(args.n_resamples),
    }

    now = datetime.now(timezone.utc)
    result = {
        "phase": 62,
        "date": now.date().isoformat(),
        "generated_at": now.isoformat(),
        "meta": meta,
        "per_pair_event_slot_tally": tally,
        "pass_conditional_tally": pass_tally,
        "sign_matrix_4x4": sign_matrix,
        "pairwise_agreement_pvalue": pvalues,
        "kappa": kappa,
        "bootstrap_ci": bootstrap_ci,
        "stratified_3d": stratified,
        "simpson_flag": bool(simpson_flag),
        "simpson_diff": float(simpson_diff),
        "fee_sign_flip": fee_flip,
        "pooled_agreement": pooled_agreement,
    }

    result = _sanitize_for_json(result)

    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    LOGGER.info("wrote sign_breakdown to %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
