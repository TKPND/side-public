"""Bounded Tardis.dev downloadable CSV ingestion smoke for v5.2."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "v5.2.ingestion-smoke.1"
ACCESS_BLOCKER_SCHEMA_VERSION = "v5.2.ingestion-access-blocker.1"
DEFAULT_SOURCE_VERDICT = Path("reports/v5.2/source_selection/source_verdict.json")
DEFAULT_OUT_DIR = Path("data/v5.2/raw/tardis")
DEFAULT_REPORT_DIR = Path("reports/v5.2/ingestion_smoke")
DEFAULT_SAMPLE_DATE = date(2026, 5, 1)
DEFAULT_EXCHANGES = ("binance",)
DEFAULT_DATA_TYPES = ("trades", "incremental_book_L2")
DEFAULT_SYMBOLS = ("btcusdt", "ethusdt")
DEFAULT_MAX_ROWS = 10000
DEFAULT_API_KEY_ENV = "TARDIS_API_KEY"


def build_tardis_dataset_url(
    exchange: str,
    data_type: str,
    sample_date: date,
    symbol: str,
) -> str:
    """Build a Tardis downloadable CSV dataset URL."""
    return (
        "https://datasets.tardis.dev/v1/"
        f"{exchange}/{data_type}/{sample_date:%Y}/{sample_date:%m}/"
        f"{sample_date:%d}/{symbol}.csv.gz"
    )


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase sha256 hex digest for bytes."""
    return hashlib.sha256(data).hexdigest()


def scan_gzip_csv(
    data: bytes,
    timestamp_column: str = "timestamp",
    max_rows: int | None = None,
) -> dict[str, Any]:
    """Scan gzip CSV bytes for bounded row count and timestamp range."""
    with gzip.open(io.BytesIO(data), mode="rt", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = list(reader.fieldnames or [])
        row_count = 0
        min_timestamp: str | None = None
        max_timestamp: str | None = None
        for row in reader:
            if max_rows is not None and row_count >= max_rows:
                break
            row_count += 1
            timestamp = row.get(timestamp_column)
            if timestamp:
                if min_timestamp is None:
                    min_timestamp = timestamp
                max_timestamp = timestamp

    return {
        "row_count": row_count,
        "min_timestamp": min_timestamp,
        "max_timestamp": max_timestamp,
        "columns": columns,
    }


def raw_output_path(
    out_dir: Path,
    exchange: str,
    data_type: str,
    sample_date: date,
    symbol: str,
) -> Path:
    """Return the canonical v5.2 raw Tardis gzip path."""
    return out_dir / exchange / data_type / sample_date.isoformat() / f"{symbol}.csv.gz"


def _read_source_verdict(source_verdict_path: Path) -> dict[str, Any]:
    return json.loads(source_verdict_path.read_text())


def _display_path(path: Path) -> str:
    return path.as_posix()


def build_request_manifest(
    *,
    exchange: str,
    data_type: str,
    sample_date: date,
    symbol: str,
    request_url: str,
    raw_path: Path,
    raw_bytes: bytes,
    scan: dict[str, Any],
    status: str,
    http_status: int | None = 200,
    blocker_reason: str | None = None,
) -> dict[str, Any]:
    """Build one request entry from raw bytes and scan results."""
    entry = {
        "exchange": exchange,
        "data_type": data_type,
        "symbol": symbol,
        "sample_date": sample_date.isoformat(),
        "request_url": request_url,
        "request_headers": {"authorization": "redacted_or_absent"},
        "raw_path": _display_path(raw_path),
        "row_count": scan["row_count"],
        "min_timestamp": scan["min_timestamp"],
        "max_timestamp": scan["max_timestamp"],
        "columns": scan["columns"],
        "sha256": sha256_bytes(raw_bytes),
        "content_length": len(raw_bytes),
        "http_status": http_status,
        "status": status,
    }
    if blocker_reason:
        entry["blocker_reason"] = blocker_reason
    return entry


def build_blocked_request_manifest(
    *,
    exchange: str,
    data_type: str,
    sample_date: date,
    symbol: str,
    request_url: str,
    raw_path: Path,
    http_status: int | None,
    blocker_reason: str,
) -> dict[str, Any]:
    """Build one blocked request entry without fabricating rows or hashes."""
    return {
        "exchange": exchange,
        "data_type": data_type,
        "symbol": symbol,
        "sample_date": sample_date.isoformat(),
        "request_url": request_url,
        "request_headers": {"authorization": "redacted_or_absent"},
        "raw_path": _display_path(raw_path),
        "row_count": 0,
        "min_timestamp": None,
        "max_timestamp": None,
        "columns": [],
        "sha256": None,
        "content_length": 0,
        "http_status": http_status,
        "status": "blocked",
        "blocker_reason": blocker_reason,
    }


def classify_request_status(scan: dict[str, Any]) -> str:
    """Classify a parsed gzip CSV request without overstating empty data."""
    if scan["row_count"] == 0:
        return "empty"
    return "ok"


def build_manifest(
    *,
    source_verdict_path: Path = DEFAULT_SOURCE_VERDICT,
    requests: list[dict[str, Any]] | None = None,
    reproduce_command: str | None = None,
    raw_storage_root: Path = DEFAULT_OUT_DIR,
    report_root: Path = DEFAULT_REPORT_DIR,
) -> dict[str, Any]:
    """Build the Phase 119 ingestion smoke manifest."""
    verdict = _read_source_verdict(source_verdict_path)
    request_entries = requests or []
    statuses = {request.get("status") for request in request_entries}
    status = "ok"
    if "blocked" in statuses:
        status = "blocked" if statuses == {"blocked"} else "partial"
    elif "empty" in statuses:
        status = "partial"
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": 119,
        "status": status,
        "generated_at": date.today().isoformat(),
        "source_verdict_path": _display_path(source_verdict_path),
        "selected_source_id": verdict["selected_source_id"],
        "selected_provider": verdict.get("selected_provider"),
        "requirements_addressed": [
            "INGEST-V52-01",
            "INGEST-V52-02",
            "INGEST-V52-03",
        ],
        "requests": request_entries,
        "raw_storage_root": _display_path(raw_storage_root),
        "report_root": _display_path(report_root),
        "reproduce_command": reproduce_command
        or "rtk uv run python scripts/v5_2_tardis_ingest_smoke.py",
    }


