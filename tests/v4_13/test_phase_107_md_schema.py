"""Phase 107 Wave 0 RED scaffold — diagnosis_v413.md schema tests.

D-107-03 frontmatter spec (5 keys 完全一致) + 4 H2 sections + 1 page limit
+ close pathway prose (RESEARCH §"Decision Prose Branches" Branch A) を検証。

NEXTBET-V413-01 / NEXTBET-V413-03 検証 RED scaffold。

Wave 0 では `data/v4.13/diagnosis_v413.md` 未 emit のため、`diagnosis_v413_md_path`
fixture (conftest.py) が pytest.skip するか、Plan 04 emit 後に GREEN になる。

B2 invariant: v4.11 / v4.12 / aggregate_diagnosis_v413 / diagnosis_decoders は import しない。
D-17 invariant: legacy script (scripts/v4.11/, scripts/v4.12/, aggregate_diagnosis_v413.py,
diagnosis_decoders.py) を変更しない。
INTEGRITY-V413-03: 戦略 ship / 新規 strategy / 新規 FWER permutation を発生させない。
"""

from __future__ import annotations

import re
from pathlib import Path


# D-107-03 spec: frontmatter top-level keys (5 件、完全一致)
EXPECTED_FRONTMATTER_KEYS = {
    "schema_version",
    "research_ref",
    "research_commit",
    "parent_artifacts",
    "expected_parent_sources_sha256",
}

# 4 deferred keys (CONTEXT.md L148, scope creep 防止) — frontmatter に出てはならない
DEFERRED_FRONTMATTER_KEYS = {
    "pathway_branch",
    "phase",
    "top_axis",
    "trivial_baseline_pathway",
}

# D-107-03 spec: parent_artifacts 内の 5 alias keys
EXPECTED_PARENT_ARTIFACT_KEYS = {
    "aggregate_diagnosis_v413_sha256",
    "failure_modes_sha256",
    "degeneracy_proof_sha256",
    "ablation_parquet_sha256",
    "ablation_score_sha256",
}

# 4 H2 セクション (canonical order)
EXPECTED_SECTIONS = [
    "## 1. Phase 104-106 結論サマリー",
    "## 2. 5 候補スコア表",
    "## 3. Verdict と意思決定",
    "## 4. Audit Chain",
]


def _parse_frontmatter(md_text: str) -> tuple[dict, str]:
    """`---\\n...\\n---\\n` block を手動 parse (PyYAML 不使用)。

    値は double-quoted scalar を前提 (D-107-03 spec)。
    nested 1 level (parent_artifacts dict) を対応。

    Returns:
        (frontmatter_dict, body_text)
    """
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("frontmatter not found (no leading '---')")
    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("frontmatter close '---' not found")

    fm: dict = {}
    nested_key: str | None = None
    nested_dict: dict = {}
    for raw in lines[1:end_idx]:
        if not raw.strip():
            continue
        # nested block (2-space indent under `parent_artifacts:`)
        if raw.startswith("  ") and nested_key is not None:
            stripped = raw.strip()
            if ":" not in stripped:
                continue
            k, _, v = stripped.partition(":")
            v = v.strip().strip('"')
            nested_dict[k.strip()] = v
            continue
        # top-level key
        if nested_key is not None:
            fm[nested_key] = nested_dict
            nested_key = None
            nested_dict = {}
        stripped = raw.strip()
        if ":" not in stripped:
            continue
        k, _, v = stripped.partition(":")
        v = v.strip()
        if v == "":
            # nested block opener
            nested_key = k.strip()
            nested_dict = {}
        else:
            fm[k.strip()] = v.strip('"')
    if nested_key is not None:
        fm[nested_key] = nested_dict

    body = "\n".join(lines[end_idx + 1 :])
    return fm, body


