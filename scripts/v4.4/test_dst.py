"""Phase 61 CONFIG-04 DST spot-check — verify events.rs hardcoded UTC hours
against authoritative tz database via Python `zoneinfo` (stdlib 3.12+).

Standalone:
    uv run pytest scripts/v4.4/test_dst.py -v

Subprocess invocation (from audit.py run_dst_check):
    subprocess.run(['uv','run','pytest','scripts/v4.4/test_dst.py',
                    '--json-report', '--json-report-file=<tmp>'])

Authoritative values below are mirrored 1:1 from rust/side-engine/src/events.rs
via audit.py's re-declared const arrays (FOMC_DATES_2022_2023 /
FOMC_DATES_2024_2026 / ECB_DATES_2022_2023 / ECB_DATES_2024_2025 /
NFP_DATES_2022_2023 / NFP_DATES_2024_2025). The hour stored here is the
same `hour_utc` that events.rs stores; the test's job is to assert that
value against the hour computed from zoneinfo for the announcement's
local wall-clock time.

Local wall-clock conventions (per events.rs source-of-truth comments):
    - FOMC: 14:00 ET (America/New_York)
    - ECB:  14:15 CET/CEST (Europe/Berlin).
            Note: events.rs module-doc says "13:15 CET / 12:15 CEST" but the
            stored hour_utc values (13 on CET, 12 on CEST) correspond to
            14:15 local. The 13:15 comment appears stale vs the data;
            empirical alignment at 14:15 local is what the stored hours
            reflect, so that is the zoneinfo comparison base.
    - NFP:  08:30 ET (BLS release)

If any parametrized case fails, the zoneinfo-computed hour disagrees with
events.rs — that is a genuine DST drift finding and flows through audit.py
run_dst_check into drift_detected.json["dst_failures"].
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

# ---------------------------------------------------------------------------
# Authoritative (date, expected_utc_hour) tuples, mirrored from events.rs
# via scripts/v4.4/audit.py const arrays. Do NOT edit by hand; regenerate
# from audit.py whenever events.rs changes.
# ---------------------------------------------------------------------------

# FOMC: 14:00 ET announcement. Source: FOMC_DATES_2022_2023 + FOMC_DATES_2024_2026.
FOMC_AUTHORITATIVE: dict[tuple[int, int, int], int] = {
    # v4.1 epoch (2022-23)
    (2022, 1, 26): 19,
    (2022, 3, 16): 18,  # post spring-forward (Mar 13 2022)
    (2022, 5, 4): 18,
    (2022, 6, 15): 18,
    (2022, 7, 27): 18,
    (2022, 9, 21): 18,
    (2022, 11, 2): 18,  # DST ends Nov 6 — Nov 2 is still EDT (UTC-4) → 18 UTC
    (2022, 12, 14): 19,
    (2023, 1, 25): 19,
    (2023, 3, 22): 18,  # post spring-forward (Mar 12 2023)
    (2023, 5, 3): 18,
    (2023, 6, 14): 18,
    (2023, 7, 26): 18,
    (2023, 9, 20): 18,
    (2023, 11, 1): 18,  # DST ends Nov 5 — Nov 1 is still EDT (UTC-4) → 18 UTC
    (2023, 12, 13): 19,
    # v4.2 epoch (2024-26)
    (2024, 1, 31): 19,
    (2024, 3, 20): 18,  # post spring-forward (Mar 10 2024)
    (2024, 5, 1): 18,
    (2024, 6, 12): 18,
    (2024, 7, 31): 18,
    (2024, 9, 18): 18,
    (2024, 11, 7): 19,  # post fall-back (Nov 3 2024) — EST
    (2024, 12, 18): 19,
    (2025, 1, 29): 19,
    (2025, 3, 19): 18,  # post spring-forward (Mar 9 2025)
    (2025, 5, 7): 18,
    (2025, 6, 18): 18,
    (2025, 7, 30): 18,
    (2025, 9, 17): 18,
    (2025, 10, 29): 18,  # pre fall-back (Nov 2 2025) — still EDT
    (2025, 12, 10): 19,
    (2026, 1, 28): 19,
    (2026, 3, 18): 18,  # post spring-forward (Mar 8 2026)
}

# ECB: 14:15 CET/CEST announcement (Europe/Berlin tz).
# Source: ECB_DATES_2022_2023 + ECB_DATES_2024_2025.
ECB_AUTHORITATIVE: dict[tuple[int, int, int], int] = {
    (2022, 2, 3): 13,
    (2022, 3, 10): 13,
    (2022, 4, 14): 12,
    (2022, 6, 9): 12,
    (2022, 7, 21): 12,
    (2022, 9, 8): 12,
    (2022, 10, 27): 12,  # DST ends Oct 30 — Oct 27 still CEST (UTC+2) → 12 UTC
    (2022, 12, 15): 13,
    (2023, 2, 2): 13,
    (2023, 3, 16): 13,
    (2023, 5, 4): 12,
    (2023, 6, 15): 12,
    (2023, 7, 27): 12,
    (2023, 9, 7): 12,
    (2023, 10, 26): 12,
    (2023, 12, 7): 13,
    (2024, 1, 25): 13,
    (2024, 3, 7): 13,
    (2024, 4, 11): 12,  # post EU spring-forward (Mar 31 2024) — CEST (UTC+2) → 12 UTC
    (2024, 6, 6): 12,
    (2024, 7, 18): 12,
    (2024, 9, 12): 12,
    (2024, 10, 17): 12,  # pre EU fall-back (Oct 27 2024) — CEST
    (2024, 12, 12): 13,
    (2025, 1, 30): 13,
    (2025, 3, 6): 13,
    (2025, 4, 17): 12,
    (2025, 6, 5): 12,
    (2025, 7, 24): 12,
    (2025, 9, 11): 12,
    (2025, 10, 30): 13,  # post EU fall-back (Oct 26 2025) — CET (UTC+1) → 13 UTC
    (2025, 12, 18): 13,
}

# NFP: 08:30 ET release. Source: NFP_DATES_2022_2023 + NFP_DATES_2024_2025.
NFP_AUTHORITATIVE: dict[tuple[int, int, int], int] = {
    (2022, 1, 7): 13,
    (2022, 2, 4): 13,
    (2022, 3, 4): 13,  # pre US spring-forward (Mar 13 2022)
    (2022, 4, 1): 12,  # post spring-forward — EDT
    (2022, 5, 6): 12,
    (2022, 6, 3): 12,
    (2022, 7, 1): 12,
    (2022, 8, 5): 12,
    (2022, 9, 2): 12,
    (2022, 10, 7): 12,
    (2022, 11, 4): 12,  # DST ends Nov 6 — Nov 4 still EDT (UTC-4) → 12 UTC
    (2022, 12, 2): 13,
    (2023, 1, 6): 13,
    (2023, 2, 3): 13,
    (2023, 3, 10): 13,  # pre spring-forward (Mar 12 2023)
    (2023, 4, 7): 12,
    (2023, 5, 5): 12,
    (2023, 6, 2): 12,
    (2023, 7, 7): 12,
    (2023, 8, 4): 12,
    (2023, 9, 1): 12,
    (2023, 10, 6): 12,
    (2023, 11, 3): 12,  # DST ends Nov 5 — Nov 3 still EDT (UTC-4) → 12 UTC
    (2023, 12, 1): 13,
    (2024, 1, 5): 13,
    (2024, 2, 2): 13,
    (2024, 3, 8): 13,  # pre spring-forward (Mar 10 2024)
    (2024, 4, 5): 12,
    (2024, 5, 3): 12,
    (2024, 6, 7): 12,
    (2024, 7, 5): 12,
    (2024, 8, 2): 12,
    (2024, 9, 6): 12,
    (2024, 10, 4): 12,
    (2024, 11, 1): 12,  # pre fall-back (Nov 3 2024) — still EDT
    (2024, 12, 6): 13,
    (2025, 1, 10): 13,
    (2025, 2, 7): 13,
    (2025, 3, 7): 13,  # pre spring-forward (Mar 9 2025)
    (2025, 4, 4): 12,
    (2025, 5, 2): 12,
    (2025, 6, 6): 12,
    (2025, 7, 3): 12,
    (2025, 8, 1): 12,
    (2025, 9, 5): 12,
    (2025, 12, 16): 13,
}

ET = ZoneInfo("America/New_York")
CET = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")


def expected_utc_hour_et(
    year: int,
    month: int,
    day: int,
    et_hour: int,
    et_minute: int = 0,
) -> int:
    """Convert ET wall-clock to UTC hour via zoneinfo."""
    local = datetime(year, month, day, et_hour, et_minute, tzinfo=ET)
    return local.astimezone(UTC).hour


def expected_utc_hour_cet(
    year: int,
    month: int,
    day: int,
    cet_hour: int,
    cet_minute: int = 15,
) -> int:
    """Convert CET/CEST wall-clock to UTC hour via zoneinfo."""
    local = datetime(year, month, day, cet_hour, cet_minute, tzinfo=CET)
    return local.astimezone(UTC).hour


@pytest.mark.parametrize("date,expected_utc_hour", sorted(FOMC_AUTHORITATIVE.items()))
def test_fomc_utc_hour_matches_zoneinfo(date, expected_utc_hour):
    """events.rs FOMC hour must match zoneinfo-derived 14:00 ET → UTC."""
    y, m, d = date
    actual = expected_utc_hour_et(y, m, d, 14, 0)
    assert actual == expected_utc_hour, (
        f"FOMC {date}: events.rs hardcoded UTC hour={expected_utc_hour}, "
        f"zoneinfo computes {actual} for 14:00 ET — DST drift detected"
    )


@pytest.mark.parametrize("date,expected_utc_hour", sorted(ECB_AUTHORITATIVE.items()))
def test_ecb_utc_hour_matches_zoneinfo(date, expected_utc_hour):
    """events.rs ECB hour must match zoneinfo-derived 14:15 CET → UTC."""
    y, m, d = date
    actual = expected_utc_hour_cet(y, m, d, 14, 15)
    assert actual == expected_utc_hour, (
        f"ECB {date}: events.rs hardcoded UTC hour={expected_utc_hour}, "
        f"zoneinfo computes {actual} for 14:15 CET — DST drift detected"
    )


@pytest.mark.parametrize("date,expected_utc_hour", sorted(NFP_AUTHORITATIVE.items()))
def test_nfp_utc_hour_matches_zoneinfo(date, expected_utc_hour):
    """events.rs NFP hour must match zoneinfo-derived 08:30 ET → UTC."""
    y, m, d = date
    actual = expected_utc_hour_et(y, m, d, 8, 30)
    assert actual == expected_utc_hour, (
        f"NFP {date}: events.rs hardcoded UTC hour={expected_utc_hour}, "
        f"zoneinfo computes {actual} for 08:30 ET — DST drift detected"
    )
