"""Contract guards for the Phase 163 profit visibility cost model."""

from __future__ import annotations

import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COST_CONTRACT_DOC = ROOT / "docs/contracts/profit_visibility_cost_model_v1.md"

REQUIRED_BASE_COST_FIELDS = (
    "fees",
    "spread",
    "slippage",
    "turnover",
)
APPLICABLE_COST_FIELDS = (
    "financing",
    "borrow",
    "conversion",
    "market_access_assumptions",
)
REJECTED_COST_VALUES = (None, "", 0, 0.0, "0", "0.0", "missing", "unknown", "TBD")
REJECTED_COST_STRINGS = {
    "",
    "0",
    "0.0",
    "blank",
    "missing",
    "placeholder",
    "tba",
    "tbd",
    "unknown",
}
ADVERSE_LADDER_FIELDS = (
    "adverse_fees",
    "adverse_spread",
    "adverse_slippage",
    "adverse_turnover",
)
CAPACITY_BOUND_FIELDS = (
    "notional",
    "capacity",
    "leverage",
    "max-loss",
)
FORBIDDEN_READINESS_CLAIMS = (
    "paper-forward readiness",
    "live readiness",
    "account readiness",
    "broker readiness",
    "network readiness",
    "credential readiness",
    "runtime readiness",
)
REQUIRED_CONTRACT_FRAGMENTS = (
    "fees",
    "spread",
    "slippage",
    "turnover",
    "financing",
    "borrow",
    "conversion",
    "market-access assumptions",
    "base cost scenario",
    "adverse cost ladder",
    "cost_model_fingerprint",
    "notional",
    "capacity",
    "leverage",
    "max-loss",
    "measurement constraints only",
    "profit_visible = false",
    "unknown",
    "TBD",
)
VALID_COST_MODEL_FINGERPRINT = "sha256:" + ("a" * 64)


def read_cost_contract_doc() -> str:
    return COST_CONTRACT_DOC.read_text(encoding="utf-8")


def assert_contains_all(text: str, fragments: tuple[str, ...]) -> None:
    missing = [fragment for fragment in fragments if fragment not in text]
    assert not missing, f"missing cost contract fragments: {missing}"


