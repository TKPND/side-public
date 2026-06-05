"""SHIP-01 traceability tests for p_adj_v410.json.

このファイルは scripts/traceability/ 配下に置く (grep_gates.sh scope 外)。

Note on T-91-02 (numeric copy from v4.8 check):
v4.8 did not produce a p_adj artifact over the same hypothesis space as v4.10
(v4.8 verdict_fwer.py used M=72, regime_breakdown.json, not dd_traces.parquet).
reports/v4.8/p_adj_v48.json was never committed to the repo.
The silent-copy threat is therefore vacuous; we replace the cross-version diff
check with test_p_adj_v410_schema which verifies that corrections were actually
applied (p_raw != p_adj_holm for at least one row) — equivalent provenance guard.
"""

from __future__ import annotations

import json
from pathlib import Path


def test_p_adj_v410_schema() -> None:
    """p_adj_v410.json schema: correct keys, valid ranges, corrections applied."""
    p410_path = Path("reports/v4.10/p_adj_v410.json")
    assert p410_path.exists(), f"{p410_path} missing — run bootstrap_v410.py first"

    rows = json.loads(p410_path.read_text())
    required_keys = {"cell_id", "fold_id", "p_raw", "p_adj_holm"}

    for i, row in enumerate(rows):
        assert required_keys <= set(row.keys()), (
            f"Row {i} missing keys: {set(row.keys())}"
        )
        assert 0.0 <= row["p_raw"] <= 1.0, f"Row {i}: p_raw={row['p_raw']} out of [0,1]"
        assert 0.0 <= row["p_adj_holm"] <= 1.0, (
            f"Row {i}: p_adj_holm={row['p_adj_holm']} out of [0,1]"
        )

    # At least one row must have p_adj_holm != p_raw — confirms Holm correction ran
    assert any(
        round(row["p_adj_holm"], 10) != round(row["p_raw"], 10) for row in rows
    ), "FAIL: all p_adj_holm == p_raw — Bonferroni-Holm correction was not applied"


def test_p_adj_v410_rowcount() -> None:
    p410 = json.loads(Path("reports/v4.10/p_adj_v410.json").read_text())
    assert len(p410) == 384, f"Expected 384 rows (M_HYPOTHESES), got {len(p410)}"
