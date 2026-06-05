"""CLI error-path tests for fetch_ticks.py (Phase 96, Wave 2).

Tests verify argparse contract and semantic validation (end > start).
All tests use subprocess so no real network calls are made.
D-07 CSV lock: no pyarrow / parquet in this file.
"""

import os
import subprocess
import sys
from pathlib import Path

# Project root = 3 levels up from this file (tests/fixtures/fetch/ → repo root)
_PROJECT_ROOT = str(Path(__file__).parents[3])

# Inherit env and ensure scripts package is importable in subprocess
_ENV = os.environ.copy()
_ENV["PYTHONPATH"] = _PROJECT_ROOT + (
    os.pathsep + _ENV["PYTHONPATH"] if "PYTHONPATH" in _ENV else ""
)

# Use sys.executable directly (faster than uv run, no dep-resolve overhead)
CLI = [sys.executable, "scripts/fetch_ticks.py"]


def _run(*extra, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        CLI + list(extra),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_ENV,
        cwd=_PROJECT_ROOT,
    )


# ---------------------------------------------------------------------------
# Wave 0 baked tests (kept as-is, skip removed)
# ---------------------------------------------------------------------------


def test_missing_pair_flag_exits_nonzero():
    """--pair is required; omitting it must exit non-zero."""
    result = _run(
        "--source",
        "dukascopy",
        "--interval",
        "tick",
        "--start",
        "2024-01-08",
        "--end",
        "2024-01-09",
        "--out",
        "/tmp/x_fetch_test",
    )
    assert result.returncode != 0


def test_invalid_pair_exits_nonzero():
    """Unknown --pair value must exit non-zero."""
    result = _run(
        "--pair",
        "INVALID",
        "--source",
        "dukascopy",
        "--interval",
        "tick",
        "--start",
        "2024-01-08",
        "--end",
        "2024-01-09",
        "--out",
        "/tmp/x_fetch_test",
    )
    assert result.returncode != 0


def test_invalid_source_exits_nonzero():
    """Unknown --source value must exit non-zero."""
    result = _run(
        "--pair",
        "USDJPY",
        "--source",
        "unknown_source",
        "--interval",
        "tick",
        "--start",
        "2024-01-08",
        "--end",
        "2024-01-09",
        "--out",
        "/tmp/x_fetch_test",
    )
    assert result.returncode != 0


def test_end_before_start_exits_nonzero():
    """--end before --start must exit non-zero (semantic validation in main())."""
    result = _run(
        "--pair",
        "USDJPY",
        "--source",
        "dukascopy",
        "--interval",
        "tick",
        "--start",
        "2024-01-08",
        "--end",
        "2024-01-07",
        "--out",
        "/tmp/x_fetch_test",
    )
    assert result.returncode != 0


def test_help_exits_zero():
    """--help must exit 0 and mention all 7 expected flags."""
    result = _run("--help")
    assert result.returncode == 0
    help_text = result.stdout + result.stderr
    for flag in [
        "--pair",
        "--source",
        "--interval",
        "--start",
        "--end",
        "--out",
        "--for-bq",
    ]:
        assert flag in help_text, f"Missing flag in --help: {flag}"


# ---------------------------------------------------------------------------
# Wave 2 additions (PLAN Task 2-3)
# ---------------------------------------------------------------------------


def test_invalid_date_format_exits_nonzero():
    """Bad --start date format (01-01-2024) must exit non-zero via argparse type=."""
    result = _run(
        "--pair",
        "USDJPY",
        "--source",
        "dukascopy",
        "--interval",
        "tick",
        "--start",
        "01-01-2024",  # wrong format
        "--end",
        "2024-02-01",
        "--out",
        "/tmp/x_fetch_test",
    )
    assert result.returncode != 0


def test_xauusd_accepted_as_pair():
    """XAUUSD must appear in --help output (choices include gold pair)."""
    result = _run("--help")
    assert result.returncode == 0
    assert "XAUUSD" in (result.stdout + result.stderr)


def test_ethusd_accepted_as_pair():
    """ETHUSD must appear in --help output for v5.0 crypto ingest."""
    result = _run("--help")
    assert result.returncode == 0
    assert "ETHUSD" in (result.stdout + result.stderr)
