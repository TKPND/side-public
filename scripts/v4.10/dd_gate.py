"""DrawDown gate layer (Phase 89 Layer 3).

Implements DD-01 sum-type (Ok / InsufficientData / GateClosed) per D-01/D-02.
Module-init sha256 verify of dd_cap.json per D-12 (skip structurally impossible).
Step-down / hard cap / 192-cell apply implemented in Plan 02/03.
"""

from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass
from typing import Union, assert_never

import polars as pl  # noqa: F401  (Plan 02/03 will use directly)

# ---- SEAL constants (module-level verify on import per D-12) ----
_SEAL_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / ".planning"
    / "phases"
    / "88-pre-registration-seal-v4-10"
    / "88-SEAL"
)
_EXPECTED_DD_CAP_SHA256 = (
    "df81cec6ba960bcbbe14e5cf61fbf372275212de010c9153cc904c0dc963bfd1"
)


def _verify_dd_cap_hash() -> None:
    """D-12 module-init verify (fail-close on import)."""
    blob = (_SEAL_DIR / "dd_cap.json").read_bytes()
    actual = hashlib.sha256(blob).hexdigest()
    if actual != _EXPECTED_DD_CAP_SHA256:
        raise RuntimeError(
            f"dd_cap.json sha256 drift: expected {_EXPECTED_DD_CAP_SHA256!r}, got {actual!r}"
        )


_verify_dd_cap_hash()  # Import-time execution (skip structurally impossible)


# ---- DrawDown sum-type (D-01, D-02) ----


@dataclass(frozen=True, slots=True)
class Ok:
    """Successful DD measurement; 0.0 <= value < 0.20."""

    value: float  # 0.0 <= value < 0.20

    def __post_init__(self) -> None:
        if not (0.0 <= self.value < 0.20):
            raise ValueError(f"DD value {self.value} outside [0.0, 0.20)")


@dataclass(frozen=True)
class InsufficientData:
    """Sentinel for zero-trade fold (v4.3 regression, D-24)."""

    pass


@dataclass(frozen=True)
class GateClosed:
    """Gate is closed; reason identifies which trigger fired."""

    reason: str  # "hard_cap_20pct" | "daily_loss_3pct" | "step_down_consecutive"


DrawDown = Union[Ok, InsufficientData, GateClosed]


def pick_risk_multiplier(dd: DrawDown) -> float:
    """Return risk multiplier for a given DrawDown state.

    Match/case exhaustiveness via assert_never (D-01 fail-close, D-07 step-down spec).
    Returns 1.0 / 0.75 / 0.50 / 0.25 for Ok ranges, 0.0 for InsufficientData/GateClosed.
    """
    match dd:
        case Ok(value=v) if v < 0.05:
            return 1.0
        case Ok(value=v) if v < 0.10:
            return 0.75
        case Ok(value=v) if v < 0.15:
            return 0.50
        case Ok():
            return 0.25  # 0.15 <= v < 0.20
        case InsufficientData():
            return 0.0
        case GateClosed():
            return 0.0
        case _:
            assert_never(dd)


# ---- gate_spec_v410.json literal carry (D-07/D-08/D-09/D-10) ----
# source: .planning/phases/88-pre-registration-seal-v4-10/88-SEAL/gate_spec_v410.json
# {"consecutive_loss_gate":5,"daily_loss_limit":0.03,"dd_hard_cap":0.2,
#  "step_down_risk_pct":[0.75,0.5,0.25],"step_down_thresholds":[0.05,0.1,0.15]}
_DD_HARD_CAP = 0.20  # D-08
_DAILY_LOSS_LIMIT = 0.03  # D-09 (positive value = loss magnitude)
_CONSECUTIVE_LOSS_GATE = 5  # D-10


def apply_step_down(fold_dd: float, consecutive_loss: int) -> DrawDown:
    """D-07 step-down + D-10 consecutive_loss handling.

    Returns Ok(fold_dd) for normal step-down ranges (risk multiplier
    applied via pick_risk_multiplier). Returns GateClosed for consecutive
    loss saturation. Hard cap (>=20%) is delegated to apply_hard_cap.

    Note: This function does NOT trigger hard_cap; the caller chains
    apply_hard_cap first to short-circuit on dd >= 0.20.
    """
    if consecutive_loss >= _CONSECUTIVE_LOSS_GATE:
        return GateClosed(reason="step_down_consecutive")
    # Range guarded by Ok.__post_init__: 0.0 <= fold_dd < 0.20
    # If caller passes >= 0.20, ValueError leaks (caller should chain apply_hard_cap first)
    return Ok(value=fold_dd)


