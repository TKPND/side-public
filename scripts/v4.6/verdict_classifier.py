"""Phase 72 verdict classifier — Fleiss / VIF / n_eff → signal|drift|noise.

Phase boundaries and decisions:
- Input: Phase 71 `sign_breakdown.json` (provenance `fresh-wfd-rerun-2026-04-19-70303ac`).
- Output: `docs/reports/v4.6-verdict-resolution/report.json` (CONTEXT D-09 schema).
- Cascade: Phase 69 D-02/D-03/D-04 pre-registered strict `<` boundaries (commit 432a885).
- Reuses: `scripts/v4.4/report.py::compute_vif_block` (D-03) via sibling-script
  `sys.path` bootstrap, and `scripts/v4.4/sign_breakdown.py::_sanitize_for_json`
  for NaN/Inf JSON safety.
- Forbidden reuse (CONTEXT D-06/D-07): v4.4 kappa interpreter and v4.4 verdict
  deriver must NOT be imported here. Boundary and taxonomy mismatches vs
  Phase 69 pre-registration. Classification is re-implemented from Phase 69.

Usage:
    uv run python scripts/v4.6/verdict_classifier.py \\
        --sign docs/reports/v4.6-verdict-resolution/sign-forensics/sign_breakdown.json \\
        --output-dir docs/reports/v4.6-verdict-resolution \\
        --threshold-commit 432a885
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# Sibling-script import (Phase 61/62 pattern — matches scripts/v4.4/report.py).
SCRIPT_DIR = Path(__file__).resolve().parent
V44_DIR = SCRIPT_DIR.parent / "v4.4"
for _p in (SCRIPT_DIR, V44_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from report import compute_vif_block  # noqa: E402  # D-03 reuse
from sign_breakdown import _sanitize_for_json  # noqa: E402  # NaN/Inf JSON safety

LOGGER = logging.getLogger(__name__)

# --- v4.5 baseline constants (REQUIREMENTS.md / ROADMAP.md Phase 72 headline).
V45_MAX_VIF = 1e12
V45_N_EFF = 1.2e-11

# --- Multiple-testing configuration (CONTEXT D-11, documentation-only per D-12).
_MT_N_SLOTS_DEFAULT = 96
_MT_ALPHA_FAMILY = 0.05
_MT_BH_Q = 0.10


# -----------------------------------------------------------------------------
# I/O helpers
# -----------------------------------------------------------------------------
def _load_json(path: Path) -> dict:
    """Fail-fast JSON loader — missing or malformed → SystemExit(2)."""
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        LOGGER.error("cannot read %s: %s", path, exc)
        raise SystemExit(2) from exc


def _harvest_pairwise_pvalues(sign_breakdown: dict) -> list[float]:
    """Flatten Phase 71 `pairwise_agreement_pvalue` (4×4 pair-major dict) → list.

    Diagonal entries (pair==pair) and NaN / None are dropped. Returns empty list
    when the key is absent; downstream `apply_multiple_testing` safely handles
    zero p-values (pass_count → 0).
    """
    block = sign_breakdown.get("pairwise_agreement_pvalue")
    if not isinstance(block, dict):
        # Fall back to `pairwise_pvalues` if the producer schema varies.
        block = sign_breakdown.get("pairwise_pvalues")
    if not isinstance(block, dict):
        return []

    flat: list[float] = []
    for outer_key, inner in block.items():
        if not isinstance(inner, dict):
            # Single scalar per outer key — treat as non-diagonal.
            if inner is None:
                continue
            try:
                v = float(inner)
            except (TypeError, ValueError):
                continue
            if not math.isnan(v):
                flat.append(v)
            continue
        for inner_key, value in inner.items():
            if inner_key == outer_key:
                continue  # skip diagonal
            if value is None:
                continue
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            if math.isnan(v):
                continue
            flat.append(v)
    return flat


# -----------------------------------------------------------------------------
# Core verdict cascade — Phase 69 D-02/D-03/D-04 verbatim (strict `<`)
# -----------------------------------------------------------------------------
def classify_verdict(fleiss_kappa: float, max_vif: float, n_effective: float
                     ) -> tuple[str, str]:
    """Phase 69 pre-registered cascade (strict `<` boundaries, commit 432a885).

    Re-implemented from Phase 69 D-02/D-03/D-04 — do not delegate to the v4.4
    kappa interpreter (right-closed boundaries conflict with Phase 69 strict
    `<`; CONTEXT D-06) nor to the v4.4 verdict deriver (4-candidate taxonomy
    `config_drift/mixed/structural/sampling_noise` is incompatible with the
    Phase 69 3-candidate `signal/drift/noise`; CONTEXT D-07).
    """
    if max_vif >= 10.0:
        return (
            "noise",
            "VIF>=10 (Hair 2010 multicollinearity cutoff; null ship path per Phase 69 D-03)",
        )
    if n_effective < 4.0:
        return (
            "noise",
            "n_eff<4 (k=4 power floor; null ship path per Phase 69 D-04)",
        )
    if fleiss_kappa >= 0.61:
        return (
            "signal",
            "Fleiss kappa>=0.61 (Landis-Koch substantial agreement; Phase 69 D-02)",
        )
    if fleiss_kappa >= 0.41:
        return (
            "drift",
            "Fleiss kappa in [0.41, 0.61) (Landis-Koch moderate; Phase 69 D-02)",
        )
    return (
        "noise",
        "Fleiss kappa<0.41 (Landis-Koch fair or below; null ship path per Phase 69 D-02)",
    )


# -----------------------------------------------------------------------------
# Multiple-testing correction (CONTEXT D-11 documentation-only per D-12)
# -----------------------------------------------------------------------------
def apply_multiple_testing(pvalues: list[float], n_slots: int = _MT_N_SLOTS_DEFAULT
                           ) -> dict:
    """Bonferroni α=0.05/n_slots + BH q=0.10 reporter.

    Does NOT gate the verdict (Phase 69 cascade is already pre-registered). The
    returned block is written to `report.json::multiple_testing` for traceability.

    BH: for sorted p (ascending), pass_bh = max i such that p_i ≤ (i / m) * q.
    """
    bonf_alpha = _MT_ALPHA_FAMILY / n_slots
    clean = [
        float(p)
        for p in pvalues
        if p is not None and not (isinstance(p, float) and math.isnan(p))
    ]
    pass_bonf = sum(1 for p in clean if p < bonf_alpha)

    sorted_p = sorted(clean)
    m = len(sorted_p)
    pass_bh = 0
    if m > 0:
        for i, p in enumerate(sorted_p, start=1):
            if p <= (i / m) * _MT_BH_Q:
                pass_bh = i  # largest i satisfying BH criterion
    return {
        "n_slots": n_slots,
        "bonferroni_alpha": bonf_alpha,  # = 0.05 / n_slots
        "bh_q": _MT_BH_Q,
        "method": "BH",
        "pass_count_bonf": int(pass_bonf),
        "pass_count_bh": int(pass_bh),
    }


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 72 verdict classifier — Fleiss/VIF/n_eff → signal|drift|noise",
    )
    parser.add_argument(
        "--sign", type=Path, required=True,
        help="Phase 71 sign_breakdown.json path",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="Emit report.json here (CONTEXT D-08)",
    )
    parser.add_argument(
        "--threshold-commit", default="432a885",
        help="Phase 69 seal commit (CONTEXT D-09 meta)",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    sign_breakdown = _load_json(args.sign)

    # D-03: reuse compute_vif_block (returns {per_pair, max, n_nominal, n_effective, rule}).
    vif_block = compute_vif_block(sign_breakdown)

    # D-04: promote Fleiss scalar from the already-computed sign_breakdown value.
    fleiss_raw = (
        sign_breakdown.get("kappa", {}).get("fleiss", {}).get("value")
        if isinstance(sign_breakdown.get("kappa"), dict) else None
    )
    if fleiss_raw is None:
        LOGGER.error("sign_breakdown.kappa.fleiss.value missing — Phase 71 output malformed")
        raise SystemExit(2)
    fleiss_kappa = float(fleiss_raw)

    # D-11: harvest pairwise p-values and run correction.
    pvalues = _harvest_pairwise_pvalues(sign_breakdown)
    mt_block = apply_multiple_testing(pvalues, n_slots=_MT_N_SLOTS_DEFAULT)

    # D-05: verdict cascade.
    verdict, rationale = classify_verdict(
        fleiss_kappa=fleiss_kappa,
        max_vif=float(vif_block["max"]),
        n_effective=float(vif_block["n_effective"]),
    )

    # D-15: null-ship-path suffix when verdict=noise.
    if verdict == "noise":
        rationale = rationale + " [null ship path (Phase 69 D-02/D-03/D-04 に基づく)]"

    improved = (
        float(vif_block["max"]) < V45_MAX_VIF
        and float(vif_block["n_effective"]) > V45_N_EFF
    )
    held = verdict == "noise"

    now = datetime.now(timezone.utc)
    report = {
        "phase": 72,
        "milestone": "v4.6-verdict-resolution",
        "date": now.date().isoformat(),
        "generated_at": now.isoformat(),
        "fleiss_kappa": fleiss_kappa,
        "vif": vif_block,
        "n_effective": float(vif_block["n_effective"]),
        "multiple_testing": mt_block,
        "verdict": verdict,
        "verdict_rationale": rationale,
        "v45_baseline_diff": {
            "v45_max_vif": V45_MAX_VIF,
            "v45_n_eff": V45_N_EFF,
            "v46_max_vif": float(vif_block["max"]),
            "v46_n_eff": float(vif_block["n_effective"]),
            "v46_fleiss_kappa": fleiss_kappa,
            "improved": bool(improved),
            "held": bool(held),
        },
        "meta": {
            "input_provenance_stamp": sign_breakdown.get("meta", {}).get(
                "input_provenance_stamp"
            ),
            "sign_breakdown_source": str(args.sign),
            "threshold_ref": ".planning/phases/69-scope-lock-pre-registration/69-CONTEXT.md",
            "threshold_commit": args.threshold_commit,
        },
    }
    report = _sanitize_for_json(report)

    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    LOGGER.info("wrote report.json to %s (verdict=%s)", out, verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
