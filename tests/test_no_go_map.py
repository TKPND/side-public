import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_no_go_map.py"
RENDERER = ROOT / "scripts" / "render_no_go_map_md.py"


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def base_entry(**overrides):
    entry = {
        "id": "v5_1_top_of_book_tick_imbalance_short_horizon",
        "family_id": "crypto_top_of_book_imbalance_proxy",
        "entry_type": "signal_hypothesis",
        "hypothesis": "BTCUSD/ETHUSD top-of-book quote imbalance proxy has fee-adjusted short-horizon edge",
        "status": "REJECTED",
        "domain": "crypto_tick",
        "asset_scope": ["BTCUSD", "ETHUSD"],
        "milestones": ["v5.1"],
        "phases": ["113", "114", "115", "116", "117"],
        "data_source": "Dukascopy tick top-of-book quote proxy",
        "source_semantics": "top-of-book quote proxy; not exchange-native L2 depth and not true aggressor trade flow",
        "validation_gate": "IS/OOS, execution-cost PF, Holm FWER, permutation/DSR/KILL",
        "failure_mode": "empty eligible candidate set after cost-adjusted PF/FWER",
        "classification_rationale": "No eligible cells survived execution-cost PF and Holm FWER. This rejects the tested proxy family, not exchange-native imbalance research.",
        "same_form_scope": "Dukascopy top-of-book proxy + BTCUSD/ETHUSD + 1m/3m/5m horizons + execution-cost PF/FWER gates",
        "evidence_artifacts": [
            "reports/v5.1/is_backtest_fwer_summary.json",
            "reports/v5.1/phase116/final_verdict.json",
        ],
        "planning_conditions": [
            "Only revisit with true exchange-native trade tape or L2 depth evidence",
        ],
    }
    entry.update(overrides)
    return entry


def base_map(entries):
    return {
        "schema_version": "no_go_map.v1",
        "project": "side",
        "scope": "v4.x through v5.2",
        "generated_from": {
            "milestones": ["v4.x", "v5.0", "v5.1", "v5.2"],
            "as_of": "2026-05-01",
        },
        "entries": entries,
    }


def run_validator(json_path: Path, md_path: Path | None = None):
    cmd = [sys.executable, str(VALIDATOR), str(json_path)]
    if md_path is not None:
        cmd.append(str(md_path))
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)


def write_markdown_counts(path: Path, status_rows: list[str], domain_rows: list[str]) -> None:
    path.write_text(
        "\n".join(
            [
                "# No-Go Map",
                "",
                "This map is a warning reference, not a hard gate.",
                "",
                "WEAK means weak or incomplete evidence, not weak alpha.",
                "",
                "## Status Counts",
                "",
                "| Status | Count |",
                "| --- | --- |",
                *status_rows,
                "",
                "## Domain Counts",
                "",
                "| Domain | Count |",
                "| --- | --- |",
                *domain_rows,
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_validator_accepts_valid_map(tmp_path):
    path = tmp_path / "no_go_map.json"
    write_json(path, base_map([base_entry()]))

    result = run_validator(path)

    assert result.returncode == 0, result.stderr
    assert "validated 1 entries" in result.stdout


def test_validator_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "no_go_map.json"
    write_json(path, base_map([base_entry(), base_entry()]))

    result = run_validator(path)

    assert result.returncode == 1
    assert "duplicate id" in result.stderr


def test_validator_requires_same_form_scope_for_rejected(tmp_path):
    path = tmp_path / "no_go_map.json"
    write_json(path, base_map([base_entry(same_form_scope="")]))

    result = run_validator(path)

    assert result.returncode == 1
    assert "same_form_scope" in result.stderr


def test_validator_requires_blocker_type_for_blocked(tmp_path):
    path = tmp_path / "no_go_map.json"
    write_json(
        path,
        base_map(
            [
                base_entry(
                    id="v5_2_tardis_source_readiness",
                    family_id="exchange_native_source_readiness",
                    entry_type="source_readiness",
                    status="BLOCKED",
                    failure_mode="bounded provider request returned HTTP 400",
                    same_form_scope="",
                )
            ]
        ),
    )

    result = run_validator(path)

    assert result.returncode == 1
    assert "blocker_type" in result.stderr


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"scope": ""}, "scope"),
        ({"generated_from": {"milestones": ["v5.1"], "as_of": ""}}, "generated_from.as_of"),
        ({"generated_from": {"milestones": [], "as_of": "2026-05-01"}}, "generated_from.milestones"),
        ({"generated_from": {"milestones": ["v5.1", ""], "as_of": "2026-05-01"}}, "generated_from.milestones"),
    ],
)
def test_validator_rejects_invalid_top_level_shape(tmp_path, override, expected):
    path = tmp_path / "no_go_map.json"
    payload = base_map([base_entry()])
    payload.update(override)
    write_json(path, payload)

    result = run_validator(path)

    assert result.returncode == 1
    assert expected in result.stderr