def apply_hard_cap(fold_dd: float, daily_loss_sum: float) -> DrawDown:
    """D-08 hard_cap 20% + D-09 daily_loss_limit 3%.

    Order: hard_cap > daily_loss > Ok. hard_cap takes precedence so a
    20% drawdown day with 5% daily loss reports the structural failure.
    """
    if fold_dd >= _DD_HARD_CAP:
        return GateClosed(reason="hard_cap_20pct")
    if daily_loss_sum >= _DAILY_LOSS_LIMIT:
        return GateClosed(reason="daily_loss_3pct")
    return Ok(value=fold_dd)


# ---- 5-pin stamp (D-18 verbatim from 88-SEAL.md) ----
_THRESHOLD_COMMIT = "6527cbc"  # git short SHA, v4.7 Phase 74
_REGIME_COMMIT = "90bf4b2"  # git short SHA, v4.8 Phase 79
_SIZING_EXIT_COMMIT = "8a4e49d2000b08e9e1b93b5f9f0de661d5dff7613d8dfc8339313452a3b81fab"  # sha256 (NOT git)
_SIZING_EXIT_COMMIT_V410 = "a5f71831851bc09fea1ac5f1335e8f3e01465913ec1a4e771c1c53072b51f27f"  # sha256 (NOT git)
_DATA_PROVENANCE_PREFIX = "gate-redesign-v410"


def _resolve_sha7() -> str:
    """Resolve current HEAD short SHA (7 chars) for data_provenance stamp.

    Per CONTEXT.md D-18: data_provenance = 'gate-redesign-v410-<sha7>'.
    """
    import subprocess

    result = subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def build_quint_pin_stamp() -> dict[str, str]:
    """Build 5-pin stamp dict (D-18). All values are str (polars metadata constraint).

    threshold_commit / regime_commit are git short SHAs.
    sizing_exit_commit / sizing_exit_commit_v410 are sha256 canonical-bytes hashes
    (NOT git object SHAs; do not verify with `git cat-file -e`).
    data_provenance resolves <sha7> at runtime via `git rev-parse --short=7 HEAD`.
    """
    return {
        "threshold_commit": _THRESHOLD_COMMIT,
        "regime_commit": _REGIME_COMMIT,
        "sizing_exit_commit": _SIZING_EXIT_COMMIT,
        "sizing_exit_commit_v410": _SIZING_EXIT_COMMIT_V410,
        "data_provenance": f"{_DATA_PROVENANCE_PREFIX}-{_resolve_sha7()}",
    }


# ---- dd_traces.parquet emit (D-14 hive partition, D-17 metadata) ----


