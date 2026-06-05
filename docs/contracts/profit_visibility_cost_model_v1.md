# Profit Visibility Cost Model v1

## Boundary

This contract is documentation/test scope only. It freezes the Phase 163 cost
and capacity rules that must exist before any profit-visible claim can be made.

The contract covers realistic costs, missing/zero/unknown/TBD rejection,
explicit nonzero effective-cost rationale, base and adverse sensitivity gates,
cost_model_fingerprint traceability, and capacity bounds. It does not calculate
economic metrics and does not create report generation, runtime CLI wiring,
live/account/broker/network/credential paths, public schema expansion,
protected archives, golden files, seal files, parity fixtures, or SHA fixture
updates.

## Realistic Base Cost Model

Every candidate family needs a realistic base cost model before it can support
`profit_visible`. The base cost scenario is a pre-evaluation assumption set, not
post-hoc tuning.

| field | semantics | minimal example | rejection behavior |
|---|---|---|---|
| `fees` | Explicit commission, exchange, regulatory, or venue fee assumption. | `1.5 bps round trip` | Reject missing, zero, `unknown`, or `TBD` values unless covered by an explicit nonzero effective-cost rationale. |
| `spread` | Bid/ask spread or comparable crossing-cost assumption. | `0.5 bps round trip` | Reject missing, zero, `unknown`, or `TBD` values unless covered by an explicit nonzero effective-cost rationale. |
| `slippage` | Slippage assumption from execution delay, impact, queue priority, or fill uncertainty. | `2.0 bps round trip` | Reject missing, zero, `unknown`, or `TBD` values unless covered by an explicit nonzero effective-cost rationale. |
| `turnover` | Expected trading turnover used to scale recurring costs. | `1.1 portfolio turns per day` | Reject missing, zero, `unknown`, or `TBD` values unless covered by an explicit nonzero effective-cost rationale. |
| `financing` | Applicable financing carry, margin, funding, or cash drag assumption. | `0.25 bps per holding day` | Reject missing, zero, `unknown`, or `TBD` values when financing is applicable. |
| `borrow` | Applicable borrow fee, short locate cost, hard-to-borrow cost, or equivalent. | `0.25 bps per holding day` | Reject missing, zero, `unknown`, or `TBD` values when borrow is applicable. |
| `conversion` | Applicable FX conversion, venue conversion, or settlement conversion cost. | `0.10 bps round trip` | Reject missing, zero, `unknown`, or `TBD` values when conversion is applicable. |
| `market_access_assumptions` | Market-access assumptions, venue access costs, routing constraints, liquidity tier, or execution access limits. | `0.05 bps equivalent access cost` | Reject missing, zero, `unknown`, or `TBD` values when market-access assumptions are applicable. |

The literal market-access assumptions field may be represented as
`market_access_assumptions` in test fixtures, but the human contract language
uses market-access assumptions.

## Missing Zero Unknown And TBD Cost Rejection

missing, zero, unknown, or TBD costs cannot support `profit_visible`.

If any required cost field is absent, blank, zero, `unknown`, or `TBD`, the cost
model status is `cost_incomplete` and any cost-supported claim must route to
`profit_visible = false` or a non-profit preparatory outcome. Missing cost
assumptions are not warnings, and Fee=0 alpha is not acceptable evidence.

This rejection applies to `fees`, `spread`, `slippage`, `turnover`, and every
applicable `financing`, `borrow`, `conversion`, and market-access assumptions
field.

## Explicit Nonzero Effective Cost Rationale

An explicit nonzero effective-cost rationale satisfies cost presence only.

The rationale can explain why a literal zero in one accounting bucket is already
represented by another nonzero realistic cost field, such as nonzero spread,
nonzero slippage, nonzero turnover-scaled cost, nonzero borrow, nonzero
financing, nonzero conversion, or nonzero market-access assumptions. The
rationale must be written before the profit decision and must name the nonzero
field that carries the effective cost.

An explicit nonzero effective-cost rationale does not by itself approve `profit_visible`.
It only prevents a cost-presence rejection. The candidate still must survive
the registered base cost scenario, adverse cost ladder, OOS/WFD or holdout
evidence, leakage checks, sample gates, and multiple-testing control before any
profit-visible claim can be made.

## Base And Adverse Cost Ladder

The base cost scenario and adverse cost ladder are both required. A candidate
must survive both the base cost scenario and the adverse cost ladder to remain
eligible for `profit_visible`.

The adverse ladder must worsen the core recurring cost assumptions:

