# Profit Visibility Contract v1

## Boundary

This contract is documentation/test scope only. It freezes the v9.0 outcome
vocabulary, no-live scope, post-checkpoint stage-gate ledger, minimal
candidate-registration semantics, and links to the concrete Phase 163
registration and cost companion contracts before evidence generation.

This contract links Phase 163 companion contracts for evaluation protocol,
pre-registration anchor, candidate count, multiple-testing, disclosure, cost,
and capacity surfaces. It does not approve evidence generation, split
generation, leakage algorithms, multiple-testing-control computation, OTS proof
creation, OTS verifier integration, `ProfitVisibilityReport.v1`, full cost
calculator behavior, economic metric computation, runtime CLI wiring,
broker/account/network/credential code, public schema expansion, protected
archives, golden/seal/parity/SHA fixture updates, or tiny live trades.

## Outcome Truth Table

| outcome | required preconditions | allowed claims | forbidden claims |
|---|---|---|---|
| `profit_visible` | D-01: registered candidate; realistic costs; OOS/WFD or holdout evidence; multiple-testing control; leakage checks; sample gates. | A candidate family has passed the registered profit-evidence gate under the stated cost and statistical controls. | missing, zero, or post-hoc-relaxed costs force `profit_visible = false`; profitability implications without `profit_visible`; live readiness claims; broker/account/network readiness claims; profit claims based on missing, zero, or post-hoc-relaxed costs. |
| `honest_null_ship` | D-02: registration, costs, OOS/WFD or holdout, and multiple-testing were completed, but no candidate survived. | The checkpoint reached a real profit decision and stopped without carrying a candidate forward. | Profitability, paper-forward, broker, account, network, or live-readiness claims. |
| `plumbing_only` | D-03: infrastructure, documentation, or report plumbing exists, but the evidence needed for a profit decision is incomplete. | The repo has preparatory plumbing only. | It must not be used as a profit-readiness or live-readiness label; infrastructure readiness is not profit readiness. |

## Allowed And Forbidden Claims

D-04 and D-05 require claim boundaries to be explicit. Allowed claims are limited
to the exact outcome row whose preconditions were satisfied.

Forbidden claims:

- profitability implications without `profit_visible`.
- live readiness claims.
- broker/account/network readiness claims.
- profit claims based on missing, zero, or post-hoc-relaxed costs.
- infrastructure readiness is not profit readiness.

## v9.0 No-Live Scope Ban

Phase 162 carries the full D-07 no-live scope ban. The following surfaces remain
out of scope for v9.0 and for this contract phase:

- live account fetcher.
- credential loader/storage.
- network/SDK/HTTP/socket/cloud secret/subprocess fetch.
- broker adapter/order/mutation.
- `side live`.
- runtime public live emission expansion.
- public schema expansion.
- tiny live trade.

D-08 rule: any forbidden surface is a v9.0 scope violation that blocks the v9.0 claim. It cannot be downgraded to `plumbing_only`.

## False-Positive Inspection Policy

D-06 and D-09 intentionally reject broad forbidden-word checks. Docs and tests
must mention live, broker, account, credential, and `side live` vocabulary to
state the ban. Therefore targeted grep is inspection evidence, not a broad repo-wide forbidden-word hard fail.

## Post-v9.0 Stage-Gate Ledger

This ledger creates no automatic promotion. Every later stage requires separate phase approval and its own verification before any implementation or readiness claim.

| stage | allowed actions | forbidden actions | required proof | promotion gate |
|---|---|---|---|---|
| read-only account snapshot | Design and test a read-only account snapshot boundary. | Credential storage, broker mutation, order submission, public raw account output. | Credential hygiene, secret non-logging, freshness, fail-closed sanitization, and no raw public output. | Separate read-only account phase approval. |
| no-order live shadow preflight | Run no-order shadow checks after a safe read-only account boundary exists. | Broker order submission, cancellation, modification, runtime public live emission expansion without approval. | No-order proof, sanitized account/market/order-intent proof, kill-switch proof, idempotency proof, and rollback plan. | Separate no-order live shadow phase approval. |
| broker dry-run or sandbox validation | Validate broker integration only in dry-run, sandbox, or validate-only mode. | Real order mutation, real-money fills, hidden network/credential expansion outside approved scope. | Broker dry-run/sandbox logs, idempotency, kill-switch, monitoring, reconciliation, and rollback evidence. | Separate broker dry-run/sandbox phase approval. |
| tiny one-order mutation smoke test | Final stage only; evaluate a tiny mutation plan after all prior gates exist. | Any tiny live trade before read-only account, no-order shadow, broker dry-run/sandbox, hard notional cap, max-loss cap, manual kill, idempotency, and reconciliation gates. | read-only account, no-order shadow, broker dry-run/sandbox, hard notional cap, max-loss cap, manual kill, idempotency, and reconciliation gates. | Separate tiny one-order mutation smoke test phase approval. |

## Candidate Registration Surface

Phase 162 freezes only the PVC-REG-01 minimal fields. Each field is a
pre-evaluation input, not result evidence.

