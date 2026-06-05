"""Tests for the v5.2 microstructure normalization and audit helper."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "scripts")
import v5_2_microstructure_audit as audit  # noqa: E402


def test_normalize_trades_keeps_documented_side() -> None:
    result = audit.normalize_trades(
        [
            {
                "timestamp": "2026-05-01T00:00:00.000Z",
                "side": "buy",
                "price": "60000.5",
                "amount": "0.1",
            },
            {
                "timestamp": "2026-05-01T00:00:01.000Z",
                "side": "sell",
                "price": "60001.5",
                "amount": "0.2",
            },
        ],
        exchange="binance",
        symbol="btcusdt",
    )

    assert result["status"] == "ok"
    assert result["row_count"] == 2
    assert result["failures"] == []
    assert result["rows"] == [
        {
            "timestamp": "2026-05-01T00:00:00.000Z",
            "exchange": "binance",
            "symbol": "btcusdt",
            "side": "buy",
            "price": 60000.5,
            "amount": 0.1,
        },
        {
            "timestamp": "2026-05-01T00:00:01.000Z",
            "exchange": "binance",
            "symbol": "btcusdt",
            "side": "sell",
            "price": 60001.5,
            "amount": 0.2,
        },
    ]


def test_normalize_trades_without_side_does_not_infer_aggressor() -> None:
    result = audit.normalize_trades(
        [
            {
                "timestamp": "2026-05-01T00:00:00.000Z",
                "price": "60000.5",
                "amount": "0.1",
            }
        ],
        exchange="binance",
        symbol="btcusdt",
    )

    assert result["status"] == "ok"
    assert result["row_count"] == 1
    assert result["failures"] == []
    assert result["rows"][0]["side"] == "unknown"


def test_normalize_l2_maps_buy_sell_to_bid_ask() -> None:
    result = audit.normalize_l2(
        [
            {
                "timestamp": "2026-05-01T00:00:00.000Z",
                "side": "buy",
                "price": "60000.5",
                "amount": "1.0",
            },
            {
                "timestamp": "2026-05-01T00:00:00.100Z",
                "side": "sell",
                "price": "60001.5",
                "amount": "1.2",
            },
        ],
        exchange="binance",
        symbol="btcusdt",
    )

    assert result["status"] == "ok"
    assert result["row_count"] == 2
    assert result["failures"] == []
    assert [row["side"] for row in result["rows"]] == ["bid", "ask"]


def test_normalize_l2_requires_side_price_amount_and_timestamp() -> None:
    result = audit.normalize_l2(
        [
            {"timestamp": "2026-05-01T00:00:00.000Z", "price": "1", "amount": "1"},
            {"timestamp": "2026-05-01T00:00:00.000Z", "side": "bid", "amount": "1"},
            {"timestamp": "2026-05-01T00:00:00.000Z", "side": "bid", "price": "1"},
            {"timestamp": "not-a-time", "side": "bid", "price": "1", "amount": "1"},
            {
                "timestamp": "2026-05-01T00:00:00.000Z",
                "side": "bid",
                "price": "-1",
                "amount": "1",
            },
            {
                "timestamp": "2026-05-01T00:00:00.000Z",
                "side": "bid",
                "price": "1",
                "amount": "-1",
            },
        ],
        exchange="binance",
        symbol="btcusdt",
    )

    assert result["status"] == "failed"
    assert result["row_count"] == 0
    assert [failure["field"] for failure in result["failures"]] == [
        "side",
        "price",
        "amount",
        "timestamp",
        "price",
        "amount",
    ]


def test_audit_counts_timestamp_duplicates_and_book_states() -> None:
    trades = audit.normalize_trades(
        [
            {
                "timestamp": "2026-05-01T00:00:01.000Z",
                "side": "buy",
                "price": "60000",
                "amount": "0.1",
            },
            {
                "timestamp": "2026-05-01T00:00:00.000Z",
                "side": "buy",
                "price": "60000",
                "amount": "0.1",
            },
            {
                "timestamp": "2026-05-01T00:00:00.000Z",
                "side": "buy",
                "price": "60000",
                "amount": "0.1",
            },
        ],
        exchange="binance",
        symbol="btcusdt",
    )
    l2 = audit.normalize_l2(
        [
            {
                "timestamp": "2026-05-01T00:00:00.000Z",
                "side": "bid",
                "price": "60000",
                "amount": "1",
            },
            {
                "timestamp": "2026-05-01T00:00:00.100Z",
                "side": "ask",
                "price": "59999",
                "amount": "1",
            },
            {
                "timestamp": "2026-05-01T00:00:00.200Z",
                "side": "ask",
                "price": "60000",
                "amount": "1",
            },
        ],
        exchange="binance",
        symbol="btcusdt",
    )

    quality = audit.audit_normalized_rows(trades, l2)

    assert quality["timestamp_disorder_count"] == 1
    assert quality["duplicate_row_count"] == 1
    assert quality["crossed_book_count"] == 1
    assert quality["locked_book_count"] == 1
    assert quality["spread_warning_count"] == 2


def test_blocked_provider_evidence_yields_neither_live_supported(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    access_blocker_path = tmp_path / "access_blocker.json"
    source_verdict_path = tmp_path / "source_verdict.json"
    audit.write_json(
        {
            "schema_version": "v5.2.ingestion-smoke.1",
            "status": "blocked",
            "requests": [
                {
                    "data_type": "trades",
                    "row_count": 0,
                    "sha256": None,
                    "status": "blocked",
                },
                {
                    "data_type": "incremental_book_L2",
                    "row_count": 0,
                    "sha256": None,
                    "status": "blocked",
                },
            ],
        },
        manifest_path,
    )
    audit.write_json(
        {
            "schema_version": "v5.2.ingestion-access-blocker.1",
            "blocker_reasons": ["HTTP Error 400: Bad Request"],
            "blocked_requests": [{"status": "blocked"}],
        },
        access_blocker_path,
    )
    audit.write_json(
        {
            "schema_version": "v5.2.source-verdict.1",
            "selected_source_id": "tardis_historical_spot",
        },
        source_verdict_path,
    )
    trades = audit.normalize_trades(
        [
            {
                "timestamp": "2026-05-01T00:00:00.000Z",
                "side": "buy",
                "price": "60000",
                "amount": "0.1",
            }
        ],
        exchange="binance",
        symbol="btcusdt",
    )
    l2 = audit.normalize_l2(
        [
            {
                "timestamp": "2026-05-01T00:00:00.000Z",
                "side": "bid",
                "price": "60000",
                "amount": "1",
            }
        ],
        exchange="binance",
        symbol="btcusdt",
    )

    report = audit.build_audit_report(
        trades,
        l2,
        manifest_path,
        access_blocker_path,
        source_verdict_path,
    )

    assert report["provider_evidence_status"] == "blocked"
    assert report["supported_claim_class"] == "neither_live_supported"
    assert report["trades_claim_support"] == "schema_ready_evidence_blocked"
    assert report["l2_claim_support"] == "schema_ready_evidence_blocked"
    assert report["supported_claim_class"] != "supported"
