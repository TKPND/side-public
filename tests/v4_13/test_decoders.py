"""decoder unit test: milestone × cell_id format 4 ケース + negative path.

Wave 0 RED state: scripts/v4.13/diagnosis_decoders.py が未作成のため
`pytest.importorskip("diagnosis_decoders")` で skip。Wave 1 で decoder 実装後に GREEN 化。
"""

from __future__ import annotations

import pytest


def test_decoder_module_importable():
    """Wave 1 で diagnosis_decoders.py が作られるまで RED でよい (RED state confirmed)."""
    pytest.importorskip("diagnosis_decoders")


def test_parse_cell_id_v411():
    diagnosis_decoders = pytest.importorskip("diagnosis_decoders")
    result = diagnosis_decoders.parse_cell_id("v4.11", "0-60m_x_HIGH")
    assert result["window"] == "0-60m"
    assert result["regime_cuts"] == "VOL_HIGH"


def test_parse_cell_id_unknown_milestone_raises():
    diagnosis_decoders = pytest.importorskip("diagnosis_decoders")
    with pytest.raises(ValueError, match="unknown milestone"):
        diagnosis_decoders.parse_cell_id("v9.99", "anything")
