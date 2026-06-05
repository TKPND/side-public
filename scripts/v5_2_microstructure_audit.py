"""Fixture-backed microstructure normalization and audit helpers for v5.2."""

from __future__ import annotations

import json
import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "v5.2.microstructure-audit.1"
DEFAULT_REPORT_DIR = Path("reports/v5.2/microstructure_audit")
DEFAULT_INGEST_MANIFEST = Path("reports/v5.2/ingestion_smoke/manifest.json")
DEFAULT_ACCESS_BLOCKER = Path("reports/v5.2/ingestion_smoke/access_blocker.json")
DEFAULT_SOURCE_VERDICT = Path("reports/v5.2/source_selection/source_verdict.json")

TRADE_SIDES = {"buy", "sell", "unknown", "ambiguous"}
L2_SIDE_MAP = {
    "bid": "bid",
    "ask": "ask",
    "buy": "bid",
    "sell": "ask",
}


def _parse_timestamp(value: Any) -> str:
    if value is None or str(value).strip() == "":
        raise ValueError("missing timestamp")
    raw = str(value).strip()
    datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return raw


def _parse_float(value: Any, *, field: str) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError(f"missing {field}")
    return float(str(value).strip())


def _failure(index: int, field: str, reason: str) -> dict[str, Any]:
    return {"row_index": index, "field": field, "reason": reason}


