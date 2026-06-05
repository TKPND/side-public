"""Source-search guards for frozen risk_contract.v2 public proof fields."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

CONSUMER_AUDIT = Path("reports/v8.4/risk_contract_v2_consumer_contract_audit.md")

BACKTEST_SCAN_SOURCES = (
    Path("rust/side-cli/tests/backtest_cli_test.rs"),
    Path("rust/side-cli/tests/risk_gate_test.rs"),
    Path("scripts/generate_backtest_v2_runtime_adoption_evidence.py"),
    Path("scripts/generate_scan_v2_runtime_adoption_evidence.py"),
)

PAPER_SOURCES = (
    Path("rust/side-cli/tests/paper_cli_test.rs"),
    Path("rust/side-engine/tests/paper_risk_test.rs"),
    Path("scripts/generate_paper_v2_runtime_adoption_evidence.py"),
)

BACKTEST_SCAN_FIELDS = (
    "schema_version",
    "contract_version",
    "validator_result_schema_version",
    "schema_ref",
    "validated_schema_ref",
    "validator",
)

PAPER_FIELDS = (
    "risk_contract_schema_version",
    "risk_contract_version",
    "validator_result_schema_version",
    "validated_schema_ref",
    "validator",
)

FROZEN_VALUES = (
    "risk_contract.v2",
    "v2",
    "risk_contract_validator_result.v2",
    "risk/contracts/v2/risk_contract_v2.schema.json",
    "scripts/validate_risk_contract.py",
)


def read_source(path: Path) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def combined_source(paths: tuple[Path, ...]) -> str:
    return "\n".join(read_source(path) for path in paths)


def assert_literals_present(source: str, literals: tuple[str, ...]) -> None:
    missing = [literal for literal in literals if literal not in source]
    assert not missing, f"missing frozen public proof literals: {missing}"


def test_consumer_audit_freezes_current_public_proof_field_matrix() -> None:
    audit = read_source(CONSUMER_AUDIT)

    assert_literals_present(audit, BACKTEST_SCAN_FIELDS)
    assert_literals_present(audit, PAPER_FIELDS)
    assert_literals_present(audit, FROZEN_VALUES)
    assert "Do not normalize paper to the scan/backtest names in-place" in audit


def test_backtest_and_scan_public_proof_fields_are_source_search_guarded() -> None:
    source = combined_source(BACKTEST_SCAN_SOURCES)

    assert_literals_present(source, BACKTEST_SCAN_FIELDS)
    assert_literals_present(source, FROZEN_VALUES)

    for field in (
        "schema_version",
        "contract_version",
        "validator_result_schema_version",
        "schema_ref",
        "validated_schema_ref",
    ):
        assert f'risk_gate.get("{field}")' in source

    assert 'value["risk_gate"]["schema_version"]' in source
    assert 'first["risk_gate"]["schema_version"]' in source
    assert 'risk_gate["validator"]' in source


def test_paper_public_proof_fields_are_source_search_guarded() -> None:
    source = combined_source(PAPER_SOURCES)

    assert_literals_present(source, PAPER_FIELDS)
    assert_literals_present(source, FROZEN_VALUES)

    for field in PAPER_FIELDS:
        assert f'paper_evidence.get("{field}")' in source or f'evidence["{field}"]' in source

    assert 'evidence.risk_contract_schema_version.as_deref()' in source
    assert 'evidence.validator.as_deref()' in source


def test_public_proof_invariance_guard_reads_current_sources_not_snapshots() -> None:
    guarded_paths = BACKTEST_SCAN_SOURCES + PAPER_SOURCES

    assert all((ROOT / path).is_file() for path in guarded_paths)
    assert all(path.suffix in {".rs", ".py"} for path in guarded_paths)
    assert not any(path.suffix == ".json" for path in guarded_paths)
