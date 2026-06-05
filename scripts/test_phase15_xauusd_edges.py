"""Phase 15 validation: XAUUSD BQ fullscan schema contract tests.

Validates that:
- scripts/bq_xauusd_fullscan.sql references xauusd_ticks (not usdjpy_ticks)
- data/xauusd_edges.json exists and is valid JSON
- All required edge fields are present
- asset == "XAUUSD"
- source_query == "bq_xauusd_fullscan.sql"
- timeframe field exists (value changed 1h→1m by Phase 16 fix)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SQL_FILE = REPO_ROOT / "scripts" / "bq_xauusd_fullscan.sql"
EDGES_FILE = REPO_ROOT / "data" / "xauusd_edges.json"

REQUIRED_FIELDS = [
    "entry_minute",
    "direction",
    "hold_h_candidates",
    "t_stat",
    "bh_q",
    "dsr_p",
    "source_query",
    "asset",
    "timeframe",
]


@pytest.fixture(scope="module")
def edges() -> list[dict]:
    assert EDGES_FILE.exists(), f"{EDGES_FILE} が存在しない"
    return json.loads(EDGES_FILE.read_text())


def test_sql_uses_xauusd_ticks() -> None:
    """T1: SQL が xauusd_ticks を参照し usdjpy_ticks を含まないこと。"""
    assert SQL_FILE.exists(), f"{SQL_FILE} が存在しない"
    content = SQL_FILE.read_text()
    assert "xauusd_ticks" in content, "xauusd_ticks が SQL に見つからない"
    assert "usdjpy_ticks" not in content, "usdjpy_ticks が SQL に残っている (置換漏れ)"


def test_edges_json_exists_and_valid() -> None:
    """T3: edges.json が存在し valid JSON であること。"""
    assert EDGES_FILE.exists(), f"{EDGES_FILE} が存在しない"
    data = json.loads(EDGES_FILE.read_text())
    assert isinstance(data, list), "edges.json のトップレベルが list でない"


def test_edges_schema_required_fields(edges: list[dict]) -> None:
    """T4: 全 edges に必須フィールドが揃っていること。"""
    if not edges:
        pytest.skip("edges が空 (McLean-Pontiff 枠) — スキーマ検証スキップ")
    for i, edge in enumerate(edges):
        for field in REQUIRED_FIELDS:
            assert field in edge, f"Edge {i}: 必須フィールド '{field}' が欠けている"


def test_edges_asset_is_xauusd(edges: list[dict]) -> None:
    """T4: 全 edges の asset が XAUUSD であること。"""
    if not edges:
        pytest.skip("edges が空")
    for i, edge in enumerate(edges):
        assert edge["asset"] == "XAUUSD", (
            f"Edge {i}: asset={edge['asset']} (期待: XAUUSD)"
        )


def test_edges_source_query(edges: list[dict]) -> None:
    """T4: 全 edges の source_query が bq_xauusd_fullscan.sql であること。"""
    if not edges:
        pytest.skip("edges が空")
    for i, edge in enumerate(edges):
        assert edge["source_query"] == "bq_xauusd_fullscan.sql", (
            f"Edge {i}: source_query={edge['source_query']}"
        )


def test_edges_timeframe_field_exists(edges: list[dict]) -> None:
    """T4: timeframe フィールドが存在すること。

    Note: Phase 15 では '1h' で生成。Phase 16 で '1m' に修正済み (意図的)。
    値は固定しない — フィールド存在のみを検証。
    """
    if not edges:
        pytest.skip("edges が空")
    for i, edge in enumerate(edges):
        assert "timeframe" in edge, f"Edge {i}: timeframe フィールドが欠けている"
        assert edge["timeframe"] in ("1h", "1m", "5m", "15m", "30m"), (
            f"Edge {i}: timeframe={edge['timeframe']} — 想定外の値"
        )
