"""Validate v5.0 claim cell SEAL frontmatter."""

from __future__ import annotations

from pathlib import Path

import yaml

CLAIM_PATH = Path("docs/v5.0_phase1_b_claim.md")
REQUIRED_KEYS = [
    "threshold_commit",
    "inherits_from",
    "downstream_consumers",
    "requirements_sealed",
]


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    parts = text.split("---", 2)
    assert len(parts) == 3, "Missing YAML frontmatter delimiters"
    return yaml.safe_load(parts[1])


def validate_seal_frontmatter(path: Path) -> list[str]:
    """Return missing required frontmatter keys. Empty list means valid."""
    fm = _frontmatter(path)
    return [key for key in REQUIRED_KEYS if key not in fm]


def test_claim_frontmatter_complete():
    missing = validate_seal_frontmatter(CLAIM_PATH)
    assert missing == [], f"Missing frontmatter keys: {missing}"


def test_requirements_sealed_contains_all_claim_requirements():
    fm = _frontmatter(CLAIM_PATH)
    sealed = fm.get("requirements_sealed", [])
    for req in ["CLAIM-V50-01", "CLAIM-V50-02", "CLAIM-V50-03"]:
        assert req in sealed, f"{req} missing from requirements_sealed"


def test_threshold_commit_is_not_placeholder():
    fm = _frontmatter(CLAIM_PATH)
    value = str(fm.get("threshold_commit", ""))
    assert value
    assert value not in {"PLACEHOLDER", "TBD"}
