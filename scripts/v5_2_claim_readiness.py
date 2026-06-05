"""Build the v5.2 source-level claim-readiness verdict."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "v5.2.claim-readiness.1"
DEFAULT_REPORT_DIR = Path("reports/v5.2/claim_readiness")
DEFAULT_SOURCE_VERDICT = Path("reports/v5.2/source_selection/source_verdict.json")
DEFAULT_INGEST_MANIFEST = Path("reports/v5.2/ingestion_smoke/manifest.json")
DEFAULT_ACCESS_BLOCKER = Path("reports/v5.2/ingestion_smoke/access_blocker.json")
DEFAULT_MICROSTRUCTURE_AUDIT = Path("reports/v5.2/microstructure_audit/audit.json")
DEFAULT_V5_1_VERDICT = Path("reports/v5.1/phase116/final_verdict.json")

REQUIREMENTS_ADDRESSED = [
    "CLAIM52-V52-01",
    "CLAIM52-V52-02",
    "CLAIM52-V52-03",
]
EVIDENCE_REFS = [
    DEFAULT_SOURCE_VERDICT.as_posix(),
    DEFAULT_INGEST_MANIFEST.as_posix(),
    DEFAULT_ACCESS_BLOCKER.as_posix(),
    DEFAULT_MICROSTRUCTURE_AUDIT.as_posix(),
    DEFAULT_V5_1_VERDICT.as_posix(),
]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_json(report: dict[str, Any], path: Path) -> None:
    """Write canonical JSON with deterministic key ordering."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def _provider_data_types(manifest: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(request["data_type"])
            for request in manifest.get("requests", [])
            if request.get("data_type")
        }
    )


def _provider_symbols(manifest: dict[str, Any]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    symbols: list[dict[str, str]] = []
    for request in manifest.get("requests", []):
        exchange = str(request.get("exchange") or "")
        symbol = str(request.get("symbol") or "")
        data_type = str(request.get("data_type") or "")
        key = (exchange, symbol, data_type)
        if not exchange or not symbol or not data_type or key in seen:
            continue
        seen.add(key)
        symbols.append(
            {
                "provider_exchange": exchange,
                "provider_symbol": symbol,
                "data_type": data_type,
            }
        )
    return sorted(
        symbols,
        key=lambda item: (
            item["provider_exchange"],
            item["provider_symbol"],
            item["data_type"],
        ),
    )


def _blocked_contract_candidate(
    source_verdict: dict[str, Any],
    ingest_manifest: dict[str, Any],
) -> dict[str, Any]:
    return {
        "selected_source_id": "tardis_historical_spot",
        "sealed_for_downstream_claim": False,
        "downstream_consumable": False,
        "not_consumable_reason": "provider_evidence_blocked",
        "provider_data_types": _provider_data_types(ingest_manifest),
        "provider_symbols": _provider_symbols(ingest_manifest),
        "selected_symbols": source_verdict.get("selected_symbols", []),
        "blocker_evidence": {
            "ingest_manifest": DEFAULT_INGEST_MANIFEST.as_posix(),
            "access_blocker": DEFAULT_ACCESS_BLOCKER.as_posix(),
            "microstructure_audit": DEFAULT_MICROSTRUCTURE_AUDIT.as_posix(),
        },
    }


def _null_ship_reasons(access_blocker: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "category": "source",
            "code": "source_live_raw_evidence_absent",
            "detail": "No live/raw provider rows, timestamp ranges, or raw hashes are available for downstream claim support.",
        },
        {
            "category": "access",
            "code": "access_blocked_provider_http_400",
            "detail": "; ".join(access_blocker.get("blocker_reasons", []))
            or "Provider access is blocked.",
        },
        {
            "category": "semantics",
            "code": "semantics_schema_ready_but_evidence_blocked",
            "detail": "Fixture-backed schema readiness is not live/raw source support.",
        },
    ]


