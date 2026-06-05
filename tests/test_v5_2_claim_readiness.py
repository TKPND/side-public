"""Tests for the v5.2 claim-readiness verdict helper."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "scripts")
import v5_2_claim_readiness as readiness  # noqa: E402


def _write_json(path: Path, content: str) -> None:
    path.write_text(content + "\n")


def _fixture_paths(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    source_verdict_path = tmp_path / "source_verdict.json"
    ingest_manifest_path = tmp_path / "manifest.json"
    access_blocker_path = tmp_path / "access_blocker.json"
    microstructure_audit_path = tmp_path / "audit.json"
    v5_1_verdict_path = tmp_path / "final_verdict.json"

    _write_json(
        source_verdict_path,
        """{
  "schema_version": "v5.2.source-verdict.1",
  "phase": 118,
  "selected_source_id": "tardis_historical_spot",
  "selected_symbols": [
    {
      "project_pair": "BTCUSD",
      "provider_exchange": "binance",
      "provider_symbol": "btcusdt"
    },
    {
      "project_pair": "ETHUSD",
      "provider_exchange": "binance",
      "provider_symbol": "ethusdt"
    }
  ],
  "v5_1_preserved": {
    "verdict": "null_ship"
  }
}""",
    )
    _write_json(
        ingest_manifest_path,
        """{
  "schema_version": "v5.2.ingestion-smoke.1",
  "status": "blocked",
  "selected_source_id": "tardis_historical_spot",
  "requests": [
    {
      "data_type": "trades",
      "exchange": "binance",
      "symbol": "btcusdt",
      "row_count": 0,
      "sha256": null,
      "status": "blocked"
    },
    {
      "data_type": "incremental_book_L2",
      "exchange": "binance",
      "symbol": "ethusdt",
      "row_count": 0,
      "sha256": null,
      "status": "blocked"
    }
  ]
}""",
    )
    _write_json(
        access_blocker_path,
        """{
  "schema_version": "v5.2.ingestion-access-blocker.1",
  "blocker_reasons": ["HTTP Error 400: Bad Request"],
  "blocked_requests": [
    {
      "data_type": "trades",
      "symbol": "btcusdt",
      "status": "blocked",
      "blocker_reason": "HTTP Error 400: Bad Request"
    }
  ]
}""",
    )
    _write_json(
        microstructure_audit_path,
        """{
  "schema_version": "v5.2.microstructure-audit.1",
  "phase": 120,
  "provider_evidence_status": "blocked",
  "selected_source_id": "tardis_historical_spot",
  "supported_claim_class": "neither_live_supported",
  "trades_claim_support": "schema_ready_evidence_blocked",
  "l2_claim_support": "schema_ready_evidence_blocked"
}""",
    )
    _write_json(
        v5_1_verdict_path,
        """{
  "schema_version": "v5.1.phase116.1",
  "phase": 116,
  "ship_verdict": false,
  "verdict": "null_ship"
}""",
    )
    return (
        source_verdict_path,
        ingest_manifest_path,
        access_blocker_path,
        microstructure_audit_path,
        v5_1_verdict_path,
    )


def _build_report(tmp_path: Path) -> dict:
    return readiness.build_claim_readiness_verdict(*_fixture_paths(tmp_path))


def test_blocked_provider_evidence_forces_null_ship_verdict(tmp_path: Path) -> None:
    report = _build_report(tmp_path)

    assert report["schema_version"] == "v5.2.claim-readiness.1"
    assert report["phase"] == 121
    assert report["verdict"] == "null_ship"
    assert report["ship_verdict"] is False
    assert report["source_readiness"] == "source_level_null_ship"
    assert report["downstream_claim_ready"] is False
    assert report["sealed_for_downstream_claim"] is False
    assert report["requirements_addressed"] == [
        "CLAIM52-V52-01",
        "CLAIM52-V52-02",
        "CLAIM52-V52-03",
    ]


def test_blocked_contract_candidate_is_not_downstream_consumable(
    tmp_path: Path,
) -> None:
    report = _build_report(tmp_path)
    candidate = report["blocked_contract_candidate"]

    assert candidate["selected_source_id"] == "tardis_historical_spot"
    assert candidate["sealed_for_downstream_claim"] is False
    assert candidate["downstream_consumable"] is False
    assert candidate["not_consumable_reason"] == "provider_evidence_blocked"
    assert candidate["provider_data_types"] == ["incremental_book_L2", "trades"]
    assert candidate["blocker_evidence"] == {
        "ingest_manifest": "reports/v5.2/ingestion_smoke/manifest.json",
        "access_blocker": "reports/v5.2/ingestion_smoke/access_blocker.json",
        "microstructure_audit": "reports/v5.2/microstructure_audit/audit.json",
    }


def test_verdict_records_reason_taxonomy_and_evidence_refs(tmp_path: Path) -> None:
    report = _build_report(tmp_path)

    assert {
        (reason["category"], reason["code"])
        for reason in report["null_ship_reasons"]
    } == {
        ("source", "source_live_raw_evidence_absent"),
        ("access", "access_blocked_provider_http_400"),
        ("semantics", "semantics_schema_ready_but_evidence_blocked"),
    }
    assert report["evidence_refs"] == [
        "reports/v5.2/source_selection/source_verdict.json",
        "reports/v5.2/ingestion_smoke/manifest.json",
        "reports/v5.2/ingestion_smoke/access_blocker.json",
        "reports/v5.2/microstructure_audit/audit.json",
        "reports/v5.1/phase116/final_verdict.json",
    ]
    assert report["v5_1_preserved"] == {
        "ship_verdict": False,
        "verdict": "null_ship",
        "ref": "reports/v5.1/phase116/final_verdict.json",
    }


def test_render_verdict_markdown_exposes_null_ship_fields(tmp_path: Path) -> None:
    markdown = readiness.render_verdict_markdown(_build_report(tmp_path))

    assert "source_level_null_ship" in markdown
    assert "access_blocked_provider_http_400" in markdown
    assert "sealed_for_downstream_claim: false" in markdown
