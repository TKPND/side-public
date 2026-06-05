import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_risk_contract.py"
RESULT_SCHEMA = ROOT / "risk/contracts/v2/risk_contract_validator_result_v2.schema.json"

RESULT_EXAMPLES = (
    (
        "base_valid",
        "risk/contracts/v2/fixtures/valid/base_valid.json",
        "risk/contracts/v2/result_examples/valid/base_valid_result.json",
        0,
    ),
    (
        "invalid_runtime_surface_scope",
        "risk/contracts/v2/fixtures/invalid/invalid_runtime_surface_scope.json",
        "risk/contracts/v2/result_examples/invalid/invalid_runtime_surface_scope_result.json",
        1,
    ),
)


def run_validator(contract_path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), contract_path],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def load_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def load_result_schema() -> dict:
    return json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("case_id", "contract_path", "example_path", "expected_returncode"),
    RESULT_EXAMPLES,
    ids=[case[0] for case in RESULT_EXAMPLES],
)
def test_v2_result_examples_match_validator_cli_output(
    case_id: str,
    contract_path: str,
    example_path: str,
    expected_returncode: int,
) -> None:
    del case_id

    result = run_validator(contract_path)
    expected_payload = load_json(example_path)

    assert result.returncode == expected_returncode
    assert json.loads(result.stdout) == expected_payload


@pytest.mark.parametrize(
    ("case_id", "contract_path", "example_path", "expected_returncode"),
    RESULT_EXAMPLES,
    ids=[case[0] for case in RESULT_EXAMPLES],
)
def test_v2_result_examples_follow_result_schema_envelope(
    case_id: str,
    contract_path: str,
    example_path: str,
    expected_returncode: int,
) -> None:
    del case_id, expected_returncode

    schema = load_result_schema()
    payload = load_json(example_path)

    assert set(payload) == set(schema["required"])
    assert payload["schema_version"] == schema["properties"]["schema_version"]["const"]
    assert payload["checked_path"] == contract_path
    assert payload["contract_identity"] == {
        "schema_version": "risk_contract.v2",
        "contract_version": "v2",
    }
    assert payload["validated_schema"] == {
        "schema_version": "risk_contract.v2",
        "contract_version": "v2",
        "path": "risk/contracts/v2/risk_contract_v2.schema.json",
    }
    assert payload["dispatch"]["status"] in schema["$defs"]["dispatch"]["properties"]["status"]["enum"]

    if payload["valid"]:
        assert payload["dispatch"] == {"status": "validated", "reason": None}
        assert payload["errors"] == []
    else:
        assert payload["dispatch"]["status"] == "validation_failed"
        assert payload["dispatch"]["reason"] == payload["errors"][0]["code"]