def build_access_blocker(
    *,
    source_verdict_path: Path,
    requests: list[dict[str, Any]],
    blocker_reasons: list[str],
    reproduce_command: str,
) -> dict[str, Any]:
    """Build machine-readable fail-close access evidence."""
    verdict = _read_source_verdict(source_verdict_path)
    return {
        "schema_version": ACCESS_BLOCKER_SCHEMA_VERSION,
        "phase": 119,
        "generated_at": date.today().isoformat(),
        "source_verdict_path": _display_path(source_verdict_path),
        "selected_source_id": verdict["selected_source_id"],
        "selected_provider": verdict.get("selected_provider"),
        "requirements_addressed": ["INGEST-V52-03"],
        "reproduce_command": reproduce_command,
        "blocker_reasons": blocker_reasons,
        "blocked_requests": requests,
    }


def write_json(report: dict[str, Any], path: Path) -> None:
    """Write canonical JSON with deterministic key ordering."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def _fixture_path(
    offline_fixture_dir: Path,
    exchange: str,
    data_type: str,
    sample_date: date,
    symbol: str,
) -> Path:
    nested = (
        offline_fixture_dir
        / exchange
        / data_type
        / sample_date.isoformat()
        / f"{symbol}.csv.gz"
    )
    if nested.exists():
        return nested
    return offline_fixture_dir / f"{exchange}_{data_type}_{sample_date}_{symbol}.csv.gz"


def _fetch_dataset(
    *,
    request_url: str,
    api_key: str | None,
    offline_path: Path | None,
) -> tuple[bytes, int]:
    if offline_path is not None:
        return offline_path.read_bytes(), 200

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(request_url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read(), int(response.status)


def build_reproduce_command(
    *,
    sample_date: date,
    exchanges: list[str],
    data_types: list[str],
    symbols: list[str],
    out_dir: Path,
    report_dir: Path,
    max_rows: int,
    source_verdict: Path = DEFAULT_SOURCE_VERDICT,
    api_key_env: str = DEFAULT_API_KEY_ENV,
) -> str:
    """Build the command that replays the same bounded request set."""
    parts = [
        "rtk uv run python scripts/v5_2_tardis_ingest_smoke.py",
        f"--sample-date {sample_date.isoformat()}",
    ]
    for exchange in exchanges:
        parts.append(f"--exchange {exchange}")
    for data_type in data_types:
        parts.append(f"--data-type {data_type}")
    for symbol in symbols:
        parts.append(f"--symbol {symbol}")
    parts.extend(
        [
            f"--source-verdict {source_verdict.as_posix()}",
            f"--out-dir {out_dir.as_posix()}",
            f"--report-dir {report_dir.as_posix()}",
            f"--max-rows {max_rows}",
            f"--api-key-env {api_key_env}",
        ]
    )
    return " ".join(parts)


def render_manifest_markdown(manifest: dict[str, Any]) -> str:
    """Render ingestion manifest evidence as Markdown."""
    lines = [
        "# v5.2 Ingestion Smoke Manifest",
        "",
        f"- schema_version: {manifest['schema_version']}",
        f"- status: {manifest['status']}",
        f"- generated_at: {manifest['generated_at']}",
        f"- selected_source_id: {manifest['selected_source_id']}",
        f"- selected_provider: {manifest['selected_provider']}",
        f"- raw_storage_root: {manifest['raw_storage_root']}",
        f"- report_root: {manifest['report_root']}",
        "",
        "## Reproduce Command",
        "",
        "```bash",
        manifest["reproduce_command"],
        "```",
        "",
        "## Raw Artifacts",
        "",
        "| Exchange | Data Type | Symbol | Date | Status | Rows | Timestamp Range | sha256 | Raw Path |",
        "|----------|-----------|--------|------|--------|------|-----------------|--------|----------|",
    ]
    for request in manifest["requests"]:
        timestamp_range = f"{request['min_timestamp']} -> {request['max_timestamp']}"
        lines.append(
            "| {exchange} | {data_type} | {symbol} | {sample_date} | {status} | {row_count} | {timestamp_range} | {sha256} | {raw_path} |".format(
                exchange=request["exchange"],
                data_type=request["data_type"],
                symbol=request["symbol"],
                sample_date=request["sample_date"],
                status=request["status"],
                row_count=request["row_count"],
                timestamp_range=timestamp_range,
                sha256=request["sha256"],
                raw_path=request["raw_path"],
            )
        )

    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-date",
        type=date.fromisoformat,
        default=DEFAULT_SAMPLE_DATE,
    )
    parser.add_argument("--exchange", action="append", default=None)
    parser.add_argument("--data-type", action="append", default=None)
    parser.add_argument("--symbol", action="append", default=None)
    parser.add_argument("--source-verdict", type=Path, default=DEFAULT_SOURCE_VERDICT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--offline-fixture-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.exchange = args.exchange or list(DEFAULT_EXCHANGES)
    args.data_type = args.data_type or list(DEFAULT_DATA_TYPES)
    args.symbol = args.symbol or list(DEFAULT_SYMBOLS)
    api_key = os.environ.get(args.api_key_env)
    requests: list[dict[str, Any]] = []

    for exchange in args.exchange:
        for data_type in args.data_type:
            for symbol in args.symbol:
                request_url = build_tardis_dataset_url(
                    exchange=exchange,
                    data_type=data_type,
                    sample_date=args.sample_date,
                    symbol=symbol,
                )
                raw_path = raw_output_path(
                    args.out_dir,
                    exchange,
                    data_type,
                    args.sample_date,
                    symbol,
                )
                offline_path = (
                    _fixture_path(
                        args.offline_fixture_dir,
                        exchange,
                        data_type,
                        args.sample_date,
                        symbol,
                    )
                    if args.offline_fixture_dir is not None
                    else None
                )
                try:
                    raw_bytes, http_status = _fetch_dataset(
                        request_url=request_url,
                        api_key=api_key,
                        offline_path=offline_path,
                    )
                    scan = scan_gzip_csv(raw_bytes, max_rows=args.max_rows)
                    status = classify_request_status(scan)
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_path.write_bytes(raw_bytes)
                    requests.append(
                        build_request_manifest(
                            exchange=exchange,
                            data_type=data_type,
                            sample_date=args.sample_date,
                            symbol=symbol,
                            request_url=request_url,
                            raw_path=raw_path,
                            raw_bytes=raw_bytes,
                            scan=scan,
                            status=status,
                            http_status=http_status,
                            blocker_reason=(
                                "empty gzip CSV returned by provider"
                                if status == "empty"
                                else None
                            ),
                        )
                    )
                except (OSError, EOFError, csv.Error, urllib.error.HTTPError) as exc:
                    requests.append(
                        build_blocked_request_manifest(
                            exchange=exchange,
                            data_type=data_type,
                            sample_date=args.sample_date,
                            symbol=symbol,
                            request_url=request_url,
                            raw_path=raw_path,
                            http_status=getattr(exc, "code", None),
                            blocker_reason=str(exc),
                        )
                    )

    reproduce_command = build_reproduce_command(
        sample_date=args.sample_date,
        exchanges=args.exchange,
        data_types=args.data_type,
        symbols=args.symbol,
        source_verdict=args.source_verdict,
        out_dir=args.out_dir,
        report_dir=args.report_dir,
        max_rows=args.max_rows,
        api_key_env=args.api_key_env,
    )
    manifest = build_manifest(
        source_verdict_path=args.source_verdict,
        requests=requests,
        reproduce_command=reproduce_command,
        raw_storage_root=args.out_dir,
        report_root=args.report_dir,
    )
    write_json(manifest, args.report_dir / "manifest.json")
    (args.report_dir / "manifest.md").write_text(render_manifest_markdown(manifest))
    blocked_requests = [
        request for request in requests if request["status"] == "blocked"
    ]
    if blocked_requests:
        write_json(
            build_access_blocker(
                source_verdict_path=args.source_verdict,
                requests=blocked_requests,
                blocker_reasons=sorted(
                    {request["blocker_reason"] for request in blocked_requests}
                ),
                reproduce_command=reproduce_command,
            ),
            args.report_dir / "access_blocker.json",
        )
        return 3 if manifest["status"] == "blocked" else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