def write_dd_traces_parquet(
    df: pl.DataFrame,
    output_dir: pathlib.Path,
    stamp: dict[str, str],
) -> None:
    """polars 1.40 idiom: write_parquet(partition_by=..., metadata=...).

    IMPORTANT (Pitfall 2): use polars, NOT pyarrow pq.write_table(partition_cols=...)
    which does not exist. polars df.write_parquet(metadata=dict) embeds metadata
    into each partition file's key-value metadata; verified round-trip in research.

    IMPORTANT (Pitfall 6): output_dir is a directory path (not file). polars creates
    output_dir/cell_id=<id>/00000000.parquet etc.

    IMPORTANT (Pitfall 1): stamp values are all str. sizing_exit_commit fields are
    sha256 canonical-bytes hashes; do not interpret as git SHAs.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(
        output_dir,
        partition_by=["cell_id"],
        metadata=stamp,
    )


# ---- 192-cell entrypoint (D-11) ----


def apply_all_cells(
    cells: list[dict],
    sized_pnl: pl.DataFrame,
    output_dir: pathlib.Path,
) -> pl.DataFrame:
    """Apply DD gate to all 192 cells (D-11 fail-close on != 192).

    Per cell x fold:
      - High-water mark reset on fold boundary (D-03 fold start peak-to-trough).
      - daily_loss_sum tracked per event_day (D-05 UTC 24h window via _event_day_of).
      - consecutive_loss carried within fold; reset on win OR fold rest (D-06).
      - For each bar: chain apply_hard_cap -> apply_step_down. hard_cap takes
        precedence; daily_loss next; consecutive next; else Ok with step-down mul.

    Emits dd_traces.parquet (cell_id hive partition, fold x bar grain, D-16 schema)
    with 5-pin stamp metadata via write_dd_traces_parquet.

    Returns the assembled DataFrame for in-memory inspection.
    """
    if len(cells) != 192:
        raise RuntimeError(f"expected 192 cells, got {len(cells)} (D-11 fail-close)")
    # Note: module-init _verify_dd_cap_hash() already fired at import (D-12).

    rows: list[dict] = []
    for cell in cells:
        cell_id = cell["cell_id"]
        for fold_id in (1, 2):
            fold_data = sized_pnl.filter(
                (pl.col("cell_id") == cell_id) & (pl.col("fold_id") == fold_id)
            ).sort("bar_ts")
            if fold_data.is_empty():
                # InsufficientData fold (D-24 v4.3 zero-trade regression)
                continue

            start_equity = (
                float(fold_data[0, "equity"]) if "equity" in fold_data.columns else 1.0
            )
            high_water = start_equity
            consecutive_loss = 0
            daily_loss_sum_by_day: dict[str, float] = {}

            for row in fold_data.iter_rows(named=True):
                equity = float(row.get("equity", start_equity))
                bar_ts = row["bar_ts"]
                pnl = float(row.get("pnl", 0.0))

                high_water = max(high_water, equity)
                fold_dd = max(0.0, (high_water - equity) / high_water)

                event_day = _event_day_of(bar_ts)
                if pnl < 0:
                    daily_loss_sum_by_day[event_day] = daily_loss_sum_by_day.get(
                        event_day, 0.0
                    ) + abs(pnl)
                    consecutive_loss += 1
                elif pnl > 0:
                    consecutive_loss = 0
                daily_loss_sum = daily_loss_sum_by_day.get(event_day, 0.0)

                # Chain: hard_cap > daily_loss > consecutive > step_down > Ok
                if fold_dd >= _DD_HARD_CAP:
                    dd: DrawDown = GateClosed(reason="hard_cap_20pct")
                elif daily_loss_sum >= _DAILY_LOSS_LIMIT:
                    dd = GateClosed(reason="daily_loss_3pct")
                elif consecutive_loss >= _CONSECUTIVE_LOSS_GATE:
                    dd = GateClosed(reason="step_down_consecutive")
                else:
                    dd = apply_step_down(
                        fold_dd=fold_dd, consecutive_loss=consecutive_loss
                    )

                match dd:
                    case Ok():
                        state = "ok"
                    case InsufficientData():
                        state = "insufficient_data"
                    case GateClosed(reason=r):
                        state = f"gate_closed:{r}"
                    case _:
                        assert_never(dd)

                rows.append(
                    {
                        "cell_id": cell_id,
                        "fold_id": fold_id,
                        "bar_ts": bar_ts,
                        "equity": equity,
                        "dd_value": fold_dd,
                        "dd_state": state,
                        "risk_multiplier": pick_risk_multiplier(dd),
                        "rest_flag": isinstance(dd, GateClosed)
                        and dd.reason == "step_down_consecutive",
                        "consecutive_loss_count": consecutive_loss,
                        "daily_loss_sum": daily_loss_sum,
                    }
                )

    result_df = pl.DataFrame(
        rows,
        schema={
            "cell_id": pl.Utf8,
            "fold_id": pl.UInt8,
            "bar_ts": pl.Datetime("ms", "UTC"),
            "equity": pl.Float64,
            "dd_value": pl.Float64,
            "dd_state": pl.Utf8,
            "risk_multiplier": pl.Float64,
            "rest_flag": pl.Boolean,
            "consecutive_loss_count": pl.UInt8,
            "daily_loss_sum": pl.Float64,
        },
    )
    write_dd_traces_parquet(result_df, output_dir, stamp=build_quint_pin_stamp())
    return result_df


def _event_day_of(bar_ts) -> str:
    """D-05 event day = UTC 24h window containing the bar.

    Returns ISO date string 'YYYY-MM-DD' of the UTC date component.
    DST safe (uses pure UTC, not local). Accepts datetime / pl.Datetime / str.
    """
    if isinstance(bar_ts, str):
        from datetime import datetime

        bar_ts = datetime.fromisoformat(bar_ts.replace("Z", "+00:00"))
    if hasattr(bar_ts, "astimezone"):
        from datetime import timezone

        bar_ts = bar_ts.astimezone(timezone.utc)
    return bar_ts.strftime("%Y-%m-%d")
