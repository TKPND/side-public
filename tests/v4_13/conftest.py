"""conftest.py — Phase 104 Wave 0: pytest infrastructure for v4.13.

- Injects scripts/v4.13 onto sys.path (the dotted directory name prevents normal
  Python package import).
- Exposes PROJECT_ROOT fixture and 9-source SHA256 drift-detection dict.
- Skip-if-absent loaders for diagnosis_v413.parquet / diagnosis_v413_sources.json
  (Wave 0 RED state — Phase 104 aggregator not yet emitted).

Citations:
- 104-01-PLAN.md Task 1
- 104-CONTEXT.md D-V413-03 (source mapping table) / D-V413-07 (canonical bytes)
- 104-PATTERNS.md セクション E (sys.path injection) / F (drift detection sha256 dict)
- analog: scripts/v4.12/tests/conftest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ── sys.path injection (scripts/v4.13 is not importable as a package, dotted dir) ──
# tests/v4_13/conftest.py → parents[2] が repo root (parents[0]=v4_13, parents[1]=tests)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_V413_DIR = _REPO_ROOT / "scripts" / "v4.13"
if str(_V413_DIR) not in sys.path:
    sys.path.insert(0, str(_V413_DIR))


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Repository root path."""
    return _REPO_ROOT


@pytest.fixture(scope="session")
def expected_input_sha256() -> dict:
    """9 元 artifact の SHA256 (104-INVENTORY-v412.md sha256_baseline と同期)."""
    return {
        "data/v4.9/power_budget_v49.json": "0e5cce191429f37beaee92cccbfa2de7a016e36f6f91150e3c6d97aa44fc2608",
        "reports/v4.10/per_cell_metrics.json": "1c811137386dcc23bf7ae04b845bf28613dde389d9ec3c8fe855ab113cb87e4c",
        "reports/v4.10/p_adj_v410.json": "9bdad7287f304d959d4bc70bfdc5e22eef60db2045d4ce57d2bd264f1b9912f0",
        "data/v4.11/cells_post_filter.parquet": "b00e9a05f76d85c926f96046860187cd8c009ad12827addbc4e29c2b5abc5c47",
        "reports/v4.11/active_mode/p_adj_v411.json": "0d1bcc2d2e7193f1a80fd8eefa30c939779f1376f9c097d3dcd3d76503335f18",
        "reports/v4.11/active_mode/permutation_null_v411.json": "2ebc60a5c6ab5b50358aae8ff9c47ae1bf5c29220c82f8f04d72efa3958c651a",
        "data/v4.12/cells_post_compound_filter.parquet": "1f4b31c953a7ca183b46953f6852d7849b49a66d1e7fb40e1edda035f6206b79",
        "data/v4.12/p_adj_v412.json": "ba078f3e81e26a281968b119f656111afd3c7d38b8bb623c815b0e600ff19451",
        "data/v4.12/permutation_null_v412.json": "5f754bdbe8e4cba6845ad86ecfc418cd20d892d9197a67b057e4ddd0723c5633",
    }


@pytest.fixture(scope="session")
def diagnosis_v413_parquet():
    """Phase 104 emitted parquet read-only loader.

    Phase 105 Wave 2 で `data/v4.13/diagnosis_v413.parquet` は in-place 上書き
    (12 列/v4.13.0 → 13 列/v4.13.1) されるため、Phase 104 contract test
    (test_aggregate_schema / test_aggregate_rowcount) は W5 1-shot backup
    `.phase104_backup` を真実として参照する.

    Phase 104 contract:
        - 12 列 canonical order (failure_mode 列を含まない)
        - schema_version='v4.13.0'
        - 480 行 (Phase 105 でも保持)
    """
    import polars as pl

    backup = _REPO_ROOT / "data" / "v4.13" / "diagnosis_v413.parquet.phase104_backup"
    live = _REPO_ROOT / "data" / "v4.13" / "diagnosis_v413.parquet"
    # Phase 105 W5 backup を優先 (in-place upgrade 後でも Phase 104 contract を保証)
    path = backup if backup.exists() else live
    if not path.exists():
        pytest.skip(f"Phase 104 parquet not yet emitted: {path}")
    return pl.read_parquet(path)


