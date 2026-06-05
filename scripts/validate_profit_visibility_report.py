"""Validate local ProfitVisibilityReport.v1 evidence helper payloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


SUPPORTED_MTC_METHOD = "FWER/Holm"
SUPPORTED_ERROR_RATE_TARGET = "FWER"
SUPPORTED_ALPHA = 0.05
ALLOWED_PAPER_FORWARD_WORDING = "eligible for paper-forward prerequisite review"
ACCEPTED_ANCHOR_KINDS = {"opentimestamps"}
SUPPORTED_SYNTHETIC_DATA_PROVENANCE = {
    "synthetic_fixture_not_market",
    "synthetic_known_noise_not_market",
    "synthetic_known_edge_not_market",
}
SYNTHETIC_FIXTURE_NON_CLAIMABLE = "synthetic_fixture_non_claimable"
SYNTHETIC_EDGE_NON_CLAIMABLE = "synthetic_edge_non_claimable"
P_VALUE_PADDING_SOURCES = {
    "padding",
    "p_value_padding",
    "p=1 padding",
    "p1_padding",
}
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
PAPER_FORWARD_PREREQUISITE_REFS = (
    {
        "category": "risk_gate",
        "path": "tests/test_risk_engine_policy_evaluation.py",
    },
    {
        "category": "sizing",
        "path": "reports/v6.5/paper_cost_model_audit.md",
    },
    {
        "category": "accounting",
        "path": "reports/v6.6/runtime_net_pnl_contract_audit.md",
    },
    {
        "category": "paper_forward_rehearsal",
        "path": "tests/test_generate_paper_v2_runtime_adoption_evidence.py",
    },
)
FORBIDDEN_CLAIM_WORDING = (
    "paper_forward_ready",
    "paper-forward ready",
    "paper forward ready",
    "market evidence",
    "market proof",
    "profit ready",
    "profit readiness",
    "profit claimable",
    "paper-forward readiness",
    "paper forward readiness",
    "live-shadow candidate",
    "live-shadow ready",
    "live-shadow readiness",
    "live shadow candidate",
    "live shadow ready",
    "live shadow readiness",
    "live ready",
    "live readiness",
    "account ready",
    "account readiness",
    "broker ready",
    "broker readiness",
    "network ready",
    "network readiness",
    "credential ready",
    "credential readiness",
    "runtime ready",
    "runtime readiness",
)
NEGATED_CLAIM_WORDING_PATTERNS = (
    r"\b(?:not|never|no)\s+{phrase}",
    r"\bdoes\s+not\s+(?:grant|approve)(?:\s+[A-Za-z0-9_][\w-]*){{0,1}}\s+{phrase}",
    r"\bmust\s+not\s+treat(?:\s+[A-Za-z0-9_][\w-]*){{0,1}}\s+as\s+{phrase}",
)
REQUIRED_GATE_FIELDS = (
    "registration_anchor",
    "cost",
    "sample",
    "leakage",
    "oos_wfd_or_holdout",
    "mtc",
    "cost_sensitivity",
    "paper_forward_mapping",
)
REQUIRED_METRIC_FIELDS = (
    "net_profit_factor",
    "net_expectancy",
    "max_drawdown",
    "turnover",
    "trade_count",
    "capacity_notional_bound",
    "slippage_sensitivity",
)
REQUIRED_ROW_FIELDS = (
    "family_id",
    "hypothesis_id",
    "sealed_hypothesis_index",
    "signal_family",
    "universe",
    "timeframe",
    "parameter_set",
    "filter_set",
    "split_protocol",
    "cost_model_fingerprint",
    *REQUIRED_METRIC_FIELDS,
    "gate_statuses",
    "primary_stop_reason",
    "all_failures",
    "paper_forward_mapping_status",
)
TYPED_NULL_REASON_FIELDS = tuple(
    f"{field}_unavailable_reason" for field in REQUIRED_METRIC_FIELDS
)


def _fail_closed(status: str, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "status": status,
        "profit_visible": False,
        "reason": reason,
        **details,
    }


def _nonblank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalized_sha256(value: Any) -> str | None:
    if isinstance(value, bytes):
        return "sha256:" + hashlib.sha256(value).hexdigest()
    if not isinstance(value, str):
        return None
    stripped = value.strip().lower()
    if stripped.startswith("sha256:"):
        digest = stripped.removeprefix("sha256:")
    else:
        digest = stripped
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return None
    return "sha256:" + digest


def _is_all_identical_sha256(value: Any) -> bool:
    normalized = _normalized_sha256(value)
    if normalized is None:
        return False
    digest = normalized.removeprefix("sha256:")
    return len(set(digest)) == 1


def _normalized_label(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", "_")


def _proof_source_refs(row: dict[str, Any]) -> list[Any]:
    refs: list[Any] = []
    for key in (
        "source_ref",
        "source_refs",
        "data_source_ref",
        "data_source_refs",
        "evaluation_source_ref",
        "evaluation_source_refs",
        "supported_evaluation_run_ref",
        "supported_evaluation_run_refs",
    ):
        value = row.get(key)
        if isinstance(value, (list, tuple)):
            refs.extend(value)
        elif value is not None:
            refs.append(value)

    pvalue_provenance = row.get("p_value_provenance")
    if isinstance(pvalue_provenance, dict):
        for key in (
            "source_ref",
            "source_refs",
            "supported_evaluation_run_ref",
            "supported_evaluation_run_refs",
        ):
            value = pvalue_provenance.get(key)
            if isinstance(value, (list, tuple)):
                refs.extend(value)
            elif value is not None:
                refs.append(value)
    return refs


def _direct_proof_source_refs(row: dict[str, Any]) -> list[Any]:
    refs: list[Any] = []
    for key in (
        "source_ref",
        "source_refs",
        "data_source_ref",
        "data_source_refs",
        "evaluation_source_ref",
        "evaluation_source_refs",
        "supported_evaluation_run_ref",
        "supported_evaluation_run_refs",
    ):
        if key not in row:
            continue
        value = row.get(key)
        if isinstance(value, (list, tuple)):
            refs.extend(value)
        elif value is not None:
            refs.append(value)
    return refs


def validate_data_provenance(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return _fail_closed("invalid_disqualified", "row_not_object")

    provenance = row.get("data_provenance")
    if provenance is None:
        return _fail_closed("invalid_disqualified", "data_provenance_missing")
    if not isinstance(provenance, str):
        return _fail_closed("invalid_disqualified", "data_provenance_not_string")

    label = provenance.strip()
    if not label:
        return _fail_closed("invalid_disqualified", "data_provenance_blank")
    if label not in SUPPORTED_SYNTHETIC_DATA_PROVENANCE:
        return _fail_closed(
            "invalid_disqualified",
            "data_provenance_unsupported",
            data_provenance=label,
        )

    return {
        "status": "data_provenance_valid",
        "profit_visible": False,
        "reason": "synthetic_non_market_provenance",
        "data_provenance": label,
        "provenance_class": "synthetic",
        "market_evidence": False,
        "claimable": False,
    }


def classify_proof_material(row: dict[str, Any]) -> dict[str, Any]:
    provenance_result = validate_data_provenance(row)
    if provenance_result["status"] != "data_provenance_valid":
        return provenance_result

    if _is_all_identical_sha256(row.get("cost_model_fingerprint")):
        return _fail_closed(
            "proof_material_rejected",
            "placeholder_sha256_fingerprint",
            proof_material_status="not_proof",
            data_provenance=provenance_result["data_provenance"],
        )

    direct_source_refs = _direct_proof_source_refs(row)
    source_refs = _proof_source_refs(row)
    if (
        direct_source_refs
        and not any(_nonblank_string(ref) for ref in direct_source_refs)
    ) or not any(_nonblank_string(ref) for ref in source_refs):
        return _fail_closed(
            "proof_material_rejected",
            "empty_source_refs",
            proof_material_status="not_proof",
            data_provenance=provenance_result["data_provenance"],
        )

    return {
        "status": "proof_material_classified",
        "profit_visible": False,
        "reason": "fixture_proof_material_non_claimable",
        "proof_material_status": "fixture_only_not_market_proof",
        "data_provenance": provenance_result["data_provenance"],
        "market_evidence": False,
        "claimable": False,
    }


def _blocked_readiness_claims() -> dict[str, bool]:
    return {
        "market_evidence": False,
        "claimable": False,
        "paper_forward_eligible": False,
        "live_shadow_ready": False,
        "live_ready": False,
        "account_ready": False,
        "broker_ready": False,
        "network_ready": False,
        "credential_ready": False,
        "runtime_ready": False,
    }


def _synthetic_claim_boundary_status(data_provenance: str) -> str:
    if data_provenance == "synthetic_known_edge_not_market":
        return SYNTHETIC_EDGE_NON_CLAIMABLE
    return SYNTHETIC_FIXTURE_NON_CLAIMABLE


def _fixture_anchor_payload(row: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("registration_anchor", "registration", "anchor"):
        value = row.get(key)
        if isinstance(value, dict):
            return value
    return None


def classify_candidate_claim_boundary(row: dict[str, Any]) -> dict[str, Any]:
    provenance_result = validate_data_provenance(row)
    if provenance_result["status"] != "data_provenance_valid":
        return _fail_closed(
            provenance_result["status"],
            provenance_result["reason"],
            claim_boundary_status=provenance_result["status"],
            **_blocked_readiness_claims(),
        )

    proof_result = classify_proof_material(row)
    data_provenance = provenance_result["data_provenance"]
    claim_boundary_status = _synthetic_claim_boundary_status(data_provenance)
    proof_details = {
        "proof_material_status": proof_result.get("proof_material_status"),
        "proof_material_reason": proof_result.get("reason"),
    }
    anchor_result = None
    anchor_payload = _fixture_anchor_payload(row)
    if anchor_payload is not None:
        anchor_result = validate_fixture_anchor_boundary(anchor_payload)
    anchor_details = {}
    if anchor_result is not None:
        anchor_details = {
            "anchor_boundary_status": anchor_result.get("status"),
            "anchor_boundary_reason": anchor_result.get("reason"),
        }

    return {
        "status": claim_boundary_status,
        "claim_boundary_status": claim_boundary_status,
        "profit_visible": False,
        "reason": "synthetic_provenance_non_claimable",
        "data_provenance": data_provenance,
        "provenance_class": provenance_result["provenance_class"],
        **proof_details,
        **anchor_details,
        **_blocked_readiness_claims(),
    }


def _registration_hash(
    registration: dict[str, Any], keys: tuple[str, ...]
) -> str | None:
    for key in keys:
        normalized = _normalized_sha256(registration.get(key))
        if normalized is not None:
            return normalized
    return None


def validate_fixture_anchor_boundary(registration: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(registration, dict):
        return _fail_closed("invalid_disqualified", "registration_anchor_not_object")

    anchor_kind = _normalized_label(registration.get("anchor_kind"))
    anchor_refs = [
        _normalized_label(registration.get(key))
        for key in (
            "anchor_ref",
            "registration_anchor_ref",
            "proof_ref",
            "registered_protocol_ref",
        )
    ]
    external_anchor_verified = registration.get(
        "external_anchor_verified", registration.get("anchor_verified", False)
    )

    if anchor_kind == "fixture_unanchored":
        proof_like_fields = {
            key: registration.get(key)
            for key in (
                "ots_proof_path",
                "proof_path",
                "proof_ref",
                "ots_proof_ref",
                "external_anchor_ref",
                "external_proof_ref",
            )
            if registration.get(key) not in (None, "", False)
        }
        if external_anchor_verified is not False or proof_like_fields:
            return _fail_closed(
                "fixture_anchor_rejected",
                "fixture_anchor_external_proof_drift",
                anchor_kind=registration.get("anchor_kind"),
                external_anchor_verified=external_anchor_verified,
                proof_like_fields=sorted(proof_like_fields),
            )
        return {
            "status": "fixture_anchor_only",
            "profit_visible": False,
            "reason": "fixture_unanchored_not_external_proof",
            "anchor_kind": "fixture_unanchored",
            "anchor_scope": "fixture_only",
            "external_anchor_verified": False,
            "market_evidence": False,
            "claimable": False,
        }

    labels = [anchor_kind, *anchor_refs]
    if external_anchor_verified is True and any(
        label == "phase163_registration_contract_only" or "contract_only" in label
        for label in labels
    ):
        return _fail_closed(
            "fixture_anchor_rejected",
            "contract_only_anchor_not_external_proof",
            proof_material_status="not_proof",
            anchor_kind=registration.get("anchor_kind"),
        )

    if anchor_kind in ACCEPTED_ANCHOR_KINDS:
        return _fail_closed(
            "external_anchor_verifier_missing",
            "external_anchor_verifier_not_implemented",
            anchor_kind=registration.get("anchor_kind"),
        )

    return _fail_closed(
        "fixture_anchor_rejected",
        "unsupported_fixture_anchor_kind",
        anchor_kind=registration.get("anchor_kind"),
    )


def validate_registration_anchor(registration: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(registration, dict):
        return _fail_closed("invalid_disqualified", "registration_anchor_not_object")

    registered_hash = _registration_hash(
        registration,
        ("registered_bytes_sha256", "registered_sha256", "sha256", "registered_bytes"),
    )
    current_hash = _registration_hash(
        registration,
        ("current_bytes_sha256", "current_sha256", "current_bytes"),
    )
    if registered_hash is None or current_hash is None:
        return _fail_closed("invalid_disqualified", "missing_hash_metadata")
    if registered_hash != current_hash:
        return _fail_closed("invalid_disqualified", "registered_bytes_mismatch")

    proof_path = str(
        registration.get("ots_proof_path") or registration.get("proof_path") or ""
    ).strip()
    if not proof_path.endswith(".ots"):
        return _fail_closed("invalid_disqualified", "missing_ots_proof")

    anchor_kind = str(registration.get("anchor_kind") or "").strip().lower()
    if not anchor_kind:
        return _fail_closed("invalid_disqualified", "missing_anchor_kind")
    if anchor_kind not in ACCEPTED_ANCHOR_KINDS:
        return _fail_closed("invalid_disqualified", "unsupported_anchor_kind")

    anchor_verified = registration.get(
        "external_anchor_verified", registration.get("anchor_verified", False)
    )
    if anchor_verified is not True:
        return _fail_closed("invalid_disqualified", "anchor_not_verified")
    if registration.get("anchor_stale") or registration.get("stale_anchor"):
        return _fail_closed("invalid_disqualified", "stale_anchor")
    if registration.get("anchor_author_controlled") or registration.get(
        "author_controlled_anchor"
    ):
        return _fail_closed("invalid_disqualified", "author_controlled_anchor")
    if registration.get("anchor_force_pushable") or registration.get(
        "force_pushable_anchor"
    ):
        return _fail_closed("invalid_disqualified", "force_pushable_anchor")

    return {
        "status": "registration_anchor_valid",
        "registered_bytes_sha256": registered_hash,
        "current_bytes_sha256": current_hash,
        "ots_proof_path": proof_path,
        "anchor_kind": registration.get("anchor_kind"),
    }


def validate_pvalue_provenance(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return _fail_closed("invalid_disqualified", "row_not_object")

    p_raw = row.get("p_raw")
    if isinstance(p_raw, bool) or not isinstance(p_raw, (int, float)):
        return _fail_closed("invalid_disqualified", "missing_p_raw")
    p_value = float(p_raw)
    if not math.isfinite(p_value) or p_value < 0.0 or p_value > 1.0:
        return _fail_closed("invalid_disqualified", "invalid_p_raw")

    provenance = row.get("p_value_provenance")
    if not isinstance(provenance, dict):
        return _fail_closed("invalid_disqualified", "missing_p_value_provenance")

    required = (
        "sealed_protocol_ref",
        "hypothesis_id",
        "supported_evaluation_run_ref",
    )
    missing = [
        field for field in required if not _nonblank_string(provenance.get(field))
    ]
    if missing:
        return _fail_closed(
            "invalid_disqualified",
            "missing_p_value_provenance_fields",
            missing=missing,
        )

    source = str(
        provenance.get("p_value_source") or provenance.get("source") or ""
    ).strip().lower()
    source = source.replace("-", "_")
    if (
        source in P_VALUE_PADDING_SOURCES
        or provenance.get("is_padding")
        or row.get("p_value_padding")
    ):
        return _fail_closed("profit_visible_false", "p_value_padding_forbidden")

    if provenance.get("hypothesis_id") != row.get("hypothesis_id"):
        return _fail_closed("invalid_disqualified", "p_value_hypothesis_mismatch")

    anchor_ref = row.get("registration_anchor_ref") or row.get("sealed_protocol_ref")
    if (
        _nonblank_string(anchor_ref)
        and provenance.get("sealed_protocol_ref") != anchor_ref
    ):
        return _fail_closed("invalid_disqualified", "p_value_unregistered_protocol")

    return {"status": "pvalue_provenance_valid", "p_raw": p_value}


def _holm_adjusted_pvalues(p_values: list[float]) -> tuple[list[float], list[int]]:
    count = len(p_values)
    ranked = sorted(enumerate(p_values), key=lambda item: (item[1], item[0]))
    adjusted = [0.0] * count
    ranks = [0] * count
    previous = 0.0
    for rank, (original_index, p_value) in enumerate(ranked, start=1):
        raw_adjusted = min(1.0, p_value * (count - rank + 1))
        monotonic_adjusted = max(previous, raw_adjusted)
        previous = monotonic_adjusted
        adjusted[original_index] = round(monotonic_adjusted, 12)
        ranks[original_index] = rank
    return adjusted, ranks


def apply_holm_fwer(
    rows: list[dict[str, Any]],
    sealed_hypothesis_count: int,
    method: str,
    error_rate_target: str,
    alpha: float,
) -> dict[str, Any]:
    if method != SUPPORTED_MTC_METHOD:
        return _fail_closed("profit_visible_false", "mtc_method_mismatch")
    if error_rate_target != SUPPORTED_ERROR_RATE_TARGET:
        return _fail_closed("profit_visible_false", "error_rate_target_mismatch")
    try:
        alpha_value = float(alpha)
    except (TypeError, ValueError):
        return _fail_closed("profit_visible_false", "alpha_mismatch")
    if isinstance(alpha, bool) or alpha_value != SUPPORTED_ALPHA:
        return _fail_closed("profit_visible_false", "alpha_mismatch")
    if not isinstance(rows, list):
        return _fail_closed("invalid_disqualified", "rows_not_list")
    if isinstance(sealed_hypothesis_count, bool) or not isinstance(
        sealed_hypothesis_count, int
    ):
        return _fail_closed("invalid_disqualified", "sealed_denominator_not_int")
    if sealed_hypothesis_count <= 0:
        return _fail_closed("invalid_disqualified", "sealed_denominator_not_positive")

    input_count = len(rows)
    if input_count != sealed_hypothesis_count:
        return _fail_closed(
            "profit_visible_false",
            "sealed_denominator_mismatch",
            sealed_denominator=sealed_hypothesis_count,
            input_count=input_count,
        )
    if not all(isinstance(row, dict) for row in rows):
        return _fail_closed("invalid_disqualified", "row_not_object")

    hypothesis_ids = [row.get("hypothesis_id") for row in rows]
    if not all(_nonblank_string(hypothesis_id) for hypothesis_id in hypothesis_ids):
        return _fail_closed("invalid_disqualified", "missing_hypothesis_id")
    if len(set(hypothesis_ids)) != len(hypothesis_ids):
        return _fail_closed("profit_visible_false", "duplicate_hypothesis_id")

    sealed_indexes = [row.get("sealed_hypothesis_index") for row in rows]
    if not all(
        isinstance(sealed_index, int) and not isinstance(sealed_index, bool)
        for sealed_index in sealed_indexes
    ):
        return _fail_closed("profit_visible_false", "sealed_index_mismatch")
    if sorted(sealed_indexes) != list(range(1, sealed_hypothesis_count + 1)):
        return _fail_closed("profit_visible_false", "sealed_index_mismatch")

    p_values: list[float] = []
    for row in rows:
        provenance_result = validate_pvalue_provenance(row)
        if provenance_result["status"] != "pvalue_provenance_valid":
            return provenance_result
        p_values.append(float(provenance_result["p_raw"]))

    adjusted_pvalues, ranks = _holm_adjusted_pvalues(p_values)
    adjusted_rows: list[dict[str, Any]] = []
    for row, p_holm_adjusted, holm_rank in zip(
        rows, adjusted_pvalues, ranks, strict=True
    ):
        mtc_passed = p_holm_adjusted <= SUPPORTED_ALPHA
        adjusted_rows.append(
            {
                **row,
                "p_raw": float(row["p_raw"]),
                "p_holm_adjusted": p_holm_adjusted,
                "holm_rank": holm_rank,
                "mtc_passed": mtc_passed,
                "mtc_reason": "mtc_passed" if mtc_passed else "mtc_failed",
                "sealed_denominator": sealed_hypothesis_count,
                "mtc_method": method,
                "error_rate_target": error_rate_target,
                "alpha": SUPPORTED_ALPHA,
                "mtc_input_count": input_count,
            }
        )

    family_summaries: dict[str, dict[str, Any]] = {}
    for row in adjusted_rows:
        family_id = str(row.get("family_id") or "")
        summary = family_summaries.setdefault(
            family_id,
            {
                "family_id": family_id,
                "candidate_count": 0,
                "mtc_passed_count": 0,
                "mtc_failed_count": 0,
                "sealed_denominator": sealed_hypothesis_count,
                "method": method,
                "error_rate_target": error_rate_target,
                "alpha": SUPPORTED_ALPHA,
                "input_count": input_count,
            },
        )
        summary["candidate_count"] += 1
        if row["mtc_passed"]:
            summary["mtc_passed_count"] += 1
        else:
            summary["mtc_failed_count"] += 1

    any_passed = any(row["mtc_passed"] for row in adjusted_rows)
    return {
        "status": "mtc_passed" if any_passed else "mtc_failed",
        "profit_visible": any_passed,
        "sealed_denominator": sealed_hypothesis_count,
        "method": method,
        "error_rate_target": error_rate_target,
        "alpha": SUPPORTED_ALPHA,
        "input_count": input_count,
        "rows": adjusted_rows,
        "family_summaries": list(family_summaries.values()),
    }


def _append_unique(strings: list[str], value: Any) -> None:
    if isinstance(value, str) and value and value not in strings:
        strings.append(value)


def choose_primary_stop_reason(failures: list[str] | tuple[str, ...]) -> str | None:
    observed = [failure for failure in failures if isinstance(failure, str) and failure]
    for stop_reason in STOP_PRECEDENCE:
        if stop_reason in observed:
            return stop_reason
    return observed[0] if observed else None


def _row_failures(row: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    raw_failures = row.get("all_failures")
    if isinstance(raw_failures, (list, tuple)):
        for failure in raw_failures:
            _append_unique(failures, failure)
    _append_unique(failures, row.get("primary_stop_reason"))

    gates = row.get("gate_statuses")
    if isinstance(gates, dict):
        for gate_name, gate_payload in gates.items():
            if not isinstance(gate_payload, dict):
                continue
            status = gate_payload.get("status")
            reason = gate_payload.get("reason")
            if gate_name == "registration_anchor" and status == "failed":
                _append_unique(failures, "registration_anchor_invalid")
            if status == "failed" or reason in STOP_PRECEDENCE:
                _append_unique(failures, reason)
    return failures


def _row_claim_boundary(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict) or "data_provenance" not in row:
        return None
    return classify_candidate_claim_boundary(row)


def _append_claim_boundary_statuses(
    statuses: list[str], boundary: dict[str, Any] | None
) -> None:
    if boundary is None:
        return
    _append_unique(
        statuses,
        boundary.get("claim_boundary_status") or boundary.get("status"),
    )


def _synthetic_boundary_outcome(statuses: list[str]) -> str | None:
    if SYNTHETIC_EDGE_NON_CLAIMABLE in statuses:
        return SYNTHETIC_EDGE_NON_CLAIMABLE
    if SYNTHETIC_FIXTURE_NON_CLAIMABLE in statuses:
        return SYNTHETIC_FIXTURE_NON_CLAIMABLE
    return None


def _row_is_profit_survivor(row: dict[str, Any]) -> bool:
    claim_boundary = _row_claim_boundary(row)
    if claim_boundary is not None and claim_boundary.get("claimable") is False:
        return False
    if row.get("paper_forward_mapping_status") != "eligible_for_mapping":
        return False
    if row.get("mtc_passed") is not True:
        return False
    gates = row.get("gate_statuses")
    if not isinstance(gates, dict):
        return False
    for gate in COMPLETED_CHECKPOINT_GATES:
        gate_payload = gates.get(gate)
        if not isinstance(gate_payload, dict):
            return False
        if gate_payload.get("status") != "passed":
            return False
    return True


def _checkpoint_complete(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    for row in rows:
        gates = row.get("gate_statuses")
        if not isinstance(gates, dict):
            return False
        for gate in COMPLETED_CHECKPOINT_GATES:
            gate_payload = gates.get(gate)
            if not isinstance(gate_payload, dict):
                return False
            if gate_payload.get("status") not in {"passed", "failed"}:
                return False
    return True


def derive_family_outcome(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(rows, list) or not rows:
        return {
            "family_id": None,
            "family_outcome": "plumbing_only",
            "candidate_count": 0,
            "survivor_count": 0,
            "checkpoint_complete": False,
            "primary_stop_reason": None,
            "all_failures": [],
        }

    family_id = str(rows[0].get("family_id") or "")
    all_failures: list[str] = []
    claim_boundary_statuses: list[str] = []
    for row in rows:
        for failure in _row_failures(row):
            _append_unique(all_failures, failure)
        claim_boundary = _row_claim_boundary(row)
        if claim_boundary is not None:
            _append_claim_boundary_statuses(claim_boundary_statuses, claim_boundary)
            if claim_boundary.get("reason") == "synthetic_provenance_non_claimable":
                _append_unique(all_failures, claim_boundary["reason"])

    primary_stop_reason = choose_primary_stop_reason(all_failures)
    survivor_count = sum(1 for row in rows if _row_is_profit_survivor(row))
    checkpoint_complete = _checkpoint_complete(rows)
    synthetic_boundary_outcome = _synthetic_boundary_outcome(claim_boundary_statuses)

    if primary_stop_reason == "registration_anchor_invalid":
        family_outcome = "invalid_disqualified"
    elif checkpoint_complete and survivor_count > 0:
        family_outcome = "profit_visible"
    elif not checkpoint_complete:
        family_outcome = "plumbing_only"
    elif synthetic_boundary_outcome is not None:
        family_outcome = synthetic_boundary_outcome
    else:
        family_outcome = "honest_null_ship"

    return {
        "family_id": family_id,
        "family_outcome": family_outcome,
        "candidate_count": len(rows),
        "survivor_count": survivor_count,
        "checkpoint_complete": checkpoint_complete,
        "primary_stop_reason": primary_stop_reason,
        "all_failures": all_failures,
        "profit_visible": family_outcome == "profit_visible",
        "claim_boundary_statuses": claim_boundary_statuses,
    }


def derive_overall_outcome(families: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(families, list) or not families:
        return {
            "overall_outcome": "plumbing_only",
            "family_count": 0,
            "families": [],
            "primary_stop_reason": None,
            "all_failures": [],
        }

    outcomes = [family.get("family_outcome") for family in families]
    if "invalid_disqualified" in outcomes:
        overall_outcome = "invalid_disqualified"
    elif "profit_visible" in outcomes:
        overall_outcome = "profit_visible"
    elif "plumbing_only" in outcomes:
        overall_outcome = "plumbing_only"
    elif SYNTHETIC_EDGE_NON_CLAIMABLE in outcomes:
        overall_outcome = SYNTHETIC_EDGE_NON_CLAIMABLE
    elif SYNTHETIC_FIXTURE_NON_CLAIMABLE in outcomes:
        overall_outcome = SYNTHETIC_FIXTURE_NON_CLAIMABLE
    elif outcomes and all(outcome == "honest_null_ship" for outcome in outcomes):
        overall_outcome = "honest_null_ship"
    else:
        overall_outcome = "plumbing_only"

    all_failures: list[str] = []
    claim_boundary_statuses: list[str] = []
    for family in families:
        failures = family.get("all_failures")
        if isinstance(failures, (list, tuple)):
            for failure in failures:
                _append_unique(all_failures, failure)
        statuses = family.get("claim_boundary_statuses")
        if isinstance(statuses, (list, tuple)):
            for status in statuses:
                _append_unique(claim_boundary_statuses, status)

    return {
        "overall_outcome": overall_outcome,
        "family_count": len(families),
        "families": families,
        "primary_stop_reason": choose_primary_stop_reason(all_failures),
        "all_failures": all_failures,
        "profit_visible": overall_outcome == "profit_visible",
        "claim_boundary_statuses": claim_boundary_statuses,
    }


def map_paper_forward_prerequisites(survivor: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(survivor, dict):
        return {
            "status": "paper_forward_mapping_blocked",
            "paper_forward_mapping_status": "blocked",
            "reason": "survivor_not_object",
            "divergence_reasons": [],
        }
    claim_boundary = _row_claim_boundary(survivor)
    if claim_boundary is not None and claim_boundary.get("claimable") is False:
        return {
            "status": "paper_forward_mapping_blocked",
            "paper_forward_mapping_status": "blocked",
            "reason": "synthetic_provenance_non_claimable",
            "claim_boundary_status": claim_boundary.get("claim_boundary_status"),
            "profit_visible": False,
            "market_evidence": False,
            "paper_forward_eligible": False,
            "divergence_reasons": [],
            "runtime_actions": [],
        }
    if survivor.get("paper_forward_mapping_status") != "eligible_for_mapping":
        return {
            "status": "paper_forward_mapping_blocked",
            "paper_forward_mapping_status": "blocked",
            "reason": "survivor_not_eligible_for_mapping",
            "divergence_reasons": [],
        }

    return {
        "status": "eligible_for_paper_forward_prerequisite_review",
        "paper_forward_mapping_status": "eligible_for_mapping",
        "claim_wording": ALLOWED_PAPER_FORWARD_WORDING,
        "prerequisite_refs": [dict(ref) for ref in PAPER_FORWARD_PREREQUISITE_REFS],
        "workflow_trigger": None,
        "handoff_artifact_path": None,
        "runtime_actions": [],
    }


def detect_paper_forward_divergence(
    backtest_or_wfd_assumptions: dict[str, Any],
    paper_forward_assumptions: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(backtest_or_wfd_assumptions, dict) or not isinstance(
        paper_forward_assumptions, dict
    ):
        return {
            "status": "paper_forward_mapping_blocked",
            "paper_forward_mapping_status": "blocked",
            "divergence_reasons": ["assumption_payload_invalid"],
        }

    divergence_reasons = [
        f"{field}_divergence"
        for field in DIVERGENCE_FIELDS
        if field not in backtest_or_wfd_assumptions
        or field not in paper_forward_assumptions
        or backtest_or_wfd_assumptions.get(field)
        != paper_forward_assumptions.get(field)
    ]
    if divergence_reasons:
        return {
            "status": "paper_forward_mapping_blocked",
            "paper_forward_mapping_status": "blocked",
            "divergence_reasons": divergence_reasons,
            "primary_stop_reason": "paper_forward_mapping_blocked",
            "all_failures": ["paper_forward_mapping_blocked", *divergence_reasons],
        }

    return {
        "status": "paper_forward_mapping_clear",
        "paper_forward_mapping_status": "eligible_for_mapping",
        "divergence_reasons": [],
    }


def _claim_phrase_pattern(phrase: str) -> str:
    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    return rf"(?<![\w-]){escaped}(?![\w-])"


def _claim_wording_occurrence_is_exempt(
    line: str,
    phrase: str,
    start: int,
    end: int,
) -> bool:
    phrase_pattern = _claim_phrase_pattern(phrase)
    for template in NEGATED_CLAIM_WORDING_PATTERNS:
        pattern = template.format(phrase=phrase_pattern)
        for match in re.finditer(pattern, line, flags=re.IGNORECASE):
            if match.start() <= start and match.end() >= end:
                return True
    return False


def _claim_wording_forbidden_matches(text: str) -> list[str]:
    if not isinstance(text, str):
        return []
    matches: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for phrase in FORBIDDEN_CLAIM_WORDING:
            pattern = _claim_phrase_pattern(phrase)
            for match in re.finditer(pattern, line, flags=re.IGNORECASE):
                if not _claim_wording_occurrence_is_exempt(
                    line,
                    phrase,
                    match.start(),
                    match.end(),
                ):
                    matches.append(f"line {line_number}: {match.group(0)}")
    return matches


def validate_claim_wording(text: str) -> dict[str, Any]:
    if text == ALLOWED_PAPER_FORWARD_WORDING:
        return {
            "status": "claim_wording_valid",
            "allowed_wording": ALLOWED_PAPER_FORWARD_WORDING,
        }

    forbidden_matches = _claim_wording_forbidden_matches(text)
    if not forbidden_matches:
        return {
            "status": "claim_wording_valid",
            "allowed_wording": ALLOWED_PAPER_FORWARD_WORDING,
            "forbidden_matches": [],
        }

    return {
        "status": "claim_wording_rejected",
        "allowed_wording": ALLOWED_PAPER_FORWARD_WORDING,
        "forbidden_matches": forbidden_matches,
    }


def validate_report_shape(report: dict[str, Any]) -> dict[str, Any]:
    rows = report.get("candidate_rows")
    if not isinstance(rows, list) or not rows:
        return {"status": "shape_invalid", "reason": "missing_candidate_rows"}

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return {"status": "shape_invalid", "reason": f"row_{index}_not_object"}
        missing = [field for field in REQUIRED_ROW_FIELDS if field not in row]
        if missing:
            return {
                "status": "shape_invalid",
                "reason": "missing_row_fields",
                "missing": missing,
            }
        gates = row.get("gate_statuses")
        if not isinstance(gates, dict):
            return {"status": "shape_invalid", "reason": "gate_statuses_not_object"}
        missing_gates = [gate for gate in REQUIRED_GATE_FIELDS if gate not in gates]
        if missing_gates:
            return {
                "status": "shape_invalid",
                "reason": "missing_gate_fields",
                "missing": missing_gates,
            }
        for metric, reason_field in zip(
            REQUIRED_METRIC_FIELDS, TYPED_NULL_REASON_FIELDS, strict=True
        ):
            value = row.get(metric)
            reason = row.get(reason_field)
            if value is None and not (isinstance(reason, str) and reason.strip()):
                return {
                    "status": "shape_invalid",
                    "reason": "missing_typed_null_reason",
                    "metric": metric,
                }
            if value in ("", "TBD", "unknown"):
                return {
                    "status": "shape_invalid",
                    "reason": "metric_placeholder",
                    "metric": metric,
                }
    return {"status": "shape_valid", "row_count": len(rows)}


def summarize_survivor_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        family_id = str(row["family_id"])
        counts.setdefault(family_id, {"candidate_count": 0, "survivor_count": 0})
        counts[family_id]["candidate_count"] += 1
        if _row_is_profit_survivor(row):
            counts[family_id]["survivor_count"] += 1
    return counts


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a local ProfitVisibilityReport.v1 payload."
    )
    parser.add_argument(
        "report",
        nargs="?",
        type=Path,
        help="Path to a ProfitVisibilityReport.v1 JSON file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.report is None:
        return 0

    report = json.loads(args.report.read_text(encoding="utf-8"))
    result = validate_report_shape(report)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "shape_valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
