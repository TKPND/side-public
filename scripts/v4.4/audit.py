"""Phase 61 sign_forensics audit — config-matrix builder (CONFIG-01).

Plan 04 scope: load v4.1 + v4.2 report.json files (zero re-scan), build the
pair × event dimension matrix (9 cells — eurusd_compat=FALSE per Plan 01
probe), emit audit_matrix.json.

Plan 05 will extend this same file with drift detection (CONFIG-02),
event-date intersection re-aggregation (CONFIG-03), DST spot-check (CONFIG-04),
and audit verdict gate (CONFIG-05). Plan 06 emits the markdown view (CONFIG-06).

Per CONTEXT.md D-07 / D-08: ingest existing report.json + events.rs accessor
results. NO re-scan. NO touching scanner/validation/backtest/strategies/gate.

EURUSD gap: Plan 01 probe determined eurusd_compat=FALSE (v3.9-cross-pair
report.json is summary-only, incompatible with the audit ingest schema).
Phase 61 therefore covers 9/12 cells; EURUSD re-evaluation is deferred to
Phase 62 entry checkpoint. See 61-04-SUMMARY.md for the documented
limitation paragraph.

Usage (eurusd_compat=FALSE — 9 cells):
    uv run python scripts/v4.4/audit.py \
        --v41-report docs/reports/v4.1-n-expansion/report.json \
        --v42-report docs/reports/v4.2-audusd/report.json \
        --v42-report docs/reports/v4.2-eurjpy/report.json \
        --output docs/reports/v4.4-sign-forensics/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# events.rs const arrays re-declared in Python (CONTEXT.md `specifics` allows
# either re-declare or Rust dump fixture; re-declare chosen for minimal
# coupling in Plan 04).
#
# Source of truth: rust/side-engine/src/events.rs §197 / §262 / §322 / §388 /
# §446 / §502. Tuple schema: (year, month, day, hour_utc, direction).
#
# Drift detector (T-61-04-01 mitigation): `cargo test fomc_dir_at_covers_both_epochs`
# covers 3 accessor tests (v4.1+v4.2 epoch sample per event). If these arrays
# drift from events.rs over time, Plan 06 markdown will surface cells with
# unexpected signal_dir values; additionally Plan 03 unit tests fail fast.
# ---------------------------------------------------------------------------

# FOMC 2022-23 (16 rows, events.rs §388)
FOMC_DATES_2022_2023: list[tuple[int, int, int, int, int]] = [
    (2022, 1, 26, 19, -1),
    (2022, 3, 16, 18, 1),
    (2022, 5, 4, 18, 1),
    (2022, 6, 15, 18, 1),
    (2022, 7, 27, 18, 1),
    (2022, 9, 21, 18, 1),
    (2022, 11, 2, 19, 1),
    (2022, 12, 14, 19, 1),
    (2023, 1, 25, 19, 1),
    (2023, 3, 22, 18, 1),
    (2023, 5, 3, 18, 0),
    (2023, 6, 14, 18, -1),
    (2023, 7, 26, 18, -1),
    (2023, 9, 20, 18, 0),
    (2023, 11, 1, 19, 0),
    (2023, 12, 13, 19, 1),
]

# FOMC 2024-26 (18 rows, events.rs §197)
FOMC_DATES_2024_2026: list[tuple[int, int, int, int, int]] = [
    (2024, 1, 31, 19, 1),
    (2024, 3, 20, 18, 1),
    (2024, 5, 1, 18, 1),
    (2024, 6, 12, 18, 1),
    (2024, 7, 31, 18, -1),
    (2024, 9, 18, 18, -1),
    (2024, 11, 7, 19, -1),
    (2024, 12, 18, 19, 1),
    (2025, 1, 29, 19, 1),
    (2025, 3, 19, 18, 1),
    (2025, 5, 7, 18, 1),
    (2025, 6, 18, 18, 1),
    (2025, 7, 30, 18, 1),
    (2025, 9, 17, 18, -1),
    (2025, 10, 29, 18, 1),
    (2025, 12, 10, 19, 1),
    (2026, 1, 28, 19, 1),
    (2026, 3, 18, 18, 1),
]

# ECB 2022-23 (16 rows, events.rs §446)
ECB_DATES_2022_2023: list[tuple[int, int, int, int, int]] = [
    (2022, 2, 3, 13, 0),
    (2022, 3, 10, 13, 1),
    (2022, 4, 14, 12, 1),
    (2022, 6, 9, 12, 1),
    (2022, 7, 21, 12, 1),
    (2022, 9, 8, 12, 1),
    (2022, 10, 27, 13, 1),
    (2022, 12, 15, 13, 1),
    (2023, 2, 2, 13, 1),
    (2023, 3, 16, 13, 1),
    (2023, 5, 4, 12, 1),
    (2023, 6, 15, 12, 0),
    (2023, 7, 27, 12, -1),
    (2023, 9, 7, 12, -1),
    (2023, 10, 26, 12, -1),
    (2023, 12, 7, 13, -1),
]

# ECB 2024-25 (16 rows, events.rs §262)
ECB_DATES_2024_2025: list[tuple[int, int, int, int, int]] = [
    (2024, 1, 25, 13, 0),
    (2024, 3, 7, 13, 0),
    (2024, 4, 11, 13, 0),
    (2024, 6, 6, 12, -1),
    (2024, 7, 18, 12, 0),
    (2024, 9, 12, 12, -1),
    (2024, 10, 17, 12, -1),
    (2024, 12, 12, 13, -1),
    (2025, 1, 30, 13, -1),
    (2025, 3, 6, 13, -1),
    (2025, 4, 17, 12, -1),
    (2025, 6, 5, 12, -1),
    (2025, 7, 24, 12, 0),
    (2025, 9, 11, 12, 0),
    (2025, 10, 30, 12, 0),
    (2025, 12, 18, 13, 0),
]

# NFP 2022-23 (24 rows, events.rs §502)
NFP_DATES_2022_2023: list[tuple[int, int, int, int, int]] = [
    (2022, 1, 7, 13, 1),
    (2022, 2, 4, 13, 1),
    (2022, 3, 4, 13, 1),
    (2022, 4, 1, 12, 1),
    (2022, 5, 6, 12, 1),
    (2022, 6, 3, 12, 1),
    (2022, 7, 1, 12, 1),
    (2022, 8, 5, 12, -1),
    (2022, 9, 2, 12, -1),
    (2022, 10, 7, 12, -1),
    (2022, 11, 4, 13, -1),
    (2022, 12, 2, 13, 1),
    (2023, 1, 6, 13, 1),
    (2023, 2, 3, 13, 0),
    (2023, 3, 10, 13, 1),
    (2023, 4, 7, 12, -1),
    (2023, 5, 5, 12, -1),
    (2023, 6, 2, 12, 0),
    (2023, 7, 7, 12, 1),
    (2023, 8, 4, 12, 0),
    (2023, 9, 1, 12, -1),
    (2023, 10, 6, 12, 0),
    (2023, 11, 3, 13, -1),
    (2023, 12, 1, 13, 0),
]

# NFP 2024-25 (22 rows, events.rs §322)
NFP_DATES_2024_2025: list[tuple[int, int, int, int, int]] = [
    (2024, 1, 5, 13, 1),
    (2024, 2, 2, 13, 1),
    (2024, 3, 8, 13, 1),
    (2024, 4, 5, 12, 1),
    (2024, 5, 3, 12, -1),
    (2024, 6, 7, 12, 1),
    (2024, 7, 5, 12, 0),
    (2024, 8, 2, 12, -1),
    (2024, 9, 6, 12, -1),
    (2024, 10, 4, 12, 1),
    (2024, 11, 1, 12, -1),
    (2024, 12, 6, 13, 1),
    (2025, 1, 10, 13, 1),
    (2025, 2, 7, 13, -1),
    (2025, 3, 7, 13, 0),
    (2025, 4, 4, 12, 1),
    (2025, 5, 2, 12, 1),
    (2025, 6, 6, 12, 0),
    (2025, 7, 3, 12, 1),
    (2025, 8, 1, 12, -1),
    (2025, 9, 5, 12, -1),
    (2025, 12, 16, 13, 1),
]

# Epoch → (event → rows) lookup. Keys align with report.json top-level event keys.
EPOCH_EVENT_DATES: dict[str, dict[str, list[tuple[int, int, int, int, int]]]] = {
    "v4.1": {
        "fomc": FOMC_DATES_2022_2023,
        "ecb": ECB_DATES_2022_2023,
        "nfp": NFP_DATES_2022_2023,
    },
    "v4.2": {
        "fomc": FOMC_DATES_2024_2026,
        "ecb": ECB_DATES_2024_2025,
        "nfp": NFP_DATES_2024_2025,
    },
    # Phase 71: v4.6 fresh WFD scope is 2022-2023 (AUDUSD/EURJPY CSVs truncated
    # at 2024-01-01 UTC per Phase 70 Pitfall 5 closure). Reuses 2022-2023 calendars.
    "v4.6": {
        "fomc": FOMC_DATES_2022_2023,
        "ecb": ECB_DATES_2022_2023,
        "nfp": NFP_DATES_2022_2023,
    },
}

# Long-only policy per event (CONTEXT.md ARCHITECTURE):
#   FOMC post-v3.7 = long_only=True; ECB/NFP = long_only=False.
LONG_ONLY_BY_EVENT: dict[str, bool] = {"fomc": True, "ecb": False, "nfp": False}

# Canonical lowercase pair name set (used by `infer_pair` parent-dir parse).
PAIRS: frozenset[str] = frozenset({"usdjpy", "eurusd", "audusd", "eurjpy"})

# Pair inference from path hint → canonical lowercase pair name.
PAIR_FROM_PATH_HINT: dict[str, str] = {
    "v4.1-n-expansion": "usdjpy",
    "v4.2-audusd": "audusd",
    "v4.2-eurjpy": "eurjpy",
    "v3.9-cross-pair": "eurusd",  # eurusd_compat=FALSE → not ingested in Plan 04
}

# Nominal event grid enforced in the matrix (plan acceptance gate = 9 cells).
NOMINAL_EVENTS: tuple[str, ...] = ("fomc", "ecb", "nfp")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments. Mirrors the Plan 02 subprocess contract."""
    parser = argparse.ArgumentParser(
        description="Phase 61 sign_forensics audit — config-matrix builder (CONFIG-01).",
    )
    parser.add_argument(
        "--v41-report",
        action="append",
        default=[],
        type=Path,
        help="Path to v4.1 report.json (2022-23 epoch). Repeatable.",
    )
    parser.add_argument(
        "--v42-report",
        action="append",
        default=[],
        type=Path,
        help="Path to v4.2 report.json (2024-26 epoch). Repeatable.",
    )
    parser.add_argument(
        "--v46-report",
        action="append",
        default=[],
        type=Path,
        help=(
            "Path to v4.6 fresh WFD per-event report.json (2022-23 epoch). "
            "Repeatable. v4.6 mode disables eurusd_compat=FALSE skip "
            "(12-cell coverage incl. EURUSD)."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory for audit_matrix.json.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bypass user confirmation gate when drift detected (Plan 05 CONFIG-05). "
            "Plan 04 ignores this flag."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def infer_pair(path: Path) -> str | None:
    """Derive canonical pair name from report directory hint.

    Returns None for paths that are not primary per-pair reports (e.g.
    cross_pair_summary.json) or whose hint is unknown.
    """
    s = str(path)
    for hint, pair in PAIR_FROM_PATH_HINT.items():
        if hint in s:
            return pair
    # Phase 65 addition: parent-dir parse for production v4.2 layout
    parent_name = path.parent.name  # e.g. "v4.2-usdjpy"
    if parent_name.startswith("v4.2-"):
        candidate = parent_name[len("v4.2-") :]
        if candidate in PAIRS:
            return candidate
    # Phase 71 addition: fresh WFD layout `.../per-pair/<pair>/<event>/report.json`
    # — the grandparent dir is the canonical pair name. Used by --v46-report flag.
    if path.parent.parent.name in PAIRS:
        return path.parent.parent.name
    if "cross_pair_summary" in path.name:
        return None
    LOGGER.warning("could not infer pair from path %s; skipping", path)
    return None


def load_pair_report(path: Path) -> dict[str, list[dict]]:
    """Load report.json and return `{event: [slot_dict, ...]}`.

    Handles BOTH v4.1 flat layout and v4.2 composite layout (F-3 per
    PATTERNS.md — both expose flat `{fomc, ecb, nfp} -> [slot...]` at root).
    Event subdirs on disk are ignored: we read ROOT report.json only.
    """
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        LOGGER.error("report not found: %s", path)
        return {}
    except json.JSONDecodeError as exc:
        LOGGER.error("report %s is not valid JSON: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        LOGGER.warning("unexpected top-level type in %s: %s", path, type(data))
        return {}
    out: dict[str, list[dict]] = {}
    for key in NOMINAL_EVENTS:
        value = data.get(key)
        if isinstance(value, list):
            out[key] = value
        else:
            # Key missing OR non-list: treat as empty source for that event.
            out[key] = []
    return out


# ---------------------------------------------------------------------------
# Dimension extraction
# ---------------------------------------------------------------------------


def extract_slot_dimensions(
    slots: list[dict],
) -> tuple[list[int], list[str], list[int], list[float]]:
    """Extract unique dimension sets from the 96-slot grid.

    Returns sorted lists for (window_offset, exit_type, hold_bars, fee_bps).
    Empty slots produce empty lists (caller handles the empty-source cell).
    """
    window_offsets: set[int] = set()
    exit_types: set[str] = set()
    hold_bars: set[int] = set()
    fee_bps: set[float] = set()
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        wo = slot.get("window_offset")
        hb = slot.get("hold_bars")
        et = slot.get("exit_type")
        if isinstance(wo, int):
            window_offsets.add(wo)
        if isinstance(hb, int):
            hold_bars.add(hb)
        if isinstance(et, str):
            exit_types.add(et)
        for fee in slot.get("fee_results", []) or []:
            if isinstance(fee, dict):
                fb = fee.get("fee_bps")
                if isinstance(fb, (int, float)):
                    fee_bps.add(float(fb))
    return (
        sorted(window_offsets),
        sorted(exit_types),
        sorted(hold_bars),
        sorted(fee_bps),
    )


def build_cell(
    pair: str,
    event: str,
    epoch: str,
    slots: list[dict],
    source_path: Path,
) -> dict:
    """Assemble a single (pair, event, epoch) cell of the audit matrix.

    Dimension fields populated (Plan 04 CONFIG-01):
      - window_offset (sorted unique list from 96-slot grid)
      - dst_utc_hour  (sorted unique UTC hours from events.rs calendar for this epoch)
      - signal_dir    (per-date [y,m,d,dir] list from events.rs — Plan 05 intersection input)
      - long_only     (bool — event policy hardcode)
      - fold_size     (event count from events.rs; WFD = one fold per event)
      - event_count   (count of populated slots ÷ slot-grid factor — see below)
      - fee_bps       (sorted unique fee levels seen across slot fee_results)

    Also emits `event_dates: [[y,m,d], ...]` for Plan 05 intersection re-aggregator.
    """
    calendar = EPOCH_EVENT_DATES.get(epoch, {}).get(event, [])
    window_offset, exit_type, hold_bars, fee_bps = extract_slot_dimensions(slots)
    dst_hours = sorted({row[3] for row in calendar})
    signal_dir = [[row[0], row[1], row[2], row[4]] for row in calendar]
    event_dates = [[row[0], row[1], row[2]] for row in calendar]

    # event_count = number of calendar events contributing to this cell.
    # An empty-source cell (e.g. AUDUSD × ECB — EMPTY slot list) contributes 0
    # events regardless of calendar — the source report has no data for it.
    if slots:
        event_count = len(calendar)
        fold_size = len(calendar)
        status = "populated"
    else:
        event_count = 0
        fold_size = 0
        status = "empty_source"

    return {
        "pair": pair,
        "event": event,
        "epoch": epoch,
        "status": status,
        "source_path": str(source_path),
        "dimensions": {
            "window_offset": window_offset,
            "dst_utc_hour": dst_hours,
            "signal_dir": signal_dir,
            "long_only": LONG_ONLY_BY_EVENT.get(event),
            "fold_size": fold_size,
            "event_count": event_count,
            "fee_bps": fee_bps,
        },
        "slot_grid": {
            "exit_type": exit_type,
            "hold_bars": hold_bars,
            "n_slots": len(slots),
        },
        "event_dates": event_dates,
    }


# ---------------------------------------------------------------------------
# Matrix assembly
# ---------------------------------------------------------------------------


def _load_fresh_v46_report(path: Path) -> tuple[str | None, list[dict]]:
    """Load a fresh WFD per-event report.json (Phase 70 layout).

    Returns `(event_name, slots)` from in-file `event` and `slots` keys.
    In-file keys win over path inference (71-CONTEXT.md D-04 priority 1).
    Returns `(None, [])` on parse failure or schema mismatch.
    """
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        LOGGER.error("v4.6 report not found: %s", path)
        return None, []
    except json.JSONDecodeError as exc:
        LOGGER.error("v4.6 report %s is not valid JSON: %s", path, exc)
        return None, []
    if not isinstance(data, dict):
        LOGGER.warning("v4.6 report %s: unexpected top-level type %s", path, type(data))
        return None, []
    event_name = data.get("event")
    slots = data.get("slots")
    if event_name not in NOMINAL_EVENTS:
        LOGGER.warning(
            "v4.6 report %s: in-file 'event' key %r not in %s",
            path, event_name, NOMINAL_EVENTS,
        )
        return None, []
    if not isinstance(slots, list):
        LOGGER.warning("v4.6 report %s: 'slots' is not a list", path)
        return event_name, []
    return event_name, slots


def build_audit_matrix(
    v41_paths: Iterable[Path],
    v42_paths: Iterable[Path],
    v46_paths: Iterable[Path] = (),
) -> dict:
    """Build the audit matrix (9-cell legacy or 12-cell v4.6 mode).

    Legacy v4.1/v4.2 paths: per eurusd_compat=FALSE gate (Plan 01 probe), EURUSD
    is NOT ingested. v3.9-cross-pair paths are silently skipped via `infer_pair`
    returning None for non-per-pair report hints.

    v4.6 paths (Phase 71 SIGN-04): fresh WFD per-event reports
    `.../per-pair/<pair>/<event>/report.json`. EURUSD skip is **disabled** —
    4 pair × 3 event = 12 cells emitted, closing the Phase 61 'EURUSD deferred'
    caveat. In-file `event` / `pair` keys take priority over path inference.

    Each (pair, epoch, event) from NOMINAL_EVENTS is emitted as a cell. Legacy
    flat-schema cells with empty slot lists are tagged `status="empty_source"`.
    """
    cells: list[dict] = []

    def _ingest_legacy(path: Path, epoch: str) -> None:
        pair = infer_pair(path)
        if pair is None:
            LOGGER.info("skipping non-per-pair source: %s", path)
            return
        if pair == "eurusd" and epoch != "v4.6":
            # Plan 01 probe verdict: eurusd_compat=FALSE for pre-v4.6 v3.9-cross-pair
            # legacy schema. v4.6 fresh WFD reports lift this restriction.
            LOGGER.info(
                "skipping eurusd path %s (eurusd_compat=FALSE; epoch=%s, pre-v4.6)",
                path, epoch,
            )
            return
        events = load_pair_report(path)
        for event_name in NOMINAL_EVENTS:
            slots = events.get(event_name, [])
            cells.append(build_cell(pair, event_name, epoch, slots, path))

    def _ingest_v46(path: Path) -> None:
        # In-file `pair` key has priority 1 over path inference (D-04).
        try:
            raw = json.loads(path.read_text())
            in_file_pair = raw.get("pair") if isinstance(raw, dict) else None
        except (FileNotFoundError, json.JSONDecodeError):
            in_file_pair = None
        pair = in_file_pair if in_file_pair in PAIRS else infer_pair(path)
        if pair is None:
            LOGGER.info("skipping non-per-pair v4.6 source: %s", path)
            return
        # eurusd skip explicitly disabled for v4.6 (12-cell coverage).
        event_name, slots = _load_fresh_v46_report(path)
        if event_name is None:
            return
        cells.append(build_cell(pair, event_name, "v4.6", slots, path))

    for path in v41_paths:
        _ingest_legacy(Path(path), "v4.1")
    for path in v42_paths:
        _ingest_legacy(Path(path), "v4.2")
    for path in v46_paths:
        _ingest_v46(Path(path))

    eurusd_compat = any(c["pair"] == "eurusd" for c in cells)
    eurusd_gap_note = (
        ""
        if eurusd_compat
        else (
            "Plan 01 probe verdict eurusd_compat=FALSE. v3.9-cross-pair/report.json is "
            "summary-only (top-level keys ['comparison_summary', 'pair_reports']). "
            "Phase 61 audit_matrix covers 9/12 cells; EURUSD deferred to Phase 62 "
            "entry re-evaluation. See docs/reports/v4.4-sign-forensics/report_schema_probe.md."
        )
    )
    return {
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "eurusd_compat": eurusd_compat,
        "eurusd_gap_note": eurusd_gap_note,
        "cells": cells,
    }


# ---------------------------------------------------------------------------
# Plan 05 — CONFIG-02 SHA256 cell-hash drift detector
# ---------------------------------------------------------------------------


def cell_hash(cell: dict) -> str:
    """SHA256 of dimension tuple. Identical cells must share hash.

    Empty-source cells are normalized to a sentinel hash so they don't drift
    against populated ones spuriously — `status` is included in the canonical
    form so populated vs empty cells hash differently when their dimensions
    coincidentally match.
    """
    payload = {
        "status": cell.get("status"),
        "dimensions": cell.get("dimensions", {}),
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def detect_drift(cells: list[dict]) -> list[dict]:
    """Group cells by (pair, event) and (event, epoch); flag hash mismatches.

    Returns a list of drift entries. Each entry contains either:
      - {pair, event, drift_kind="epoch_drift", hashes, epochs_diff}
      - {event, epoch, drift_kind="pair_drift", hash_groups}
    """
    drift: list[dict] = []

    by_pair_event: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in cells:
        by_pair_event[(c["pair"], c["event"])].append(c)
    for (pair, event), group in by_pair_event.items():
        if len(group) < 2:
            continue
        hashes_to_cells: dict[str, list[dict]] = defaultdict(list)
        for c in group:
            hashes_to_cells[cell_hash(c)].append(c)
        if len(hashes_to_cells) > 1:
            drift.append(
                {
                    "pair": pair,
                    "event": event,
                    "drift_kind": "epoch_drift",
                    "hashes": list(hashes_to_cells.keys()),
                    "epochs_diff": [c["epoch"] for c in group],
                }
            )

    by_event_epoch: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in cells:
        by_event_epoch[(c["event"], c["epoch"])].append(c)
    for (event, epoch), group in by_event_epoch.items():
        if len(group) < 2:
            continue
        hash_groups: dict[str, list[str]] = defaultdict(list)
        for c in group:
            hash_groups[cell_hash(c)].append(c["pair"])
        if len(hash_groups) > 1:
            drift.append(
                {
                    "event": event,
                    "epoch": epoch,
                    "drift_kind": "pair_drift",
                    "hash_groups": {h: pairs for h, pairs in hash_groups.items()},
                }
            )

    return drift


def split_structural_drift(
    drift_cells: list[dict],
    cells: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Separate CONFIG-03 intent structural asymmetries from genuine drift.

    Structural drift (Plan 61-07 rule):
      - drift_kind == "pair_drift"
      - event == "ecb", epoch == "v4.2"
      - hash_groups' pair union is exactly {audusd, eurjpy}
      - one pair has status="empty_source" while the other is "populated"
        (the expected CONFIG-03 asymmetry — AUDUSD does not use ECB as event
        source per STATE.md decision #6 / 61-CONTEXT)

    Such entries are moved into a separate ``structural_drift`` bucket so
    ``audit_verdict`` is not failed by expected structural pair_drift.

    Returns (remaining_drift_cells, structural_drift_list).
    """
    remaining: list[dict] = []
    structural: list[dict] = []

    # Build (pair, event, epoch) -> status lookup for the empty_source vs
    # populated asymmetry check.
    status_by_cell: dict[tuple[str, str, str], str] = {
        (c["pair"], c["event"], c["epoch"]): c.get("status", "populated") for c in cells
    }

    for entry in drift_cells:
        if entry.get("drift_kind") != "pair_drift":
            remaining.append(entry)
            continue
        event = entry.get("event")
        epoch = entry.get("epoch")
        if event != "ecb" or epoch != "v4.2":
            remaining.append(entry)
            continue
        hash_groups = entry.get("hash_groups", {})
        pair_union: set[str] = set()
        for pairs in hash_groups.values():
            pair_union.update(pairs)
        if pair_union != {"audusd", "eurjpy"}:
            remaining.append(entry)
            continue
        statuses = {
            pair: status_by_cell.get((pair, event, epoch), "populated")
            for pair in pair_union
        }
        if set(statuses.values()) != {"empty_source", "populated"}:
            remaining.append(entry)
            continue
        empty_pair = next(p for p, s in statuses.items() if s == "empty_source")
        populated_pair = next(p for p, s in statuses.items() if s == "populated")
        structural.append(
            {
                "epoch": epoch,
                "event": event,
                "reason": "pair_drift_expected_structural_asymmetry",
                "pairs": sorted(pair_union),
                "detail": (
                    f"{empty_pair}=empty_source vs {populated_pair}=populated "
                    "(CONFIG-03 intent)"
                ),
            }
        )

    return remaining, structural


# ---------------------------------------------------------------------------
# Plan 05 — CONFIG-03 intersection re-aggregator + events.rs accessor check
# ---------------------------------------------------------------------------


def event_dir_at(event: str, year: int, month: int, day: int) -> int | None:
    """Python mirror of events.rs `{fomc,ecb,nfp}_dir_at` accessors.

    Searches both v4.1 (2022-23) and v4.2 (2024-26) const arrays for the event,
    returns the direction byte at (year, month, day) or None if not present.
    """
    pools: dict[str, list[list[tuple[int, int, int, int, int]]]] = {
        "fomc": [FOMC_DATES_2022_2023, FOMC_DATES_2024_2026],
        "ecb": [ECB_DATES_2022_2023, ECB_DATES_2024_2025],
        "nfp": [NFP_DATES_2022_2023, NFP_DATES_2024_2025],
    }
    for arr in pools.get(event, []):
        for row in arr:
            if row[0] == year and row[1] == month and row[2] == day:
                return row[4]
    return None


def compute_intersection_dates(cells: list[dict]) -> dict:
    """For each (event, epoch), intersect event_dates across pairs.

    Returns:
        { (event, epoch): {pair: {"dates": [(y,m,d), ...], "n_intersection": int}} }

    Empty-source cells contribute no dates and are skipped from the
    intersection computation (their pair simply has zero coverage).
    """
    by_event_epoch: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in cells:
        if c.get("status") != "populated":
            continue
        by_event_epoch[(c["event"], c["epoch"])].append(c)

    result: dict[tuple[str, str], dict[str, dict]] = {}
    for (event, epoch), group in by_event_epoch.items():
        if not group:
            continue
        sets = [{tuple(d) for d in c.get("event_dates", [])} for c in group]
        intersection = set.intersection(*sets) if sets else set()
        per_pair: dict[str, dict] = {}
        for c in group:
            per_pair[c["pair"]] = {
                "dates": sorted(intersection),
                "n_intersection": len(intersection),
            }
        result[(event, epoch)] = per_pair
    return result


def events_calendar_check(intersections: dict) -> list[dict]:
    """Validate every intersection date resolves via event_dir_at.

    Any (event, date) tuple where the accessor returns None is flagged.
    """
    missing: list[dict] = []
    for (event, epoch), pair_data in intersections.items():
        for pair, info in pair_data.items():
            for date in info.get("dates", []):
                d = tuple(date)
                if event_dir_at(event, d[0], d[1], d[2]) is None:
                    missing.append(
                        {
                            "event": event,
                            "epoch": epoch,
                            "pair": pair,
                            "date": [d[0], d[1], d[2]],
                            "reason": "events.rs accessor returned None",
                        }
                    )
    return missing


# ---------------------------------------------------------------------------
# Plan 05 — CONFIG-04 DST subprocess wiring (UNCONDITIONAL)
# ---------------------------------------------------------------------------


def run_dst_check(
    test_module: Path = Path("scripts/v4.4/test_dst.py"),
) -> list[dict]:
    """UNCONDITIONALLY spawn `uv run pytest <test_module> --json-report ...`
    as subprocess, parse the JSON report, return list of failed test details.

    REVISION (Plan 05): wiring is permanent and unconditional. DST findings
    cannot be silently dropped from the verdict gate.

    Returns:
        list[{nodeid, outcome, longrepr}] for every test in {failed, error}.
        Empty list if all pass. Non-empty synthetic entry on infrastructure
        errors (timeout / missing uv / missing report / malformed JSON) so the
        verdict gate fails-safe rather than fails-open.
    """
    # mkstemp-style: random name, fd 0600, race-safe
    with tempfile.NamedTemporaryFile(mode="r", suffix=".json", delete=False) as tmp:
        json_report_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [
                "uv",
                "run",
                "pytest",
                str(test_module),
                "--json-report",
                f"--json-report-file={json_report_path}",
                "-q",
                "--no-header",
            ],
            check=False,  # pytest exits non-zero on failures; we want the report regardless
            capture_output=True,
            text=True,
            timeout=120,
        )
        LOGGER.info(
            "DST pytest exit=%d stdout_tail=%s",
            result.returncode,
            (result.stdout or "")[-200:],
        )
    except subprocess.TimeoutExpired:
        LOGGER.error("DST pytest subprocess timed out — synthetic failure entry")
        json_report_path.unlink(missing_ok=True)
        return [
            {
                "nodeid": "<timeout>",
                "outcome": "error",
                "longrepr": "DST pytest subprocess timed out after 120s",
            }
        ]
    except FileNotFoundError as e:
        LOGGER.error("uv not on PATH: %s", e)
        json_report_path.unlink(missing_ok=True)
        return [
            {
                "nodeid": "<missing-uv>",
                "outcome": "error",
                "longrepr": f"uv binary not found: {e}",
            }
        ]

    if not json_report_path.exists():
        LOGGER.error(
            "pytest-json-report did not write %s — synthetic error entry",
            json_report_path,
        )
        return [
            {
                "nodeid": "<missing-report>",
                "outcome": "error",
                "longrepr": (
                    "pytest-json-report plugin did not produce a JSON report. "
                    "Check that pytest-json-report is installed (Plan 01 dev dep)."
                ),
            }
        ]

    try:
        report = json.loads(json_report_path.read_text())
    except json.JSONDecodeError as e:
        LOGGER.error("malformed json report: %s", e)
        return [
            {
                "nodeid": "<malformed-report>",
                "outcome": "error",
                "longrepr": f"pytest-json-report produced malformed JSON: {e}",
            }
        ]
    finally:
        json_report_path.unlink(missing_ok=True)

    failures: list[dict] = []
    for test in report.get("tests", []):
        outcome = test.get("outcome")
        if outcome in {"failed", "error"}:
            call = test.get("call") or {}
            failures.append(
                {
                    "nodeid": test.get("nodeid", "<unknown>"),
                    "outcome": outcome,
                    "longrepr": call.get("longrepr") or test.get("longrepr") or "",
                }
            )
    LOGGER.info(
        "DST pytest summary: total=%d failures=%d",
        report.get("summary", {}).get("total", 0),
        len(failures),
    )
    return failures


# ---------------------------------------------------------------------------
# Plan 05 — CONFIG-05 verdict + drift_detected.json emitter
# ---------------------------------------------------------------------------


def emit_drift_detected(
    output: Path,
    drift_cells: list[dict],
    intersections: dict,
    missing: list[dict],
    dst_failures: list[dict],
    structural_drift: list[dict] | None = None,
) -> dict:
    """Write drift_detected.json and return the dict written.

    audit_verdict = PASS iff (drift_cells + dst_failures + missing) are all
    empty; `structural_drift` is reported separately and does NOT influence
    the verdict (see Plan 61-07 / CONFIG-03 intent: AUDUSD×EURJPY ECB pair
    asymmetry is expected structural, not a code bug).
    Intersections dict is serialized with stringified (event, epoch) keys.
    """
    structural_drift = structural_drift or []
    verdict = (
        "PASS" if (not drift_cells and not missing and not dst_failures) else "FAIL"
    )
    serialized_intersections = {
        f"{event}|{epoch}": pairs for (event, epoch), pairs in intersections.items()
    }
    result = {
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "audit_verdict": verdict,
        "drift_cells": drift_cells,
        "structural_drift": structural_drift,
        "intersections": serialized_intersections,
        "events_calendar_missing": missing,
        "dst_failures": dst_failures,
        "phase_62_blocker": verdict == "FAIL",
    }
    output.mkdir(parents=True, exist_ok=True)
    out_path = output / "drift_detected.json"
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str))
    LOGGER.info(
        "audit_verdict=%s drift=%d missing=%d dst_failures=%d structural=%d",
        verdict,
        len(drift_cells),
        len(missing),
        len(dst_failures),
        len(structural_drift),
    )
    return result


def emit_audit_matrix_md(
    output: Path,
    matrix: dict,
    drift_result: dict,
) -> Path:
    """Render ``audit_matrix.md`` — 9/12-cell config table + per-dimension heatmap.

    Per CONFIG-06 + CONTEXT.md D-15: text-only heatmap (PASS / WARN / FAIL /
    n/a markers), no plotly/seaborn/altair (STACK.md reject list).
    Mirrors ``cross_report_4.rs`` YAML-frontmatter convention
    (``phase`` / ``date`` / ``audit_verdict`` / ``nyquist_compliant``).

    Classification rule (post Plan 61-07):
      * ``(pair, event) ∈ drift_result["drift_cells"]``          → **FAIL**
      * ``(pair, event) ∈ drift_result["structural_drift"]``     → WARN
      * cell ``status == "empty_source"``                        → n/a
      * otherwise                                                → PASS
    """
    cells = matrix["cells"]
    verdict = drift_result["audit_verdict"]

    # (pair, event) sets — structural_drift is a separate bucket (Plan 61-07)
    drift_pairs_events = {
        (d.get("pair", "*"), d.get("event", "*"))
        for d in drift_result.get("drift_cells", [])
    }
    structural_pairs_events: set[tuple[str, str]] = set()
    for d in drift_result.get("structural_drift", []):
        pairs_field = d.get("pairs")
        event = d.get("event", "*")
        if isinstance(pairs_field, list):
            for p in pairs_field:
                structural_pairs_events.add((p, event))
        else:
            structural_pairs_events.add((d.get("pair", "*"), event))

    # cells keyed by (pair, event) for empty_source lookup
    cell_status: dict[tuple[str, str], str] = {
        (c["pair"], c["event"]): c.get("status", "populated") for c in cells
    }

    lines: list[str] = []

    # YAML frontmatter (per PATTERNS.md, mirrors cross_report_4.rs)
    lines.append("---")
    lines.append("phase: 61")
    lines.append(f"date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    lines.append(f"audit_verdict: {verdict}")
    lines.append("nyquist_compliant: true")
    lines.append("---")
    lines.append("")
    lines.append("# Phase 61 Audit Matrix — v4.4 Cross-Pair Sign Forensics")
    lines.append("")
    lines.append(f"**Generated:** {matrix['generated_at']}  ")
    lines.append(f"**Audit verdict:** **{verdict}**  ")
    lines.append(f"**Phase 62 blocker:** {drift_result['phase_62_blocker']}  ")
    lines.append(
        f"**Cells covered:** {len(cells)} "
        "(target: 12 = 4 pair × 3 event; EURUSD deferred per Plan 04 "
        "`eurusd_compat=FALSE`)  "
    )
    if not matrix.get("eurusd_compat", True):
        lines.append("")
        lines.append("> **Coverage gap:** " + str(matrix.get("eurusd_gap_note", "")))
    lines.append("")

    # ------------------------------------------------------------------
    # 12-cell config-matrix table
    # ------------------------------------------------------------------
    lines.append("## Config Matrix")
    lines.append("")
    lines.append(
        "| Pair | Event | Epoch | window_offset | DST UTC hr | signal_dir | "
        "long_only | fold_size | event_count | fee_bps |"
    )
    lines.append(
        "|------|-------|-------|---------------|------------|------------|"
        "-----------|-----------|-------------|---------|"
    )

    def _fmt(val: object) -> str:
        if val is None:
            return "-"
        if isinstance(val, list):
            return ",".join(str(v) for v in val) if val else "-"
        return str(val)

    for c in sorted(cells, key=lambda c: (c["pair"], c["event"], c["epoch"])):
        d = c.get("dimensions", {})
        row = (
            f"| {c['pair']} | {c['event']} | {c['epoch']} "
            f"| {_fmt(d.get('window_offset'))} | {_fmt(d.get('dst_utc_hour'))} "
            f"| {_fmt(d.get('signal_dir'))} | {_fmt(d.get('long_only'))} "
            f"| {_fmt(d.get('fold_size'))} | {_fmt(d.get('event_count'))} "
            f"| {_fmt(d.get('fee_bps'))} |"
        )
        lines.append(row)
    lines.append("")

    # ------------------------------------------------------------------
    # Drift heatmap — surface drift / structural asymmetry / empty sources
    # ------------------------------------------------------------------
    lines.append("## Drift Heatmap")
    lines.append("")
    lines.append(
        "Cell-level drift status. **PASS** = no drift; **WARN** = expected "
        "structural asymmetry (Plan 61-07 bucket, not verdict-impacting); "
        "**FAIL** = SHA256 pair-drift detected; **n/a** = empty_source cell "
        "(no slot list from source report.json)."
    )
    lines.append("")

    events = sorted({c["event"] for c in cells})
    pairs = sorted({c["pair"] for c in cells})
    header = "| Pair \\ Event |" + "|".join(f" {e} " for e in events) + "|"
    sep = "|--------------|" + "|".join("-----" for _ in events) + "|"
    lines.append(header)
    lines.append(sep)
    for p in pairs:
        row_cells = []
        for e in events:
            if (p, e) in drift_pairs_events:
                row_cells.append(" **FAIL** ")
            elif (p, e) in structural_pairs_events:
                row_cells.append(" WARN ")
            elif cell_status.get((p, e)) == "empty_source":
                row_cells.append(" n/a ")
            else:
                row_cells.append(" PASS ")
        lines.append(f"| {p} |" + "|".join(row_cells) + "|")
    lines.append("")

    # ------------------------------------------------------------------
    # Optional detail sections
    # ------------------------------------------------------------------
    if drift_result.get("drift_cells"):
        lines.append("## Drift Details")
        lines.append("")
        for d in drift_result["drift_cells"]:
            lines.append(
                f"- **{d.get('drift_kind')}**: "
                f"{json.dumps(d, sort_keys=True, default=str)}"
            )
        lines.append("")

    if drift_result.get("structural_drift"):
        lines.append("## Structural Drift (expected; non-verdict-impacting)")
        lines.append("")
        for d in drift_result["structural_drift"]:
            lines.append(
                f"- **{d.get('drift_kind', 'structural')}**: "
                f"{json.dumps(d, sort_keys=True, default=str)}"
            )
        lines.append("")

    if drift_result.get("events_calendar_missing"):
        lines.append("## Events Calendar Mismatches (Plan 03 accessor returned None)")
        lines.append("")
        for m in drift_result["events_calendar_missing"]:
            lines.append(
                f"- {m.get('pair')} {m.get('event')} {m.get('date')} "
                f"({m.get('epoch')}): {m.get('reason')}"
            )
        lines.append("")

    if drift_result.get("dst_failures"):
        lines.append("## DST Spot-Check Failures (CONFIG-04)")
        lines.append("")
        for f in drift_result["dst_failures"]:
            lines.append(f"- {f}")
        lines.append("")

    # ------------------------------------------------------------------
    # Next step — Phase 62 gate status
    # ------------------------------------------------------------------
    lines.append("## Next Step")
    lines.append("")
    if verdict == "PASS":
        lines.append(
            "Phase 62 plan-phase is **UNBLOCKED**. Sign breakdown + bootstrap CI "
            "work can proceed."
        )
    else:
        lines.append(
            "Phase 62 plan-phase is **BLOCKED** pending user review of "
            "`drift_detected.json`. Either fix the drift, or document "
            "acceptance per CONTEXT.md CONFIG-05 wording "
            '("structural-explanation 許容" requires explicit user '
            "acknowledgement)."
        )
    lines.append("")

    output.mkdir(parents=True, exist_ok=True)
    out_path = output / "audit_matrix.md"
    out_path.write_text("\n".join(lines))
    LOGGER.info(
        "wrote audit_matrix.md (%d bytes) to %s",
        out_path.stat().st_size,
        out_path,
    )
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    LOGGER.info(
        "loading %d v4.1 + %d v4.2 + %d v4.6 reports",
        len(args.v41_report),
        len(args.v42_report),
        len(args.v46_report),
    )

    # CONFIG-01 (Plan 04) — build matrix and emit audit_matrix.json
    matrix = build_audit_matrix(args.v41_report, args.v42_report, args.v46_report)
    out_path = args.output / "audit_matrix.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(matrix, indent=2, sort_keys=True))
    LOGGER.info("wrote %d cells to %s", len(matrix["cells"]), out_path)

    # CONFIG-02 SHA256 cell-hash drift detector
    drift_cells_all = detect_drift(matrix["cells"])
    # Plan 61-07 — split out CONFIG-03 intent structural asymmetry (AUDUSD×EURJPY)
    drift_cells, structural_drift = split_structural_drift(
        drift_cells_all, matrix["cells"]
    )
    # CONFIG-03 v4.1 ∩ v4.2 intersection per (event, epoch) + events.rs cross-check
    intersections = compute_intersection_dates(matrix["cells"])
    missing = events_calendar_check(intersections)
    # CONFIG-04 — UNCONDITIONAL DST subprocess wiring (Plan 05 revision)
    dst_failures = run_dst_check()
    # CONFIG-05 verdict + drift_detected.json emit
    result = emit_drift_detected(
        args.output,
        drift_cells,
        intersections,
        missing,
        dst_failures,
        structural_drift=structural_drift,
    )

    # CONFIG-06 (Plan 06) — human-readable audit matrix
    emit_audit_matrix_md(args.output, matrix, result)

    if result["audit_verdict"] == "FAIL" and not args.force:
        print()
        print("=" * 70)
        print("AUDIT VERDICT = FAIL — Phase 62 BLOCKER GATE (CONFIG-05)")
        print("=" * 70)
        print(f"Drift cells:        {len(drift_cells)}")
        print(f"Calendar missing:   {len(missing)}")
        print(f"DST failures:       {len(dst_failures)}")
        print(f"See: {args.output}/drift_detected.json")
        print()
        print("Resume with: re-run with --force after reviewing drift_detected.json,")
        print("OR fix the underlying drift and re-run.")
        return 2

    if result["audit_verdict"] == "FAIL" and args.force:
        LOGGER.warning(
            "AUDIT VERDICT = FAIL but --force set; continuing (drift=%d missing=%d dst=%d)",
            len(drift_cells),
            len(missing),
            len(dst_failures),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