@pytest.fixture(scope="session")
def diagnosis_v413_sidecar():
    """Phase 104 sidecar JSON loader. Skip if absent."""
    path = _REPO_ROOT / "data" / "v4.13" / "diagnosis_v413_sources.json"
    if not path.exists():
        pytest.skip(f"Phase 104 sidecar not yet emitted: {path}")
    return json.loads(path.read_text())


# ── Phase 105 fixtures (Wave 0 RED scaffold) ──────────────────────────────────
# B3 反映: phase104_frozen_hashes fixture は **作らない** (circular fixture 排除).
# Phase 104 baseline hash は tests/v4_13/test_d17_invariant.py の module-level
# constants (AGGREGATE_HASH_PHASE104 / DECODERS_HASH_PHASE104) を直接 import.


@pytest.fixture(scope="session")
def phase105_diagnosis_path() -> Path:
    """Phase 105 Wave 2 で in-place 上書きされる diagnosis_v413.parquet の path.

    Wave 0 (本 fixture 利用 test 群) では Phase 104 emit 済の 12 列 480 行版を
    そのまま read する。Wave 2 が `failure_mode` 列追加 + schema_version=v4.13.1 に
    in-place 上書きするため、path は同一 (data/v4.13/diagnosis_v413.parquet)。
    """
    return _REPO_ROOT / "data" / "v4.13" / "diagnosis_v413.parquet"


@pytest.fixture(scope="session")
def phase105_failure_modes_path() -> Path:
    """Phase 105 Wave 2 で新規 emit される failure_modes histogram parquet の path.

    Wave 0 時点では未存在 → test は明示的に `pytest.fail(...)` で RED させる。
    """
    return _REPO_ROOT / "data" / "v4.13" / "diagnosis_v413_failure_modes.parquet"


@pytest.fixture(scope="session")
def phase105_evidence_path() -> Path:
    """Phase 105 Wave 2 で新規 emit される degeneracy_evidence sidecar JSON の path."""
    return _REPO_ROOT / "data" / "v4.13" / "diagnosis_v413_degeneracy_evidence.json"


# ── Phase 106 fixtures (Wave 0 RED scaffold) ──────────────────────────────────


@pytest.fixture(scope="session")
def phase106_ablation_path() -> Path:
    """Phase 106 Wave 1 で emit される ablation parquet path (5×4=20 行 long-format).

    Wave 0 時点では未存在 → test は明示的に pytest.fail(...) で RED させる。
    """
    return _REPO_ROOT / "data" / "v4.13" / "diagnosis_v413_ablation.parquet"


@pytest.fixture(scope="session")
def phase106_score_path() -> Path:
    """Phase 106 Wave 1 で emit される ablation_score.json (Rich schema, D-106-04)."""
    return _REPO_ROOT / "data" / "v4.13" / "ablation_score.json"


@pytest.fixture(scope="session")
def phase106_sources_path() -> Path:
    """Phase 106 Wave 1 で emit される sources sidecar (SHA chain pin)."""
    return _REPO_ROOT / "data" / "v4.13" / "diagnosis_v413_ablation_sources.json"


# ── Phase 107 fixtures (Wave 0 RED scaffold) ──────────────────────────────────


@pytest.fixture
def synthetic_nontrivial_ablation_score() -> dict:
    """non-trivial 分岐テスト用 synthetic ablation_score。

    first_order に 4 軸の異なる値を入れ、top_axis (window=0.34) が一意決定可能。
    """
    return {
        "schema_version": "v4.13.1",
        "trivial_baseline_pathway": False,
        "first_order": {
            "regime_cuts": 0.12,
            "pair": 0.05,
            "window": 0.34,
            "sizing": 0.18,
        },
        "top_axis": "window",
    }


@pytest.fixture
def diagnosis_v413_md_path(project_root: Path) -> Path:
    """data/v4.13/diagnosis_v413.md path fixture (skip-if-absent)。

    Wave 0 RED 状態では未 emit のため pytest.skip。Plan 04 emit 後に GREEN 化。
    """
    path = project_root / "data" / "v4.13" / "diagnosis_v413.md"
    if not path.exists():
        pytest.skip(f"{path} not yet emitted (Plan 04 で生成)")
    return path
