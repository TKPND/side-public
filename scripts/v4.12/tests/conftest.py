"""
conftest.py — Phase 101 Wave 0 Task 2: pytest infrastructure for v4.12.

- Injects scripts/v4.12 onto sys.path (the dotted directory name prevents normal
  Python package import).
- Loads HF_COMMIT_SHA.json (Task 3) and exposes via fixture.
- Exposes path constants for the macro_stance_labels.csv frozen fixture (Wave 1).

Citations: 101-01-PLAN.md Task 2, D-69 (LABEL_MAP), D-71 (parquet schema).

Phase 102 additions (Wave 0, 102-01-PLAN.md):
- _V411_DIR sys.path injection (ship_metrics_emitter_v411 import)
- baseline_ship_decision_path / baseline_ship_decision_json (D-05)
- cells_post_filter_monkeypatch factory fixture (D-17 emitter UNTOUCHED)
- expected_input_sha256 (RESEARCH Pitfall 4 drift detection)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ── sys.path injection (scripts/v4.12 is not importable as a package) ──
_V412_DIR = Path(__file__).resolve().parent.parent
if str(_V412_DIR) not in sys.path:
    sys.path.insert(0, str(_V412_DIR))

# ── scripts/v4.11 を sys.path に追加 (ship_metrics_emitter_v411 import 用, D-17) ──
_V411_DIR = _V412_DIR.parent / "v4.11"
if str(_V411_DIR) not in sys.path:
    sys.path.insert(0, str(_V411_DIR))


_HF_COMMIT_SHA_PATH = _V412_DIR / "HF_COMMIT_SHA.json"
_FROZEN_LABELS_CSV = Path("data/v4.12/labels/macro_stance_labels.csv")


@pytest.fixture(scope="session")
def hf_commit_sha() -> dict:
    """Loads HF_COMMIT_SHA.json (Task 3 artifact). Skip if not yet generated."""
    if not _HF_COMMIT_SHA_PATH.exists():
        pytest.skip(
            f"HF_COMMIT_SHA.json not found at {_HF_COMMIT_SHA_PATH}. "
            f"Run: uv run python scripts/v4.12/prewarm_hf_cache.py"
        )
    return json.loads(_HF_COMMIT_SHA_PATH.read_text())


@pytest.fixture(scope="session")
def frozen_labels_csv_path() -> Path:
    """Path to data/v4.12/labels/macro_stance_labels.csv (Wave 1 prerequisite)."""
    return _FROZEN_LABELS_CSV


@pytest.fixture(scope="session")
def v412_dir() -> Path:
    """Absolute path to scripts/v4.12/."""
    return _V412_DIR


# ── PARITY-V412-01 baseline fixtures (D-05) ──────────────────────────────────

_REPO_ROOT = _V412_DIR.parent.parent
_BASELINE_SHIP_DECISION = (
    _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "v4_11_ship_decision.json"
)


@pytest.fixture(scope="session")
def baseline_ship_decision_path() -> Path:
    """v4.11 active_mode ship_decision.json (PARITY-V412-01 diff baseline, D-05)."""
    return _BASELINE_SHIP_DECISION


@pytest.fixture(scope="session")
def baseline_ship_decision_json(baseline_ship_decision_path: Path) -> dict:
    """Parsed dict of v4.11 active_mode ship_decision.json (D-05)."""
    return json.loads(baseline_ship_decision_path.read_text(encoding="utf-8"))


# ── monkeypatch factory (D-17 emitter UNTOUCHED, Pitfall 1 Option A) ─────────


@pytest.fixture
def cells_post_filter_monkeypatch(monkeypatch):
    """Factory: ship_metrics_emitter_v411._CELLS_POST_FILTER を tmp_compound_parquet に差し替える。

    D-17 invariant: emitter 本体は変更せず、test runtime のみ path を差し替える。
    pytest monkeypatch (function scope) が test 終了時に自動 restore する (T-102-03 mitigate)。

    Usage::

        def test_foo(tmp_path, cells_post_filter_monkeypatch):
            emitter_module = cells_post_filter_monkeypatch(tmp_path / "cells_post_compound_filter.parquet")
            doc = emitter_module.build_ship_decision_doc()
    """

    def _patch(tmp_compound_parquet: Path):
        import ship_metrics_emitter_v411 as emitter_module  # type: ignore[import]

        monkeypatch.setattr(emitter_module, "_CELLS_POST_FILTER", tmp_compound_parquet)
        return emitter_module

    return _patch


# ── baseline drift 検出 (RESEARCH Pitfall 4) ─────────────────────────────────


@pytest.fixture(scope="session")
def expected_input_sha256() -> dict:
    """既知入力ファイルの SHA256 (test 先頭で assert してドリフト検出, T-102-01 mitigate).

    値は 102-01-PLAN.md Task 2 実行時に sha256sum で実測して埋めた。
    """
    return {
        "data/v4.11/cells_post_filter.parquet": "b00e9a05f76d85c926f96046860187cd8c009ad12827addbc4e29c2b5abc5c47",
        "reports/v4.11/active_mode/p_adj_v411.json": "0d1bcc2d2e7193f1a80fd8eefa30c939779f1376f9c097d3dcd3d76503335f18",
        "reports/v4.11/active_mode/permutation_null_v411.json": "2ebc60a5c6ab5b50358aae8ff9c47ae1bf5c29220c82f8f04d72efa3958c651a",
        "reports/v4.11/active_mode/v4_11_ship_decision.json": "edeaf07184839130b92bd2944db060beec7881081037a221f9a095cf9d97107a",
    }


# ── Phase 103 Wave 0: cells_post_compound_filter loader (D-03 read-only) ─────

_CELLS_POST_COMPOUND_FILTER = (
    _REPO_ROOT / "data" / "v4.12" / "cells_post_compound_filter.parquet"
)


@pytest.fixture(scope="session")
def cells_post_compound_filter():
    """Phase 102 SHIPPED parquet (192 cells, 6 strata) read-only loader.

    D-03 invariant: Phase 102 artifact is read-only for Phase 103 consumers.
    Skip if file absent (Phase 102 not yet shipped in test env).
    """
    import polars as pl

    if not _CELLS_POST_COMPOUND_FILTER.exists():
        pytest.skip(
            f"Phase 102 SHIPPED parquet not found at {_CELLS_POST_COMPOUND_FILTER}"
        )
    return pl.read_parquet(_CELLS_POST_COMPOUND_FILTER)


@pytest.fixture(scope="session")
def cells_post_compound_filter_path() -> Path:
    """Absolute path to Phase 102 cells_post_compound_filter.parquet (D-03 read-only)."""
    return _CELLS_POST_COMPOUND_FILTER
