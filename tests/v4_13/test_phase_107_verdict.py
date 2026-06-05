"""Phase 107 Wave 0 RED scaffold — verdict_map 純関数テスト.

3 件の RED test:
- test_trivial_branch: trivial=True → A-D は (None, "reject"), E は "live_required"
- test_non_trivial_branch: top-1 軸が "recommended"、他 3 軸が "deprioritized"
- test_tie_break_alphabetical: 全軸同点時、candidate_id alphabetical で top-1 決定

Wave 0 では `from scripts.v4_13.emit_diagnosis_v413 import verdict_map` が
ImportError で fail (TDD RED phase)。Plan 02 で emit_diagnosis_v413.py 実装後 GREEN 化。

NEXTBET-V413-02 検証 RED scaffold。

B2 / D-17 / INTEGRITY-V413-03 invariant 維持。
"""

from __future__ import annotations

import importlib


# Plan 02 で実装される verdict_map signature (interfaces 由来):
#   verdict_map(trivial: bool, first_order: dict)
#       -> list[tuple[str, str, float | None, str]]
# Returns: [(candidate_id, axis_label, score, verdict)]
#   trivial=True:
#       A-D candidates: score=None, verdict="reject"
#       E candidate:    score=None, verdict="live_required"
#   trivial=False:
#       top-1 候補:     verdict="recommended"
#       他:             verdict="deprioritized"
#       E:              verdict="live_required" (paper trade 軸)
#   tie-break: candidate_id alphabetical (A=regime_cuts, B=pair, C=window, D=sizing)


def _load_verdict_map():
    """Plan 02 で実装される emit_diagnosis_v413.verdict_map を import。

    Wave 0 では ImportError で RED (期待状態)。
    """
    # conftest.py が scripts/v4.13 を sys.path に注入済 (dotted dir 対応)
    module = importlib.import_module("emit_diagnosis_v413")
    return getattr(module, "verdict_map")


def test_trivial_branch() -> None:
    """trivial=True 時、5 候補 A-D は reject、E は live_required。"""
    verdict_map = _load_verdict_map()
    first_order = {
        "regime_cuts": None,
        "pair": None,
        "window": None,
        "sizing": None,
    }
    result = verdict_map(trivial=True, first_order=first_order)
    assert isinstance(result, list), f"verdict_map must return list, got {type(result)}"
    assert len(result) == 5, f"expected 5 candidates, got {len(result)}"

    # canonical alphabetical order: A, B, C, D, E
    candidate_ids = [row[0] for row in result]
    assert candidate_ids == ["A", "B", "C", "D", "E"], (
        f"candidate_id order must be alphabetical, got {candidate_ids}"
    )

    # A-D: (cand_id, axis_label, None, "reject")
    for row in result[:4]:
        cand_id, axis_label, score, verdict = row
        assert score is None, (
            f"trivial=True: candidate {cand_id} score must be None, got {score!r}"
        )
        assert verdict == "reject", (
            f"trivial=True: candidate {cand_id} verdict must be 'reject', got {verdict!r}"
        )

    # E: paper_trade axis, live_required
    e_id, e_axis, e_score, e_verdict = result[4]
    assert e_id == "E", f"5th row must be candidate E, got {e_id!r}"
    assert e_axis == "paper_trade", (
        f"E axis_label must be 'paper_trade', got {e_axis!r}"
    )
    assert e_score is None, f"E score must be None (trivial), got {e_score!r}"
    assert e_verdict == "live_required", (
        f"E verdict must be 'live_required', got {e_verdict!r}"
    )


def test_non_trivial_branch(synthetic_nontrivial_ablation_score: dict) -> None:
    """non-trivial 分岐: top-1 (window=0.34) が recommended、他 3 軸 deprioritized、
    E は live_required。
    """
    verdict_map = _load_verdict_map()
    first_order = synthetic_nontrivial_ablation_score["first_order"]
    result = verdict_map(trivial=False, first_order=first_order)
    assert len(result) == 5, f"expected 5 rows, got {len(result)}"

    by_id = {row[0]: row for row in result}
    # window 軸 = candidate C (per CONTEXT D-107-01 mapping A=regime/B=pair/C=window/D=sizing/E=paper)
    top_row = by_id["C"]
    _, _, _, top_verdict = top_row
    assert top_verdict == "recommended", (
        f"top-1 (C=window, score=0.34) verdict must be 'recommended', got {top_verdict!r}"
    )

    # 他 3 軸 (A=regime_cuts / B=pair / D=sizing) は deprioritized
    for cand_id in ["A", "B", "D"]:
        _, _, _, v = by_id[cand_id]
        assert v == "deprioritized", (
            f"non-top axis {cand_id} verdict must be 'deprioritized', got {v!r}"
        )

    # E は依然 live_required (paper trade 軸は score 比較対象外)
    _, _, _, e_verdict = by_id["E"]
    assert e_verdict == "live_required", (
        f"E verdict must remain 'live_required' regardless of trivial flag, got {e_verdict!r}"
    )


def test_tie_break_alphabetical() -> None:
    """全軸同点 (0.5, 0.5, 0.5, 0.5) 時、candidate_id alphabetical で top-1 = A。"""
    verdict_map = _load_verdict_map()
    first_order = {
        "regime_cuts": 0.5,
        "pair": 0.5,
        "window": 0.5,
        "sizing": 0.5,
    }
    result = verdict_map(trivial=False, first_order=first_order)
    by_id = {row[0]: row for row in result}

    # tie-break: alphabetical → A=regime_cuts が top-1
    a_verdict = by_id["A"][3]
    assert a_verdict == "recommended", (
        f"tie-break: A (regime_cuts) must be 'recommended' (alphabetical first), got {a_verdict!r}"
    )

    # B, C, D は deprioritized
    for cand_id in ["B", "C", "D"]:
        v = by_id[cand_id][3]
        assert v == "deprioritized", (
            f"tie-break: {cand_id} must be 'deprioritized', got {v!r}"
        )
