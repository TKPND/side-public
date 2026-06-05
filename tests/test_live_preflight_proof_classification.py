"""Phase 156 tests for deterministic live-preflight proof classification."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from tests.helpers.live_preflight_proof_classification import (
    FakeProofClock,
    classify_freshness,
    classify_idempotency,
    classify_kill_switch,
)


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "tests/helpers/live_preflight_proof_classification.py"
SCHEMA_PATH = ROOT / "docs/contracts/live_preflight_result_v1.schema.json"
PASSED_EXAMPLE = (
    ROOT / "docs/examples/live_preflight/result_v1/valid/passed_no_order_preflight.json"
)


def _schema_properties(definition_name: str) -> set[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return set(schema["$defs"][definition_name]["properties"])


def _passed_proof(block: str) -> dict[str, Any]:
    example = json.loads(PASSED_EXAMPLE.read_text(encoding="utf-8"))
    return dict(example["live_preflight"][block])


def test_freshness_classification_uses_fake_clock_values() -> None:
    clock = FakeProofClock()
    account_proof = _passed_proof("account_proof")
    idempotency_proof = _passed_proof("idempotency_proof")

    current_account = classify_freshness(account_proof, now=clock, stale=False)
    stale_account = classify_freshness(account_proof, now=clock, stale=True)
    current_idempotency = classify_freshness(
        idempotency_proof,
        now=clock,
        stale=False,
    )
    stale_idempotency = classify_freshness(
        idempotency_proof,
        now=clock,
        stale=True,
    )

    assert current_account["snapshot_ts"] == "2026-05-19T00:00:10Z"
    assert current_account["snapshot_max_age_ms"] == 30000
    assert stale_account["snapshot_ts"] == "2026-05-18T23:59:00Z"
    assert stale_account["snapshot_max_age_ms"] == 1
    assert current_idempotency["proof_ts"] == "2026-05-19T00:00:10Z"
    assert current_idempotency["proof_max_age_ms"] == 10000
    assert stale_idempotency["proof_ts"] == "2026-05-18T23:59:00Z"
    assert stale_idempotency["proof_max_age_ms"] == 1


def test_idempotency_classification_sets_public_status_and_refs() -> None:
    clock = FakeProofClock()
    proof = _passed_proof("idempotency_proof")

    clean = classify_idempotency(proof, now=clock, duplicate=False)
    duplicate = classify_idempotency(proof, now=clock, duplicate=True)

    assert clean["duplicate_check_status"] == "passed"
    assert clean["duplicate_check_ref"] == "duplicate-check-sha256-clean"
    assert clean["proof_ts"] == clock.now_iso
    assert clean["proof_max_age_ms"] == 10000
    assert duplicate["duplicate_check_status"] == "duplicate_detected"
    assert duplicate["duplicate_check_ref"] == "duplicate-check-sha256-duplicate"
    assert "idempotency_key" not in clean
    assert "raw_idempotency_key" not in duplicate


def test_kill_switch_classification_sets_active_and_inactive_gates() -> None:
    clock = FakeProofClock()
    proof = _passed_proof("kill_switch_proof")

    inactive = classify_kill_switch(proof, now=clock, active=False)
    active = classify_kill_switch(proof, now=clock, active=True)

    for key in (
        "global_gate_status",
        "strategy_gate_status",
        "symbol_gate_status",
        "broker_account_gate_status",
    ):
        assert inactive[key] == "passed"

    assert active["global_gate_status"] == "blocked"
    assert active["global_gate_ref"] == "gate-ref-sha256-global-active"
    assert active["strategy_gate_status"] == "passed"
    assert active["symbol_gate_status"] == "passed"
    assert active["broker_account_gate_status"] == "passed"
    assert active["proof_ts"] == clock.now_iso
    assert active["proof_max_age_ms"] == 10000


def test_classification_helpers_keep_schema_field_subsets_only() -> None:
    clock = FakeProofClock()

    account = classify_freshness(
        _passed_proof("account_proof"),
        now=clock,
        stale=False,
    )
    idempotency = classify_idempotency(
        _passed_proof("idempotency_proof"),
        now=clock,
        duplicate=True,
    )
    kill_switch = classify_kill_switch(
        _passed_proof("kill_switch_proof"),
        now=clock,
        active=True,
    )

    assert set(account) <= _schema_properties("account_proof")
    assert set(idempotency) <= _schema_properties("idempotency_proof")
    assert set(kill_switch) <= _schema_properties("kill_switch_proof")


def test_classification_helpers_copy_inputs_without_mutation() -> None:
    clock = FakeProofClock()
    proof = _passed_proof("idempotency_proof")
    original = dict(proof)

    classified = classify_idempotency(proof, now=clock, duplicate=True)

    assert proof == original
    assert classified is not proof
    assert classified != proof


def test_proof_classification_helper_has_no_runtime_dependency_surface() -> None:
    source = HELPER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    forbidden_import_roots = {
        "datetime",
        "time",
        "os",
        "requests",
        "urllib",
        "httpx",
        "socket",
        "broker",
        "account_fetcher",
        "credential",
        "side_cli",
        "side_engine",
    }
    assert forbidden_import_roots.isdisjoint({name.split(".")[0] for name in imports})

    forbidden_text = {
        "datetime.now",
        "datetime.utcnow",
        "time.time",
        "time.monotonic",
        "os.environ",
        "requests",
        "urllib",
        "httpx",
        "socket",
        "broker_client",
        "account_fetcher",
        "credential_loader",
        "credential_client",
        "environment_secret",
        "argparse",
        "subprocess",
        'if __name__ == "__main__"',
    }
    for text in forbidden_text:
        assert text not in source