def test_validator_rejects_duplicate_markdown_count_rows(tmp_path):
    json_path = tmp_path / "no_go_map.json"
    md_path = tmp_path / "no_go_map.md"
    write_json(json_path, base_map([base_entry()]))
    write_markdown_counts(
        md_path,
        status_rows=["| REJECTED | 1 |", "| REJECTED | 1 |"],
        domain_rows=["| crypto_tick | 1 |"],
    )

    result = run_validator(json_path, md_path)

    assert result.returncode == 1
    assert "duplicate" in result.stderr


def test_validator_rejects_malformed_markdown_count_rows(tmp_path):
    json_path = tmp_path / "no_go_map.json"
    md_path = tmp_path / "no_go_map.md"
    write_json(json_path, base_map([base_entry()]))
    write_markdown_counts(
        md_path,
        status_rows=["| REJECTED | 1 | extra"],
        domain_rows=["| crypto_tick | 1 |"],
    )

    result = run_validator(json_path, md_path)

    assert result.returncode == 1
    assert "malformed" in result.stderr


@pytest.mark.parametrize("domain", ["crypto|tick", "crypto\ntick", "crypto\rtick"])
def test_validator_rejects_domain_values_that_break_markdown_tables(tmp_path, domain):
    path = tmp_path / "no_go_map.json"
    write_json(path, base_map([base_entry(domain=domain)]))

    result = run_validator(path)

    assert result.returncode == 1
    assert "domain" in result.stderr
    assert "Markdown table" in result.stderr


