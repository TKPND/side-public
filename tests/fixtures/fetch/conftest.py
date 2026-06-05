"""Shared fixtures for fetch test suite (Phase 97 Parquet-only).

Provides:
  - fetch_fixture_dir: Path to this conftest's directory
  - reference_tick_parquet: Path to the Phase 97 tick Parquet fixture (D-03)
  - reference_1h_parquet: Path to the Phase 97 1h Parquet fixture (D-04)
  - expected_tick_parquet_sha256: SHA-256 hex digest for the tick Parquet
    (loaded from FIXTURE_HASHES.txt, single source of truth)
"""

from pathlib import Path

import pytest


@pytest.fixture
def fetch_fixture_dir() -> Path:
    """Return the directory containing this conftest."""
    return Path(__file__).parent


@pytest.fixture
def reference_tick_parquet(fetch_fixture_dir: Path) -> Path:
    """Path to the Phase 97 tick Parquet reference (USDJPY 2024-01-08, baked Wave 0)."""
    return fetch_fixture_dir / "reference" / "usdjpy_ticks_2024-01-08.parquet"


@pytest.fixture
def reference_1h_parquet(fetch_fixture_dir: Path) -> Path:
    """Path to the Phase 97 1h Parquet reference (USDJPY sample, baked Wave 0)."""
    return fetch_fixture_dir / "reference" / "usdjpy_1h_sample.parquet"


@pytest.fixture
def expected_tick_parquet_sha256(fetch_fixture_dir: Path) -> str:
    """SHA-256 hex digest of the tick Parquet, from FIXTURE_HASHES.txt (single source of truth)."""
    hashes_path = fetch_fixture_dir / "reference" / "FIXTURE_HASHES.txt"
    for line in hashes_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("usdjpy_ticks_2024-01-08.parquet"):
            return line.split()[1]
    raise RuntimeError(
        "hash row for usdjpy_ticks_2024-01-08.parquet not found in FIXTURE_HASHES.txt"
    )
