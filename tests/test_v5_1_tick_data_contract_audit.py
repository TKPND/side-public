"""Tests for the v5.1 tick data contract audit helper."""

from __future__ import annotations

import sys

sys.path.insert(0, "scripts")
import v5_1_tick_data_contract_audit as audit  # noqa: E402


def test_constants_are_sealed() -> None:
    assert audit.PAIRS == ("BTCUSD", "ETHUSD")
    assert audit.LOOKBACK_SECONDS == (30, 60, 300)
    assert audit.INTERPRETATION == "top-of-book quote imbalance proxy"


def test_audit_sql_contains_required_fields() -> None:
    sql = audit.build_audit_sql("BTCUSD")
    assert "`example-gcp-project.fx_tick_data.btcusd_ticks`" in sql
    for field in (
        "timestamp",
        "bidPrice",
        "askPrice",
        "bidVolume",
        "askVolume",
        "negative_spread_count",
        "row_count",
    ):
        assert field in sql


def test_bucket_sql_contains_missing_bucket_count() -> None:
    sql = audit.build_bucket_sql("ETHUSD", 300)
    assert "`example-gcp-project.fx_tick_data.ethusd_ticks`" in sql
    assert "missing_bucket_count" in sql
    assert "300 AS bucket_seconds" in sql


def test_blocker_report_sets_phase114_blocked() -> None:
    report = audit.blocker_report("credentials unavailable")
    assert report["phase114_blocked"] is True
    assert report["blocker_reason"] == "credentials unavailable"
    assert report["claims"]["aggressor_flow_claim"] is False


def test_pass_report_contains_pairs_without_banned_claims() -> None:
    report = audit.pass_report({"BTCUSD": {"source": []}, "ETHUSD": {"source": []}})
    assert set(report["diagnostics"]) == {"BTCUSD", "ETHUSD"}
    encoded = str(report)
    assert "aggressor_flow_claim': True" not in encoded
    assert "l2_depth_claim': True" not in encoded
    assert "market_depth_claim': True" not in encoded


def test_rendered_blocker_markdown_has_required_gate_text() -> None:
    md = audit.render_markdown(audit.blocker_report("bq missing"))
    assert "# v5.1 Tick Data Contract Report" in md
    assert "top-of-book quote imbalance proxy" in md
    assert "No exchange-native L2 order book depth is claimed." in md
    assert "No true aggressor trade flow is claimed." in md
    assert "phase114_blocked: true" in md
