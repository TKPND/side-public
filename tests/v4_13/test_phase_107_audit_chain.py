"""Phase 107 Wave 0 RED scaffold — audit chain tests.

3 件の RED test:
- test_5_parent_sha_match: PATTERNS L621-628 alias map で frontmatter SHA と
  実ファイル sha256 を照合
- test_research_commit_pin: `git log -1 --format=%H -- 107-RESEARCH.md` と
  frontmatter `research_commit` の equality (HEAD pin pitfall 防止)
- test_expected_parent_sources_sha256: D-V413-07 canonical bytes pin
  (Phase 106 sources.json の sha256)

B2 / D-17 / INTEGRITY-V413-03 invariant 維持。PyYAML 不使用。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest


# PATTERNS.md L621-628: frontmatter alias key ↔ 実ファイル名 mapping
ALIAS_TO_PATH = {
    "aggregate_diagnosis_v413_sha256": "data/v4.13/aggregate_diagnosis_v413.parquet",
    "failure_modes_sha256": "data/v4.13/diagnosis_v413_failure_modes.parquet",
    "degeneracy_proof_sha256": "data/v4.13/diagnosis_v413_degeneracy_evidence.json",
    "ablation_parquet_sha256": "data/v4.13/diagnosis_v413_ablation.parquet",
    "ablation_score_sha256": "data/v4.13/ablation_score.json",
}

EXPECTED_PARENT_SOURCES_PATH = "data/v4.13/diagnosis_v413_ablation_sources.json"

RESEARCH_REF_REL = ".planning/phases/107-next-bet-selection-v4-14/107-RESEARCH.md"


def _parse_frontmatter(md_text: str) -> dict:
    """double-quoted scalar 前提の手動 frontmatter parser (PyYAML 不使用)。
    nested 1 level (parent_artifacts) 対応。
    """
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("frontmatter not found")
    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("frontmatter close not found")

    fm: dict = {}
    nested_key: str | None = None
    nested_dict: dict = {}
    for raw in lines[1:end_idx]:
        if not raw.strip():
            continue
        if raw.startswith("  ") and nested_key is not None:
            stripped = raw.strip()
            if ":" not in stripped:
                continue
            k, _, v = stripped.partition(":")
            nested_dict[k.strip()] = v.strip().strip('"')
            continue
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
            nested_key = k.strip()
            nested_dict = {}
        else:
            fm[k.strip()] = v.strip('"')
    if nested_key is not None:
        fm[nested_key] = nested_dict
    return fm


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_5_parent_sha_match(diagnosis_v413_md_path: Path, project_root: Path) -> None:
    """frontmatter `parent_artifacts` 5 alias keys が実ファイル sha256 と一致。

    実ファイル absent な alias は当該キーのみ skip 扱い (mapping 全体は assert)。
    """
    md_text = diagnosis_v413_md_path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(md_text)
    parent = fm.get("parent_artifacts")
    assert isinstance(parent, dict), (
        f"parent_artifacts must be dict, got {type(parent)}"
    )
    assert set(parent.keys()) == set(ALIAS_TO_PATH.keys()), (
        f"alias map mismatch: got {sorted(parent.keys())}, "
        f"expected {sorted(ALIAS_TO_PATH.keys())}"
    )

    skipped: list[str] = []
    for alias, rel_path in ALIAS_TO_PATH.items():
        actual_path = project_root / rel_path
        if not actual_path.exists():
            skipped.append(rel_path)
            continue
        expected_sha = parent[alias]
        actual_sha = _sha256_file(actual_path)
        assert expected_sha == actual_sha, (
            f"SHA256 drift for {alias} ({rel_path}): "
            f"frontmatter={expected_sha} actual={actual_sha}"
        )
    if len(skipped) == len(ALIAS_TO_PATH):
        pytest.skip(f"all parent artifacts absent (Wave 0 RED): {skipped}")


def test_research_commit_pin(diagnosis_v413_md_path: Path, project_root: Path) -> None:
    """frontmatter `research_commit` が `git log -1 --format=%H -- 107-RESEARCH.md`
    出力と一致。HEAD ではなく RESEARCH.md last-modified ピンであることを保証
    (research_commit pin pitfall 防止 — 107-RESEARCH.md L390)。
    """
    md_text = diagnosis_v413_md_path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(md_text)
    fm_commit = fm.get("research_commit")
    assert fm_commit is not None, "frontmatter missing research_commit"
    assert re.fullmatch(r"[0-9a-f]{40}", fm_commit), (
        f"research_commit must be 40-char hex, got {fm_commit!r}"
    )

    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", RESEARCH_REF_REL],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = result.stdout.strip()
    assert expected, (
        f"git log returned empty for {RESEARCH_REF_REL} "
        f"(file may be untracked or path wrong)"
    )
    assert fm_commit == expected, (
        f"research_commit drift: frontmatter={fm_commit} "
        f"git log -1 (-- {RESEARCH_REF_REL})={expected}. "
        f"emit_diagnosis_v413.py must pin RESEARCH.md last-modified, not HEAD."
    )


def test_expected_parent_sources_sha256(
    diagnosis_v413_md_path: Path, project_root: Path
) -> None:
    """frontmatter `expected_parent_sources_sha256` が
    `hashlib.sha256(data/v4.13/diagnosis_v413_ablation_sources.json)` 実 sha256 と一致。

    Phase 106 sources.json の D-V413-07 canonical bytes pin を保証。
    """
    md_text = diagnosis_v413_md_path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(md_text)
    fm_sha = fm.get("expected_parent_sources_sha256")
    assert fm_sha is not None, "frontmatter missing expected_parent_sources_sha256"
    assert re.fullmatch(r"[0-9a-f]{64}", fm_sha), (
        f"expected_parent_sources_sha256 must be 64-char hex, got {fm_sha!r}"
    )

    sources_path = project_root / EXPECTED_PARENT_SOURCES_PATH
    if not sources_path.exists():
        pytest.skip(
            f"{EXPECTED_PARENT_SOURCES_PATH} not yet emitted (Phase 106 dependency)"
        )
    actual_sha = _sha256_file(sources_path)
    assert fm_sha == actual_sha, (
        f"expected_parent_sources_sha256 drift: "
        f"frontmatter={fm_sha} actual={actual_sha} "
        f"(D-V413-07 canonical bytes pin violated)"
    )