| field | semantics | minimal example | rejection behavior |
|---|---|---|---|
| `adverse_fees` | Worsened fee assumption relative to base. | `base fees + 2.0 bps` | Missing, zero, `unknown`, or `TBD` adverse fees fail the sensitivity gate. |
| `adverse_spread` | Worsened spread assumption relative to base. | `base spread * 2` | Missing, zero, `unknown`, or `TBD` adverse spread fails the sensitivity gate. |
| `adverse_slippage` | Worsened slippage or impact assumption relative to base. | `base slippage * 2` | Missing, zero, `unknown`, or `TBD` adverse slippage fails the sensitivity gate. |
| `adverse_turnover` | Worsened turnover or churn assumption relative to base. | `base turnover * 1.5` | Missing, zero, `unknown`, or `TBD` adverse turnover fails the sensitivity gate. |

## Cost Sensitivity Null-Ship Gate

Adverse cost ladder failure forces `profit_visible = false` or honest null-ship
routing. If a candidate fails any adverse row, the checkpoint cannot relabel
that failure as live readiness, paper-forward readiness, or plumbing success.

The expected fail-closed outcomes are:

- `profit_visible = false` when cost sensitivity is the immediate blocker.
- `honest_null_ship` when the rest of the checkpoint completed and no candidate
  survived under base plus adverse costs.
- `plumbing_only` only when the cost contract or report plumbing exists but the
  evidence needed for a profit decision is incomplete.

## Cost Model Fingerprint

`cost_model_fingerprint` is required before any cost-supported claim. The
fingerprint records the exact cost assumption payload used for the base cost
scenario and adverse cost ladder.

The fingerprint vocabulary follows the existing v6.5/v6.6 reports and uses
`sha256:` prefixes, for example:

```text
cost_model_fingerprint = sha256:ae690904fa2eb30146092a6b993a90b57770d67ce1dd52856c2ca66ff1a6e864
```

The fingerprint must be stable enough for a later `ProfitVisibilityReport.v1`
to cite the same cost assumptions without reinterpreting them. A missing,
malformed, non-`sha256:` or post-hoc changed `cost_model_fingerprint` blocks
cost-supported `profit_visible` claims.

## Capacity Measurement Constraints

Capacity fields make later stages measurable. They are measurement constraints
only, and they are not operational readiness claims.

| field | semantics | minimal example | rejection behavior |
|---|---|---|---|
| `notional` | Candidate notional bound for measuring economics and risk. | `max 10,000 USD equivalent per candidate` | Missing, `unknown`, or `TBD` notional bounds block paper-forward measurement. |
| `capacity` | Capacity estimate or liquidity ceiling for the candidate family. | `max 2 percent of median dollar volume` | Missing, `unknown`, or `TBD` capacity bounds block paper-forward measurement. |
| `leverage` | Leverage assumption or upper bound used for measurement. | `1.0x gross leverage` | Missing, `unknown`, or `TBD` leverage bounds block paper-forward measurement. |
| `max-loss` | Maximum loss, stop, or drawdown budget used for measurement. | `max-loss 2 percent of allocated notional` | Missing, `unknown`, or `TBD` max-loss bounds block paper-forward measurement. |

These capacity rows are not paper-forward readiness, not live readiness, not account readiness, not broker readiness, not network readiness, not credential readiness, and not runtime readiness.

## Cost Verification Contract

Focused verification for this contract consists of:

- `rtk uv run pytest -q tests/test_profit_visibility_cost_model.py`
- `rtk uv run pytest -q tests/test_profit_visibility_registration.py tests/test_profit_visibility_cost_model.py`
- `rtk git diff --check`

The verification proves the contract includes realistic base costs, rejects
missing/zero/unknown/TBD costs, recognizes explicit nonzero effective-cost
rationale as cost-presence-only, requires base plus adverse cost sensitivity,
requires `cost_model_fingerprint`, and keeps notional/capacity/leverage/max-loss
as measurement constraints only.

## Non-Goals

Phase 163 does not implement economic metric computation,
`ProfitVisibilityReport.v1`, runtime CLI wiring, live/account/broker/network/
credential paths, public schema expansion, protected archives, golden, seal,
parity, or SHA fixture updates.

This contract does not approve or implement:

- production cost calculator behavior.
- actual economic metric computation belongs to Phase 164.
- report generator behavior.
- runtime CLI wiring.
- live account fetching.
- broker adapter, broker SDK, broker order, or broker mutation paths.
- credential loading, keyring access, token handling, or private endpoint paths.
- network, HTTP, socket, SDK, cloud secret, or subprocess-driven fetch paths.
- public schema expansion.
- protected archive, golden, seal, parity, or SHA fixture updates.