def _result(rows: list[dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "ok" if not failures else "failed",
        "rows": rows,
        "row_count": len(rows),
        "failures": failures,
    }


def normalize_trades(
    rows: list[dict[str, str]],
    exchange: str,
    symbol: str,
) -> dict[str, Any]:
    """Normalize Tardis trade fixture rows without inferring aggressor side."""
    normalized: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        try:
            timestamp = _parse_timestamp(row.get("timestamp"))
        except ValueError as exc:
            failures.append(_failure(index, "timestamp", str(exc)))
            continue

        try:
            price = _parse_float(row.get("price"), field="price")
        except (TypeError, ValueError) as exc:
            failures.append(_failure(index, "price", str(exc)))
            continue
        if price <= 0:
            failures.append(_failure(index, "price", "price must be positive"))
            continue

        try:
            amount = _parse_float(row.get("amount"), field="amount")
        except (TypeError, ValueError) as exc:
            failures.append(_failure(index, "amount", str(exc)))
            continue
        if amount < 0:
            failures.append(_failure(index, "amount", "amount must be non-negative"))
            continue

        side = str(row.get("side") or "").strip().lower() or "unknown"
        if side not in TRADE_SIDES:
            failures.append(_failure(index, "side", f"unsupported trade side: {side}"))
            continue

        normalized.append(
            {
                "timestamp": timestamp,
                "exchange": exchange,
                "symbol": symbol,
                "side": side,
                "price": price,
                "amount": amount,
            }
        )

    return _result(normalized, failures)


def normalize_l2(
    rows: list[dict[str, str]],
    exchange: str,
    symbol: str,
) -> dict[str, Any]:
    """Normalize Tardis incremental_book_L2 fixture rows fail-close."""
    normalized: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        try:
            timestamp = _parse_timestamp(row.get("timestamp"))
        except ValueError as exc:
            failures.append(_failure(index, "timestamp", str(exc)))
            continue

        raw_side = str(row.get("side") or "").strip().lower()
        side = L2_SIDE_MAP.get(raw_side)
        if side is None:
            failures.append(_failure(index, "side", "missing or unsupported L2 side"))
            continue

        try:
            price = _parse_float(row.get("price"), field="price")
        except (TypeError, ValueError) as exc:
            failures.append(_failure(index, "price", str(exc)))
            continue
        if price <= 0:
            failures.append(_failure(index, "price", "price must be positive"))
            continue

        try:
            amount = _parse_float(row.get("amount"), field="amount")
        except (TypeError, ValueError) as exc:
            failures.append(_failure(index, "amount", str(exc)))
            continue
        if amount < 0:
            failures.append(_failure(index, "amount", "amount must be non-negative"))
            continue

        normalized.append(
            {
                "timestamp": timestamp,
                "exchange": exchange,
                "symbol": symbol,
                "side": side,
                "price": price,
                "amount": amount,
            }
        )

    return _result(normalized, failures)


def write_json(report: dict[str, Any], path: Path) -> None:
    """Write canonical JSON with deterministic key ordering."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def _timestamp_sort_key(row: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))


def _duplicate_key(row: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, str(value)) for key, value in row.items()))


def audit_normalized_rows(
    trades_result: dict[str, Any],
    l2_result: dict[str, Any],
) -> dict[str, Any]:
    """Count deterministic quality warnings across normalized fixture rows."""
    all_rows = list(trades_result.get("rows", [])) + list(l2_result.get("rows", []))
    timestamp_disorder_count = 0
    previous_timestamp: datetime | None = None
    for row in all_rows:
        timestamp = _timestamp_sort_key(row)
        if previous_timestamp is not None and timestamp < previous_timestamp:
            timestamp_disorder_count += 1
        previous_timestamp = timestamp

    duplicate_row_count = 0
    seen_rows: set[tuple[tuple[str, str], ...]] = set()
    for row in all_rows:
        key = _duplicate_key(row)
        if key in seen_rows:
            duplicate_row_count += 1
        else:
            seen_rows.add(key)

    crossed_book_count = 0
    locked_book_count = 0
    spread_warning_count = 0
    best_bid: float | None = None
    best_ask: float | None = None
    for row in sorted(l2_result.get("rows", []), key=_timestamp_sort_key):
        if row["side"] == "bid":
            best_bid = max(best_bid, row["price"]) if best_bid is not None else row["price"]
        elif row["side"] == "ask":
            ask_price = row["price"]
            best_ask = min(best_ask, ask_price) if best_ask is not None else ask_price
            if best_bid is None:
                continue
            if ask_price < best_bid:
                crossed_book_count += 1
                spread_warning_count += 1
            elif ask_price == best_bid:
                locked_book_count += 1
                spread_warning_count += 1

    return {
        "timestamp_disorder_count": timestamp_disorder_count,
        "duplicate_row_count": duplicate_row_count,
        "crossed_book_count": crossed_book_count,
        "locked_book_count": locked_book_count,
        "spread_warning_count": spread_warning_count,
        "trade_contract_failure_count": len(trades_result.get("failures", [])),
        "l2_contract_failure_count": len(l2_result.get("failures", [])),
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _provider_evidence_blocked(
    manifest: dict[str, Any],
    access_blocker_path: Path,
) -> bool:
    if access_blocker_path.exists():
        return True
    requests = manifest.get("requests", [])
    if not requests:
        return manifest.get("status") == "blocked"
    return all(
        request.get("status") == "blocked"
        or (request.get("row_count") == 0 and request.get("sha256") is None)
        for request in requests
    )


def build_audit_report(
    trades_result: dict[str, Any],
    l2_result: dict[str, Any],
    ingest_manifest_path: Path,
    access_blocker_path: Path,
    source_verdict_path: Path,
) -> dict[str, Any]:
    """Build Phase 120 quality audit and claim-support verdict."""
    manifest = _read_json(ingest_manifest_path)
    access_blocker = _read_json(access_blocker_path)
    source_verdict = _read_json(source_verdict_path)
    provider_blocked = _provider_evidence_blocked(manifest, access_blocker_path)
    provider_evidence_status = "blocked" if provider_blocked else "available"
    if provider_blocked:
        trades_claim_support = "schema_ready_evidence_blocked"
        l2_claim_support = "schema_ready_evidence_blocked"
        supported_claim_class = "neither_live_supported"
    else:
        trades_claim_support = "schema_ready"
        l2_claim_support = "schema_ready"
        supported_claim_class = "fixture_schema_ready_only"

    return {
        "schema_version": SCHEMA_VERSION,
        "phase": 120,
        "requirements_addressed": [
            "MICRO-V52-01",
            "MICRO-V52-02",
            "MICRO-V52-03",
        ],
        "provider_evidence_status": provider_evidence_status,
        "selected_source_id": source_verdict.get("selected_source_id"),
        "trades_contract": trades_result,
        "l2_contract": l2_result,
        "quality_audit": audit_normalized_rows(trades_result, l2_result),
        "trades_claim_support": trades_claim_support,
        "l2_claim_support": l2_claim_support,
        "supported_claim_class": supported_claim_class,
        "blocker_evidence": {
            "ingest_manifest": ingest_manifest_path.as_posix(),
            "access_blocker": access_blocker_path.as_posix()
            if access_blocker_path.exists()
            else None,
            "manifest_status": manifest.get("status"),
            "blocker_reasons": access_blocker.get("blocker_reasons", []),
        },
    }


def render_audit_markdown(report: dict[str, Any]) -> str:
    """Render Phase 120 audit report as human-readable Markdown."""
    quality = report["quality_audit"]
    lines = [
        "# v5.2 Microstructure Audit",
        "",
        f"- schema_version: {report['schema_version']}",
        f"- provider_evidence_status: {report['provider_evidence_status']}",
        f"- supported_claim_class: {report['supported_claim_class']}",
        f"- trades_claim_support: {report['trades_claim_support']}",
        f"- l2_claim_support: {report['l2_claim_support']}",
        "",
        "## Quality Counts",
        "",
        "| Metric | Count |",
        "|--------|-------|",
    ]
    for key in [
        "timestamp_disorder_count",
        "duplicate_row_count",
        "crossed_book_count",
        "locked_book_count",
        "spread_warning_count",
        "trade_contract_failure_count",
        "l2_contract_failure_count",
    ]:
        lines.append(f"| {key} | {quality[key]} |")
    lines.extend(
        [
            "",
            "## Evidence Gate",
            "",
            "Fixture normalization can be schema-ready while live provider evidence remains blocked.",
            "Phase 121 must consume the JSON verdict fields rather than treating fixture success as live support.",
            "",
        ]
    )
    return "\n".join(lines)


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _fixture_csv_path(fixture_dir: Path, data_type: str) -> Path:
    candidates = [
        fixture_dir / f"{data_type}.csv",
        fixture_dir / "binance" / f"{data_type}.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"missing fixture CSV for {data_type} in {fixture_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--ingest-manifest", type=Path, default=DEFAULT_INGEST_MANIFEST)
    parser.add_argument("--access-blocker", type=Path, default=DEFAULT_ACCESS_BLOCKER)
    parser.add_argument("--source-verdict", type=Path, default=DEFAULT_SOURCE_VERDICT)
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--symbol", default="btcusdt")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    trades_rows = _read_csv_rows(_fixture_csv_path(args.fixture_dir, "trades"))
    l2_rows = _read_csv_rows(_fixture_csv_path(args.fixture_dir, "incremental_book_L2"))
    trades_result = normalize_trades(
        trades_rows,
        exchange=args.exchange,
        symbol=args.symbol,
    )
    l2_result = normalize_l2(l2_rows, exchange=args.exchange, symbol=args.symbol)
    report = build_audit_report(
        trades_result,
        l2_result,
        args.ingest_manifest,
        args.access_blocker,
        args.source_verdict,
    )
    args.report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report, args.report_dir / "audit.json")
    (args.report_dir / "audit.md").write_text(render_audit_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