| field | semantics | minimal example | rejection behavior |
|---|---|---|---|
| signal family | Human-readable family name for the candidate signal idea. | `mean_reversion_close_to_vwap` | Reject blank, unknown, or TBD values. |
| universe | Tradable universe before evaluation begins. | `top_100_us_equities_by_dollar_volume` | Reject blank, unknown, or TBD values. |
| timeframe | Bar interval and evaluation horizon family. | `daily bars, 2018-2025 candidate family` | Reject blank, unknown, or TBD values. |
| data source | Source of market data used by the candidate family. | `repo-supported adjusted OHLCV dataset` | Reject blank, unknown, or TBD values. |
| execution assumption | Fill, delay, and market-access assumptions used before evaluation. | `next-bar close with realistic spread/slippage model later specified` | Reject blank, unknown, or TBD values. |
| sizing assumption | Position-sizing premise before evaluation. | `fixed fractional notional cap later bounded by Phase 163 cost/capacity protocol` | Reject blank, unknown, or TBD values. |
| expected economic rationale | Pre-evaluation hypothesis for why the edge could exist. | `temporary liquidity imbalance should mean-revert after excessive close displacement` | Reject blank, unknown, or TBD values; expected economic rationale is a pre-evaluation hypothesis, not profit evidence. |

The expected economic rationale is a pre-evaluation hypothesis, not profit evidence. thin, post-hoc, or result-derived rationale blocks `profit_visible` claims.

## Phase 163 Protocol Contracts

Phase 163 replaces the old reservation with concrete companion contracts. These
contracts are still documentation/test scope only; they do not create Phase 164
evidence behavior.

### Registration Protocol Link

`docs/contracts/profit_visibility_registration_protocol_v1.md` is the concrete
registration protocol source for:

- split/OOS/WFD or holdout protocol fields.
- leakage checks.
- minimum eligible sample criteria.
- supported evaluation run definitions for repo-supported scripts, tests,
  report generators, or documented workflows that compute candidate
  performance, survivor status, multiple-testing inputs, or paper-forward
  readiness.
- strict byte seal rules over exact artifact bytes.
- OpenTimestamps as the primary anchor path.
- `.ots` proof metadata and accepted external forgery-resistant anchor classes.
- pre_anchor_result_bearing_runs disclosure handling.
- protocol-critical immutability for thresholds, features, filters, acceptance
  criteria, multiple-testing-control method, equivalent-control criteria, named
  error-rate target, alpha level, candidate-family boundary, and
  finest-granularity hypothesis count.
- FWER/Holm as the standard multiple-testing control.
- error_rate_target = FWER.
- alpha = 0.05.
- finest-granularity hypothesis count rules that include every evaluated
  parameter, threshold, filter, universe, timeframe, and split/protocol variant.
- equivalent-control dossier requirements and fallback/null-ship conditions.

### Cost Model Link

`docs/contracts/profit_visibility_cost_model_v1.md` is the concrete cost and
capacity protocol source for:

- fees.
- spread.
- slippage.
- turnover.
- financing.
- borrow.
- conversion.
- market-access assumptions.
- missing, zero, unknown, or TBD cost rejection.
- explicit nonzero effective-cost rationale as cost-presence-only evidence.
- base cost scenario requirements.
- adverse cost ladder requirements.
- cost_model_fingerprint traceability.
- notional measurement constraints.
- capacity measurement constraints.
- leverage measurement constraints.
- max-loss measurement constraints.
- measurement constraints only; capacity rows are not paper-forward, live,
  account, broker, network, credential, or runtime readiness claims.

## Verification Contract

Focused verification for this contract consists of:

- `rtk uv run pytest -q tests/test_profit_visibility_registration.py tests/test_profit_visibility_cost_model.py tests/test_profit_visibility_contract.py`
- `PHASE162_SCOPE_GUARD=1 PHASE162_DIFF_BASE=b08ed3b rtk uv run pytest -q tests/test_profit_visibility_contract.py`
- `PHASE162_SCOPE_GUARD=1 PHASE162_DIFF_BASE=b08ed3b rtk uv run pytest -q tests/test_profit_visibility_contract.py tests/test_live_preflight_result_contract.py tests/test_risk_contract_v2_public_proof_invariance.py`
- `rtk rg -n "side live|broker|credential|account fetch|profit_visible|honest_null_ship|plumbing_only" .planning docs tests`
- `rtk git diff --check`

## Non-Goals

This contract does not approve or implement:

- evidence generation.
- OTS proof creation.
- full OTS verifier integration.
- `ProfitVisibilityReport.v1`.
- full cost calculator.
- economic metric computation.
- evaluation protocol implementation beyond docs/test contract links.
- pre-registration anchor creation.
- split/OOS/WFD or holdout mechanics or split generation.
- leakage algorithms.
- candidate count or multiple-testing computation.
- runtime CLI wiring.
- live/account/broker/network/credential paths.
- broker adapter/order/mutation paths.
- live account fetcher.
- credential loader/storage.
- network/SDK/HTTP/socket/cloud secret/subprocess fetch.
- `side live`.
- runtime public live emission expansion.
- public schema expansion.
- protected archives.
- protected archive, golden, seal, parity, or SHA fixture updates.
- tiny live trade.
