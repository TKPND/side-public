"""v5.2 BTCUSD/ETHUSD source selection audit helper.

Phase 118 compares exchange-native L2 order book and trade tape source paths
before any new signal work. The output is deterministic so downstream phases
can consume the same evidence without reinterpreting provider documentation.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "v5.2.source-selection.1"
VERDICT_SCHEMA_VERSION = "v5.2.source-verdict.1"
EVIDENCE_CHECKED_AT = "2026-05-01"
DEFAULT_JSON_OUT = Path("reports/v5.2/source_selection/source_candidates.json")
DEFAULT_MD_OUT = Path("reports/v5.2/source_selection/source_candidates.md")
DEFAULT_VERDICT_JSON_OUT = Path("reports/v5.2/source_selection/source_verdict.json")
DEFAULT_VERDICT_MD_OUT = Path("reports/v5.2/source_selection/source_verdict.md")

BINANCE_WS_DOC = (
    "https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams"
)
BINANCE_REST_DOC = (
    "https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints"
)
COINBASE_WS_DOC = (
    "https://docs.cdp.coinbase.com/coinbase-business/"
    "advanced-trade-apis/websocket/websocket-channels"
)
TARDIS_DATA_DOC = "https://docs.tardis.dev/faq/data"
TARDIS_HISTORY_DOC = "https://docs.tardis.dev/historical-data-details/overview"
TARDIS_CSV_DOC = "https://docs.tardis.dev/downloadable-csv-files"


def _symbols(*items: tuple[str, str, str]) -> list[dict[str, str]]:
    return [
        {
            "project_pair": project_pair,
            "provider_exchange": provider_exchange,
            "provider_symbol": provider_symbol,
        }
        for project_pair, provider_exchange, provider_symbol in items
    ]


CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "binance_spot_direct",
        "provider": "Binance Spot",
        "source_type": "exchange_direct_live",
        "symbols": _symbols(
            ("BTCUSD", "binance", "BTCUSDT"),
            ("ETHUSD", "binance", "ETHUSDT"),
        ),
        "fields": {
            "market_data_types": ["trade", "depth", "rest_order_book"],
            "l2_depth": [
                "bid_price_levels",
                "bid_sizes",
                "ask_price_levels",
                "ask_sizes",
                "snapshot_plus_delta_ordering",
            ],
            "trade_tape": [
                "trade_id",
                "price",
                "quantity",
                "trade_time",
                "buyer_is_market_maker",
            ],
            "unsupported_as_claim_basis": ["top_of_book", "ohlcv", "kline"],
        },
        "history_depth": "Direct API is live-first; historical replay requires self-recording or a separate archive provider.",
        "api_access": "Public WebSocket market data plus REST order book and trade endpoints.",
        "licensing": "Exchange API terms apply; Phase 118 records no redistribution approval.",
        "cost": "No long-term paid subscription committed for Phase 118.",
        "timestamp_precision": "Millisecond event/trade timestamps by default; WebSocket supports microsecond timeUnit parameter.",
        "reproducibility": "Reproducible live smoke parameters; not sufficient as a bounded historical replay source by itself.",
        "bounded_sample_without_api_key": False,
        "long_term_subscription_committed": False,
        "claim_support": {
            "l2_depth_imbalance": True,
            "trade_flow_imbalance": True,
            "trade_side_semantics": "maker_side_proxy_buyer_is_market_maker",
            "historical_replay": False,
        },
        "source_urls": [BINANCE_WS_DOC, BINANCE_REST_DOC],
        "rejection_or_blocker_notes": [
            "Live semantics candidate only until historical capture or replay source is available.",
            "Trade side must be documented as a maker-side proxy, not inferred taker-side flow.",
        ],
    },
    {
        "candidate_id": "coinbase_advanced_direct",
        "provider": "Coinbase Advanced Trade",
        "source_type": "exchange_direct_live",
        "symbols": _symbols(
            ("BTCUSD", "coinbase", "BTC-USD"),
            ("ETHUSD", "coinbase", "ETH-USD"),
        ),
        "fields": {
            "market_data_types": ["level2", "market_trades"],
            "l2_depth": [
                "bid_price_levels",
                "bid_sizes",
                "ask_price_levels",
                "ask_sizes",
                "snapshot_and_update_events",
            ],
            "trade_tape": [
                "trade_id",
                "price",
                "size",
                "event_time",
                "side",
            ],
            "unsupported_as_claim_basis": ["top_of_book", "ohlcv", "kline"],
        },
        "history_depth": "Direct Advanced Trade WebSocket is live-first; historical replay is not established by this path alone.",
        "api_access": "Advanced Trade WebSocket level2 and market_trades channels.",
        "licensing": "Coinbase developer/API terms apply; Phase 118 records no redistribution approval.",
        "cost": "No long-term paid subscription committed for Phase 118.",
        "timestamp_precision": "Documented event timestamps on WebSocket messages.",
        "reproducibility": "Reproducible live smoke parameters; insufficient for 24-month historical replay by itself.",
        "bounded_sample_without_api_key": False,
        "long_term_subscription_committed": False,
        "claim_support": {
            "l2_depth_imbalance": True,
            "trade_flow_imbalance": True,
            "trade_side_semantics": "documented_exchange_side_field",
            "historical_replay": False,
        },
        "source_urls": [COINBASE_WS_DOC],
        "rejection_or_blocker_notes": [
            "Live semantics candidate only until historical capture or replay source is available.",
            "Use side semantics exactly as documented by Coinbase.",
        ],
    },
    {
        "candidate_id": "tardis_historical_spot",
        "provider": "Tardis.dev historical spot",
        "source_type": "historical_market_data_provider",
        "symbols": _symbols(
            ("BTCUSD", "binance", "btcusdt"),
            ("ETHUSD", "binance", "ethusdt"),
            ("BTCUSD", "coinbase", "BTC-USD"),
            ("ETHUSD", "coinbase", "ETH-USD"),
        ),
        "fields": {
            "market_data_types": [
                "trades",
                "incremental_book_L2",
                "book_snapshot_5",
                "book_snapshot_25",
            ],
            "l2_depth": [
                "bid_price_levels",
                "bid_sizes",
                "ask_price_levels",
                "ask_sizes",
                "incremental_l2_updates",
                "snapshot_reconstruction_inputs",
            ],
            "trade_tape": ["timestamp", "price", "amount", "side_or_exchange_payload"],
            "unsupported_as_claim_basis": ["top_of_book", "ohlcv", "kline"],
        },
        "history_depth": "Historical normalized CSV/API datasets and raw replay coverage for supported exchanges.",
        "api_access": "Downloadable CSV/API; first day of each month is documented as accessible without an API key.",
        "licensing": "Provider terms/subscription required for continuous history; sample use only is assumed for Phase 119 smoke.",
        "cost": "Bounded no-key monthly sample available; continuous research history requires a later subscription decision.",
        "timestamp_precision": "Exchange timestamp plus local capture timestamp semantics are documented by provider.",
        "reproducibility": "Best Phase 119 bounded historical smoke fit: request parameters, row counts, timestamp range, and raw hashes can be recorded.",
        "bounded_sample_without_api_key": True,
        "long_term_subscription_committed": False,
        "claim_support": {
            "l2_depth_imbalance": True,
            "trade_flow_imbalance": True,
            "trade_side_semantics": "exchange_specific_or_raw_payload_semantics_required",
            "historical_replay": True,
        },
        "source_urls": [TARDIS_DATA_DOC, TARDIS_HISTORY_DOC, TARDIS_CSV_DOC],
        "rejection_or_blocker_notes": [
            "Preferred Phase 119 bounded historical smoke candidate if no-key sample access remains available.",
            "Continuous historical coverage remains a cost/access gate, not a Phase 118 commitment.",
        ],
    },
    {
        "candidate_id": "gemini_tardis_alternate",
        "provider": "Tardis.dev Gemini",
        "source_type": "alternate_exchange_historical_provider",
        "symbols": _symbols(
            ("BTCUSD", "gemini", "BTCUSD"),
            ("ETHUSD", "gemini", "ETHUSD"),
        ),
        "fields": {
            "market_data_types": ["trade", "l2_updates", "book_snapshot_5"],
            "l2_depth": [
                "bid_price_levels",
                "bid_sizes",
                "ask_price_levels",
                "ask_sizes",
                "l2_updates",
            ],
            "trade_tape": ["timestamp", "price", "amount", "exchange_payload"],
            "unsupported_as_claim_basis": ["top_of_book", "ohlcv", "kline"],
        },
        "history_depth": "Historical provider path for exact BTCUSD/ETHUSD Gemini labels.",
        "api_access": "Tardis.dev historical API/CSV provider path.",
        "licensing": "Provider terms/subscription required beyond public sample data.",
        "cost": "No long-term paid subscription committed for Phase 118.",
        "timestamp_precision": "Provider timestamp semantics apply.",
        "reproducibility": "Useful alternate-provider row; liquidity/comparability must be documented before claim use.",
        "bounded_sample_without_api_key": True,
        "long_term_subscription_committed": False,
        "claim_support": {
            "l2_depth_imbalance": True,
            "trade_flow_imbalance": True,
            "trade_side_semantics": "exchange_specific_or_raw_payload_semantics_required",
            "historical_replay": True,
        },
        "source_urls": [TARDIS_DATA_DOC, TARDIS_HISTORY_DOC, TARDIS_CSV_DOC],
        "rejection_or_blocker_notes": [
            "Alternate row only; Phase 119 should prefer the selected Binance/Coinbase historical path unless Gemini comparability is explicitly chosen.",
        ],
    },
)


def _sorted_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(candidate)
    item["symbols"] = sorted(
        item["symbols"],
        key=lambda symbol: (
            symbol["project_pair"],
            symbol["provider_exchange"],
            symbol["provider_symbol"],
        ),
    )
    return item


def build_candidate_report() -> dict[str, Any]:
    """Build the deterministic Phase 118 candidate comparison report."""
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": 118,
        "generated_at": date.today().isoformat(),
        "evidence_checked_at": EVIDENCE_CHECKED_AT,
        "requirements_addressed": ["SOURCE-V52-01", "SOURCE-V52-02"],
        "project_pairs": ["BTCUSD", "ETHUSD"],
        "v5_1_preserved": {
            "verdict": "null_ship",
            "reason": "v5.1 remains a top-of-book quote proxy null-ship and is not reopened.",
            "read_only_refs": [
                "reports/v5.1/phase116/final_verdict.json",
                "reports/v5.1/tick_data_contract_report.json",
            ],
        },
        "insufficient_source_rejections": [
            {
                "source_type": "top_of_book",
                "claim_support": "neither",
                "reason": "Best bid/ask only cannot support L2 depth imbalance or true trade-flow imbalance.",
            },
            {
                "source_type": "ohlcv",
                "claim_support": "neither",
                "reason": "Candles collapse order book and trade sequence semantics.",
            },
            {
                "source_type": "kline",
                "claim_support": "neither",
                "reason": "Klines are aggregate candles, not exchange-native L2 or trade-tape evidence.",
            },
        ],
        "candidates": [_sorted_candidate(candidate) for candidate in CANDIDATES],
    }


def _symbol_summary(candidate: dict[str, Any]) -> str:
    return ", ".join(
        f"{symbol['project_pair']}->{symbol['provider_exchange']}:{symbol['provider_symbol']}"
        for symbol in candidate["symbols"]
    )


def _claim_summary(candidate: dict[str, Any]) -> str:
    claim_support = candidate["claim_support"]
    parts = [
        f"L2={str(claim_support['l2_depth_imbalance']).lower()}",
        f"trade_flow={str(claim_support['trade_flow_imbalance']).lower()}",
        f"historical={str(claim_support['historical_replay']).lower()}",
    ]
    return ", ".join(parts)


def render_candidates_markdown(report: dict[str, Any]) -> str:
    """Render the candidate report as Markdown."""
    lines = [
        "# v5.2 Source Selection Candidate Report",
        "",
        f"- schema_version: {report['schema_version']}",
        f"- evidence_checked_at: {report['evidence_checked_at']}",
        "- requirements_addressed: SOURCE-V52-01, SOURCE-V52-02",
        "- v5.1 remains a top-of-book quote proxy null-ship and is not reopened.",
        "- v4.x archive-zone artifacts are read-only inputs, not modified outputs.",
        "",
        "## Candidate Comparison",
        "",
        "| Candidate | Symbols | Source type | Claim support | History/access | Cost/license | Notes |",
        "|-----------|---------|-------------|---------------|----------------|--------------|-------|",
    ]
    for candidate in report["candidates"]:
        notes = "; ".join(candidate["rejection_or_blocker_notes"])
        lines.append(
            "| {provider} | {symbols} | {source_type} | {claim_support} | {history}; {access} | {cost}; {license} | {notes} |".format(
                provider=candidate["provider"],
                symbols=_symbol_summary(candidate),
                source_type=candidate["source_type"],
                claim_support=_claim_summary(candidate),
                history=candidate["history_depth"],
                access=candidate["api_access"],
                cost=candidate["cost"],
                license=candidate["licensing"],
                notes=notes,
            )
        )

    lines.extend(
        [
            "",
            "## Rejected Non-Claim Sources",
            "",
        ]
    )
    for rejection in report["insufficient_source_rejections"]:
        lines.append(
            f"- {rejection['source_type']}: {rejection['reason']} ({rejection['claim_support']})"
        )

    lines.extend(
        [
            "",
            "## Phase 119 Fit",
            "",
            "- Direct Binance/Coinbase paths are live semantics candidates but not sufficient historical replay by themselves.",
            "- Tardis.dev historical spot is the preferred Phase 119 bounded historical smoke candidate if no-key sample access remains available.",
            "- No long-term paid subscription is committed by Phase 118.",
            "",
        ]
    )
    return "\n".join(lines)


def _candidate_lookup(candidate_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        candidate["candidate_id"]: candidate
        for candidate in candidate_report["candidates"]
    }


def _has_project_pairs(candidate: dict[str, Any]) -> bool:
    return {
        symbol["project_pair"] for symbol in candidate["symbols"]
    } == {"BTCUSD", "ETHUSD"}


def _select_tardis(candidate_report: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    candidates = _candidate_lookup(candidate_report)
    candidate = candidates.get("tardis_historical_spot")
    if candidate is None:
        return None, ["source candidate tardis_historical_spot is missing"]

    checks = {
        "supports trades": "trades" in candidate["fields"]["market_data_types"],
        "supports incremental_book_L2": "incremental_book_L2"
        in candidate["fields"]["market_data_types"],
        "maps BTCUSD and ETHUSD": _has_project_pairs(candidate),
        "bounded sample access": candidate["bounded_sample_without_api_key"] is True,
        "no long-term subscription committed": candidate[
            "long_term_subscription_committed"
        ]
        is False,
    }
    blockers = [f"access/semantics check failed: {name}" for name, ok in checks.items() if not ok]
    if blockers:
        return None, blockers
    return candidate, []


def build_source_verdict(candidate_report: dict[str, Any]) -> dict[str, Any]:
    """Build the Phase 118 selected-source or no-source verdict."""
    selected, blockers = _select_tardis(candidate_report)
    common: dict[str, Any] = {
        "schema_version": VERDICT_SCHEMA_VERSION,
        "phase": 118,
        "generated_at": date.today().isoformat(),
        "evidence_checked_at": candidate_report["evidence_checked_at"],
        "requirements_addressed": ["SOURCE-V52-03"],
        "provenance": {
            "candidate_report": "reports/v5.2/source_selection/source_candidates.json",
            "read_only_refs": [
                "reports/v5.1/phase116/final_verdict.json",
                "reports/v5.1/tick_data_contract_report.json",
            ],
        },
        "v5_1_preserved": candidate_report["v5_1_preserved"],
        "long_term_subscription_committed": False,
    }

    if selected is None:
        return {
            **common,
            "verdict": "no_source",
            "selected_source_id": None,
            "selected_symbols": [],
            "phase119_smoke_ready": False,
            "bounded_sample_access": False,
            "next_phase": None,
            "null_ship_reasons": blockers
            or ["source/access/semantics gates did not identify a claim-ready candidate"],
        }

    return {
        **common,
        "verdict": "selected_source",
        "selected_source_id": selected["candidate_id"],
        "selected_provider": selected["provider"],
        "selected_symbols": selected["symbols"],
        "supported_claim_class": [
            "historical_l2_depth_imbalance_smoke",
            "historical_trade_tape_smoke_with_side_semantics_audit",
        ],
        "phase119_smoke_ready": True,
        "bounded_sample_access": selected["bounded_sample_without_api_key"],
        "next_phase": 119,
        "null_ship_reasons": [],
        "fail_close_reasons": [],
    }


def render_verdict_markdown(verdict: dict[str, Any]) -> str:
    """Render the source verdict as Markdown."""
    lines = [
        "# v5.2 Source Selection Verdict",
        "",
        f"- schema_version: {verdict['schema_version']}",
        f"- verdict: {verdict['verdict']}",
        f"- long-term subscription committed: {str(verdict['long_term_subscription_committed']).lower()}",
        "- v5.1 remains null_ship and read-only.",
        "- v4.x archive-zone artifacts were not modified.",
        "",
    ]
    if verdict["verdict"] == "selected_source":
        lines.extend(
            [
                "## Selected Source",
                "",
                f"- selected_source_id: {verdict['selected_source_id']}",
                f"- selected_provider: {verdict['selected_provider']}",
                f"- phase119_smoke_ready: {str(verdict['phase119_smoke_ready']).lower()}",
                f"- bounded_sample_access: {str(verdict['bounded_sample_access']).lower()}",
                f"- next_phase: {verdict['next_phase']}",
                "",
                "## Selected Symbols",
                "",
            ]
        )
        for symbol in verdict["selected_symbols"]:
            lines.append(
                f"- {symbol['project_pair']} -> {symbol['provider_exchange']}:{symbol['provider_symbol']}"
            )
    else:
        lines.extend(
            [
                "## No-Source Blockers",
                "",
            ]
        )
        for reason in verdict["null_ship_reasons"]:
            lines.append(f"- {reason}")

    lines.extend(
        [
            "",
            "## Provenance",
            "",
            f"- candidate_report: {verdict['provenance']['candidate_report']}",
        ]
    )
    for ref in verdict["provenance"]["read_only_refs"]:
        lines.append(f"- read_only_ref: {ref}")

    lines.append("")
    return "\n".join(lines)


def write_json(report: dict[str, Any], path: Path) -> None:
    """Write canonical JSON with deterministic key ordering."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def write_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--md-out", type=Path, default=DEFAULT_MD_OUT)
    parser.add_argument("--verdict-json-out", type=Path, default=DEFAULT_VERDICT_JSON_OUT)
    parser.add_argument("--verdict-md-out", type=Path, default=DEFAULT_VERDICT_MD_OUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_candidate_report()
    verdict = build_source_verdict(report)
    write_json(report, args.json_out)
    write_text(render_candidates_markdown(report), args.md_out)
    write_json(verdict, args.verdict_json_out)
    write_text(render_verdict_markdown(verdict), args.verdict_md_out)


if __name__ == "__main__":
    main()