def test_frontmatter_keys(diagnosis_v413_md_path: Path) -> None:
    """D-107-03: frontmatter top-level keys が EXACTLY 5 件、`schema_version`/`research_ref`
    値固定、SHA hex 長さ、parent_artifacts 5 keys を assert。

    deferred keys 4 件 (`pathway_branch`/`phase`/`top_axis`/`trivial_baseline_pathway`)
    が frontmatter に **存在しない** ことを assert (CONTEXT.md L148)。
    """
    md_text = diagnosis_v413_md_path.read_text(encoding="utf-8")
    fm, _ = _parse_frontmatter(md_text)

    assert set(fm.keys()) == EXPECTED_FRONTMATTER_KEYS, (
        f"frontmatter keys mismatch: got {sorted(fm.keys())}, "
        f"expected {sorted(EXPECTED_FRONTMATTER_KEYS)}"
    )

    # deferred keys 4 件は **存在しない** こと (scope creep 防止)
    for deferred in DEFERRED_FRONTMATTER_KEYS:
        assert deferred not in fm, (
            f"deferred key '{deferred}' must not appear in frontmatter "
            f"(CONTEXT.md L148 deferred ideas, D-107-03 scope)"
        )

    assert fm["schema_version"] == "v4.13.1", (
        f"schema_version must be 'v4.13.1' (D-107-03), got {fm['schema_version']!r}"
    )
    assert (
        fm["research_ref"]
        == ".planning/phases/107-next-bet-selection-v4-14/107-RESEARCH.md"
    ), f"research_ref drift: got {fm['research_ref']!r}"
    assert re.fullmatch(r"[0-9a-f]{40}", fm["research_commit"]), (
        f"research_commit must be 40-char hex SHA, got {fm['research_commit']!r}"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", fm["expected_parent_sources_sha256"]), (
        f"expected_parent_sources_sha256 must be 64-char hex, "
        f"got {fm['expected_parent_sources_sha256']!r}"
    )

    parent = fm["parent_artifacts"]
    assert isinstance(parent, dict), (
        f"parent_artifacts must be dict, got {type(parent)}"
    )
    assert set(parent.keys()) == EXPECTED_PARENT_ARTIFACT_KEYS, (
        f"parent_artifacts keys mismatch: got {sorted(parent.keys())}, "
        f"expected {sorted(EXPECTED_PARENT_ARTIFACT_KEYS)}"
    )
    for k, v in parent.items():
        assert re.fullmatch(r"[0-9a-f]{64}", v), (
            f"parent_artifacts[{k!r}] must be 64-char hex SHA, got {v!r}"
        )


def test_4_sections(diagnosis_v413_md_path: Path) -> None:
    """body に 4 H2 (`## 1. ...` .. `## 4. ...`) が canonical order で存在。"""
    md_text = diagnosis_v413_md_path.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(md_text)

    h2_lines = [line for line in body.splitlines() if re.match(r"^## ", line)]
    assert len(h2_lines) == 4, (
        f"expected exactly 4 H2 headers, got {len(h2_lines)}: {h2_lines}"
    )
    for got, want in zip(h2_lines, EXPECTED_SECTIONS):
        assert got.strip() == want, (
            f"H2 order/text mismatch: got {got!r}, expected {want!r}"
        )


def test_one_page_limit(diagnosis_v413_md_path: Path) -> None:
    """D-107-04: 1 ページ目安 (≤ 80 lines)."""
    md_text = diagnosis_v413_md_path.read_text(encoding="utf-8")
    lines = md_text.splitlines()
    assert len(lines) <= 80, (
        f"diagnosis_v413.md exceeds 1-page limit (≤ 80 lines): got {len(lines)}"
    )


def test_decision_close_prose(diagnosis_v413_md_path: Path) -> None:
    """RESEARCH §"Decision Prose Branches" Branch A: close pathway prose 文言検証。

    Phase 番号 (87/91/95/103) は **enumerate しない** (CONTEXT.md L148 deferred ideas)。
    """
    md_text = diagnosis_v413_md_path.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(md_text)

    # キーワードプレゼンス
    assert "trivial_baseline_pathway" in body, (
        "close prose must reference trivial_baseline_pathway (Phase 106 close trigger)"
    )
    assert "ablation" in body, (
        "close prose must reference ablation (Phase 106 5 軸 Δ=0)"
    )
    assert "side" in body and ("close" in body or "正式 close" in body), (
        "close prose must reference 'side プロジェクト close'"
    )
    assert "null-ship" in body and ("4 マイルストーン" in body or "4 連続" in body), (
        "close prose must reference null-ship 4 マイルストーン / 4 連続"
    )
    assert "discovery 失敗" in body, "close prose must reference 'discovery 失敗'"


def test_close_pathway(diagnosis_v413_md_path: Path) -> None:
    """trivial=true 時の close pathway 散文が Section 3 に存在することを間接検証。

    frontmatter には `trivial_baseline_pathway` キーは無い (D-107-03 spec、scope-out)
    ため、Section 3 散文に「全 5 候補却下」「side プロジェクト close」文言が両方
    存在することで trivial branch が選択されたことを保証。
    """
    md_text = diagnosis_v413_md_path.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(md_text)

    # Section 3 (`## 3. Verdict と意思決定`) を抽出
    sections = re.split(r"^## ", body, flags=re.MULTILINE)
    section3 = None
    for s in sections:
        if s.startswith("3. Verdict"):
            section3 = s
            break
    assert section3 is not None, (
        "Section 3 ('## 3. Verdict と意思決定') not found in body"
    )

    assert (
        ("全 5 候補却下" in section3)
        or ("5 候補却下" in section3)
        or ("全候補却下" in section3)
    ), "Section 3 must contain '全 5 候補却下' / '5 候補却下' / '全候補却下'"
    assert "side" in section3 and ("close" in section3 or "正式 close" in section3), (
        "Section 3 must contain 'side プロジェクト close' phrasing"
    )
