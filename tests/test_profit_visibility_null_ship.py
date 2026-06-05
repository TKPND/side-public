"""Null-ship and outcome guards for the v9.0 profit visibility checkpoint."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_CONTRACT_DOC = ROOT / "docs/contracts/profit_visibility_report_v1.md"
VALIDATOR = ROOT / "scripts" / "validate_profit_visibility_report.py"

STOP_PRECEDENCE = (
    "registration_anchor_invalid",
    "cost_incomplete",
    "sample_or_leakage_failed",
    "oos_wfd_or_holdout_failed",
    "mtc_failed",
    "cost_sensitivity_failed",
    "paper_forward_mapping_blocked",
)
COMPLETED_CHECKPOINT_GATES = (
    "registration_anchor",
    "cost",
    "sample",
    "leakage",
    "oos_wfd_or_holdout",
    "mtc",
    "cost_sensitivity",
)
DIVERGENCE_FIELDS = (
    "cost_basis",
    "notional_capacity",
    "turnover",
    "slippage",
    "sizing",
    "accounting",
)


def load_validator_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "validate_profit_visibility_report", VALIDATOR
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_report_contract_doc() -> str:
    return REPORT_CONTRACT_DOC.read_text(encoding="utf-8")


def assert_contains_all(text: str, fragments: tuple[str, ...]) -> None:
    missing = [fragment for fragment in fragments if fragment not in text]
    assert not missing, f"missing null-ship contract fragments: {missing}"


def gate_statuses(
    overrides: dict[str, dict[str, str]] | None = None,
) -> dict[str, dict[str, str]]:
    gates = {
        gate: {"status": "passed", "reason": "passed"}
        for gate in COMPLETED_CHECKPOINT_GATES
    }
    if overrides:
        gates.update(overrides)
    return gates


def build_row_fixture(
    family_id: str,
    hypothesis_id: str,
    *,
    gate_overrides: dict[str, dict[str, str]] | None = None,
    failures: list[str] | None = None,
    mtc_passed: bool | None = None,
    paper_forward_mapping_status: str = "blocked",
) -> dict[str, object]:
    observed_failures = list(failures or [])
    row: dict[str, object] = {
        "family_id": family_id,
        "hypothesis_id": hypothesis_id,
        "gate_statuses": gate_statuses(gate_overrides),
        "primary_stop_reason": None,
        "all_failures": observed_failures,
        "paper_forward_mapping_status": paper_forward_mapping_status,
    }
    if mtc_passed is not None:
        row["mtc_passed"] = mtc_passed
    return row


def build_family_fixture(kind: str, family_id: str) -> list[dict[str, object]]:
    if kind == "invalid_registration":
        return [
            build_row_fixture(
                family_id,
                f"{family_id}.h001",
                gate_overrides={
                    "registration_anchor": {
                        "status": "failed",
                        "reason": "registration_anchor_invalid",
                    },
                    "cost": {"status": "not_evaluated", "reason": "blocked"},
                    "sample": {"status": "not_evaluated", "reason": "blocked"},
                    "leakage": {"status": "not_evaluated", "reason": "blocked"},
                    "oos_wfd_or_holdout": {
                        "status": "not_evaluated",
                        "reason": "blocked",
                    },
                    "mtc": {"status": "not_evaluated", "reason": "blocked"},
                    "cost_sensitivity": {
                        "status": "not_evaluated",
                        "reason": "blocked",
                    },
                },
                failures=["registration_anchor_invalid"],
            )
        ]
    if kind == "incomplete_cost":
        return [
            build_row_fixture(
                family_id,
                f"{family_id}.h001",
                gate_overrides={
                    "cost": {"status": "not_evaluated", "reason": "cost_incomplete"},
                    "sample": {"status": "not_evaluated", "reason": "blocked"},
                    "leakage": {"status": "not_evaluated", "reason": "blocked"},
                    "oos_wfd_or_holdout": {
                        "status": "not_evaluated",
                        "reason": "blocked",
                    },
                    "mtc": {"status": "not_evaluated", "reason": "blocked"},
                    "cost_sensitivity": {
                        "status": "not_evaluated",
                        "reason": "blocked",
                    },
                },
                failures=["cost_incomplete"],
            )
        ]
    if kind == "leakage_fail":
        return [
            build_row_fixture(
                family_id,
                f"{family_id}.h001",
                gate_overrides={
                    "leakage": {
                        "status": "failed",
                        "reason": "sample_or_leakage_failed",
                    }
                },
                failures=["sample_or_leakage_failed"],
            )
        ]
    if kind == "oos_fail":
        return [
            build_row_fixture(
                family_id,
                f"{family_id}.h001",
                gate_overrides={
                    "oos_wfd_or_holdout": {
                        "status": "failed",
                        "reason": "oos_wfd_or_holdout_failed",
                    }
                },
                failures=["oos_wfd_or_holdout_failed"],
            )
        ]
    if kind == "mtc_fail":
        return [
            build_row_fixture(
                family_id,
                f"{family_id}.h001",
                gate_overrides={
                    "mtc": {"status": "failed", "reason": "mtc_failed"}
                },
                failures=["mtc_failed"],
            )
        ]
    if kind == "cost_sensitivity_fail":
        return [
            build_row_fixture(
                family_id,
                f"{family_id}.h001",
                gate_overrides={
                    "cost_sensitivity": {
                        "status": "failed",
                        "reason": "cost_sensitivity_failed",
                    }
                },
                failures=["cost_sensitivity_failed"],
            )
        ]
    if kind == "zero_survivor_complete":
        return [
            build_row_fixture(
                family_id,
                f"{family_id}.h001",
                failures=["paper_forward_mapping_blocked"],
            ),
            build_row_fixture(
                family_id,
                f"{family_id}.h002",
                failures=["paper_forward_mapping_blocked"],
            ),
        ]
    if kind == "one_survivor_complete":
        return [
            build_row_fixture(
                family_id,
                f"{family_id}.h001",
                mtc_passed=True,
                paper_forward_mapping_status="eligible_for_mapping",
            ),
            build_row_fixture(
                family_id,
                f"{family_id}.h002",
                failures=["mtc_failed"],
            ),
        ]
    if kind == "incomplete_plumbing":
        return [
            build_row_fixture(
                family_id,
                f"{family_id}.h001",
                gate_overrides={
                    "sample": {"status": "not_evaluated", "reason": "missing_sample"},
                    "leakage": {
                        "status": "not_evaluated",
                        "reason": "missing_leakage",
                    },
                    "oos_wfd_or_holdout": {
                        "status": "not_evaluated",
                        "reason": "blocked",
                    },
                    "mtc": {"status": "not_evaluated", "reason": "blocked"},
                    "cost_sensitivity": {
                        "status": "not_evaluated",
                        "reason": "blocked",
                    },
                },
                failures=[],
            )
        ]
    raise ValueError(f"unsupported fixture kind: {kind}")


def build_paper_forward_assumptions() -> dict[str, str]:
    return {
        "cost_basis": "phase163-realistic-base-plus-adverse-costs",
        "notional_capacity": "phase163-notional-capacity-bound",
        "turnover": "phase163-turnover-assumption",
        "slippage": "phase163-slippage-ladder",
        "sizing": "risk-contract-v2-sizing-policy",
        "accounting": "runtime-net-pnl-contract",
    }


def test_report_contract_pins_null_ship_sections() -> None:
    text = read_report_contract_doc()

    assert_contains_all(
        text,
        (
            "## Stop Reason Precedence",
            "## Honest Null-Ship Conditions",
            "## Family And Overall Outcomes",
            "invalid registration/anchor is not honest null-ship",
            "family_outcome",
            "overall_outcome",
            *STOP_PRECEDENCE,
        ),
    )


def test_primary_stop_reason_uses_ordered_fail_closed_precedence() -> None:
    validator = load_validator_module()
    failures = [
        "paper_forward_mapping_blocked",
        "mtc_failed",
        "cost_incomplete",
        "sample_or_leakage_failed",
    ]

    assert validator.choose_primary_stop_reason(failures) == "cost_incomplete"


def test_invalid_registration_is_not_honest_null_ship() -> None:
    validator = load_validator_module()

    family = validator.derive_family_outcome(
        build_family_fixture("invalid_registration", "family.invalid")
    )

    assert family["family_outcome"] == "invalid_disqualified"
    assert family["family_outcome"] != "honest_null_ship"
    assert family["primary_stop_reason"] == "registration_anchor_invalid"
    assert family["survivor_count"] == 0


def test_anchor_validator_failure_reasons_route_to_invalid_disqualified() -> None:
    validator = load_validator_module()
    anchor_result = validator.validate_registration_anchor(
        {
            "registered_bytes_sha256": "sha256:" + ("a" * 64),
            "current_bytes_sha256": "sha256:" + ("b" * 64),
            "ots_proof_path": "registrations/phase163-candidates.ots",
            "anchor_kind": "OpenTimestamps",
            "external_anchor_verified": True,
            "anchor_stale": False,
            "anchor_author_controlled": False,
            "anchor_force_pushable": False,
        }
    )
    assert anchor_result["status"] == "invalid_disqualified"
    assert anchor_result["reason"] == "registered_bytes_mismatch"

    row = build_row_fixture(
        "family.validator_invalid",
        "family.validator_invalid.h001",
        gate_overrides={
            "registration_anchor": {
                "status": "failed",
                "reason": anchor_result["reason"],
            },
            "cost": {"status": "not_evaluated", "reason": "blocked"},
            "sample": {"status": "not_evaluated", "reason": "blocked"},
            "leakage": {"status": "not_evaluated", "reason": "blocked"},
            "oos_wfd_or_holdout": {"status": "not_evaluated", "reason": "blocked"},
            "mtc": {"status": "not_evaluated", "reason": "blocked"},
            "cost_sensitivity": {"status": "not_evaluated", "reason": "blocked"},
        },
        failures=[anchor_result["reason"]],
    )

    family = validator.derive_family_outcome([row])

    assert family["family_outcome"] == "invalid_disqualified"
    assert family["primary_stop_reason"] == "registration_anchor_invalid"
    assert family["all_failures"] == [
        "registered_bytes_mismatch",
        "registration_anchor_invalid",
    ]


def test_completed_zero_survivor_checkpoint_is_honest_null_ship() -> None:
    validator = load_validator_module()

    family = validator.derive_family_outcome(
        build_family_fixture("zero_survivor_complete", "family.zero")
    )

    assert family["family_outcome"] == "honest_null_ship"
    assert family["checkpoint_complete"] is True
    assert family["survivor_count"] == 0
    assert family["candidate_count"] == 2


def test_profit_visible_requires_passed_registration_and_mtc() -> None:
    validator = load_validator_module()
    rows = build_family_fixture("one_survivor_complete", "family.profit_gate")

    assert validator.derive_family_outcome(rows)["family_outcome"] == "profit_visible"

    missing_mtc_rows = build_family_fixture("one_survivor_complete", "family.missing_mtc")
    missing_mtc_rows[0].pop("mtc_passed")
    missing_mtc = validator.derive_family_outcome(missing_mtc_rows)
    assert missing_mtc["family_outcome"] != "profit_visible"
    assert missing_mtc["survivor_count"] == 0

    failed_anchor_rows = build_family_fixture("one_survivor_complete", "family.failed_anchor")
    failed_anchor_rows[0]["gate_statuses"]["registration_anchor"] = {
        "status": "failed",
        "reason": "anchor_not_verified",
    }
    failed_anchor = validator.derive_family_outcome(failed_anchor_rows)
    assert failed_anchor["family_outcome"] == "invalid_disqualified"
    assert failed_anchor["primary_stop_reason"] == "registration_anchor_invalid"


def test_synthetic_only_family_and_overall_never_profit_visible() -> None:
    validator = load_validator_module()
    synthetic_rows = [
        {
            **build_row_fixture(
                "family.synthetic",
                "family.synthetic.h001",
                mtc_passed=True,
                paper_forward_mapping_status="eligible_for_mapping",
            ),
            "data_provenance": "synthetic_fixture_not_market",
            "source_refs": ["fixtures/profit_visibility/synthetic-fixture.json"],
        },
        {
            **build_row_fixture(
                "family.synthetic",
                "family.synthetic.h002",
                mtc_passed=True,
                paper_forward_mapping_status="eligible_for_mapping",
            ),
            "data_provenance": "synthetic_known_edge_not_market",
            "source_refs": ["fixtures/profit_visibility/synthetic-known-edge.json"],
        },
    ]

    family = validator.derive_family_outcome(synthetic_rows)
    overall = validator.derive_overall_outcome([family])

    assert family["family_outcome"] == "synthetic_edge_non_claimable"
    assert family["family_outcome"] != "profit_visible"
    assert family["family_outcome"] != "honest_null_ship"
    assert family["survivor_count"] == 0
    assert family["claim_boundary_statuses"] == [
        "synthetic_fixture_non_claimable",
        "synthetic_edge_non_claimable",
    ]
    assert family["profit_visible"] is False
    assert overall["overall_outcome"] == "synthetic_edge_non_claimable"
    assert overall["overall_outcome"] != "profit_visible"
    assert overall["profit_visible"] is False
    assert overall["claim_boundary_statuses"] == [
        "synthetic_fixture_non_claimable",
        "synthetic_edge_non_claimable",
    ]


def test_incomplete_checkpoint_routes_to_plumbing_only() -> None:
    validator = load_validator_module()

    family = validator.derive_family_outcome(
        build_family_fixture("incomplete_plumbing", "family.incomplete")
    )

    assert family["family_outcome"] == "plumbing_only"
    assert family["family_outcome"] != "honest_null_ship"
    assert family["checkpoint_complete"] is False
    assert family["survivor_count"] == 0


def test_family_and_overall_outcomes_are_reported() -> None:
    validator = load_validator_module()

    profit = validator.derive_family_outcome(
        build_family_fixture("one_survivor_complete", "family.profit")
    )
    honest = validator.derive_family_outcome(
        build_family_fixture("zero_survivor_complete", "family.zero")
    )
    plumbing = validator.derive_family_outcome(
        build_family_fixture("incomplete_cost", "family.plumbing")
    )
    invalid = validator.derive_family_outcome(
        build_family_fixture("invalid_registration", "family.invalid")
    )

    assert profit["family_outcome"] == "profit_visible"
    assert honest["family_outcome"] == "honest_null_ship"
    assert plumbing["family_outcome"] == "plumbing_only"
    assert invalid["family_outcome"] == "invalid_disqualified"

    report = validator.derive_overall_outcome([profit, honest, plumbing, invalid])
    assert report["overall_outcome"] == "invalid_disqualified"
    assert [family["family_outcome"] for family in report["families"]] == [
        "profit_visible",
        "honest_null_ship",
        "plumbing_only",
        "invalid_disqualified",
    ]

    assert (
        validator.derive_overall_outcome([profit, honest, plumbing])[
            "overall_outcome"
        ]
        == "profit_visible"
    )
    assert (
        validator.derive_overall_outcome([honest, plumbing])["overall_outcome"]
        == "plumbing_only"
    )
    assert (
        validator.derive_overall_outcome([honest])["overall_outcome"]
        == "honest_null_ship"
    )


def test_all_failures_are_preserved_when_primary_reason_is_selected() -> None:
    validator = load_validator_module()
    failures = [
        "cost_sensitivity_failed",
        "mtc_failed",
        "cost_incomplete",
        "paper_forward_mapping_blocked",
    ]
    row = build_row_fixture(
        "family.failures",
        "family.failures.h001",
        gate_overrides={
            "cost": {"status": "not_evaluated", "reason": "cost_incomplete"},
            "mtc": {"status": "failed", "reason": "mtc_failed"},
            "cost_sensitivity": {
                "status": "failed",
                "reason": "cost_sensitivity_failed",
            },
        },
        failures=failures,
    )

    family = validator.derive_family_outcome([row])

    assert family["primary_stop_reason"] == "cost_incomplete"
    assert family["all_failures"] == failures


def test_economics_divergence_blocks_paper_forward_mapping() -> None:
    validator = load_validator_module()
    backtest = build_paper_forward_assumptions()

    for field in DIVERGENCE_FIELDS:
        paper_forward = dict(backtest)
        paper_forward[field] = f"paper-forward-different-{field}"

        result = validator.detect_paper_forward_divergence(backtest, paper_forward)

        assert result["status"] == "paper_forward_mapping_blocked", field
        assert result["paper_forward_mapping_status"] == "blocked"
        assert f"{field}_divergence" in result["divergence_reasons"]

    missing_result = validator.detect_paper_forward_divergence({}, {})
    assert missing_result["status"] == "paper_forward_mapping_blocked"
    assert missing_result["divergence_reasons"] == [
        f"{field}_divergence" for field in DIVERGENCE_FIELDS
    ]


def test_mapping_block_preserves_typed_divergence_reasons() -> None:
    validator = load_validator_module()
    backtest = build_paper_forward_assumptions()
    paper_forward = {
        field: f"paper-forward-different-{field}" for field in DIVERGENCE_FIELDS
    }

    result = validator.detect_paper_forward_divergence(backtest, paper_forward)

    assert result["status"] == "paper_forward_mapping_blocked"
    assert result["divergence_reasons"] == [
        f"{field}_divergence" for field in DIVERGENCE_FIELDS
    ]
    assert all(
        isinstance(reason, str) and reason
        for reason in result["divergence_reasons"]
    )
