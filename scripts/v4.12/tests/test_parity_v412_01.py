"""PARITY-V412-01 acceptance gate (FILT-V412-03).

Phase 102 acceptance gate (D-06/D-07/D-08/D-13):
  - --neutral-mode-macro bypass で v4.11 ship_metrics 6 fields を bit-exact 再現
  - 比較対象: reports/v4.11/active_mode/v4_11_ship_decision.json (D-05)
  - 比較方法: jq -cS '{ship_metrics: {edge_count_p_adj_005, ship_verdict,
              turnover_sharpe_median, es_median, coverage_tier, data_provenance}}' (D-13)

関連 D-decision:
  D-04 code path 分岐 / D-05 baseline / D-06 pytest / D-07 CLI E2E /
  D-08 emitter 経由 / D-09 additive schema / D-13 6 fields select / D-17 emitter UNTOUCHED

TDD state: RED (Wave 0, 102-01-PLAN.md)
  - macro_stance_filter.py 未実装のため全 test が CalledProcessError で FAIL する
  - Plan 02 で macro_stance_filter.py を実装すると GREEN になる
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import polars as pl

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_CLI_SCRIPT = _REPO_ROOT / "scripts" / "v4.12" / "macro_stance_filter.py"
_BASELINE_JSON = (
    _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "v4_11_ship_decision.json"
)
_BASELINE_FIXTURE = (
    _REPO_ROOT
    / "scripts"
    / "v4.12"
    / "tests"
    / "fixtures"
    / "parity_v412_01_baseline.json"
)

# D-13: 6 fields explicit select (turnover_sharpe_median / es_median は primary_metrics 配下)
_JQ_SELECT = (
    "{ship_metrics: {"
    "edge_count_p_adj_005: .ship_metrics.edge_count_p_adj_005, "
    "ship_verdict: .ship_metrics.ship_verdict, "
    "turnover_sharpe_median: .ship_metrics.primary_metrics.turnover_sharpe_median, "
    "es_median: .ship_metrics.primary_metrics.es_median, "
    "coverage_tier: .ship_metrics.coverage_tier, "
    "data_provenance: .ship_metrics.data_provenance}}"
)


def _run_filter(tmp_path: pathlib.Path, neutral_mode_macro: bool) -> pathlib.Path:
    """macro_stance_filter.py を CLI 起動 (D-07)。出力 parquet path を返す。

    Plan 02 完了前は _CLI_SCRIPT が存在しないため CalledProcessError → test RED。
    """
    cmd = [
        "uv",
        "run",
        "python",
        str(_CLI_SCRIPT),
        "--output-dir",
        str(tmp_path),
    ]
    if neutral_mode_macro:
        cmd.append("--neutral-mode-macro")  # D-04 code path 分岐
    result = subprocess.run(
        cmd, cwd=_REPO_ROOT, capture_output=True, text=True, check=True
    )
    out = tmp_path / "cells_post_compound_filter.parquet"
    assert out.exists(), f"expected {out} to be emitted; stderr:\n{result.stderr}"
    return out


def _jq_normalize(json_path: pathlib.Path) -> str:
    """jq -cS で D-13 6 fields explicit select 正規化。"""
    result = subprocess.run(
        ["jq", "-cS", _JQ_SELECT, str(json_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# ── Test 1: FILT-V412-01 row count invariant ─────────────────────────────────


def test_compound_filter_row_count(tmp_path):
    """active mode (--neutral-mode-macro なし) で行 drop ゼロ (D-01 / D-09).

    FILT-V412-01: 192 cell * events = 864 rows。pass_flag は Boolean。
    """
    out_path = _run_filter(tmp_path, neutral_mode_macro=False)
    df = pl.read_parquet(out_path)
    assert df.height == 864, (
        f"expected 864 rows (192 cells * events, D-09 additive schema), got {df.height}"
    )
    assert df.schema["pass_flag"] == pl.Boolean, (
        f"pass_flag must be Boolean, got {df.schema['pass_flag']}"
    )


# ── Test 2: FILT-V412-02 additive schema ─────────────────────────────────────


def test_parquet_schema(tmp_path):
    """cells_post_compound_filter.parquet schema = base + stance 直交 column (D-09).

    FILT-V412-02: 4 columns = cell_id / pass_flag / bucket / stance。
    stance は Utf8 (NULL 許容, D-12: NULL stance は pass_flag 維持)。
    """
    out_path = _run_filter(tmp_path, neutral_mode_macro=False)
    df = pl.read_parquet(out_path)
    cols = set(df.columns)
    expected = {"cell_id", "pass_flag", "bucket", "stance"}
    assert cols == expected, (
        f"schema mismatch — unexpected: {cols - expected}, missing: {expected - cols}"
    )
    assert df.schema["stance"] == pl.Utf8, (
        f"stance must be Utf8 (NULL-able), got {df.schema['stance']}"
    )


# ── Test 3: FILT-V412-03 PARITY-V412-01 bit-exact ────────────────────────────


def test_parity_v412_01(
    tmp_path,
    cells_post_filter_monkeypatch,
    baseline_ship_decision_path,
):
    """--neutral-mode-macro → emitter (monkeypatch) → jq -cS 6 fields = baseline と bit-exact.

    FILT-V412-03 / PARITY-V412-01:
      D-07 CLI E2E → D-17 monkeypatch (emitter UNTOUCHED) → D-08 emitter 経由 → D-13 jq diff
    """
    # 1. Filter CLI 起動 (D-07 / D-04 bypass)
    compound_parquet = _run_filter(tmp_path, neutral_mode_macro=True)

    # 2. Emitter の _CELLS_POST_FILTER を monkeypatch で差し替え (D-17)
    emitter_module = cells_post_filter_monkeypatch(compound_parquet)

    # 3. ship_decision doc 生成 → tmp_path に書き出し (D-08)
    doc = emitter_module.build_ship_decision_doc()
    generated_json = tmp_path / "v4_12_parity_ship_decision.json"
    generated_json.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    # 4. jq -cS 6 fields 正規化 (D-13)
    baseline_norm = _jq_normalize(baseline_ship_decision_path)
    generated_norm = _jq_normalize(generated_json)

    # 5. bit-exact assert
    assert baseline_norm == generated_norm, (
        f"PARITY-V412-01 FAIL (not bit-exact after --neutral-mode-macro)\n"
        f"  baseline:  {baseline_norm}\n"
        f"  generated: {generated_norm}"
    )


# ── Test 4: FILT-V412-03 補強 (diff exit code) ───────────────────────────────


def test_jq_diff_exit_code(
    tmp_path,
    cells_post_filter_monkeypatch,
    baseline_ship_decision_path,
):
    """diff コマンドで exit=0 を確認 (CI / shell スクリプト互換性検証).

    FILT-V412-03 補強: jq -cS 正規化テキストを diff にかけ returncode=0 を assert。
    T-102-02 (baseline fixture drift) の外部ツール経路での確認。
    """
    compound_parquet = _run_filter(tmp_path, neutral_mode_macro=True)
    emitter_module = cells_post_filter_monkeypatch(compound_parquet)
    doc = emitter_module.build_ship_decision_doc()
    generated_json = tmp_path / "v4_12_parity_ship_decision.json"
    generated_json.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    baseline_txt = tmp_path / "baseline.norm.txt"
    generated_txt = tmp_path / "generated.norm.txt"
    baseline_txt.write_text(
        _jq_normalize(baseline_ship_decision_path) + "\n", encoding="utf-8"
    )
    generated_txt.write_text(_jq_normalize(generated_json) + "\n", encoding="utf-8")

    diff_result = subprocess.run(
        ["diff", str(baseline_txt), str(generated_txt)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert diff_result.returncode == 0, (
        f"jq -cS diff exit={diff_result.returncode} (T-102-02 drift detected)\n"
        f"{diff_result.stdout}"
    )


# ── 補助 test: fixture snapshot drift 検出 (T-102-02) ────────────────────────


def test_baseline_fixture_matches_source(baseline_ship_decision_path):
    """fixtures/parity_v412_01_baseline.json と source v4_11_ship_decision.json の 6 fields が一致.

    T-102-02: baseline fixture が source JSON とずれていないかを早期検出。
    source が変わった場合はこの test が fail し、Task 2 の再実行を要求する。
    """
    from_source = _jq_normalize(baseline_ship_decision_path)
    from_fixture = _BASELINE_FIXTURE.read_text(encoding="utf-8").strip()
    assert from_source == from_fixture, (
        "baseline fixture drift detected — "
        "re-run 102-01-PLAN.md Task 2 to regenerate parity_v412_01_baseline.json"
    )
