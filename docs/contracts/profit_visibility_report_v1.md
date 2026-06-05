# Profit Visibility Report v1

## Boundary

`ProfitVisibilityReport.v1` is a repo-supported evidence helper contract for
the v9.0 Profit Visibility Checkpoint. It defines report rows, metric fields,
survivor counts, family summaries, and fail-closed report validation behavior.

This contract is not a public JSON schema and does not approve live runtime
behavior, account fetching, broker paths, credential or network handling,
public schema expansion, protected archive changes, golden files, seal files,
parity fixtures, SHA fixture updates, package installation, or dependency
changes.

## Canonical Hypothesis Rows

The report's canonical hypothesis rows are the decision surface. The report
contains one row for every sealed finest-granularity hypothesis. Family summary
output may exist, but it is derived only and cannot replace the canonical
hypothesis rows.

Required row identity and context fields:

- `family_id`
- `hypothesis_id`
- `sealed_hypothesis_index`
- `signal_family`
- `universe`
- `timeframe`
- `parameter_set`
- `filter_set`
- `split_protocol`
- `cost_model_fingerprint`

## Required Candidate Row Fields

Every candidate row includes the required economic metrics, gate status fields,
stop-reason fields, and paper-forward mapping status.

Metric fields:

- `net_profit_factor`
- `net_expectancy`
- `max_drawdown`
- `turnover`
- `trade_count`
- `capacity_notional_bound`
- `slippage_sensitivity`

Gate fields under `gate_statuses`:

- `registration_anchor`
- `cost`
- `sample`
- `leakage`
- `oos_wfd_or_holdout`
- `mtc`
- `cost_sensitivity`
- `paper_forward_mapping`

Decision and mapping fields:

- `gate_statuses`
- `primary_stop_reason`
- `all_failures`
- `paper_forward_mapping_status`

## Typed Null Metrics

Failed or ineligible rows use typed null values with explicit unavailable
reasons. The report must not use `0`, empty strings, `unknown`, `TBD`,
placeholders, omitted fields, or inferred defaults to represent uncomputed or
invalid metrics.

When a metric is null, the corresponding reason field is required:

- `net_profit_factor_unavailable_reason`
- `net_expectancy_unavailable_reason`
- `max_drawdown_unavailable_reason`
- `turnover_unavailable_reason`
- `trade_count_unavailable_reason`
- `capacity_notional_bound_unavailable_reason`
- `slippage_sensitivity_unavailable_reason`

## Survivor Counts

Survivor counts are explicit after cost-adjusted train/OOS, WFD, or holdout
gates and after the report's later multiple-testing and cost-sensitivity gates.
Zero-survivor output is a first-class report state. Families with zero survivors
remain present with `survivor_count = 0`.

## Family Summary

Family summary output is optional and derived only. If emitted, each family
summary includes `derived_from_all_hypothesis_rows = true`, `candidate_count`,
and `survivor_count`.

Family summaries are not the canonical decision surface and cannot be used to
shrink the multiple-testing denominator or hide failed hypothesis rows.

## Registration Anchor Gate

The `registration_anchor` gate passes only when the Phase 163 registration
artifact is byte-identical to the sealed artifact and externally anchored before
evaluation. The report helper validates anchor metadata before any economic
claim can become visible.

Required anchor evidence includes:

- `registered_bytes_sha256`
- `current_bytes_sha256`
- an `.ots` proof path
- `anchor_kind`
- verified external anchor evidence
- non-stale anchor evidence
- non-author-controlled anchor evidence
- non-force-pushable anchor evidence

Missing `.ots` proof metadata, byte mismatch, missing verification, stale
anchor evidence, author-controlled timestamps, or force-pushable refs fail
closed with `invalid_disqualified` and cannot support `profit_visible`.

## FWER/Holm Multiple-Testing Control

The initial claimable multiple-testing control method is exactly
`method = FWER/Holm`, `error_rate_target = FWER`, and `alpha = 0.05`.
Alternative methods, alternative targets, alpha drift, or unregistered
equivalent-control claims force `profit_visible_false`.

Each canonical row with a p-value receives deterministic Holm output in original
row order:

- `p_raw`
- `p_holm_adjusted`
- `holm_rank`
- `mtc_passed`
- `mtc_reason`
- `sealed_denominator`
- `mtc_method`
- `error_rate_target`
- `alpha`
- `mtc_input_count`

Family MTC summaries are derived from the canonical rows and expose
`candidate_count`, `mtc_passed_count`, `mtc_failed_count`, `sealed_denominator`,
`method`, `error_rate_target`, `alpha`, and `input_count`.

## P-Value Provenance

Every `p_raw` input requires typed `p_value_provenance` binding the value to:

- `sealed_protocol_ref`
- `hypothesis_id`
- `supported_evaluation_run_ref`

The provenance `hypothesis_id` must match the canonical row. When a row exposes
`registration_anchor_ref`, the provenance `sealed_protocol_ref` must match that
anchor reference. Missing provenance, non-finite p-values, p-values outside
`[0, 1]`, or unregistered provenance fail closed with `invalid_disqualified`.

