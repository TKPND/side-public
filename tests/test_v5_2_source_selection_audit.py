"""Tests for the v5.2 source selection audit helper."""

from __future__ import annotations

import sys

sys.path.insert(0, "scripts")
import v5_2_source_selection_audit as audit  # noqa: E402


REQUIRED_CANDIDATE_IDS = {
    "binance_spot_direct",
    "coinbase_advanced_direct",
    "tardis_historical_spot",
    "gemini_tardis_alternate",
}


def _candidate_by_id(report: dict, candidate_id: str) -> dict:
    return {
        candidate["candidate_id"]: candidate for candidate in report["candidates"]
    }[candidate_id]


def test_candidate_report_schema_and_required_fields() -> None:
    report = audit.build_candidate_report()

    assert report["schema_version"] == "v5.2.source-selection.1"
    assert set(report["requirements_addressed"]) == {
        "SOURCE-V52-01",
        "SOURCE-V52-02",
    }
    assert {
        candidate["candidate_id"] for candidate in report["candidates"]
    } == REQUIRED_CANDIDATE_IDS

    required_fields = {
        "fields",
        "history_depth",
        "api_access",
        "licensing",
        "cost",
        "timestamp_precision",
        "reproducibility",
        "symbols",
        "claim_support",
        "source_urls",
    }
    for candidate in report["candidates"]:
        for field in required_fields:
            assert candidate[field], f"{candidate['candidate_id']} missing {field}"
        for symbol in candidate["symbols"]:
            assert symbol["project_pair"] in {"BTCUSD", "ETHUSD"}
            assert symbol["provider_symbol"]


def test_non_claim_bearing_market_data_is_rejected() -> None:
    report = audit.build_candidate_report()

    for candidate in report["candidates"]:
        support = set(candidate["fields"]["market_data_types"])
        claim_support = candidate["claim_support"]
        if support <= {"top_of_book", "ohlcv", "kline"}:
            assert claim_support["l2_depth_imbalance"] is False
            assert claim_support["trade_flow_imbalance"] is False


def test_tardis_records_bounded_no_key_sample() -> None:
    report = audit.build_candidate_report()
    tardis = _candidate_by_id(report, "tardis_historical_spot")

    assert tardis["bounded_sample_without_api_key"] is True


def test_source_verdict_selects_or_fails_closed() -> None:
    verdict = audit.build_source_verdict(audit.build_candidate_report())

    assert verdict["schema_version"] == "v5.2.source-verdict.1"
    assert verdict["verdict"] in {"selected_source", "no_source"}
    assert "SOURCE-V52-03" in verdict["requirements_addressed"]

    if verdict["verdict"] == "selected_source":
        assert verdict["selected_source_id"] == "tardis_historical_spot"
        assert verdict["phase119_smoke_ready"] is True
        assert verdict["bounded_sample_access"] is True
        assert verdict["long_term_subscription_committed"] is False
        assert verdict["next_phase"] == 119
    else:
        assert verdict["null_ship_reasons"]
        encoded = " ".join(verdict["null_ship_reasons"]).lower()
        assert any(term in encoded for term in ("source", "access", "semantics"))


def test_source_verdict_preserves_prior_artifacts_as_read_only() -> None:
    verdict = audit.build_source_verdict(audit.build_candidate_report())

    refs = verdict["provenance"]["read_only_refs"]
    assert "reports/v5.1/phase116/final_verdict.json" in refs
    assert "reports/v5.1/tick_data_contract_report.json" in refs
    assert verdict["v5_1_preserved"]["verdict"] == "null_ship"
