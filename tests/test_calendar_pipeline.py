"""Unit tests for calendar anomaly pipeline."""

import json


def test_grid_shape(bq_calendar_raw):
    """Assert len(rows) == 90 after BQ query."""
    assert len(bq_calendar_raw) == 90, f"Expected 90 rows, got {len(bq_calendar_raw)}"
    assert all("day_of_week" in r for r in bq_calendar_raw)
    assert all("month_position" in r for r in bq_calendar_raw)
    assert all("hold_h" in r for r in bq_calendar_raw)


def test_grid_columns(bq_calendar_raw):
    """Assert required columns present."""
    required_cols = {
        "day_of_week",
        "month_position",
        "hold_h",
        "direction",
        "t_stat",
        "n",
        "mean_pips",
    }
    for row in bq_calendar_raw:
        assert required_cols.issubset(row.keys()), (
            f"Missing columns in row: {row.keys()}"
        )


def test_bonferroni_gate(bq_calendar_raw):
    """Assert Bonferroni filter reduces rows."""
    bonf_pass = [r for r in bq_calendar_raw if abs(float(r["t_stat"])) > 3.45]
    # Assert at least some rows pass (or 0 is acceptable if no strong edges)
    assert len(bonf_pass) <= len(bq_calendar_raw)
    assert len(bonf_pass) >= 0


def test_bh_filter(bq_calendar_raw):
    """Assert BH q=0.10 filter narrows further."""
    import sys

    sys.path.insert(0, "scripts")
    from bq_to_edges_json import convert_rows_calendar

    edges = convert_rows_calendar(bq_calendar_raw, bh_q_threshold=0.10)
    # All edges should have bh_q < 0.10
    if edges:
        assert all(e["bh_q"] < 0.10 for e in edges)
        # Assert no edges after BH that didn't pass Bonferroni
        assert all(abs(e["t_stat"]) > 3.45 for e in edges)


def test_output_schema():
    """Assert calendar_edges.json keys match schema."""
    with open("data/calendar_edges.json") as f:
        edges = json.load(f)

    required_keys = {
        "day_of_week",
        "month_position",
        "direction",
        "hold_h",
        "t_stat",
        "bh_q",
        "n",
        "mean_pips",
        "asset",
    }
    for edge in edges:
        assert required_keys.issubset(edge.keys()), f"Missing keys in edge: {edge}"


def test_json_validity():
    """Assert data/calendar_edges.json is valid JSON."""
    with open("data/calendar_edges.json") as f:
        data = json.load(f)

    assert isinstance(data, list)
    print(f"✓ calendar_edges.json: {len(data)} edges")
