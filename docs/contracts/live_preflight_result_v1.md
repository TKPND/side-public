# Live Preflight Result v1 Contract

## Boundary

`side.live_preflight.result.v1` is a dedicated standalone public artifact for a future live no-order preflight result. It is not mixed into existing backtest, scan, or paper runtime `risk_gate` outputs, and it does not rename, normalize, or extend those adopted public proof fields.

The schema is present at `docs/contracts/live_preflight_result_v1.schema.json`. That schema validates the public result artifact shape and canonical examples only; validation scope is docs/examples/tests only. This phase creates no `risk/contracts/**/live_preflight_result*.schema.json` file and does not approve generation code, live CLI/runtime wiring, account fetchers, no-order preflight implementation, broker adapters, broker order paths, credential/network paths, or runtime public emission.

## Artifact Envelope

Top-level fields are ordered and named exactly as follows:

| Order | Field | Required value or meaning |
|---:|---|---|
| 1 | `schema_version` | `schema_version = side.live_preflight.result.v1` |
| 2 | `artifact_kind` | `artifact_kind = live_preflight_result` |
| 3 | `execution_mode` | `execution_mode = no_order_preflight` |
| 4 | `result` | Result status, failure vocabulary, and terminal gate. |
| 5 | `risk_gate` | Nested risk contract v2 validator proof reference. |
| 6 | `live_preflight` | No-order mutation flags and sanitized live proof blocks. |
| 7 | `emission` | Public persistence policy and protected-root status. |

The artifact is standalone. Existing backtest and scan public v2 proof remains nested under their runtime `risk_gate` outputs, and existing paper public v2 proof remains at its evidence-root fields.

## Risk Gate Reference Fields

Nested `risk_gate` preserves the frozen backtest/scan public v2 proof field names:

| Field | Required value |
|---|---|
| `schema_version` | `risk_contract.v2` |
| `contract_version` | `v2` |
| `validator_result_schema_version` | `risk_contract_validator_result.v2` |
| `schema_ref` | `risk/contracts/v2/risk_contract_v2.schema.json` |
| `validated_schema_ref` | `risk/contracts/v2/risk_contract_v2.schema.json` |
| `validator` | `scripts/validate_risk_contract.py` |

Do not add paper-prefixed fields to this nested live artifact `risk_gate`, and do not instruct readers to normalize paper-prefixed proof fields.

## Live Preflight Fields

Nested `live_preflight` fields are:

| Field | Meaning |
|---|---|
| `order_mutation_allowed` | Must be `false` for no-order preflight examples. |
| `order_mutation_attempted` | Must be `false` in valid persisted examples. |
| `broker_mutation_attempted` | Must be `false` in valid persisted examples. |
| `account_proof` | Sanitized account snapshot proof using aliases, refs, digests, and hashes only. |
| `market_proof` | Sanitized market snapshot proof. |
| `order_intent` | Sanitized order-intent preview proof, not a broker order. |
| `kill_switch_proof` | Sanitized kill-switch state proof. |
| `idempotency_proof` | Sanitized duplicate/idempotency proof. |

The three mutation booleans must be false in valid persisted examples.

## Result Semantics

`result` contains `status`, `failure_class`, `failure_reason`, and `terminal_gate`.

| Field | Approved values or examples |
|---|---|
| `result.status` | `passed`, `risk_stopped`, `failed` |
| `failure_class` | `null`, `risk_decision`, `stale_proof`, `duplicate_idempotency`, `unsafe_material`, `mutation_attempt`, `protected_output_root`, `infrastructure` |
| `failure_reason` | `null`, `broker_risk_block`, `kill_switch_active`, `risk_validator_rejected`, `account_proof_stale`, `market_proof_stale`, `idempotency_replay_detected`, `raw_secret_or_private_identifier`, `order_mutation_attempted`, `broker_mutation_attempted`, `protected_output_root_denied`, `preflight_dependency_unavailable` |

Detailed semantics live in `failure_class` and `failure_reason`, not in an expanding `status` vocabulary. Successful risk stops are distinct from infrastructure failures: a `risk_decision` stop means the candidate was valid enough to be evaluated and stopped; an `infrastructure` failure means preflight dependencies or proof requirements failed before a valid risk decision.

