"""Validate v5.1 tick imbalance claim SEAL frontmatter and locked prose."""

from __future__ import annotations

from pathlib import Path

import yaml

CLAIM_PATH = Path("docs/v5.1_tick_imbalance_claim.md")

REQUIRED_KEYS = [
    "seal_evidence",
    "downstream_consumers",
    "requirements_sealed",
    "score_families",
    "lookbacks",
    "thresholds",
    "directions",
    "horizons",
    "fwer_denominator",
    "permutation_seed",
    "dsr_n_trials",
]

PHASE_113_REQUIREMENTS = [
    "DATA-V51-01",
    "DATA-V51-02",
    "DATA-V51-03",
    "CLAIM-V51-01",
    "CLAIM-V51-02",
    "CLAIM-V51-03",
]


def _claim_text() -> str:
    return CLAIM_PATH.read_text()


def _frontmatter() -> dict:
    text = _claim_text()
    parts = text.split("---", 2)
    assert len(parts) == 3, "Missing YAML frontmatter delimiters"
    return yaml.safe_load(parts[1])


def test_required_frontmatter_keys_exist() -> None:
    fm = _frontmatter()
    missing = [key for key in REQUIRED_KEYS if key not in fm]
    assert missing == []


def test_all_phase_113_requirements_are_sealed() -> None:
    sealed = _frontmatter()["requirements_sealed"]
    for req in PHASE_113_REQUIREMENTS:
        assert req in sealed


def test_fwer_denominator_is_full_cartesian_family() -> None:
    fm = _frontmatter()
    assert fm["fwer_denominator"] == 216
    assert fm["dsr_n_trials"] == 216
    assert fm["permutation_seed"] == 515113
    text = _claim_text()
    assert (
        "2 pairs x 2 score families x 3 lookbacks x 3 thresholds x 2 directions x 3 horizons = 216 hypotheses"
        in text
    )


def test_score_family_and_grid_literals_are_locked() -> None:
    fm = _frontmatter()
    assert fm["score_families"] == [
        "volume_ratio_imbalance",
        "mid_price_tick_direction_imbalance",
    ]
    assert fm["lookbacks"] == ["30s", "60s", "300s"]
    assert fm["thresholds"] == ["p80", "p90", "p95"]
    assert fm["directions"] == ["mean_reversion", "momentum"]
    assert fm["horizons"] == ["1m", "3m", "5m"]


def test_no_l2_or_aggressor_claims() -> None:
    text = _claim_text()
    assert "top-of-book quote imbalance proxy" in text
    assert "No exchange-native L2 order book depth is claimed." in text
    assert "No true aggressor trade flow is claimed." in text


def test_cost_first_language_is_locked() -> None:
    text = _claim_text()
    assert "Gross PF is diagnostic only." in text
    assert "Execution costs are deducted before ship metrics." in text


def test_formulas_are_locked() -> None:
    text = _claim_text()
    assert (
        "volume_ratio_imbalance = (bidVolume - askVolume) / (bidVolume + askVolume)"
        in text
    )
    assert (
        "mid_price_tick_direction_imbalance = (up_mid_ticks - down_mid_ticks) / total_mid_ticks"
        in text
    )
