"""Deterministic proof shaping helpers for test-owned live preflight proofs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FakeProofClock:
    now_iso: str = "2026-05-19T00:00:10Z"
    stale_iso: str = "2026-05-18T23:59:00Z"


def classify_freshness(
    proof: dict[str, Any],
    *,
    now: FakeProofClock,
    stale: bool,
) -> dict[str, Any]:
    classified = dict(proof)
    proof_ts = now.stale_iso if stale else now.now_iso
    snapshot_max_age_ms = 1 if stale else 30000
    proof_max_age_ms = 1 if stale else 10000

    _set_time_window(
        classified,
        ts_key="snapshot_ts",
        max_age_key="snapshot_max_age_ms",
        ts=proof_ts,
        max_age_ms=snapshot_max_age_ms,
    )
    _set_time_window(
        classified,
        ts_key="market_ts",
        max_age_key="market_max_age_ms",
        ts=proof_ts,
        max_age_ms=snapshot_max_age_ms,
    )
    _set_time_window(
        classified,
        ts_key="proof_ts",
        max_age_key="proof_max_age_ms",
        ts=proof_ts,
        max_age_ms=proof_max_age_ms,
    )
    return classified


def classify_idempotency(
    proof: dict[str, Any],
    *,
    now: FakeProofClock,
    duplicate: bool,
) -> dict[str, Any]:
    classified = {
        key: value
        for key, value in proof.items()
        if key not in {"idempotency_key", "raw_idempotency_key"}
    }
    if duplicate:
        classified["duplicate_check_status"] = "duplicate_detected"
        classified["duplicate_check_ref"] = "duplicate-check-sha256-duplicate"
    else:
        classified["duplicate_check_status"] = "passed"
        classified["duplicate_check_ref"] = "duplicate-check-sha256-clean"
    classified["proof_ts"] = now.now_iso
    classified["proof_max_age_ms"] = 10000
    return classified


def classify_kill_switch(
    proof: dict[str, Any],
    *,
    now: FakeProofClock,
    active: bool,
) -> dict[str, Any]:
    classified = dict(proof)
    status_keys = (
        "global_gate_status",
        "strategy_gate_status",
        "symbol_gate_status",
        "broker_account_gate_status",
    )
    for key in status_keys:
        if key in classified:
            classified[key] = "passed"
    if active:
        classified["global_gate_status"] = "blocked"
        classified["global_gate_ref"] = "gate-ref-sha256-global-active"
    classified["proof_ts"] = now.now_iso
    classified["proof_max_age_ms"] = 10000
    return classified


def _set_time_window(
    proof: dict[str, Any],
    *,
    ts_key: str,
    max_age_key: str,
    ts: str,
    max_age_ms: int,
) -> None:
    if ts_key in proof or max_age_key in proof:
        proof[ts_key] = ts
        proof[max_age_key] = max_age_ms