def build_claim_readiness_verdict(
    source_verdict_path: Path,
    ingest_manifest_path: Path,
    access_blocker_path: Path,
    microstructure_audit_path: Path,
    v5_1_verdict_path: Path,
) -> dict[str, Any]:
    """Build the Phase 121 null-ship verdict from existing evidence only."""
    source_verdict = _read_json(source_verdict_path)
    ingest_manifest = _read_json(ingest_manifest_path)
    access_blocker = _read_json(access_blocker_path)
    microstructure_audit = _read_json(microstructure_audit_path)
    v5_1_verdict = _read_json(v5_1_verdict_path)

    provider_evidence_status = microstructure_audit.get("provider_evidence_status")
    supported_claim_class = microstructure_audit.get("supported_claim_class")

    return {
        "schema_version": SCHEMA_VERSION,
        "phase": 121,
        "verdict": "null_ship",
        "ship_verdict": False,
        "source_readiness": "source_level_null_ship",
        "downstream_claim_ready": False,
        "sealed_for_downstream_claim": False,
        "selected_source_id": "tardis_historical_spot",
        "provider_evidence_status": provider_evidence_status,
        "supported_claim_class": supported_claim_class,
        "requirements_addressed": REQUIREMENTS_ADDRESSED,
        "null_ship_reasons": _null_ship_reasons(access_blocker),
        "blocked_contract_candidate": _blocked_contract_candidate(
            source_verdict,
            ingest_manifest,
        ),
        "evidence_refs": EVIDENCE_REFS,
        "source_inputs": {
            "source_verdict": source_verdict_path.as_posix(),
            "ingest_manifest": ingest_manifest_path.as_posix(),
            "access_blocker": access_blocker_path.as_posix(),
            "microstructure_audit": microstructure_audit_path.as_posix(),
            "v5_1_verdict": v5_1_verdict_path.as_posix(),
        },
        "v5_1_preserved": {
            "ship_verdict": v5_1_verdict.get("ship_verdict"),
            "verdict": v5_1_verdict.get("verdict"),
            "ref": DEFAULT_V5_1_VERDICT.as_posix(),
        },
    }


def render_verdict_markdown(report: dict[str, Any]) -> str:
    """Render the Phase 121 verdict as human-readable Markdown."""
    lines = [
        "# v5.2 Claim Readiness Verdict",
        "",
        f"- schema_version: {report['schema_version']}",
        f"- verdict: {report['verdict']}",
        f"- ship_verdict: {str(report['ship_verdict']).lower()}",
        f"- source_readiness: {report['source_readiness']}",
        f"- downstream_claim_ready: {str(report['downstream_claim_ready']).lower()}",
        f"- sealed_for_downstream_claim: {str(report['sealed_for_downstream_claim']).lower()}",
        f"- provider_evidence_status: {report['provider_evidence_status']}",
        f"- supported_claim_class: {report['supported_claim_class']}",
        "",
        "## Null-Ship Reasons",
        "",
    ]
    for reason in report["null_ship_reasons"]:
        lines.append(
            f"- {reason['category']}: {reason['code']} — {reason['detail']}"
        )
    lines.extend(
        [
            "",
            "## Blocked Contract Candidate",
            "",
            f"- selected_source_id: {report['blocked_contract_candidate']['selected_source_id']}",
            "- sealed_for_downstream_claim: false",
            "- downstream_consumable: false",
            f"- not_consumable_reason: {report['blocked_contract_candidate']['not_consumable_reason']}",
            "",
            "## Evidence References",
            "",
        ]
    )
    for ref in report["evidence_refs"]:
        lines.append(f"- {ref}")
    lines.extend(
        [
            "",
            "## v5.1 Preservation",
            "",
            f"- ref: {report['v5_1_preserved']['ref']}",
            f"- verdict: {report['v5_1_preserved']['verdict']}",
            f"- ship_verdict: {str(report['v5_1_preserved']['ship_verdict']).lower()}",
            "- v5.1 remains null_ship and read-only; top-of-book proxy thresholds are not reopened.",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-verdict", type=Path, default=DEFAULT_SOURCE_VERDICT)
    parser.add_argument("--ingest-manifest", type=Path, default=DEFAULT_INGEST_MANIFEST)
    parser.add_argument("--access-blocker", type=Path, default=DEFAULT_ACCESS_BLOCKER)
    parser.add_argument(
        "--microstructure-audit",
        type=Path,
        default=DEFAULT_MICROSTRUCTURE_AUDIT,
    )
    parser.add_argument("--v5-1-verdict", type=Path, default=DEFAULT_V5_1_VERDICT)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_claim_readiness_verdict(
        args.source_verdict,
        args.ingest_manifest,
        args.access_blocker,
        args.microstructure_audit,
        args.v5_1_verdict,
    )
    args.report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report, args.report_dir / "verdict.json")
    (args.report_dir / "verdict.md").write_text(render_verdict_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
