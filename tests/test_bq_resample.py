"""Unit tests for scripts/bq_resample_ohlcv.py --pair flag."""

import sys

import pytest

sys.path.insert(0, "scripts")
from bq_resample_ohlcv import DEFAULT_TABLE, PAIR_TABLE_MAP, build_sql  # noqa: E402


def test_pair_table_mapping():
    """PAIR_TABLE_MAP に USDJPY/BTCUSD/ETHUSD が含まれること。"""
    assert PAIR_TABLE_MAP["USDJPY"] == "usdjpy_ticks"
    assert PAIR_TABLE_MAP["BTCUSD"] == "btcusd_ticks"
    assert PAIR_TABLE_MAP["ETHUSD"] == "ethusd_ticks"


def test_usdjpy_default_backward_compat():
    """--pair 未指定相当: build_sql に DEFAULT_TABLE が渡ると usdjpy_ticks が SQL に含まれる。"""
    sql = build_sql("1m", "2025-01-01", "2025-02-01", table=DEFAULT_TABLE)
    assert "usdjpy_ticks" in sql


def test_btcusd_table_in_sql():
    """--pair BTCUSD 相当: build_sql に btcusd_ticks を渡すと SQL に反映される。"""
    sql = build_sql("5m", "2025-01-01", "2025-02-01", table="btcusd_ticks")
    assert "btcusd_ticks" in sql
    assert "usdjpy_ticks" not in sql


@pytest.mark.parametrize(
    "pair,expected_table",
    [
        ("USDJPY", "usdjpy_ticks"),
        ("BTCUSD", "btcusd_ticks"),
        ("ETHUSD", "ethusd_ticks"),
    ],
)
def test_pair_resolves_in_sql(pair: str, expected_table: str):
    """全 pair で PAIR_TABLE_MAP → build_sql の SQL 反映を確認する。"""
    sql = build_sql("1m", "2025-01-01", "2025-02-01", table=PAIR_TABLE_MAP[pair])
    assert expected_table in sql
    # 他 pair のテーブルが混入しないことも確認
    for other_table in PAIR_TABLE_MAP.values():
        if other_table != expected_table:
            assert other_table not in sql