## Exact Denominator Fail-Closed Rules

The sealed denominator is the exact count of finest-granularity hypotheses and
must match canonical rows and p-value inputs exactly. Missing rows, extra rows,
duplicate hypothesis IDs, sealed-index drift, survivor-only input, denominator
shrinkage, and input-count mismatch force `profit_visible_false`.

p=1 padding is forbidden. Phase 164 never silently pads missing hypotheses with
synthetic `p_raw = 1.0` rows to make Holm/FWER correction appear denominator
complete.

## Stop Reason Precedence

Every row and family preserves all observed failures in `all_failures`. When a
single `primary_stop_reason` is needed, it uses this ordered fail-closed
precedence:

1. `registration_anchor_invalid`
2. `cost_incomplete`
3. `sample_or_leakage_failed`
4. `oos_wfd_or_holdout_failed`
5. `mtc_failed`
6. `cost_sensitivity_failed`
7. `paper_forward_mapping_blocked`

The primary stop reason is only a routing field. It must not remove lower
precedence failures from `all_failures`.

## Honest Null-Ship Conditions

`honest_null_ship` requires a completed checkpoint with a valid
registration/anchor, complete cost model, evaluated sample and leakage gates,
evaluated OOS/WFD or holdout gate, applied FWER/Holm MTC, evaluated cost
sensitivity, and zero survivors.

invalid registration/anchor is not honest null-ship. Invalid or mismatched
registration anchor evidence routes to `invalid_disqualified`. Missing,
not-evaluated, or incomplete checkpoint material routes to `plumbing_only`, not
`honest_null_ship`.

## Family And Overall Outcomes

Each family summary carries `family_outcome`, `candidate_count`,
`survivor_count`, `checkpoint_complete`, `primary_stop_reason`, and
`all_failures`.

Allowed `family_outcome` values are:

- `profit_visible`
- `honest_null_ship`
- `invalid_disqualified`
- `plumbing_only`

The report carries `overall_outcome` for Phase 165 handoff. Overall outcome
derivation preserves family outcomes and keeps invalid evidence visible:
`invalid_disqualified` if any family is invalid, otherwise `profit_visible` if
any completed family has survivors, otherwise `plumbing_only` if any family is
incomplete, otherwise `honest_null_ship` when all completed families have zero
survivors.

## Paper-Forward Prerequisite Mapping

Survivors emit prerequisite mapping only. A survivor may be labeled exactly
`eligible for paper-forward prerequisite review` and may point to existing
prerequisite references for risk gate, sizing, accounting, and paper-forward
rehearsal review.

The mapping is not readiness; no dedicated paper-forward handoff artifact is
created. It creates no workflow trigger and no runtime behavior. It does not
execute a paper-forward workflow and does not create a live-shadow candidate.

Allowed prerequisite reference categories:

- `risk_gate`
- `sizing`
- `accounting`
- `paper_forward_rehearsal`

## Economics Divergence Stop

Backtest/WFD economics and paper-forward assumptions must match before mapping.
Any mismatch blocks mapping with `paper_forward_mapping_blocked` and preserves a
typed divergence reason.

Typed divergence reasons include:

- `cost_basis_divergence`
- `notional_capacity_divergence`
- `turnover_divergence`
- `slippage_divergence`
- `sizing_divergence`
- `accounting_divergence`

## Claim Wording Guard

The only allowed positive mapping wording is exactly
`eligible for paper-forward prerequisite review`.

Forbidden wording includes:

- `paper_forward_ready`
- `paper-forward ready`
- `live-shadow candidate`
- `live ready`
- `live readiness`

These phrases are rejected because prerequisite review is not paper-forward
readiness, live-shadow readiness, live readiness, broker readiness, account
readiness, network readiness, credential readiness, or runtime readiness.

## Absence And Protected Surface Guard

Paper-forward prerequisite mapping uses existing docs, contracts, reports, and
tests as source of truth. It adds no new public schema, no runtime contract, no
live-preflight schema reuse, no live account fetching, no broker path, no
credential or network path, no protected archive update, no golden file update,
no seal or parity fixture update, no SHA fixture update, no package change, and
no dependency change.

## Verification Contract

Focused verification for the initial report contract consists of:

- `rtk uv run pytest -q tests/test_profit_visibility_report.py`
- `rtk uv run python scripts/validate_profit_visibility_report.py --help`
- `rtk git diff --check`

## Non-Goals

This contract does not implement or approve:

- live account fetching.
- credential loading or storage.
- network, HTTP, socket, SDK, broker, or subprocess fetch paths.
- broker adapter, broker SDK, broker order, cancellation, or mutation paths.
- `side live` runtime wiring.
- runtime public live emission expansion.
- public schema expansion.
- protected archive, golden, seal, parity, or SHA fixture updates.
- paper-forward workflow execution.
- tiny live trades.
