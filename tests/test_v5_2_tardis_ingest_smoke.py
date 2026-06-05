"""Tests for the v5.2 Tardis ingestion smoke helper."""

from __future__ import annotations

import gzip
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "scripts")
import v5_2_tardis_ingest_smoke as smoke  # noqa: E402


def _gzip_csv(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"))


def test_build_tardis_dataset_url() -> None:
    url = smoke.build_tardis_dataset_url(
        exchange="binance",
        data_type="trades",
        sample_date=date(2026, 5, 1),
        symbol="btcusdt",
    )

    assert (
        url
        == "https://datasets.tardis.dev/v1/binance/trades/2026/05/01/btcusdt.csv.gz"
    )


def test_manifest_records_hash_row_count_and_timestamp_range() -> None:
    raw_bytes = _gzip_csv(
        "\n".join(
            [
                "timestamp,local_timestamp,symbol,side,price,amount",
                "2026-05-01T00:00:00.000Z,2026-05-01T00:00:00.100Z,btcusdt,buy,60000,0.1",
                "2026-05-01T00:00:01.000Z,2026-05-01T00:00:01.100Z,btcusdt,sell,60001,0.2",
                "",
            ]
        )
    )
    scan = smoke.scan_gzip_csv(raw_bytes)
    manifest = smoke.build_manifest(
        source_verdict_path=smoke.DEFAULT_SOURCE_VERDICT,
        requests=[
            smoke.build_request_manifest(
                exchange="binance",
                data_type="trades",
                sample_date=date(2026, 5, 1),
                symbol="btcusdt",
                request_url=smoke.build_tardis_dataset_url(
                    exchange="binance",
                    data_type="trades",
                    sample_date=date(2026, 5, 1),
                    symbol="btcusdt",
                ),
                raw_path=smoke.raw_output_path(
                    smoke.DEFAULT_OUT_DIR,
                    "binance",
                    "trades",
                    date(2026, 5, 1),
                    "btcusdt",
                ),
                raw_bytes=raw_bytes,
                scan=scan,
                status="ok",
            )
        ],
    )

    request = manifest["requests"][0]
    assert scan["row_count"] == 2
    assert scan["min_timestamp"] == "2026-05-01T00:00:00.000Z"
    assert scan["max_timestamp"] == "2026-05-01T00:00:01.000Z"
    assert len(request["sha256"]) == 64
    assert request["sha256"].islower()
    assert request["row_count"] == 2
    assert request["min_timestamp"] == "2026-05-01T00:00:00.000Z"
    assert request["max_timestamp"] == "2026-05-01T00:00:01.000Z"


def test_manifest_records_phase118_source_verdict() -> None:
    manifest = smoke.build_manifest(source_verdict_path=smoke.DEFAULT_SOURCE_VERDICT)

    assert manifest["schema_version"] == "v5.2.ingestion-smoke.1"
    assert (
        manifest["source_verdict_path"]
        == "reports/v5.2/source_selection/source_verdict.json"
    )
    assert manifest["selected_source_id"] == "tardis_historical_spot"
    assert manifest["selected_provider"] == "Tardis.dev historical spot"
    assert "INGEST-V52-01" in manifest["requirements_addressed"]
    assert "INGEST-V52-02" in manifest["requirements_addressed"]
    assert "INGEST-V52-03" in manifest["requirements_addressed"]


def test_request_manifest_contains_reproducibility_fields() -> None:
    raw_bytes = _gzip_csv(
        "\n".join(
            [
                "timestamp,local_timestamp,symbol,side,price,amount",
                "2026-05-01T00:00:00.000Z,2026-05-01T00:00:00.100Z,ethusdt,buy,3000,1.5",
                "",
            ]
        )
    )
    request = smoke.build_request_manifest(
        exchange="binance",
        data_type="trades",
        sample_date=date(2026, 5, 1),
        symbol="ethusdt",
        request_url=smoke.build_tardis_dataset_url(
            exchange="binance",
            data_type="trades",
            sample_date=date(2026, 5, 1),
            symbol="ethusdt",
        ),
        raw_path=smoke.raw_output_path(
            smoke.DEFAULT_OUT_DIR,
            "binance",
            "trades",
            date(2026, 5, 1),
            "ethusdt",
        ),
        raw_bytes=raw_bytes,
        scan=smoke.scan_gzip_csv(raw_bytes),
        status="ok",
    )

    for field in {
        "exchange",
        "data_type",
        "symbol",
        "sample_date",
        "request_url",
        "raw_path",
        "row_count",
        "min_timestamp",
        "max_timestamp",
        "sha256",
        "status",
    }:
        assert field in request


def test_cli_reads_offline_fixture_and_writes_manifest(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    raw_path = (
        fixture_dir / "binance" / "trades" / "2026-05-01" / "btcusdt.csv.gz"
    )
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(
        _gzip_csv(
            "\n".join(
                [
                    "timestamp,local_timestamp,symbol,side,price,amount",
                    "2026-05-01T00:00:00.000Z,2026-05-01T00:00:00.100Z,btcusdt,buy,60000,0.1",
                    "",
                ]
            )
        )
    )

    exit_code = smoke.main(
        [
            "--sample-date",
            "2026-05-01",
            "--exchange",
            "binance",
            "--data-type",
            "trades",
            "--symbol",
            "btcusdt",
            "--out-dir",
            str(tmp_path / "raw"),
            "--report-dir",
            str(tmp_path / "report"),
            "--offline-fixture-dir",
            str(fixture_dir),
        ]
    )

    assert exit_code == 0
    out_raw = tmp_path / "raw" / "binance" / "trades" / "2026-05-01" / "btcusdt.csv.gz"
    assert out_raw.exists()
    assert (tmp_path / "report" / "manifest.json").exists()


def test_raw_output_path_is_v5_2_scoped() -> None:
    path = smoke.raw_output_path(
        Path("data/v5.2/raw/tardis"),
        "binance",
        "trades",
        date(2026, 5, 1),
        "btcusdt",
    )

    assert (
        path.as_posix()
        == "data/v5.2/raw/tardis/binance/trades/2026-05-01/btcusdt.csv.gz"
    )


def test_reproduce_command_contains_all_request_parameters() -> None:
    command = smoke.build_reproduce_command(
        sample_date=date(2026, 5, 1),
        exchanges=["binance"],
        data_types=["trades", "incremental_book_L2"],
        symbols=["btcusdt", "ethusdt"],
        out_dir=Path("data/v5.2/raw/tardis"),
        report_dir=Path("reports/v5.2/ingestion_smoke"),
        max_rows=10000,
    )

    expected_parts = [
        "rtk uv run python scripts/v5_2_tardis_ingest_smoke.py",
        "--sample-date 2026-05-01",
        "--exchange binance",
        "--data-type trades",
        "--data-type incremental_book_L2",
        "--symbol btcusdt",
        "--symbol ethusdt",
        "--out-dir data/v5.2/raw/tardis",
        "--report-dir reports/v5.2/ingestion_smoke",
    ]
    for part in expected_parts:
        assert part in command


def test_render_manifest_markdown_contains_replay_and_hashes() -> None:
    raw_bytes = _gzip_csv(
        "\n".join(
            [
                "timestamp,local_timestamp,symbol,side,price,amount",
                "2026-05-01T00:00:00.000Z,2026-05-01T00:00:00.100Z,btcusdt,buy,60000,0.1",
                "",
            ]
        )
    )
    request = smoke.build_request_manifest(
        exchange="binance",
        data_type="trades",
        sample_date=date(2026, 5, 1),
        symbol="btcusdt",
        request_url=smoke.build_tardis_dataset_url(
            exchange="binance",
            data_type="trades",
            sample_date=date(2026, 5, 1),
            symbol="btcusdt",
        ),
        raw_path=smoke.raw_output_path(
            smoke.DEFAULT_OUT_DIR,
            "binance",
            "trades",
            date(2026, 5, 1),
            "btcusdt",
        ),
        raw_bytes=raw_bytes,
        scan=smoke.scan_gzip_csv(raw_bytes),
        status="ok",
    )
    manifest = smoke.build_manifest(requests=[request])
    markdown = smoke.render_manifest_markdown(manifest)

    assert "# v5.2 Ingestion Smoke Manifest" in markdown
    assert "## Reproduce Command" in markdown
    assert "## Raw Artifacts" in markdown
    assert request["sha256"] in markdown


def test_access_failure_builds_blocker_without_fake_rows() -> None:
    request = smoke.build_blocked_request_manifest(
        exchange="binance",
        data_type="trades",
        sample_date=date(2026, 5, 1),
        symbol="btcusdt",
        request_url=smoke.build_tardis_dataset_url(
            exchange="binance",
            data_type="trades",
            sample_date=date(2026, 5, 1),
            symbol="btcusdt",
        ),
        raw_path=smoke.raw_output_path(
            smoke.DEFAULT_OUT_DIR,
            "binance",
            "trades",
            date(2026, 5, 1),
            "btcusdt",
        ),
        http_status=403,
        blocker_reason="HTTP 403 Forbidden",
    )

    assert request["status"] == "blocked"
    assert "HTTP 403" in request["blocker_reason"]
    assert request["row_count"] == 0
    assert request["sha256"] is None


def test_access_blocker_schema_records_request_metadata() -> None:
    request = smoke.build_blocked_request_manifest(
        exchange="binance",
        data_type="incremental_book_L2",
        sample_date=date(2026, 5, 1),
        symbol="ethusdt",
        request_url=smoke.build_tardis_dataset_url(
            exchange="binance",
            data_type="incremental_book_L2",
            sample_date=date(2026, 5, 1),
            symbol="ethusdt",
        ),
        raw_path=smoke.raw_output_path(
            smoke.DEFAULT_OUT_DIR,
            "binance",
            "incremental_book_L2",
            date(2026, 5, 1),
            "ethusdt",
        ),
        http_status=403,
        blocker_reason="HTTP 403 Forbidden",
    )
    blocker = smoke.build_access_blocker(
        source_verdict_path=smoke.DEFAULT_SOURCE_VERDICT,
        requests=[request],
        blocker_reasons=["HTTP 403 Forbidden"],
        reproduce_command="rtk uv run python scripts/v5_2_tardis_ingest_smoke.py",
    )

    assert blocker["schema_version"] == "v5.2.ingestion-access-blocker.1"
    assert blocker["phase"] == 119
    assert "INGEST-V52-03" in blocker["requirements_addressed"]
    blocked_request = blocker["blocked_requests"][0]
    assert blocked_request["request_url"].endswith(
        "/binance/incremental_book_L2/2026/05/01/ethusdt.csv.gz"
    )
    assert blocked_request["exchange"] == "binance"
    assert blocked_request["data_type"] == "incremental_book_L2"
    assert blocked_request["symbol"] == "ethusdt"
    assert blocked_request["sample_date"] == "2026-05-01"
    assert blocked_request["http_status"] == 403


def test_empty_gzip_does_not_report_ok_sample() -> None:
    raw_bytes = _gzip_csv("timestamp,local_timestamp,symbol,side,price,amount\n")
    scan = smoke.scan_gzip_csv(raw_bytes)
    status = smoke.classify_request_status(scan)

    assert scan["row_count"] == 0
    assert status in {"empty", "blocked"}
    assert status != "ok"