def complete_cost_model_fixture(
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    cost_model: dict[str, object] = {
        "fees": 1.5,
        "spread": 0.5,
        "slippage": 2.0,
        "turnover": 1.1,
        "financing": 0.25,
        "borrow": 0.25,
        "conversion": 0.10,
        "market_access_assumptions": 0.05,
        "cost_model_fingerprint": VALID_COST_MODEL_FINGERPRINT,
    }
    if overrides:
        cost_model.update(overrides)
    return cost_model


def _has_explicit_nonzero_effective_cost_rationale(cost_model: dict[str, object]) -> bool:
    rationale = cost_model.get("explicit_nonzero_effective_cost_rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        return False
    nonzero_fields = _nonzero_cost_fields(cost_model)
    return bool(nonzero_fields) and any(field in rationale for field in nonzero_fields)


def _is_rejected_cost_value(value: object) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return not math.isfinite(value) or value <= 0
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in REJECTED_COST_STRINGS:
            return True
        try:
            numeric = float(stripped)
        except ValueError:
            return False
        return not math.isfinite(numeric) or numeric <= 0
    return value in REJECTED_COST_VALUES


def _nonzero_cost_fields(cost_model: dict[str, object]) -> set[str]:
    return {
        field
        for field in REQUIRED_BASE_COST_FIELDS + APPLICABLE_COST_FIELDS
        if field in cost_model and not _is_rejected_cost_value(cost_model[field])
    }


def _has_valid_cost_model_fingerprint(cost_model: dict[str, object]) -> bool:
    fingerprint = cost_model.get("cost_model_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint.startswith("sha256:"):
        return False
    digest = fingerprint.removeprefix("sha256:")
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def cost_model_stub_status(cost_model: dict[str, object]) -> str:
    if not _has_valid_cost_model_fingerprint(cost_model):
        return "cost_incomplete"
    required_fields = REQUIRED_BASE_COST_FIELDS + APPLICABLE_COST_FIELDS
    rejected_fields = [
        field
        for field in required_fields
        if field not in cost_model or _is_rejected_cost_value(cost_model[field])
    ]
    if rejected_fields and not _has_explicit_nonzero_effective_cost_rationale(
        cost_model
    ):
        return "cost_incomplete"
    return "cost_complete"


def _adverse_row_incomplete(row: dict[str, object]) -> bool:
    return any(
        field not in row or _is_rejected_cost_value(row[field])
        for field in ADVERSE_LADDER_FIELDS
    )


def adverse_ladder_stub_status(
    base_status: str, adverse_rows: tuple[dict[str, object], ...]
) -> str:
    if base_status != "cost_complete":
        return "cost_incomplete"
    if not adverse_rows:
        return "profit_visible_false"
    for row in adverse_rows:
        if _adverse_row_incomplete(row):
            return "profit_visible_false"
        if not row.get("survives_cost_gate", False):
            if row.get("route") == "honest_null_ship":
                return "honest_null_ship"
            return "profit_visible_false"
    return "cost_complete"


def capacity_measurement_stub_status(capacity_bounds: dict[str, object]) -> str:
    for field in CAPACITY_BOUND_FIELDS:
        if field not in capacity_bounds or _is_rejected_cost_value(
            capacity_bounds[field]
        ):
            return "measurement_incomplete"
    return "measurement_ready"


def test_cost_model_stub_requires_valid_fingerprint() -> None:
    assert cost_model_stub_status(complete_cost_model_fixture()) == "cost_complete"
    for fingerprint in (None, "", "abc123", "md5:" + ("a" * 32), "sha256:short"):
        assert (
            cost_model_stub_status(
                complete_cost_model_fixture({"cost_model_fingerprint": fingerprint})
            )
            == "cost_incomplete"
        ), fingerprint


def test_generic_nonzero_rationale_does_not_bypass_zero_costs() -> None:
    all_zero_with_generic_rationale = {
        field: 0.0 for field in REQUIRED_BASE_COST_FIELDS + APPLICABLE_COST_FIELDS
    }
    all_zero_with_generic_rationale[
        "explicit_nonzero_effective_cost_rationale"
    ] = "the strategy should be cheap enough"
    all_zero_with_generic_rationale[
        "cost_model_fingerprint"
    ] = VALID_COST_MODEL_FINGERPRINT

    assert cost_model_stub_status(all_zero_with_generic_rationale) == (
        "cost_incomplete"
    )
    assert (
        cost_model_stub_status(
            complete_cost_model_fixture(
                {
                    "fees": 0.0,
                    "spread": 0.0,
                    "explicit_nonzero_effective_cost_rationale": (
                        "effective cost is represented by nonzero slippage"
                    ),
                }
            )
        )
        == "cost_complete"
    )


def test_adverse_ladder_requires_adverse_cost_fields() -> None:
    assert (
        adverse_ladder_stub_status(
            "cost_complete", ({"name": "spread shock", "survives_cost_gate": True},)
        )
        == "profit_visible_false"
    )
    assert (
        adverse_ladder_stub_status(
            "cost_complete",
            (
                {
                    "name": "spread shock",
                    "adverse_fees": 3.5,
                    "adverse_spread": 1.0,
                    "adverse_slippage": 4.0,
                    "adverse_turnover": 1.65,
                    "survives_cost_gate": True,
                },
            ),
        )
        == "cost_complete"
    )


def test_capacity_bounds_missing_or_placeholder_block_measurement() -> None:
    assert "capacity_measurement_stub_status" in globals()
    complete_bounds = {
        "notional": "max 10,000 USD equivalent per candidate",
        "capacity": "max 2 percent of median dollar volume",
        "leverage": "1.0x gross leverage",
        "max-loss": "max-loss 2 percent of allocated notional",
    }

    assert capacity_measurement_stub_status(complete_bounds) == "measurement_ready"
    for field in CAPACITY_BOUND_FIELDS:
        incomplete = dict(complete_bounds)
        incomplete[field] = "unknown"
        assert (
            capacity_measurement_stub_status(incomplete)
            == "measurement_incomplete"
        ), field


def test_realistic_cost_model_requires_non_placeholder_costs() -> None:
    text = read_cost_contract_doc()

    assert_contains_all(text, REQUIRED_BASE_COST_FIELDS)
    assert_contains_all(text, APPLICABLE_COST_FIELDS)
    assert_contains_all(text, REQUIRED_CONTRACT_FRAGMENTS)
    assert (
        cost_model_stub_status(
            complete_cost_model_fixture()
        )
        == "cost_complete"
    )
    assert cost_model_stub_status({"fees": 1.5}) == "cost_incomplete"


def test_zero_missing_unknown_or_tbd_costs_block_profit_visible() -> None:
    text = read_cost_contract_doc()

    assert "missing, zero, unknown, or TBD costs cannot support `profit_visible`" in text
    for rejected in (
        *REJECTED_COST_VALUES,
        "blank",
        "placeholder",
        -1.0,
        float("nan"),
    ):
        assert (
            cost_model_stub_status(
                {
                    "fees": rejected,
                    "spread": 0.5,
                    "slippage": 2.0,
                    "turnover": 1.1,
                    "financing": 0.25,
                    "borrow": 0.25,
                    "conversion": 0.10,
                    "market_access_assumptions": 0.05,
                    "cost_model_fingerprint": VALID_COST_MODEL_FINGERPRINT,
                }
            )
            == "cost_incomplete"
        )


def test_capacity_bounds_reject_blank_negative_and_nan_values() -> None:
    complete_bounds = {
        "notional": 10_000.0,
        "capacity": 0.02,
        "leverage": 1.0,
        "max-loss": 0.02,
    }

    assert capacity_measurement_stub_status(complete_bounds) == "measurement_ready"
    for rejected in ("blank", "placeholder", -1.0, math.nan):
        for field in CAPACITY_BOUND_FIELDS:
            incomplete = dict(complete_bounds)
            incomplete[field] = rejected
            assert (
                capacity_measurement_stub_status(incomplete)
                == "measurement_incomplete"
            ), (field, rejected)


def test_nonzero_effective_cost_rationale_can_satisfy_cost_presence_only() -> None:
    text = read_cost_contract_doc()

    assert "explicit nonzero effective-cost rationale" in text
    assert "satisfies cost presence only" in text
    assert "does not by itself approve `profit_visible`" in text
    assert (
        cost_model_stub_status(
            complete_cost_model_fixture(
                {
                    "fees": 0.0,
                    "spread": 0.0,
                    "slippage": 1.0,
                    "turnover": 0.8,
                    "explicit_nonzero_effective_cost_rationale": (
                        "effective cost is represented by nonzero slippage and turnover"
                    ),
                }
            )
        )
        == "cost_complete"
    )


def test_adverse_cost_ladder_can_force_null_ship() -> None:
    text = read_cost_contract_doc()

    assert_contains_all(text, ADVERSE_LADDER_FIELDS)
    assert "base cost scenario" in text
    assert "adverse cost ladder" in text
    assert "profit_visible = false" in text
    assert (
        adverse_ladder_stub_status(
            "cost_complete", ({"name": "spread shock", "survives_cost_gate": False},)
        )
        == "profit_visible_false"
    )
    assert (
        adverse_ladder_stub_status(
            "cost_complete",
            (
                {
                    "name": "turnover shock",
                    "adverse_fees": 3.5,
                    "adverse_spread": 1.0,
                    "adverse_slippage": 4.0,
                    "adverse_turnover": 1.65,
                    "survives_cost_gate": False,
                    "route": "honest_null_ship",
                },
            ),
        )
        == "honest_null_ship"
    )


def test_capacity_bounds_are_measurement_constraints_not_readiness_claims() -> None:
    text = read_cost_contract_doc()

    assert_contains_all(text, CAPACITY_BOUND_FIELDS)
    assert "measurement constraints only" in text
    for readiness_claim in FORBIDDEN_READINESS_CLAIMS:
        assert f"not {readiness_claim}" in text


def test_cost_model_fingerprint_is_required_for_claims() -> None:
    text = read_cost_contract_doc()

    assert "cost_model_fingerprint" in text
    assert "sha256:" in text
    assert "required before any cost-supported claim" in text
    assert "ProfitVisibilityReport.v1" in text
    assert "actual economic metric computation belongs to Phase 164" in text