## Failure And Emission Matrix

| Outcome | `result.status` | `failure_class` | Persistence policy |
|---|---|---|---|
| Passed no-order preflight | `passed` | `null` | `emission.persisted = true` may be used for a public artifact. |
| Broker block, kill switch, or validator reject | `risk_stopped` | `risk_decision` | `emission.persisted = true` may be used only with `order_mutation_attempted = false` and `broker_mutation_attempted = false`. |
| Stale account or market proof | `failed` | `stale_proof` | May persist only when all proof references are sanitized. |
| Duplicate idempotency proof | `failed` | `duplicate_idempotency` | May persist only when all proof references are sanitized. |
| Unsafe raw material | `failed` | `unsafe_material` | `emission.persisted = false`; do not persist a public artifact. |
| Order or broker mutation attempt | `failed` | `mutation_attempt` | `emission.persisted = false`; do not persist a public artifact. |
| Protected output root denied | `failed` | `protected_output_root` | `emission.persisted = false`; do not persist a public artifact. |
| Dependency unavailable | `failed` | `infrastructure` | May persist only when sanitized and not exposing unsafe material. |

## Gate Ordering

Terminal gate names are frozen as:

| Order | Terminal gate | Typical failure class |
|---:|---|---|
| 1 | `protected_output_root` | `protected_output_root` |
| 2 | `credential_hygiene` | `unsafe_material` |
| 3 | `account_proof` | `stale_proof` or `infrastructure` |
| 4 | `market_proof` | `stale_proof` or `infrastructure` |
| 5 | `idempotency` | `duplicate_idempotency` |
| 6 | `risk_validator` | `risk_decision` or `infrastructure` |
| 7 | `risk_decision` | `risk_decision` |
| 8 | `no_order_assertion` | `mutation_attempt` |
| 9 | `preflight_dependency` | `infrastructure` |

## Example Matrix

Valid public examples live under `docs/examples/live_preflight/result_v1/valid/`:

| Example | Purpose |
|---|---|
| `passed_no_order_preflight` | Passed no-order preflight with persisted public artifact. |
| `risk_stopped_kill_switch` | Successful risk stop for `kill_switch_active` with no mutation. |
| `failed_account_proof_stale` | Sanitized persisted stale account proof failure. |
| `failed_duplicate_idempotency` | Sanitized persisted duplicate idempotency failure. |

Invalid test-only counterexamples live under `docs/examples/live_preflight/result_v1/invalid/`:

| Counterexample | Intended violation |
|---|---|
| `invalid_order_mutation_attempted` | Order mutation attempted. |
| `invalid_broker_mutation_attempted` | Broker mutation attempted. |
| `invalid_unsafe_raw_material` | Unsafe raw account or private material present. |
| `invalid_protected_output_root_persisted` | Protected output root failure persisted as public artifact. |

Invalid counterexamples are not runtime emissions and do not imply live runtime availability.

## Verification Contract

Focused verification for this contract consists of:

- JSON parsing for every example artifact.
- Draft 2020-12 schema self-check for `docs/contracts/live_preflight_result_v1.schema.json`.
- Schema validation of every canonical example under `docs/examples/live_preflight/result_v1/{valid,invalid}/`; validation scope is docs/examples/tests only.
- Pytest assertions over the frozen top-level fields, nested `risk_gate` fields, no-order mutation flags, result/failure matrix, emission policy, and sanitized proof material.
- An exact schema-present guard that permits only `docs/contracts/live_preflight_result_v1.schema.json` to claim `side.live_preflight.result.v1`.
- Runtime absence guards proving this phase introduced no live CLI/runtime caller, account fetcher, no-order preflight runtime implementation, broker adapter, broker order path, or runtime public emission.
- Existing public proof invariance checks for adopted backtest, scan, and paper v2 proof fields.
- `rtk git diff --check`.

## Non-Goals

This contract does not approve or implement:

- generation code that depends on `side.live_preflight.result.v1`.
- live CLI or runtime wiring.
- account fetchers.
- no-order preflight runtime implementation.
- broker adapters.
- broker order paths.
- credential/network paths.
- runtime public emission.
- public proof-field changes for adopted backtest, scan, or paper v2 surfaces.
