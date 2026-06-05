"""Pytest fixtures for calendar anomaly pipeline testing."""

import pytest


@pytest.fixture
def bq_calendar_raw():
    """Mock DataFrame with 90 rows (5 DOW × 3 month-pos × 6 horizons).

    Each row has t_stat in range [-8, 8], n >= 10, mean_pips signed.
    """
    rows = []
    day_of_weeks = [1, 2, 3, 4, 5]  # Mon-Fri
    month_positions = ['early', 'mid', 'late']
    hold_h_values = [1, 2, 4, 8, 12, 24]
    directions = ['long', 'short']

    # Generate 5 × 3 × 6 = 90 rows
    idx = 0
    for dow in day_of_weeks:
        for month_pos in month_positions:
            for hold_h in hold_h_values:
                # Vary t_stat and mean_pips to simulate realistic distribution
                t_stat = -8 + (idx % 16) if idx < 90 else -8
                mean_pips = (idx % 20) - 10  # Signed return
                direction = 'long' if mean_pips > 0 else 'short'

                rows.append({
                    'day_of_week': dow,
                    'month_position': month_pos,
                    'hold_h': hold_h,
                    'direction': direction,
                    't_stat': float(t_stat),
                    'n': 10 + (idx % 50),
                    'mean_pips': float(mean_pips),
                    'bonferroni_pass': abs(t_stat) > 3.45,
                })
                idx += 1

    return rows


@pytest.fixture
def calendar_bonferroni_pass(bq_calendar_raw):
    """Filtered rows where |t_stat| > 3.45."""
    return [r for r in bq_calendar_raw if abs(r['t_stat']) > 3.45]
