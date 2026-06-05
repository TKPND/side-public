# Profit Visibility Registration Protocol v1

## Boundary

This contract is documentation/test scope only. It freezes Phase 163
candidate-family registration, supported evaluation run definitions, strict byte
seal and OpenTimestamps anchor rules, pre-anchor result-bearing run disclosure,
protocol-critical immutability, and candidate count and multiple-testing control
before any Phase 164 evaluation output exists.

Phase 163 does not create OTS proofs, integrate an OTS verifier, compute
Holm/FWER, generate `ProfitVisibilityReport.v1`, wire runtime CLI/live/account/
broker/network/credential paths, expand public schemas, or mutate protected
archives/golden/seal/parity/SHA fixtures.

OpenTimestamps is the primary Phase 163 anchor path. A future evaluator may
accept another external forgery-resistant anchor class only when it provides
non-editable, externally time-bound evidence equivalent to an RFC 3161 timestamp
token or platform-signed push certificate. Author-controlled timestamps never
qualify.

## Required Pre-Registration Fields

Every registration artifact must be complete before a supported evaluation run.
Reject blank, unknown, TBD, or placeholder values.

| field | required semantics | rejection behavior |
|---|---|---|
| split/OOS/WFD or holdout protocol | The exact in-sample and out-of-sample protocol, including OOS, WFD, holdout, PurgedKFold, and embargo settings when used. | Missing, blank, unknown, TBD, placeholder, or changed protocol blocks `profit_visible`. |
| leakage checks | The pre-evaluation leakage checks and their pass criteria. | Missing checks or post-result relaxation blocks `profit_visible`. |
| minimum eligible sample | The minimum eligible sample and trade-count criteria required before evaluation. | Missing or relaxed criteria blocks `profit_visible`. |
| multiple-testing-control method | The registered method, standard value `FWER/Holm`. | Method mismatch or unregistered alternative forces `profit_visible = false`. |
| equivalent-control choice | Either `none` or a pre-anchored equivalent-control dossier. | Missing dossier for a chosen equivalent control forces `profit_visible = false`. |
| named error-rate target | `error_rate_target = FWER`. | Target mismatch forces `profit_visible = false`. |
| alpha level | `alpha = 0.05`. | Alpha mismatch forces `profit_visible = false`. |
| candidate-family boundary | The exact family, universe, timeframe, feature/filter space, parameters, and evaluation boundary. | Boundary drift or post-result narrowing blocks `profit_visible`. |
| total candidate/hypothesis count | The total at the finest-granularity hypothesis count. | Sealed-count shrinkage or survivor-only counting forces `profit_visible = false`. |
| pre_anchor_result_bearing_runs | A required disclosure field listing every pre-anchor result-bearing run over the registered family. | Missing field, undisclosed discovered run, or false disclosure invalidates the family. |

## Supported Evaluation Runs

A supported evaluation run is any repo-supported scripts, tests, report generators, or documented workflows that computes candidate performance, survivor status, multiple-testing inputs, or paper-forward readiness for a registered candidate family.

This definition is intentionally broad. A run remains result-bearing even when
it is exploratory, local-only, not committed, not published, or later discarded,
if it computes candidate performance, survivor status, multiple-testing inputs,
or paper-forward readiness for the registered family.

## Strict Byte Seal And OpenTimestamps Anchor

The registration uses a strict byte seal. The sealed artifact bytes, sha256
digest, `.ots` proof metadata, anchor kind, anchor verification status, and
registration path are protocol material.

Rules:

- OpenTimestamps is the primary anchor.
- The sha256 value is computed over exact artifact bytes, not a parsed dict.
- Any byte mismatch, missing `.ots` proof, mismatched sha256, or failed anchor
  verification fails closed.
- The `.ots` proof must bind the registered bytes before any supported
  evaluation run.
- Alternative external forgery-resistant anchor classes must still prove
  non-editable external time before supported evaluation output.

## Disqualified Anchor Evidence

The following do not qualify as pre-registration anchor evidence:

- git commit dates.
- git tag dates.
- force-pushable refs.
- local file mtimes.
- handwritten timestamps.
- unverified CI log timestamps.

