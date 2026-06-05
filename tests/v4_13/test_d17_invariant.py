"""tests/v4_13/test_d17_invariant.py — INTEGRITY-V413-01/02 + D-17 invariant 機械検証.

CONTEXT D-17: scripts/v4.11/ scripts/v4.12/ は read-only (Phase 98 14-commit revert lesson).
INTEGRITY-V413-01: 9 source artifact septuple-pin (sidecar ↔ conftest drift 0).
INTEGRITY-V413-02: scripts/v4.12/grep_gates_v412.sh が 0-match (従来 v4.12 invariant 維持).
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

# ── Phase 104 baseline SHA256 (B3 反映, Phase 105 Wave 0) ──────────────────────
# Phase 104 ship commit (06f326c) 時点での scripts/v4.13/ 2 ファイルの SHA256.
# Phase 105 invariant tests (test_phase_105_*.py) はこの constants を import で参照し、
# inline 再計算しない (B3: circular fixture 排除)。
# D-17 invariant: aggregate_diagnosis_v413.py / diagnosis_decoders.py は post-Phase 104
# read-only (105-01-PLAN.md threats T-105-15 mitigation)。
AGGREGATE_HASH_PHASE104 = (
    "67d3123634f5c616b192ef18251410f9041138c0aa439033c8ad301fdadad1f1"
)
DECODERS_HASH_PHASE104 = (
    "5e8a6a3a1796a1e84951af9ae7df62f1244c3a18698222dcbff0d7c3d5462837"
)


def test_d17_no_legacy_script_modifications(project_root: Path) -> None:
    """D-17: scripts/v4.11/ scripts/v4.12/ に未 commit 変更がないこと."""
    out = subprocess.check_output(
        ["git", "diff", "HEAD", "--", "scripts/v4.11/", "scripts/v4.12/"],
        cwd=project_root,
        text=True,
    )
    assert out == "", (
        f"D-17 invariant violated: legacy scripts modified.\ngit diff output:\n{out}"
    )


def test_sidecar_sha256_matches_conftest_expected(
    project_root: Path,
    expected_input_sha256: dict,
) -> None:
    """INTEGRITY-V413-01: sidecar sources[].sha256 == conftest expected_input_sha256 (drift 0)."""
    sidecar_path = project_root / "data" / "v4.13" / "diagnosis_v413_sources.json"
    if not sidecar_path.exists():
        pytest.skip(f"sidecar not yet emitted: {sidecar_path}")

    sidecar = json.loads(sidecar_path.read_text())
    sidecar_dict = {s["path"]: s["sha256"] for s in sidecar["sources"]}

    # 9 件すべて conftest と一致
    for path, expected_sha in expected_input_sha256.items():
        assert path in sidecar_dict, f"sidecar missing source: {path}"
        assert sidecar_dict[path] == expected_sha, (
            f"sha256 drift on {path}: "
            f"sidecar={sidecar_dict[path]} vs conftest={expected_sha}"
        )

    # 逆方向: sidecar に conftest 未登録の path がないこと
    for path in sidecar_dict:
        assert path in expected_input_sha256, (
            f"sidecar has untracked source: {path} (conftest expected_input_sha256 に追加要)"
        )


def test_golden_parquet_matches_current(project_root: Path) -> None:
    """golden vs Phase 104 backup parquet hash 一致 (regression detection 用).

    Phase 105 Wave 2 で `diagnosis_v413.parquet` は in-place 上書きされるため、
    Phase 104 aggregator contract は W5 1-shot backup `.phase104_backup` で保証する.
    backup 未存在 (Phase 105 未 emit 状態) のときは生 parquet を fallback として読む.
    """
    backup = project_root / "data" / "v4.13" / "diagnosis_v413.parquet.phase104_backup"
    live = project_root / "data" / "v4.13" / "diagnosis_v413.parquet"
    current = backup if backup.exists() else live
    golden = (
        project_root / "tests" / "v4_13" / "fixtures" / "diagnosis_v413_golden.parquet"
    )
    if not current.exists() or not golden.exists():
        pytest.skip("parquet or golden not yet emitted")

    h_current = hashlib.sha256(current.read_bytes()).hexdigest()
    h_golden = hashlib.sha256(golden.read_bytes()).hexdigest()
    assert h_current == h_golden, (
        f"golden drift: current={h_current[:12]} vs golden={h_golden[:12]}. "
        f"aggregator output 変更時は intentional であれば fixture を update commit すること."
    )


def test_v413_aggregate_decoders_phase104_baseline(project_root: Path) -> None:
    """Phase 105 D-17: scripts/v4.13/{aggregate_diagnosis_v413,diagnosis_decoders}.py が
    Phase 104 ship 時点 (commit 06f326c) の SHA256 から drift してないこと.

    B3 反映: Phase 105 invariant tests はこの constants を import 経由で参照する.
    """
    aggregate = project_root / "scripts" / "v4.13" / "aggregate_diagnosis_v413.py"
    decoders = project_root / "scripts" / "v4.13" / "diagnosis_decoders.py"

    h_agg = hashlib.sha256(aggregate.read_bytes()).hexdigest()
    h_dec = hashlib.sha256(decoders.read_bytes()).hexdigest()

    assert h_agg == AGGREGATE_HASH_PHASE104, (
        f"D-17 violated: aggregate_diagnosis_v413.py drift "
        f"current={h_agg[:12]} vs Phase104={AGGREGATE_HASH_PHASE104[:12]}"
    )
    assert h_dec == DECODERS_HASH_PHASE104, (
        f"D-17 violated: diagnosis_decoders.py drift "
        f"current={h_dec[:12]} vs Phase104={DECODERS_HASH_PHASE104[:12]}"
    )


def test_grep_gates_v412_zero_violation(project_root: Path) -> None:
    """INTEGRITY-V413-02: scripts/v4.12/grep_gates_v412.sh が 0 violation (exit 0)."""
    script = project_root / "scripts" / "v4.12" / "grep_gates_v412.sh"
    if not script.exists():
        pytest.skip(f"grep_gates script absent: {script}")

    result = subprocess.run(
        ["bash", str(script)],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"INTEGRITY-V413-02 violated: grep_gates_v412.sh exit={result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


# ── Phase 106 B2 invariant (literal copy 順守) ────────────────────────────────


def test_phase106_no_import_from_phase105_emit(project_root: Path) -> None:
    """B2: emit_ablation_v413.py は emit_degeneracy_proof.py 等から import しない.

    Phase 106 emit script は Phase 105 helper を import せず literal copy で実装する
    (CONTEXT.md D-106 / D-17 invariant)。AST parse で実 import 文だけを抽出し、
    attribution コメント (e.g. `# literal copy from emit_degeneracy_proof.py:52`)
    を偽陽性化しないよう機械固定する。
    """
    target = project_root / "scripts" / "v4.13" / "emit_ablation_v413.py"
    if not target.exists():
        pytest.skip(f"Phase 106 emit script not yet created: {target}")
    tree = ast.parse(target.read_text())
    forbidden_modules = {
        "emit_degeneracy_proof",
        "aggregate_diagnosis_v413",
        "diagnosis_decoders",
        "scripts.v4_13.emit_degeneracy_proof",
        "scripts.v4_13.aggregate_diagnosis_v413",
        "scripts.v4_13.diagnosis_decoders",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden_modules, (
                f"B2 / D-17 violated: 'from {node.module} import ...' "
                f"at line {node.lineno} of {target}.\n"
                "Phase 106 emit script must literal-copy helpers."
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_modules, (
                    f"B2 / D-17 violated: 'import {alias.name}' "
                    f"at line {alias.lineno} of {target}.\n"
                    "Phase 106 emit script must literal-copy helpers."
                )


# ── Phase 107 B2 invariant + dynamic SHA pattern (Plan 03 追加) ─────────────────


def test_phase_107_import_isolation(project_root: Path) -> None:
    """Phase 107 emit script が sibling emit / aggregator / decoder /
    v4.11 / v4.12 から import しないことを AST scan で保証 (B2 invariant).

    Phase 106 同等の literal-copy 原則を Phase 107 emit_diagnosis_v413.py にも適用。
    forbidden prefix を絶対 import / 相対 import 両方から検出する。
    """
    target = project_root / "scripts" / "v4.13" / "emit_diagnosis_v413.py"
    if not target.exists():
        pytest.skip(f"Phase 107 emit script not yet created: {target}")
    source = target.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_prefixes = (
        "scripts.v4_11",
        "scripts.v4_12",
        "scripts.v4_13.emit_ablation_v413",
        "scripts.v4_13.emit_degeneracy_proof",
        "scripts.v4_13.aggregate_diagnosis_v413",
        "scripts.v4_13.diagnosis_decoders",
    )
    # 相対 import の末尾モジュール名 (e.g. ".aggregate_diagnosis_v413") も検出する
    forbidden_relative_tails = {
        prefix.rsplit(".", 1)[-1] for prefix in forbidden_prefixes if "." in prefix
    }
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name.startswith(p) for p in forbidden_prefixes):
                    violations.append(f"import {alias.name} (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if any(module.startswith(p) for p in forbidden_prefixes):
                violations.append(f"from {module} import ... (line {node.lineno})")
            # relative import (`from .aggregate_diagnosis_v413 import ...`) も検出
            if node.level > 0 and module in forbidden_relative_tails:
                violations.append(f"from .{module} import ... (line {node.lineno})")
    assert not violations, (
        f"B2 invariant violation in emit_diagnosis_v413.py: {violations}"
    )


def test_phase_107_dynamic_sha_pattern(project_root: Path) -> None:
    """Phase 107 emit script が動的 SHA 計算パターンを採用していることを構造検証.

    Blocker 4 修正の固定化 (Phase 107 Plan 02 で確立した invariant):
        1. EXPECTED_ABLATION_SOURCES_SHA という旧名定数は不在 (= 再導入されてない)
        2. 64-char hex literal (sha256 hardcode) が emit script 本文 (非コメント行)
           に存在しない
        3. EXPECTED_PARENT_SOURCES_PATH path 定数の存在 (動的計算対象 path 定数化)
        4. `_sha256_of_file(Path(EXPECTED_PARENT_SOURCES_PATH))` 呼び出し >= 1
        5. frontmatter dict 構築箇所に "expected_parent_sources_sha256" key の
           string literal が存在 (D-107-03 spec)
        6. D-107-03 deferred frontmatter key (pathway_branch / top_axis /
           trivial_baseline_pathway) が emit script 本文に登場しない
           (CONTEXT.md L148, Blocker 2 修正)
    """
    import re

    target = project_root / "scripts" / "v4.13" / "emit_diagnosis_v413.py"
    if not target.exists():
        pytest.skip(f"Phase 107 emit script not yet created: {target}")
    source = target.read_text(encoding="utf-8")

    # 1. 旧名定数 EXPECTED_ABLATION_SOURCES_SHA が居ないこと
    assert "EXPECTED_ABLATION_SOURCES_SHA" not in source, (
        "EXPECTED_ABLATION_SOURCES_SHA は Blocker 4 修正で削除されたはず — "
        "再導入されていれば SHA drift リスクが復活する"
    )

    # 2. 64-char hex literal (sha256 の hardcode) が居ないこと
    # コメント行 (行頭 '#') は除外して走査
    non_comment = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    hex_literals = re.findall(r'"([0-9a-f]{64})"', non_comment)
    assert not hex_literals, (
        f"64-char hex literal が emit script 本文に検出された: {hex_literals[:3]}\n"
        "Blocker 4 修正方針: SHA は emit-time に _sha256_of_file() で動的計算する"
    )

    # 3. EXPECTED_PARENT_SOURCES_PATH 定数が path string で定義されている
    assert re.search(
        r'EXPECTED_PARENT_SOURCES_PATH\s*=\s*"data/v4\.13/diagnosis_v413_ablation_sources\.json"',
        source,
    ), "EXPECTED_PARENT_SOURCES_PATH 定数が見つからない (Blocker 4 修正の path 定数化)"

    # 4. 動的 SHA 計算呼び出しの存在
    assert re.search(
        r"_sha256_of_file\(\s*Path\(\s*EXPECTED_PARENT_SOURCES_PATH\s*\)\s*\)",
        source,
    ), "_sha256_of_file(Path(EXPECTED_PARENT_SOURCES_PATH)) 呼び出しが見つからない"

    # 5. frontmatter dict に expected_parent_sources_sha256 key 存在 (D-107-03 spec)
    assert re.search(
        r'"expected_parent_sources_sha256"\s*:',
        source,
    ), "frontmatter dict に expected_parent_sources_sha256 key が無い (D-107-03 違反)"

    # 6. deferred frontmatter key の不在 (CONTEXT.md L148, Blocker 2 修正)
    # 対象は **frontmatter_dict リテラル定義ブロック内 の dict key 出現** に限定。
    # 入力 dict access (例: `score_dict["trivial_baseline_pathway"]`) は input artifact
    # のフィールド読み取りなので除外する (Rule 1: false-positive 回避)。
    deferred_keys = ("pathway_branch", "top_axis", "trivial_baseline_pathway")
    fm_match = re.search(
        r"frontmatter_dict\s*=\s*\{([^}]*)\}",
        source,
        flags=re.DOTALL,
    )
    assert fm_match, (
        "frontmatter_dict literal が main() 内で見つからない "
        "(Blocker 2 修正で削除された可能性、D-107-03 spec 違反)"
    )
    fm_body = fm_match.group(1)
    for key in deferred_keys:
        # dict key 形式 ("key":) で出現していないこと
        assert not re.search(rf'"{re.escape(key)}"\s*:', fm_body), (
            f'deferred frontmatter key "{key}" が frontmatter_dict literal に登場している '
            "(D-107-03 / CONTEXT L148 違反 — Blocker 2 修正で除去されたはず)"
        )