def test_renderer_outputs_counts_and_weak_definition(tmp_path):
    json_path = tmp_path / "no_go_map.json"
    md_path = tmp_path / "no_go_map.md"
    write_json(
        json_path,
        base_map(
            [
                base_entry(),
                base_entry(
                    id="v5_crypto_pre_announcement_event_window",
                    family_id="crypto_pre_announcement_event_window",
                    status="OPEN",
                    hypothesis="BTCUSD crypto pre-announcement event windows may have short-horizon edge",
                    domain="crypto_event",
                    milestones=["v5.0", "v5.1"],
                    phases=[],
                    data_source="Dukascopy tick data or future exchange-native source",
                    source_semantics="not directly validated in v5.0-v5.2",
                    validation_gate="not yet run",
                    failure_mode="not directly tested",
                    classification_rationale="The idea was deferred rather than validated. It remains open only if event-count power and source semantics are established before testing.",
                    same_form_scope="",
                    evidence_artifacts=[".planning/PROJECT.md"],
                    planning_conditions=["Run event-count power analysis before signal implementation"],
                ),
            ]
        ),
    )

    rendered = subprocess.run(
        [sys.executable, str(RENDERER), str(json_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    md_path.write_text(rendered.stdout, encoding="utf-8")

    text = md_path.read_text(encoding="utf-8")
    assert "| REJECTED | 1 |" in text
    assert "| OPEN | 1 |" in text
    assert "WEAK means weak or incomplete evidence" in text

    validation = run_validator(json_path, md_path)
    assert validation.returncode == 0, validation.stderr


def test_validator_rejects_markdown_missing_rendered_entry_section(tmp_path):
    json_path = tmp_path / "no_go_map.json"
    md_path = tmp_path / "no_go_map.md"
    write_json(json_path, base_map([base_entry()]))

    rendered = subprocess.run(
        [sys.executable, str(RENDERER), str(json_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    md_path.write_text(rendered.stdout.split("## REJECTED\n\n", maxsplit=1)[0], encoding="utf-8")

    validation = run_validator(json_path, md_path)

    assert validation.returncode == 1
    assert "markdown" in validation.stderr
    assert "renderer" in validation.stderr


def test_renderer_outputs_required_sections_and_entry_fields(tmp_path):
    json_path = tmp_path / "input_map.json"
    write_json(
        json_path,
        base_map(
            [
                base_entry(
                    id="weak_alpha_family",
                    status="WEAK",
                    domain="z_domain",
                    phases=[],
                    same_form_scope="",
                ),
                base_entry(
                    id="blocked_source_family",
                    entry_type="source_readiness",
                    status="BLOCKED",
                    domain="a_domain",
                    same_form_scope="",
                    blocker_type="access",
                ),
                base_entry(
                    id="open_event_family",
                    status="OPEN",
                    domain="m_domain",
                    phases=[],
                    same_form_scope="",
                ),
                base_entry(id="rejected_alpha_family", domain="m_domain"),
            ]
        ),
    )

    rendered = subprocess.run(
        [sys.executable, str(RENDERER), str(json_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert rendered.returncode == 0, rendered.stderr
    text = rendered.stdout
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert text.startswith("# Side No-Go Map\n")
    assert "Schema version: `no_go_map.v1`" in text
    assert "Scope: v4.x through v5.2" in text
    assert "Generated from as of: 2026-05-01" in text
    assert "warning reference, not a hard gate" in text
    assert "JSON source: `data/no_go_map/no_go_map_v1.json`" in text
    assert "## Status Meanings" in text
    assert "WEAK means weak or incomplete evidence, not weak alpha." in text
    assert "| Status | Count |" in text
    assert text.index("| REJECTED | 1 |") < text.index("| WEAK | 1 |") < text.index("| BLOCKED | 1 |") < text.index("| OPEN | 1 |")
    assert text.index("| a_domain | 1 |") < text.index("| m_domain | 2 |") < text.index("| z_domain | 1 |")
    assert text.index("## REJECTED") < text.index("## WEAK") < text.index("## BLOCKED") < text.index("## OPEN")
    assert "- Family: crypto_top_of_book_imbalance_proxy" in text
    assert "- Type: signal_hypothesis" in text
    assert "- Status: REJECTED" in text
    assert "- Domain: m_domain" in text
    assert "- Hypothesis: BTCUSD/ETHUSD top-of-book quote imbalance proxy has fee-adjusted short-horizon edge" in text
    assert "- Asset scope: BTCUSD, ETHUSD" in text
    assert "- Milestones: v5.1" in text
    assert "- Phases: none" in text
    assert "- Data source: Dukascopy tick top-of-book quote proxy" in text
    assert "- Source semantics: top-of-book quote proxy; not exchange-native L2 depth and not true aggressor trade flow" in text
    assert "- Validation gate: IS/OOS, execution-cost PF, Holm FWER, permutation/DSR/KILL" in text
    assert "- Failure mode: empty eligible candidate set after cost-adjusted PF/FWER" in text
    assert "- Classification rationale: No eligible cells survived execution-cost PF and Holm FWER. This rejects the tested proxy family, not exchange-native imbalance research." in text
    assert "- Same-form scope: Dukascopy top-of-book proxy + BTCUSD/ETHUSD + 1m/3m/5m horizons + execution-cost PF/FWER gates" in text
    assert "- Blocker type: access" in text
    assert "- Evidence artifacts:" in text
    assert "  - reports/v5.1/is_backtest_fwer_summary.json" in text
    assert "- Planning conditions:" in text
    assert "  - Only revisit with true exchange-native trade tape or L2 depth evidence" in text


def test_renderer_bad_usage_returns_2():
    rendered = subprocess.run(
        [sys.executable, str(RENDERER)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert rendered.returncode == 2
    assert "usage:" in rendered.stderr