These anchor forms are author-controlled, force-pushable, local, handwritten, or
unverified. They cannot support `profit_visible`.

## Pre-Anchor Result-Bearing Run Disclosure

The `pre_anchor_result_bearing_runs` field is mandatory.

| disclosure state | checkpoint outcome | claim boundary |
|---|---|---|
| no prior result-bearing run | eligible for evaluation if every other registration, cost, OOS/WFD or holdout, leakage, sample, and MTC gate passes. | May later support `profit_visible` only if Phase 164 evidence passes. |
| disclosed pre-anchor result-bearing run | forces `profit_visible = false`. | May route only to `honest_null_ship` or `plumbing_only` evidence, depending on completed checkpoint evidence. |
| undisclosed pre-anchor result-bearing run discovered later | invalid/disqualified. | Hard invalidation for that family; no profit claim and not an honest null-ship decision. |

## Protocol-Critical Immutability

Protocol-critical fields cannot be relaxed, narrowed, expanded, reinterpreted,
or changed after anchoring or after any result-bearing output exists.

| field | fail-closed rule |
|---|---|
| thresholds | Any post-anchor or post-result change fails closed. |
| features | Any post-anchor or post-result change fails closed. |
| filters | Any post-anchor or post-result change fails closed. |
| acceptance criteria | Any post-anchor or post-result relaxation fails closed. |
| multiple-testing-control method | Any method mismatch forces `profit_visible = false`. |
| equivalent-control choice and criteria | Any unregistered choice or criteria change forces `profit_visible = false`. |
| named error-rate target | Any target mismatch forces `profit_visible = false`. |
| alpha level | Any alpha mismatch forces `profit_visible = false`. |
| candidate-family boundary | Any boundary shrinkage, expansion, or reinterpretation fails closed. |
| finest-granularity hypothesis count | Any shrinkage, recounting, or survivor-only correction forces `profit_visible = false`. |

## Candidate Count And Multiple-Testing Control

The standard multiple-testing-control method is FWER/Holm. The standard named
target is `error_rate_target = FWER`, and the standard alpha is `alpha = 0.05`.
Permutation and DSR may be supplemental evidence only, or a pre-approved
equivalent control when the equivalent-control dossier is complete before the
anchor.

The finest-granularity hypothesis count includes every evaluated minimal unit:

- signal family.
- universe.
- timeframe.
- feature/filter variant.
- parameter grid/candidate.
- split/protocol variant.
- other evaluated minimal candidate unit.

recounting only reported candidates or survivors is forbidden. Sealed-count
shrinkage, survivor-only correction, method mismatch, alpha mismatch, target
mismatch, or missing equivalent-control dossier forces `profit_visible = false`.

## Equivalent-Control Dossier

An equivalent-control dossier is required before the anchor whenever the
registration uses anything other than the standard FWER/Holm method.

The equivalent-control dossier must include:

- method name.
- why it controls FWER equivalently or better.
- application scope.
- required inputs.
- approval authority.
- validity criteria.
- fallback/null-ship conditions.

Missing or incomplete dossier material forces `profit_visible = false`.

## Registration Verification Contract

Focused verification for this contract consists of:

- `rtk uv run pytest -q tests/test_profit_visibility_registration.py`
- `rtk git diff --check`

The pytest stubs model only contract behavior: missing required registration
fields, result-bearing disclosure issues, strict byte-seal problems,
protocol-critical mutation, MTC mismatch, equivalent-control dossier absence,
and denominator shrinkage cannot support `profit_visible`.

## Non-Goals

This contract does not approve or implement:

- OTS proof creation.
- OTS verifier integration.
- Holm/FWER computation.
- Phase 164 evaluator integration.
- `ProfitVisibilityReport.v1` generation.
- full cost calculator behavior.
- runtime CLI wiring.
- live account fetcher.
- credential loader/storage.
- network/SDK/HTTP/socket/cloud secret/subprocess fetch.
- broker adapter/order/mutation paths.
- `side live`.
- runtime public live emission expansion.
- public schema expansion.
- protected archive, golden, seal, parity, or SHA fixture updates.
- tiny live trade.
