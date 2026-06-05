"""scripts/v4.13/emit_diagnosis_v413.py — Phase 107 next-bet selection forensic emit.

Output (2 artifact):
    - data/v4.13/diagnosis_v413.md                            (1-page markdown forensic synthesis)
    - data/v4.13/diagnosis_v413_nextbet_sources.json          (SHA256 chain pin, audit anchor)

Invariants:
    - D-17 / B2: emit_ablation_v413.py / aggregate_*.py / decoders.py を import しない (literal copy)
    - D-V413-07: canonical bytes (sort_keys=True, indent=2, ensure_ascii=False, 末尾 \\n)
    - D-107-03: frontmatter EXACTLY 5 keys (schema_version / research_ref / research_commit /
      parent_artifacts / expected_parent_sources_sha256). 4 deferred keys
      (pathway_branch / phase / top_axis / trivial_baseline_pathway) は出力しない
      (CONTEXT.md L148 deferred ideas, Phase 87/91/95/103 enumeration NOT used)
    - W5: 2 連続 emit で md + sources.json byte-identical (D-V413-07 canonical bytes)
    - INTEGRITY-V413-03: 戦略 ship 0 / 新規 strategy 0 / 新規 FWER permutation 0

Inputs (Phase 105/106 contract LOCKED):
    - data/v4.13/aggregate_diagnosis_v413.parquet
    - data/v4.13/diagnosis_v413_failure_modes.parquet
    - data/v4.13/diagnosis_v413_degeneracy_evidence.json
    - data/v4.13/diagnosis_v413_ablation.parquet
    - data/v4.13/ablation_score.json
    - data/v4.13/diagnosis_v413_ablation_sources.json   (expected_parent_sources_sha256 anchor)

Citations:
    - 107-CONTEXT.md D-107-01..04
    - 107-RESEARCH.md Pattern 1-4 (literal-copy helpers / canonical md / frontmatter / verdict_map)
    - 107-RESEARCH.md Pitfall 1-6
    - 107-PATTERNS.md L621-628 alias map (frontmatter key alias ↔ real filename)
    - emit_ablation_v413.py:65-101 (literal copy source for helpers)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

# ── Module-level constants ───────────────────────────────────────────────────
DATA_DIR = Path("data/v4.13")
DIAGNOSIS_MD = DATA_DIR / "diagnosis_v413.md"
NEXTBET_SOURCES_JSON = DATA_DIR / "diagnosis_v413_nextbet_sources.json"

SCHEMA_VERSION_NEW = "v4.13.1"
RESEARCH_REF = ".planning/phases/107-next-bet-selection-v4-14/107-RESEARCH.md"

# PATTERNS.md L621-628 Naming Alias Map: frontmatter key alias と実ファイル名は分離。
# 5 件の (alias_key, real_path) tuple、D-107-03 で frontmatter parent_artifacts dict に展開される。
# 実ファイル名は必ず real_path 側に hardcode (alias key と区別)。
PARENT_ARTIFACTS: list[tuple[str, str]] = [
    ("aggregate_diagnosis_v413_sha256", "data/v4.13/diagnosis_v413.parquet"),
    ("failure_modes_sha256", "data/v4.13/diagnosis_v413_failure_modes.parquet"),
    ("degeneracy_proof_sha256", "data/v4.13/diagnosis_v413_degeneracy_evidence.json"),
    ("ablation_parquet_sha256", "data/v4.13/diagnosis_v413_ablation.parquet"),
    ("ablation_score_sha256", "data/v4.13/ablation_score.json"),
]

# expected_parent_sources_sha256 は emit-time に動的計算 (drift risk 排除、
# Phase 106 emit_ablation_v413.py の EXPECTED_PARENT_SHA pattern を踏襲しつつ
# hardcode を避ける)。Phase 106 sources artifact の SHA を Phase 107 emit が pin する。
EXPECTED_PARENT_SOURCES_PATH = "data/v4.13/diagnosis_v413_ablation_sources.json"

# 5 候補 axis → label 表示名 (verdict_map / score table で使用、Task 2/3 で展開)。
CANDIDATE_AXIS_MAP: list[tuple[str, str]] = [
    ("A", "regime_cuts"),
    ("B", "pair"),
    ("C", "window"),
    ("D", "sizing"),
    ("E", "paper_trade"),  # E は live_required (ablation 軸ではなく実弾検証)
]


# ── Atomic write helpers (literal copy from emit_ablation_v413.py:65-83, B2) ──
def _atomic_write_canonical_json(d: dict, path: Path) -> None:
    """canonical bytes (D-V413-07) で atomic write (JSON sidecar).

    allow_nan=False で NaN/Infinity 混入時に ValueError fail-fast (RFC 8259 準拠)。
    emit 側で float('nan') を None に事前変換すること (RESEARCH.md Pitfall 1)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(d, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        + "\n"
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _sha256_of_file(path: Path) -> str:
    """SHA256 hex digest (file 全体)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _research_commit(research_ref: str = RESEARCH_REF) -> str:
    """RESEARCH.md を最後に変更した commit hash を pin する (run-to-run 不変)。

    `git rev-parse HEAD` だと emit 実行時の HEAD に追従して artifact が drift する
    (Phase 106 Wave 1 で検出)。RESEARCH.md の last-modified commit を引くことで
    research_commit 値を RESEARCH.md の内容に紐付ける (RESEARCH.md Pitfall 1 防止)。
    """
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", research_ref],
            text=True,
        ).strip()
        return out or "unknown"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


# ── Markdown canonical writer + frontmatter (Phase 107 新規、Pitfall 2-4 防止) ──
def _atomic_write_canonical_md(path: Path, text: str) -> None:
    """canonical markdown bytes で atomic write.

    正規化:
      - LF only (CR/CRLF を LF に正規化)
      - line rstrip (行末空白除去)
      - single ``\\n`` EOF (末尾改行 1 つ)
      - utf-8 no-BOM
      - atomic via tmp + os.replace

    W5 idempotent (2x emit byte-identical) の前提条件。RESEARCH.md Pitfall 2
    (PyYAML→手書き frontmatter) の差し替え先。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # 1. CRLF/CR → LF 正規化
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    # 2. 行末空白 strip + 末尾改行 1 つ
    lines = [line.rstrip() for line in normalized.split("\n")]
    # split は末尾に空文字を残す (text が \n 終端のとき)。rstrip 後 join → 1 つの \n を末尾に保証。
    while lines and lines[-1] == "":
        lines.pop()
    payload = "\n".join(lines) + "\n"
    # 3. utf-8 no-BOM, atomic write
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _frontmatter(d: dict) -> str:
    """5-key minimal YAML frontmatter writer (PyYAML 非依存、D-107-03 厳守).

    Spec:
      - delimiter: ``---`` 開始 + ``---`` 終了 (各行末に LF)
      - sorted-key alphabetical (top-level keys + nested parent_artifacts keys)
      - all scalars double-quoted (string escape: ``\\``, ``"``)
      - nested 1 level only (parent_artifacts dict)
      - 期待 5 keys: expected_parent_sources_sha256 / parent_artifacts /
        research_commit / research_ref / schema_version (alphabetical)

    nested dict は 2-space indent + ``  key: "value"`` 形式 (PyYAML 互換 minimal subset)。
    値が dict 以外の入れ子 (list/None/bool 等) は今回の用途では発生しないため非対応で fail-fast。
    RESEARCH.md Pitfall 2 (PyYAML 依存) / Pitfall 6 (YAML reserved word) 防止。
    """

    def _quote(s: str) -> str:
        # double-quoted scalar の最小エスケープ (backslash + double-quote)
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines: list[str] = ["---"]
    for key in sorted(d.keys()):
        value = d[key]
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for sub_key in sorted(value.keys()):
                sub_value = value[sub_key]
                if not isinstance(sub_value, str):
                    raise TypeError(
                        f"_frontmatter: nested value must be str (key={key}.{sub_key}, "
                        f"got {type(sub_value).__name__})"
                    )
                lines.append(f"  {sub_key}: {_quote(sub_value)}")
        elif isinstance(value, str):
            lines.append(f"{key}: {_quote(value)}")
        else:
            raise TypeError(
                f"_frontmatter: top-level value must be str or dict (key={key}, "
                f"got {type(value).__name__})"
            )
    lines.append("---")
    return "\n".join(lines) + "\n"


# ── Verdict mapping decision tree (D-107-01 + D-107-02, RESEARCH Pattern 4) ──
def verdict_map(
    trivial: bool, first_order: dict
) -> list[tuple[str, str, float | None, str]]:
    """5 候補 (A-E) を verdict tuple で返す決定論的 mapping (D-107-01/02).

    Returns: list of (candidate_id, axis_label, score, verdict) in fixed
        alphabetical order A, B, C, D, E.

    Branches (D-107-02):
        trivial=True (今回の v4.13 case):
            A-D: score=None, verdict="reject"
            E:   score=None, verdict="live_required"
        trivial=False:
            A-D: score=first_order[axis_label], verdict="recommended" if top-1 else "deprioritized"
            E:   score=None, verdict="live_required"  (paper trade は ablation 軸対象外)

    Tie-break (trivial=False):
        最大スコアが複数候補で並んだ場合、candidate_id alphabetical で top-1 を選ぶ
        (sort key = (-(score or -inf), candidate_id) で安定確定)。

    Args:
        trivial: ablation_score.json の `trivial_baseline_pathway` フラグ。
        first_order: ablation_score.json の `first_order` dict
            (axis_label → float | None)。trivial=True 時は全 None で OK。
    """
    rows: list[tuple[str, str, float | None, str]] = []

    if trivial:
        for cand_id, axis_label in CANDIDATE_AXIS_MAP:
            if cand_id == "E":
                rows.append((cand_id, axis_label, None, "live_required"))
            else:
                rows.append((cand_id, axis_label, None, "reject"))
        return rows

    # Non-trivial: A-D を first_order スコアで rank、top-1 = recommended
    ad_axes = [(c, a) for c, a in CANDIDATE_AXIS_MAP if c != "E"]
    # sort key: 降順スコア (None は -inf 扱い) + 昇順 candidate_id (tie-break alphabetical)
    ranked = sorted(
        ad_axes,
        key=lambda ca: (-(first_order.get(ca[1]) or float("-inf")), ca[0]),
    )
    top_cand = ranked[0][0]

    for cand_id, axis_label in CANDIDATE_AXIS_MAP:
        if cand_id == "E":
            rows.append((cand_id, axis_label, None, "live_required"))
            continue
        score = first_order.get(axis_label)
        verdict = "recommended" if cand_id == top_cand else "deprioritized"
        rows.append((cand_id, axis_label, score, verdict))

    return rows


# ── Close pathway prose (RESEARCH Branch A literal text, D-107-02) ──
def _close_pathway_prose() -> str:
    """RESEARCH §"Decision Prose Branches" Branch A の close pathway prose を返す.

    重要 (CONTEXT.md L148 deferred):
      - Phase 87/91/95/103 の enumeration を **含めない** (deferred ideas)
      - 5 keyword は必ず含める: trivial_baseline_pathway / ablation /
        side プロジェクト close / null-ship / discovery 失敗 / 4 マイルストーン

    Returns: markdown 散文 (heading 抜き、本文のみ)。
    """
    # RESEARCH.md §"Decision Prose Branches" Branch A literal text
    return (
        "Phase 106 の Sobol 風 ablation 分解で全 5 軸の first-order 効果が 0 (退化解) と確定し、\n"
        "`trivial_baseline_pathway=true` フラグが立った。これは v4.13 の 480 cell 全体で\n"
        "合格セル 0 が「特定軸の不一致」ではなく「設計次元の構造的退化」に起因することを示す。\n"
        "\n"
        "候補 A-D (regime_cuts / pair / window / sizing 軸の改善) はいずれも\n"
        "ablation で動かない次元への投資となり、先行する 4 マイルストーン (v4.9-v4.12) の\n"
        "null-ship を v4.14 で覆す根拠を提供しない。\n"
        "\n"
        "候補 E (paper trade 統合) は ablation 軸に対応せず、診断データ上はスコア不能。\n"
        "live 検証で initial null-ship の robustness を再確認する選択肢としてのみ残るが、\n"
        "これは「edge 発見」ではなく「edge 不在の追認」のためのコストであり、\n"
        "side プロジェクトのゴール (4 マイルストーン以内に shipped strategy 1 件) に\n"
        "寄与しない。\n"
        "\n"
        "**結論: 全 5 候補却下。side プロジェクトを「null-ship を 4 マイルストーン続けた\n"
        "discovery 失敗」として正式 close する。**"
    )


# ── Section composers (D-107-04 Markdown Schema 4 H2 sections) ──
def _section_summary() -> str:
    """## 1. Phase 104-106 結論サマリー (3 行表、CONTEXT.md L65-70).

    evidence_artifact 列は PATTERNS.md L621-628 alias map に従い実ファイル名を hardcode。
    """
    lines = [
        "## 1. Phase 104-106 結論サマリー",
        "",
        "| Phase | 結論 | evidence_artifact | signal_to_v414 |",
        "|---|---|---|---|",
        "| 104 (Aggregation) | 480 cell × 5 dim 合格セル 0 | "
        "`data/v4.13/aggregate_diagnosis_v413.parquet` | "
        "全 milestone × 全軸で hurdle 不通過、構造的退化の疑い |",
        "| 105 (Hurdle Gap) | 全 480 cell `failure_mode='degenerate'` | "
        "`data/v4.13/diagnosis_v413_failure_modes.parquet`, "
        "`data/v4.13/diagnosis_v413_degeneracy_evidence.json` | "
        "個別 cell の失敗ではなく分布全体が hurdle 未満 |",
        "| 106 (Ablation) | 5 軸 ablation Δ=0、`trivial_baseline_pathway=true` | "
        "`data/v4.13/diagnosis_v413_ablation.parquet`, "
        "`data/v4.13/ablation_score.json` | "
        "first-order 効果 0 → close pathway 発火 |",
    ]
    return "\n".join(lines)


def _section_score_table(verdicts: list[tuple[str, str, float | None, str]]) -> str:
    """## 2. 5 候補スコア表 (verdict_map 戻り値を表に整形).

    score=None は表上 "—" 表示、数値は f"{x:.3f}" 表示。
    """
    lines = [
        "## 2. 5 候補スコア表",
        "",
        "| 候補 | 軸 | first_order | verdict |",
        "|---|---|---|---|",
    ]
    for cand_id, axis_label, score, verdict in verdicts:
        score_str = "—" if score is None else f"{score:.3f}"
        lines.append(f"| {cand_id} | {axis_label} | {score_str} | {verdict} |")
    return "\n".join(lines)


def _section_decision(trivial: bool) -> str:
    """## 3. Verdict と意思決定 (trivial=True 時のみ close pathway prose を出力).

    trivial=False 分岐は v4.14+ scope (CONTEXT.md L83 emit script に両 branch 実装、
    今回は前者だけ md 出力)。本 Plan では trivial=True path のみ書き出す。
    """
    lines = [
        "## 3. Verdict と意思決定",
        "",
    ]
    if trivial:
        lines.append(_close_pathway_prose())
    else:
        # 非 trivial 分岐 (本 Phase では未到達、Plan 04 emit データは trivial=True)。
        lines.append(
            "非 trivial baseline 検出: top-1 軸を v4.14 で優先軸として採用する。"
        )
    return "\n".join(lines)


def _section_audit(
    parent_shas: dict,
    research_commit: str,
    expected_parent_sources_sha: str,
) -> str:
    """## 4. Audit Chain (frontmatter SHA chain を本文末尾でも human-readable に再表示).

    7 行表: 5 parent SHA (alias_key) + research_commit pin + expected_parent_sources_sha。
    実ファイル名は備考欄に表示 (alias key と区別、PATTERNS L621-628)。
    """
    lines = [
        "## 4. Audit Chain",
        "",
        "| artifact alias | SHA-256 | 備考 |",
        "|---|---|---|",
    ]
    # PATTERNS L621-628 alias_key → real_path mapping (PARENT_ARTIFACTS と一致)
    alias_to_path = dict(PARENT_ARTIFACTS)
    for alias_key in sorted(parent_shas.keys()):
        sha = parent_shas[alias_key]
        real_path = alias_to_path.get(alias_key, "—")
        lines.append(f"| `{alias_key}` | `{sha}` | `{real_path}` |")
    lines.append(
        f"| `research_commit` | `{research_commit}` | "
        "RESEARCH.md last-modified pin (Phase 106 fix) |"
    )
    lines.append(
        f"| `expected_parent_sources_sha256` | `{expected_parent_sources_sha}` | "
        f"`{EXPECTED_PARENT_SOURCES_PATH}` D-V413-07 canonical bytes pin |"
    )
    return "\n".join(lines)


# ── main() — argparse + emit flow (D-107-03/04 spec, Plan 04 で実行) ──
def main(out_dir: Path | None = None) -> None:
    """Phase 107 next-bet selection forensic emit.

    Flow:
        1. PARENT_ARTIFACTS の 5 ファイル SHA256 計算 → parent_shas dict
        2. expected_parent_sources_sha256 を動的計算 (drift risk 排除)
        3. ablation_score.json read → trivial / first_order 抽出
        4. verdict_map で 5 verdict 生成
        5. 4 section 組立 + frontmatter (D-107-03 spec 5 keys 厳守)
        6. atomic write: diagnosis_v413.md + diagnosis_v413_nextbet_sources.json
    """
    import argparse

    if out_dir is None:
        parser = argparse.ArgumentParser(description="Phase 107 next-bet emit")
        parser.add_argument(
            "--out-dir",
            type=Path,
            default=DATA_DIR,
            help="output directory for diagnosis_v413.md + nextbet_sources.json",
        )
        args = parser.parse_args()
        out_dir = args.out_dir

    out_dir = Path(out_dir)
    md_path = out_dir / "diagnosis_v413.md"
    sources_path = out_dir / "diagnosis_v413_nextbet_sources.json"

    # 1. parent SHA chain (5 alias_key → SHA)
    parent_shas: dict[str, str] = {}
    for alias_key, real_path in PARENT_ARTIFACTS:
        parent_shas[alias_key] = _sha256_of_file(Path(real_path))

    # 2. expected_parent_sources_sha256 (動的計算、hardcode 禁止)
    expected_parent_sources_sha = _sha256_of_file(Path(EXPECTED_PARENT_SOURCES_PATH))

    # 3. ablation_score.json read
    ablation_score_path = Path("data/v4.13/ablation_score.json")
    score_dict = json.loads(ablation_score_path.read_text(encoding="utf-8"))
    trivial = bool(score_dict["trivial_baseline_pathway"])
    first_order = score_dict.get("first_order", {})

    # 4. verdict_map
    verdicts = verdict_map(trivial=trivial, first_order=first_order)

    # 5. research_commit + 4 section 組立
    research_commit = _research_commit()
    sections = [
        _section_summary(),
        _section_score_table(verdicts),
        _section_decision(trivial),
        _section_audit(parent_shas, research_commit, expected_parent_sources_sha),
    ]
    body = "\n\n".join(sections)

    # frontmatter dict は D-107-03 spec 5 keys のみ (CONTEXT.md L43-53)
    frontmatter_dict = {
        "schema_version": SCHEMA_VERSION_NEW,
        "research_ref": RESEARCH_REF,
        "research_commit": research_commit,
        "parent_artifacts": parent_shas,
        "expected_parent_sources_sha256": expected_parent_sources_sha,
    }
    frontmatter = _frontmatter(frontmatter_dict)

    md_text = f"{frontmatter}\n{body}\n"

    # 6. atomic write md + sources sidecar
    _atomic_write_canonical_md(md_path, md_text)

    sources_dict = {
        "schema_version": SCHEMA_VERSION_NEW,
        "research_ref": RESEARCH_REF,
        "research_commit": research_commit,
        "parent_artifacts": parent_shas,
        "expected_parent_sources_sha256": expected_parent_sources_sha,
        "verdicts": [
            {
                "candidate_id": cand_id,
                "axis_label": axis_label,
                "score": score,
                "verdict": verdict,
            }
            for cand_id, axis_label, score, verdict in verdicts
        ],
    }
    _atomic_write_canonical_json(sources_dict, sources_path)

    print(f"emit_diagnosis_v413: wrote {md_path}, {sources_path}")


if __name__ == "__main__":
    main()
