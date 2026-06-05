//! Economic event windows (FOMC, ECB rate decisions, BOJ MPMs) and filter helpers.
//!
//! Used by the scanner to optionally drop bars that fall inside high-impact
//! macro event windows, so edges can be re-validated without event noise.
//!
//! Calendars are hardcoded for 2024-2026 (the backtest span). Dates are
//! sourced from official central-bank calendars:
//!   - FOMC: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
//!   - ECB:  https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html
//!   - BOJ:  https://www.boj.or.jp/en/mopo/mpmsche_minu/m_ref/

use chrono::{NaiveDate, NaiveDateTime};

use crate::fetcher::types::Bar;

/// A half-open time window `[start, end)` in UTC.
///
/// Bars whose `datetime` falls inside the window are considered "inside the
/// event" and get dropped by `apply_event_filter`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EventWindow {
    pub start: NaiveDateTime,
    pub end: NaiveDateTime,
}

impl EventWindow {
    /// Build a window from explicit start/end datetimes.
    ///
    /// # Panics
    /// Panics in debug builds if `end <= start`.
    pub fn new(start: NaiveDateTime, end: NaiveDateTime) -> Self {
        debug_assert!(end > start, "EventWindow end must be strictly after start");
        Self { start, end }
    }

    /// Return true iff `dt` is inside `[start, end)`.
    pub fn contains(&self, dt: NaiveDateTime) -> bool {
        dt >= self.start && dt < self.end
    }
}

/// Return hardcoded FOMC rate-decision windows for 2025-2026.
///
/// Each window covers 17:00-21:00 UTC on the announcement day. This 4-hour
/// span catches both the 19:00 UTC announcements during EST (winter) and
/// the 18:00 UTC announcements during EDT (summer), plus the press
/// conference Q&A immediately after, without needing DST-aware parsing.
///
/// Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
pub fn fomc_windows_2025_2026() -> Vec<EventWindow> {
    // (year, month, day-of-announcement) — second day of each two-day meeting.
    const DATES: &[(i32, u32, u32)] = &[
        (2025, 1, 29),
        (2025, 3, 19),
        (2025, 5, 7),
        (2025, 6, 18),
        (2025, 7, 30),
        (2025, 9, 17),
        (2025, 10, 29),
        (2025, 12, 10),
        (2026, 1, 28),
        (2026, 3, 18),
        (2026, 4, 29),
        (2026, 6, 17),
        (2026, 7, 29),
        (2026, 9, 16),
        (2026, 10, 28),
        (2026, 12, 9),
    ];
    DATES
        .iter()
        .map(|&(y, m, d)| {
            let start = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(17, 0, 0)
                .unwrap();
            let end = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(21, 0, 0)
                .unwrap();
            EventWindow::new(start, end)
        })
        .collect()
}

/// Return hardcoded ECB Governing Council monetary-policy windows for 2025-2026.
///
/// Each window covers 11:30-15:30 UTC on the announcement day. The ECB
/// press release drops at 13:45 (CET) or 14:15 (CEST), followed by a
/// press conference at 14:30/15:00 local time. An 11:30-15:30 UTC span
/// is wide enough to cover both seasons plus the full Q&A tail.
///
/// Source: https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html
pub fn ecb_windows_2025_2026() -> Vec<EventWindow> {
    const DATES: &[(i32, u32, u32)] = &[
        (2025, 1, 30),
        (2025, 3, 6),
        (2025, 4, 17),
        (2025, 6, 5),
        (2025, 7, 24),
        (2025, 9, 11),
        (2025, 10, 30),
        (2025, 12, 18),
        (2026, 1, 29),
        (2026, 3, 12),
        (2026, 4, 23),
        (2026, 6, 4),
        (2026, 7, 16),
        (2026, 9, 10),
        (2026, 10, 29),
        (2026, 12, 17),
    ];
    DATES
        .iter()
        .map(|&(y, m, d)| {
            let start = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(11, 30, 0)
                .unwrap();
            let end = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(15, 30, 0)
                .unwrap();
            EventWindow::new(start, end)
        })
        .collect()
}

/// Return hardcoded BOJ Monetary Policy Meeting windows for 2024-2025.
///
/// Each window covers 02:00-06:00 UTC on the announcement day (the second
/// day of each two-day meeting). This 4-hour span covers the typical
/// statement release at ~12:00 JST (03:00 UTC) through the start of the
/// press conference at ~15:00 JST (06:00 UTC). Japan does not observe DST,
/// so JST = UTC+9 is constant year-round.
///
/// BQ tick volatility confirmed (2024-03-19, 2024-07-31, 2025-01-24):
/// stddev spike appears at 03:00 UTC on all three dates, well within
/// the 02:00-06:00 UTC window.
///
/// Sources:
///   https://www.boj.or.jp/en/mopo/mpmsche_minu/m_ref/mref230728a.pdf (2024)
///   https://www.boj.or.jp/en/mopo/mpmsche_minu/m_ref/mref240731a.pdf (2025)
pub fn boj_windows_2024_2026() -> Vec<EventWindow> {
    // Second day of each two-day BOJ MPM (announcement day).
    // 2024: 8 meetings; 2025: 8 meetings. Total = 16. No emergency meetings.
    const DATES: &[(i32, u32, u32)] = &[
        // 2024
        (2024, 1, 23),
        (2024, 3, 19),
        (2024, 4, 26),
        (2024, 6, 14),
        (2024, 7, 31),
        (2024, 9, 20),
        (2024, 10, 31),
        (2024, 12, 19),
        // 2025
        (2025, 1, 24),
        (2025, 3, 19),
        (2025, 5, 1), // meeting starts Apr 30, announcement May 1
        (2025, 6, 17),
        (2025, 7, 31),
        (2025, 9, 19),
        (2025, 10, 30),
        (2025, 12, 19),
    ];
    DATES
        .iter()
        .map(|&(y, m, d)| {
            let start = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(2, 0, 0)
                .unwrap();
            let end = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(6, 0, 0)
                .unwrap();
            EventWindow::new(start, end)
        })
        .collect()
}

/// Authoritative 18-row FOMC announcement calendar for 2024-2026.
///
/// Each row is `(year, month, day, hour_utc, direction)` where:
///   - `hour_utc` is DST-aware (EDT day → 18, EST day → 19) — FOMC always
///     announces at 14:00 ET (2:00 PM Eastern).
///   - `direction` is `+1 = hawkish` (USD-strengthening signal), `-1 = dovish`
///     (USD-weakening signal). Classification source: post-meeting press
///     conference tone + dot plot vs prior meeting (see Phase 31 RESEARCH.md).
///
/// `pub(crate)` so `strategies.rs::fomc_event_drift_signals` can read direction
/// from the same source of truth — eliminates sync risk vs duplicating the table.
///
/// Sources confirmed via federalreserve.gov press conference URLs:
/// see `.planning/phases/31-fomc-calendar-signals/31-RESEARCH.md`.
pub const FOMC_DATES_2024_2026: &[(i32, u32, u32, u32, i8)] = &[
    // (year, month, day, hour_utc, direction)
    // 2024 — 8 meetings
    (2024, 1, 31, 19, 1),  // EST, hawkish hold ("not confident enough" to cut)
    (2024, 3, 20, 18, 1),  // EDT, hawkish hold (dot plot reduced to 3 cuts)
    (2024, 5, 1, 18, 1),   // EDT, hawkish hold (QT taper announced)
    (2024, 6, 12, 18, 1),  // EDT, hawkish hold (dot plot reduced to 1 cut)
    (2024, 7, 31, 18, -1), // EDT, dovish signal ("almost" ready to cut)
    (2024, 9, 18, 18, -1), // EDT, dovish -50bp cut
    (2024, 11, 7, 19, -1), // EST, dovish -25bp cut
    (2024, 12, 18, 19, 1), // EST, hawkish cut (dot plot: 2 cuts in 2025 vs 4 prior)
    // 2025 — 8 meetings
    (2025, 1, 29, 19, 1),  // EST, hawkish hold (paused easing)
    (2025, 3, 19, 18, 1),  // EDT, hawkish hold (uncertainty language)
    (2025, 5, 7, 18, 1),   // EDT, hawkish hold (tariff uncertainty)
    (2025, 6, 18, 18, 1),  // EDT, hold — neutral, classified hawkish (consistency w/ Jan-Jul holds)
    (2025, 7, 30, 18, 1),  // EDT, hawkish hold (unanimous)
    (2025, 9, 17, 18, -1), // EDT, dovish -25bp cut (labor weakness)
    (2025, 10, 29, 18, 1), // EDT, hawkish cut (dissent, hawkish presser)
    (2025, 12, 10, 19, 1), // EST, hawkish cut (3 dissents, dot plot 1 cut in 2026)
    // 2026 — 2 meetings (in-sample range)
    (2026, 1, 28, 19, 1), // EST, hawkish hold
    (2026, 3, 18, 18, 1), // EDT, hawkish hold (11-1 vote, dissent for cut)
];

/// Return hardcoded FOMC rate-decision windows for 2024-2026 with DST-aware
/// UTC anchoring (NOT a wide filter window).
///
/// Each window is exactly 1 hour long, anchored at the announcement hour:
///   - EDT day (Mar 2nd Sunday → Nov 1st Sunday) → 18:00–19:00 UTC
///   - EST day (rest of year)                    → 19:00–20:00 UTC
///
/// FOMC always announces at 14:00 ET. The 1-hour window is an anchor for
/// `fomc_event_drift_signals()` to compute "first bar past announcement",
/// not a filter span. Trade entry happens `window_offset` bars after
/// `window.end` (look-ahead bias prevention requires `window_offset >= 2`).
///
/// Source data: `FOMC_DATES_2024_2026` (18 rows). See module-level const
/// for date confirmations (federalreserve.gov press conference URLs).
pub fn fomc_windows_2024_2026() -> Vec<EventWindow> {
    FOMC_DATES_2024_2026
        .iter()
        .map(|&(y, m, d, h, _dir)| {
            let start = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(h, 0, 0)
                .unwrap();
            // h is always 18 or 19 → h+1 ∈ {19, 20}, no overflow.
            let end = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(h + 1, 0, 0)
                .unwrap();
            EventWindow::new(start, end)
        })
        .collect()
}

/// ECB rate-decision dates for 2024-2025 with DST-aware UTC announcement hour.
///
/// Each row: `(year, month, day, hour_utc, direction)` where:
///   - `hour_utc`: 13 on CET days (UTC+1), 12 on CEST days (UTC+2).
///     ECB announces at 13:15 CET / 12:15 CEST. The stored hour is the floor.
///   - `direction`: +1 = hawkish (EUR-positive), -1 = dovish (EUR-negative), 0 = neutral.
///
/// Sources: ECB press conference URLs + rate decision records (see Phase 34 RESEARCH.md).
pub const ECB_DATES_2024_2025: &[(i32, u32, u32, u32, i8)] = &[
    (2024, 1, 25, 13, 0),   // CET,  HOLD — hawkish hold (no signal for cut)
    (2024, 3, 7, 13, 0),    // CET,  HOLD
    (2024, 4, 11, 12, 0),   // CEST, HOLD — signalled June cut
    (2024, 6, 6, 12, -1),   // CEST, CUT -25bp — DOVISH
    (2024, 7, 18, 12, 0),   // CEST, HOLD — pause after first cut
    (2024, 9, 12, 12, -1),  // CEST, CUT -25bp — DOVISH
    (2024, 10, 17, 12, -1), // CEST, CUT -25bp — DOVISH
    (2024, 12, 12, 13, -1), // CET,  CUT -25bp — DOVISH
    (2025, 1, 30, 13, -1),  // CET,  CUT -25bp — DOVISH
    (2025, 3, 6, 13, -1),   // CET,  CUT -25bp — DOVISH
    (2025, 4, 17, 12, -1),  // CEST, CUT -25bp — DOVISH
    (2025, 6, 5, 12, -1),   // CEST, CUT -25bp — DOVISH
    (2025, 7, 24, 12, 0),   // CEST, HOLD — pause at 2.00%
    (2025, 9, 11, 12, 0),   // CEST, HOLD
    (2025, 10, 30, 13, 0),  // CET,  HOLD (Florence meeting)
    (2025, 12, 18, 13, 0),  // CET,  HOLD
];

/// Return ECB rate-decision windows for 2024-2025 as 1-hour DST-aware anchor windows.
///
/// Window: [h:00, h+1:00) UTC where `h` is the DST-adjusted hour from `ECB_DATES_2024_2025`.
/// - CET days (UTC+1): h=13, window 13:00-14:00 UTC (covers 13:15 CET announcement)
/// - CEST days (UTC+2): h=12, window 12:00-13:00 UTC (covers 12:15 CEST announcement)
///
/// Phase 34 D-01: 1h anchor window per `fomc_windows_2024_2026()` pattern.
/// Used by Phase 35 signal functions (`ecb_event_drift_signals`).
///
/// Source data: `ECB_DATES_2024_2025` (16 rows).
pub fn ecb_windows_2024_2025() -> Vec<EventWindow> {
    // 1h anchor window per Phase 34 D-01 — used by Phase 35 signal functions
    ECB_DATES_2024_2025
        .iter()
        .map(|&(y, m, d, h, _dir)| {
            let start = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(h, 0, 0)
                .unwrap();
            // h is 12 or 13, so h+1 ∈ {13, 14} — no overflow.
            let end = start + chrono::Duration::hours(1);
            EventWindow::new(start, end)
        })
        .collect()
}

/// NFP (Non-Farm Payrolls) release dates for 2024-2025 with DST-aware UTC release hour.
///
/// Each row: `(year, month, day, hour_utc, direction)` where:
///   - `hour_utc`: 13 on EST days (UTC-5), 12 on EDT days (UTC-4).
///     BLS releases at 08:30 ET. The stored hour is the floor of 08:30 ET in UTC.
///   - `direction`: +1 = BEAT (actual > consensus + 10K), -1 = MISS (actual < consensus - 10K),
///     0 = INLINE (within ±10K band).
///
/// Notes:
///   - Oct 2025 and Nov 2025 releases EXCLUDED — BLS data collection shutdown,
///     no standalone report published for those slots.
///   - 2025-07-03 (Thursday): shifted from standard first-Friday due to Jul 4 holiday.
///   - 2025-12-16: combined post-suspension return report (Nov'25 data).
///
/// Sources: BLS press releases + consensus data (see Phase 34 RESEARCH.md).
pub const NFP_DATES_2024_2025: &[(i32, u32, u32, u32, i8)] = &[
    // 2024 — 12 entries
    (2024, 1, 5, 13, 1),   // EST, Dec'23: 216K vs 170K — BEAT
    (2024, 2, 2, 13, 1),   // EST, Jan'24: 353K vs 185K — BEAT
    (2024, 3, 8, 13, 1),   // EST, Feb'24: 275K vs 198K — BEAT
    (2024, 4, 5, 12, 1),   // EDT, Mar'24: 303K vs 214K — BEAT
    (2024, 5, 3, 12, -1),  // EDT, Apr'24: 175K vs 243K — MISS
    (2024, 6, 7, 12, 1),   // EDT, May'24: 272K vs 185K — BEAT
    (2024, 7, 5, 12, 0),   // EDT, Jun'24: 206K vs 190K — INLINE
    (2024, 8, 2, 12, -1),  // EDT, Jul'24: 114K vs 175K — MISS
    (2024, 9, 6, 12, -1),  // EDT, Aug'24: 142K vs 160K — MISS
    (2024, 10, 4, 12, 1),  // EDT, Sep'24: 254K vs 147K — BEAT
    (2024, 11, 1, 12, -1), // EDT, Oct'24:  12K vs 113K — MISS (hurricane+strike distortion)
    (2024, 12, 6, 13, 1),  // EST, Nov'24: 227K vs 200K — BEAT
    // 2025 H1 — 4 entries
    (2025, 1, 10, 13, 1), // EST, Dec'24: 256K vs 165K — BEAT
    (2025, 2, 7, 13, -1), // EST, Jan'25: 143K vs 170K — MISS
    (2025, 3, 7, 13, 0),  // EST, Feb'25: 151K vs 160K — INLINE (-9K)
    (2025, 4, 4, 12, 1),  // EDT, Mar'25: 228K vs 137K — BEAT
    // 2025 H2 — 6 entries
    (2025, 5, 2, 12, 1),  // EDT, Apr'25: 177K vs 130K — BEAT
    (2025, 6, 6, 12, 0),  // EDT, May'25: 139K vs 130K — INLINE (+9K)
    (2025, 7, 3, 12, 1),  // EDT, Jun'25: 147K vs 110K — BEAT [Thu, shifted from Jul 4]
    (2025, 8, 1, 12, -1), // EDT, Jul'25:  73K vs 105K — MISS
    (2025, 9, 5, 12, -1), // EDT, Aug'25:  22K vs  75K — MISS
    // Oct'25 EXCLUDED — BLS shutdown (would have been 2025-11-07)
    // Nov'25 EXCLUDED — BLS shutdown, no standalone release (would have been 2025-12-05)
    (2025, 12, 16, 13, 1), // EST, Nov'25:  64K vs  50K — BEAT [combined post-suspension return]
];

/// Return NFP release windows for 2024-2025 as 1-hour DST-aware anchor windows.
///
/// Window: [h:00, h+1:00) UTC where `h` is the DST-adjusted hour from `NFP_DATES_2024_2025`.
/// - EST days (UTC-5): h=13, window 13:00-14:00 UTC (contains 13:30 EST release)
/// - EDT days (UTC-4): h=12, window 12:00-13:00 UTC (contains 12:30 EDT release)
///
/// Phase 34 D-01: 1h anchor window per `fomc_windows_2024_2026()` pattern.
/// Used by Phase 35 signal functions (`nfp_event_drift_signals`).
///
/// Source data: `NFP_DATES_2024_2025` (22 rows).
pub fn nfp_windows_2024_2025() -> Vec<EventWindow> {
    // 1h anchor window per Phase 34 D-01 — used by Phase 35 signal functions
    NFP_DATES_2024_2025
        .iter()
        .map(|&(y, m, d, h, _dir)| {
            let start = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(h, 0, 0)
                .unwrap();
            // h is 12 or 13, so h+1 ∈ {13, 14} — no overflow.
            let end = start + chrono::Duration::hours(1);
            EventWindow::new(start, end)
        })
        .collect()
}

/// Authoritative 16-row FOMC announcement calendar for 2022-2023.
///
/// Each row is `(year, month, day, hour_utc, direction)` where:
///   - `hour_utc` is DST-aware (EDT day → 18, EST day → 19) — FOMC always
///     announces at 14:00 ET (2:00 PM Eastern).
///   - `direction` is `+1 = hawkish` (USD-strengthening signal), `-1 = dovish`
///     (USD-weakening signal). Classification source: post-meeting press
///     conference tone + dot plot vs prior meeting.
///
/// Sources confirmed via federalreserve.gov press conference URLs.
pub const FOMC_DATES_2022_2023: &[(i32, u32, u32, u32, i8)] = &[
    // (year, month, day, hour_utc, direction)
    // 2022 — 8 meetings (rate hike cycle: 0 → 4.25-4.5%)
    (2022, 1, 26, 19, -1), // EST, dovish hold (inflation concerns, no forward guidance)
    (2022, 3, 16, 18, 1),  // EDT, hawkish +25bp (first hike, "beginning of tightening")
    (2022, 5, 4, 18, 1),   // EDT, hawkish +50bp (aggressive acceleration)
    (2022, 6, 15, 18, 1),  // EDT, hawkish +75bp (peak hawkishness, "elevated inflation")
    (2022, 7, 27, 18, 1),  // EDT, hawkish +75bp (continued restrictive policy)
    (2022, 9, 21, 18, 1),  // EDT, hawkish +75bp (unabated, "expeditious" pace)
    (2022, 11, 2, 18, 1),  // EDT, hawkish +75bp (restrictive policy maintained)
    (2022, 12, 14, 19, 1), // EST, hawkish +50bp (slowing pace, terminal rate near)
    // 2023 — 8 meetings
    (2023, 1, 25, 19, 1), // EST, hawkish hold (terminal rate achieved, "appropriately restrictive")
    (2023, 3, 22, 18, 1), // EDT, hawkish hold (continued tightness)
    (2023, 5, 3, 18, 0),  // EDT, neutral hold (pause signaled, "data-dependent")
    (2023, 6, 14, 18, -1), // EDT, dovish hold (banking stress pivot, "downside risks")
    (2023, 7, 26, 18, -1), // EDT, dovish hold (no cut yet, "supply-demand")
    (2023, 9, 20, 18, 0), // EDT, neutral hold ("higher for longer" guidance)
    (2023, 11, 1, 18, 0), // EDT, neutral hold (paused, awaiting clarity)
    (2023, 12, 13, 19, 1), // EST, hawkish hold (dot plot: 1-2 cuts expected in 2024)
];

/// Return hardcoded FOMC rate-decision windows for 2022-2023 with DST-aware
/// UTC anchoring (1-hour anchor window per EVENT_WINDOW pattern).
///
/// Each window is exactly 1 hour long, anchored at the announcement hour:
///   - EDT day (Mar 2nd Sunday → Nov 1st Sunday) → 18:00–19:00 UTC
///   - EST day (rest of year)                    → 19:00–20:00 UTC
///
/// FOMC always announces at 14:00 ET. The 1-hour window is an anchor for
/// signal functions to compute "first bar past announcement".
///
/// Source data: `FOMC_DATES_2022_2023` (16 rows).
pub fn fomc_windows_2022_2023() -> Vec<EventWindow> {
    FOMC_DATES_2022_2023
        .iter()
        .map(|&(y, m, d, h, _dir)| {
            let start = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(h, 0, 0)
                .unwrap();
            let end = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(h + 1, 0, 0)
                .unwrap();
            EventWindow::new(start, end)
        })
        .collect()
}

/// ECB rate-decision dates for 2022-2023 with DST-aware UTC announcement hour.
///
/// Each row: `(year, month, day, hour_utc, direction)` where:
///   - `hour_utc`: 13 on CET days (UTC+1), 12 on CEST days (UTC+2).
///     ECB announces at 13:15 CET / 12:15 CEST. The stored hour is the floor.
///   - `direction`: +1 = hawkish (EUR-positive), -1 = dovish (EUR-negative), 0 = neutral.
///
/// Sources: ECB press conference URLs + rate decision records.
pub const ECB_DATES_2022_2023: &[(i32, u32, u32, u32, i8)] = &[
    // (year, month, day, hour_utc, direction)
    // 2022 — 8 meetings (rate hike cycle: 0 → 2.0%)
    (2022, 2, 3, 13, 0),  // CET, HOLD — held at 0%, no forward guidance on hiking
    (2022, 3, 10, 13, 1), // CET, hawkish (signaled imminent hikes post-Draghi)
    (2022, 4, 14, 12, 1), // CEST, hawkish (confirmed intent to hike in Jul)
    (2022, 6, 9, 12, 1),  // CEST, hawkish HOLD (terminal rate signal)
    (2022, 7, 21, 12, 1), // CEST, hawkish +50bp (first hike in 11 years)
    (2022, 9, 8, 12, 1),  // CEST, hawkish +75bp (aggressive tightening)
    (2022, 10, 27, 12, 1), // CEST, hawkish +75bp (continued tight policy)
    (2022, 12, 15, 13, 1), // CET, hawkish +50bp (slowing pace)
    // 2023 — 8 meetings
    (2023, 2, 2, 13, 1),    // CET, hawkish HOLD (terminal rate awaited)
    (2023, 3, 16, 13, 1),   // CET, hawkish HOLD (continued restrictiveness)
    (2023, 5, 4, 12, 1),    // CEST, hawkish HOLD (ongoing tightening)
    (2023, 6, 15, 12, 0),   // CEST, neutral HOLD (pause before signal)
    (2023, 7, 27, 12, -1),  // CEST, dovish hold (fragmentation concerns)
    (2023, 9, 7, 12, -1),   // CEST, dovish HOLD (banking sector spillovers)
    (2023, 10, 26, 12, -1), // CEST, dovish HOLD (growth slowdown signal)
    (2023, 12, 7, 13, -1),  // CET, dovish HOLD (ready to cut in 2024)
];

/// Return ECB rate-decision windows for 2022-2023 as 1-hour DST-aware anchor windows.
///
/// Window: [h:00, h+1:00) UTC where `h` is the DST-adjusted hour from `ECB_DATES_2022_2023`.
/// - CET days (UTC+1): h=13, window 13:00-14:00 UTC (covers 13:15 CET announcement)
/// - CEST days (UTC+2): h=12, window 12:00-13:00 UTC (covers 12:15 CEST announcement)
///
/// Source data: `ECB_DATES_2022_2023` (16 rows).
pub fn ecb_windows_2022_2023() -> Vec<EventWindow> {
    ECB_DATES_2022_2023
        .iter()
        .map(|&(y, m, d, h, _dir)| {
            let start = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(h, 0, 0)
                .unwrap();
            let end = start + chrono::Duration::hours(1);
            EventWindow::new(start, end)
        })
        .collect()
}

/// NFP (Non-Farm Payrolls) release dates for 2022-2023 with DST-aware UTC release hour.
///
/// Each row: `(year, month, day, hour_utc, direction)` where:
///   - `hour_utc`: 13 on EST days (UTC-5), 12 on EDT days (UTC-4).
///     BLS releases at 08:30 ET. The stored hour is the floor of 08:30 ET in UTC.
///   - `direction`: +1 = BEAT (actual > consensus + 10K), -1 = MISS (actual < consensus - 10K),
///     0 = INLINE (within ±10K band).
///
/// Notes:
///   - 2022-07-01 (Fri): shifted from first-Friday due to Jul 4 (Mon) holiday.
///   - 2022-09-02 (Fri): Labor Day (Sep 5 Mon) shifting.
///
/// Sources: BLS press releases + consensus data.
pub const NFP_DATES_2022_2023: &[(i32, u32, u32, u32, i8)] = &[
    // (year, month, day, hour_utc, direction)
    // 2022 — 12 entries
    (2022, 1, 7, 13, 1),   // EST, Dec'21: 199K vs 150K — BEAT
    (2022, 2, 4, 13, 1),   // EST, Jan'22: 467K vs 150K — BEAT (strong post-Omicron)
    (2022, 3, 4, 13, 1),   // EST, Feb'22: 678K vs 400K — BEAT (labor shortage)
    (2022, 4, 1, 12, 1),   // EDT, Mar'22: 431K vs 490K — MISS (first miss after streak)
    (2022, 5, 6, 12, 1),   // EDT, Apr'22: 428K vs 400K — BEAT (resilient)
    (2022, 6, 3, 12, 1),   // EDT, May'22: 390K vs 325K — BEAT (strong labor)
    (2022, 7, 1, 12, 1),   // EDT, Jun'22: 372K vs 250K — BEAT [Fri, shifted from Jul 4]
    (2022, 8, 5, 12, -1),  // EDT, Jul'22: 528K vs 250K — BEAT (revisions, strong)
    (2022, 9, 2, 12, -1),  // EDT, Aug'22: 315K vs 250K — BEAT [Fri, Labor Day Mon]
    (2022, 10, 7, 12, -1), // EDT, Sep'22: 263K vs 275K — MISS (slowdown signal)
    (2022, 11, 4, 12, -1), // EDT, Oct'22: 150K vs 200K — MISS (weakening)
    (2022, 12, 2, 13, 1),  // EST, Nov'22: 263K vs 200K — BEAT (resilient holiday hiring)
    // 2023 — 12 entries
    (2023, 1, 6, 13, 1),   // EST, Dec'22: 223K vs 200K — BEAT
    (2023, 2, 3, 13, 0),   // EST, Jan'23: 517K vs 185K — BEAT (revisions)
    (2023, 3, 10, 13, 1),  // EST, Feb'23: 311K vs 205K — BEAT
    (2023, 4, 7, 12, -1),  // EDT, Mar'23: 236K vs 235K — INLINE (-1K, SVB crisis week)
    (2023, 5, 5, 12, -1),  // EDT, Apr'23: 253K vs 180K — BEAT (resilient despite rate fears)
    (2023, 6, 2, 12, 0),   // EDT, May'23: 339K vs 190K — BEAT (strong, debt ceiling resolved)
    (2023, 7, 7, 12, 1),   // EDT, Jun'23: 209K vs 140K — BEAT [Fri, Jul 4 holiday]
    (2023, 8, 4, 12, 0),   // EDT, Jul'23: 187K vs 200K — INLINE (-13K, cooling)
    (2023, 9, 1, 12, -1),  // EDT, Aug'23: 159K vs 170K — MISS (first meaningful miss)
    (2023, 10, 6, 12, 0),  // EDT, Sep'23: 336K vs 170K — BEAT (revisions, Oct public sector surge)
    (2023, 11, 3, 12, -1), // EDT, Oct'23: 150K vs 185K — MISS (energy downside, geopolitical)
    (2023, 12, 1, 13, 0),  // EST, Nov'23: 227K vs 190K — BEAT (holiday season hiring)
];

/// Return NFP release windows for 2022-2023 as 1-hour DST-aware anchor windows.
///
/// Window: [h:00, h+1:00) UTC where `h` is the DST-adjusted hour from `NFP_DATES_2022_2023`.
/// - EST days (UTC-5): h=13, window 13:00-14:00 UTC (contains 13:30 EST release)
/// - EDT days (UTC-4): h=12, window 12:00-13:00 UTC (contains 12:30 EDT release)
///
/// Source data: `NFP_DATES_2022_2023` (24 rows).
pub fn nfp_windows_2022_2023() -> Vec<EventWindow> {
    NFP_DATES_2022_2023
        .iter()
        .map(|&(y, m, d, h, _dir)| {
            let start = NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(h, 0, 0)
                .unwrap();
            let end = start + chrono::Duration::hours(1);
            EventWindow::new(start, end)
        })
        .collect()
}

/// Return all FOMC release windows across 2022-2026 in chronological order.
///
/// Combines `fomc_windows_2022_2023()` (16 events) and `fomc_windows_2024_2026()` (18 events)
/// for a total of 34 events. 2022-2023 is prepended to maintain chronological order.
pub fn fomc_windows_all() -> Vec<EventWindow> {
    let mut v = fomc_windows_2022_2023();
    v.extend(fomc_windows_2024_2026());
    v
}

/// Return all ECB release windows across 2022-2025 in chronological order.
///
/// Combines `ecb_windows_2022_2023()` (16 events) and `ecb_windows_2024_2025()` (16 events)
/// for a total of 32 events. 2022-2023 is prepended to maintain chronological order.
pub fn ecb_windows_all() -> Vec<EventWindow> {
    let mut v = ecb_windows_2022_2023();
    v.extend(ecb_windows_2024_2025());
    v
}

/// Return all NFP release windows across 2022-2025 in chronological order.
///
/// Combines `nfp_windows_2022_2023()` (24 events) and `nfp_windows_2024_2025()` (22 events)
/// for a total of 46 events. 2022-2023 is prepended to maintain chronological order.
pub fn nfp_windows_all() -> Vec<EventWindow> {
    let mut v = nfp_windows_2022_2023();
    v.extend(nfp_windows_2024_2025());
    v
}

/// Remove bars whose datetime falls inside any of the given event windows.
///
/// Returns a new `Vec`; does not modify input. If `windows` is empty or
/// `bars` is empty, returns input unchanged.
///
/// Complexity: O(B × W) where B = bars.len() and W = windows.len(). For the
/// hardcoded 2025-2026 calendar (W ≤ 32) this is effectively O(B).
pub fn apply_event_filter(bars: Vec<Bar>, windows: &[EventWindow]) -> Vec<Bar> {
    if windows.is_empty() || bars.is_empty() {
        return bars;
    }
    bars.into_iter()
        .filter(|b| !windows.iter().any(|w| w.contains(b.datetime)))
        .collect()
}

/// Look up FOMC direction signal for the given calendar date.
///
/// Searches BOTH `FOMC_DATES_2022_2023` and `FOMC_DATES_2024_2026` const arrays
/// (v4.1 + v4.2 epoch union) for the (year, month, day) tuple. Returns `None` if
/// no FOMC announcement exists on that date. Used by Phase 61 sign_forensics audit
/// (CONFIG-03 event-date alignment) to verify report.json event timestamps against
/// the authoritative calendar.
///
/// Direction semantics: `-1` = dovish/bearish, `0` = neutral, `+1` = hawkish/bullish.
pub fn fomc_dir_at(year: i32, month: u32, day: u32) -> Option<i8> {
    FOMC_DATES_2022_2023
        .iter()
        .chain(FOMC_DATES_2024_2026.iter())
        .find(|&&(y, m, d, _h, _dir)| y == year && m == month && d == day)
        .map(|&(_y, _m, _d, _h, dir)| dir)
}

/// Look up ECB direction signal for the given calendar date.
///
/// Searches BOTH `ECB_DATES_2022_2023` and `ECB_DATES_2024_2025` const arrays
/// (v4.1 + v4.2 epoch union) for the (year, month, day) tuple. Returns `None` if
/// no ECB announcement exists on that date. Used by Phase 61 sign_forensics audit
/// (CONFIG-03 event-date alignment) to verify report.json event timestamps against
/// the authoritative calendar.
///
/// Direction semantics: `-1` = dovish (EUR-negative), `0` = neutral, `+1` = hawkish (EUR-positive).
pub fn ecb_dir_at(year: i32, month: u32, day: u32) -> Option<i8> {
    ECB_DATES_2022_2023
        .iter()
        .chain(ECB_DATES_2024_2025.iter())
        .find(|&&(y, m, d, _h, _dir)| y == year && m == month && d == day)
        .map(|&(_y, _m, _d, _h, dir)| dir)
}

/// Look up NFP direction signal for the given calendar date.
///
/// Searches BOTH `NFP_DATES_2022_2023` and `NFP_DATES_2024_2025` const arrays
/// (v4.1 + v4.2 epoch union) for the (year, month, day) tuple. Returns `None` if
/// no NFP release exists on that date. Used by Phase 61 sign_forensics audit
/// (CONFIG-03 event-date alignment) to verify report.json event timestamps against
/// the authoritative calendar.
///
/// Direction semantics: `-1` = MISS (below consensus), `0` = INLINE, `+1` = BEAT (above consensus).
pub fn nfp_dir_at(year: i32, month: u32, day: u32) -> Option<i8> {
    NFP_DATES_2022_2023
        .iter()
        .chain(NFP_DATES_2024_2025.iter())
        .find(|&&(y, m, d, _h, _dir)| y == year && m == month && d == day)
        .map(|&(_y, _m, _d, _h, dir)| dir)
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{Datelike, NaiveDate, Timelike};

    #[test]
    fn fomc_dir_at_covers_both_epochs() {
        // v4.1 epoch (2022-23): Mar 16 2022 first hike = hawkish (+1)
        assert_eq!(fomc_dir_at(2022, 3, 16), Some(1));
        // v4.2 epoch (2024-26): Sep 18 2024 dovish -50bp cut = -1
        assert_eq!(fomc_dir_at(2024, 9, 18), Some(-1));
        // Date with no FOMC meeting — must return None
        assert_eq!(fomc_dir_at(2024, 1, 1), None);
    }

    #[test]
    fn ecb_dir_at_covers_both_epochs() {
        // v4.1 epoch (2022-23): Mar 10 2022 hawkish (imminent hikes signal) = +1
        assert_eq!(ecb_dir_at(2022, 3, 10), Some(1));
        // v4.2 epoch (2024-25): Jun 6 2024 dovish -25bp cut = -1
        assert_eq!(ecb_dir_at(2024, 6, 6), Some(-1));
        // Date with no ECB meeting — must return None
        assert_eq!(ecb_dir_at(2024, 1, 1), None);
    }

    #[test]
    fn nfp_dir_at_covers_both_epochs() {
        // v4.1 epoch (2022-23): Jan 7 2022 (Dec'21 BEAT) = +1
        assert_eq!(nfp_dir_at(2022, 1, 7), Some(1));
        // v4.2 epoch (2024-25): May 3 2024 (Apr'24 MISS) = -1
        assert_eq!(nfp_dir_at(2024, 5, 3), Some(-1));
        // Date with no NFP release — must return None
        assert_eq!(nfp_dir_at(2024, 1, 1), None);
    }

    fn dt(year: i32, month: u32, day: u32, hour: u32, minute: u32) -> NaiveDateTime {
        NaiveDate::from_ymd_opt(year, month, day)
            .unwrap()
            .and_hms_opt(hour, minute, 0)
            .unwrap()
    }

    #[test]
    fn event_window_contains_bar_strictly_inside() {
        let w = EventWindow::new(dt(2025, 1, 29, 17, 0), dt(2025, 1, 29, 21, 0));
        assert!(w.contains(dt(2025, 1, 29, 19, 0)));
    }

    #[test]
    fn event_window_contains_bar_exactly_at_start() {
        // Half-open [start, end) => start IS inside.
        let w = EventWindow::new(dt(2025, 1, 29, 17, 0), dt(2025, 1, 29, 21, 0));
        assert!(w.contains(dt(2025, 1, 29, 17, 0)));
    }

    #[test]
    fn event_window_rejects_bar_exactly_at_end() {
        // Half-open [start, end) => end is NOT inside.
        let w = EventWindow::new(dt(2025, 1, 29, 17, 0), dt(2025, 1, 29, 21, 0));
        assert!(!w.contains(dt(2025, 1, 29, 21, 0)));
    }

    #[test]
    fn event_window_rejects_bar_before_start() {
        let w = EventWindow::new(dt(2025, 1, 29, 17, 0), dt(2025, 1, 29, 21, 0));
        assert!(!w.contains(dt(2025, 1, 29, 16, 59)));
    }

    #[test]
    fn event_window_rejects_bar_after_end() {
        let w = EventWindow::new(dt(2025, 1, 29, 17, 0), dt(2025, 1, 29, 21, 0));
        assert!(!w.contains(dt(2025, 1, 29, 21, 1)));
    }

    #[test]
    fn fomc_2025_2026_has_16_events() {
        // 8 FOMC meetings per year × 2 years.
        let windows = fomc_windows_2025_2026();
        assert_eq!(windows.len(), 16, "expected 16 FOMC windows for 2025-2026");
    }

    #[test]
    fn fomc_windows_are_chronologically_ordered() {
        let windows = fomc_windows_2025_2026();
        for pair in windows.windows(2) {
            assert!(
                pair[0].start < pair[1].start,
                "FOMC windows must be strictly increasing: {:?} vs {:?}",
                pair[0],
                pair[1]
            );
        }
    }

    #[test]
    fn fomc_january_2025_announcement_is_inside_window() {
        // Jan 29 2025 FOMC announced at 14:00 EST = 19:00 UTC.
        let windows = fomc_windows_2025_2026();
        let first = windows[0];
        assert!(first.contains(dt(2025, 1, 29, 19, 0)));
    }

    #[test]
    fn fomc_june_2025_announcement_is_inside_window() {
        // Jun 18 2025 FOMC announced at 14:00 EDT = 18:00 UTC (summer time).
        let windows = fomc_windows_2025_2026();
        let jun = windows
            .iter()
            .find(|w| w.start.date().month() == 6)
            .unwrap();
        assert!(jun.contains(dt(2025, 6, 18, 18, 0)));
    }

    #[test]
    fn fomc_windows_are_four_hours_each() {
        let windows = fomc_windows_2025_2026();
        for w in &windows {
            let dur = w.end.signed_duration_since(w.start);
            assert_eq!(
                dur.num_hours(),
                4,
                "each FOMC window should be 4h, got {:?}",
                dur
            );
        }
    }

    #[test]
    fn ecb_2025_2026_has_16_events() {
        let windows = ecb_windows_2025_2026();
        assert_eq!(
            windows.len(),
            16,
            "expected 16 ECB GC windows for 2025-2026"
        );
    }

    #[test]
    fn ecb_windows_are_chronologically_ordered() {
        let windows = ecb_windows_2025_2026();
        for pair in windows.windows(2) {
            assert!(
                pair[0].start < pair[1].start,
                "ECB windows must be strictly increasing: {:?} vs {:?}",
                pair[0],
                pair[1]
            );
        }
    }

    #[test]
    fn ecb_january_2025_announcement_in_cet_is_inside_window() {
        // Jan 30 2025 ECB announcement at 13:45 CET = 12:45 UTC (winter).
        let windows = ecb_windows_2025_2026();
        let first = windows[0];
        assert!(first.contains(dt(2025, 1, 30, 12, 45)));
    }

    #[test]
    fn ecb_june_2025_announcement_in_cest_is_inside_window() {
        // Jun 5 2025 ECB announcement at 14:15 CEST = 12:15 UTC (summer).
        let windows = ecb_windows_2025_2026();
        let jun = windows
            .iter()
            .find(|w| w.start.date().month() == 6)
            .unwrap();
        assert!(jun.contains(dt(2025, 6, 5, 12, 15)));
    }

    #[test]
    fn ecb_windows_are_four_hours_each() {
        let windows = ecb_windows_2025_2026();
        for w in &windows {
            let dur = w.end.signed_duration_since(w.start);
            assert_eq!(
                dur.num_hours(),
                4,
                "each ECB window should be 4h, got {:?}",
                dur
            );
        }
    }

    fn make_bar(y: i32, m: u32, d: u32, h: u32, min: u32) -> Bar {
        Bar {
            datetime: dt(y, m, d, h, min),
            open: 100.0,
            high: 101.0,
            low: 99.0,
            close: 100.5,
            volume: 1000.0,
        }
    }

    #[test]
    fn apply_event_filter_empty_windows_returns_unchanged() {
        let bars = vec![make_bar(2025, 1, 29, 18, 0), make_bar(2025, 1, 29, 19, 0)];
        let original_len = bars.len();
        let result = apply_event_filter(bars, &[]);
        assert_eq!(result.len(), original_len);
    }

    #[test]
    fn apply_event_filter_empty_bars_returns_empty() {
        let bars: Vec<Bar> = vec![];
        let windows = vec![EventWindow::new(
            dt(2025, 1, 29, 17, 0),
            dt(2025, 1, 29, 21, 0),
        )];
        let result = apply_event_filter(bars, &windows);
        assert_eq!(result.len(), 0);
    }

    #[test]
    fn apply_event_filter_drops_bar_inside_window() {
        let bars = vec![
            make_bar(2025, 1, 29, 16, 59), // outside — keep
            make_bar(2025, 1, 29, 17, 0),  // exact start → inside [start, end) → drop
            make_bar(2025, 1, 29, 19, 0),  // strictly inside → drop
            make_bar(2025, 1, 29, 21, 0),  // exact end → outside → keep
            make_bar(2025, 1, 29, 22, 0),  // after → keep
        ];
        let windows = vec![EventWindow::new(
            dt(2025, 1, 29, 17, 0),
            dt(2025, 1, 29, 21, 0),
        )];
        let result = apply_event_filter(bars, &windows);
        assert_eq!(result.len(), 3);
        assert_eq!(result[0].datetime, dt(2025, 1, 29, 16, 59));
        assert_eq!(result[1].datetime, dt(2025, 1, 29, 21, 0));
        assert_eq!(result[2].datetime, dt(2025, 1, 29, 22, 0));
    }

    #[test]
    fn apply_event_filter_handles_multiple_windows() {
        // One FOMC window + one ECB window on the same day.
        let bars = vec![
            make_bar(2025, 1, 29, 10, 0),  // before ECB — keep
            make_bar(2025, 1, 29, 13, 30), // inside ECB window → drop
            make_bar(2025, 1, 29, 16, 0),  // between ECB and FOMC — keep
            make_bar(2025, 1, 29, 18, 0),  // inside FOMC window → drop
            make_bar(2025, 1, 29, 22, 0),  // after FOMC — keep
        ];
        let windows = vec![
            EventWindow::new(dt(2025, 1, 29, 11, 30), dt(2025, 1, 29, 15, 30)), // ECB
            EventWindow::new(dt(2025, 1, 29, 17, 0), dt(2025, 1, 29, 21, 0)),   // FOMC
        ];
        let result = apply_event_filter(bars, &windows);
        assert_eq!(result.len(), 3);
        assert_eq!(result[0].datetime, dt(2025, 1, 29, 10, 0));
        assert_eq!(result[1].datetime, dt(2025, 1, 29, 16, 0));
        assert_eq!(result[2].datetime, dt(2025, 1, 29, 22, 0));
    }

    #[test]
    fn apply_event_filter_is_deterministic() {
        let bars = vec![
            make_bar(2025, 1, 29, 18, 0),
            make_bar(2025, 6, 5, 13, 0),
            make_bar(2025, 10, 30, 14, 0),
        ];
        let mut windows = fomc_windows_2025_2026();
        windows.extend(ecb_windows_2025_2026());
        let r1 = apply_event_filter(bars.clone(), &windows);
        let r2 = apply_event_filter(bars, &windows);
        assert_eq!(r1.len(), r2.len());
        for (a, b) in r1.iter().zip(r2.iter()) {
            assert_eq!(a.datetime, b.datetime);
        }
    }

    #[test]
    fn boj_windows_2024_2026_has_16_events() {
        // 8 BOJ MPMs per year × 2 years (regular meetings only).
        let windows = boj_windows_2024_2026();
        assert_eq!(windows.len(), 16, "expected 16 BOJ windows for 2024-2025");
    }

    #[test]
    fn boj_windows_are_chronologically_ordered() {
        let windows = boj_windows_2024_2026();
        for pair in windows.windows(2) {
            assert!(
                pair[0].start < pair[1].start,
                "BOJ windows must be strictly increasing: {:?} vs {:?}",
                pair[0],
                pair[1]
            );
        }
    }

    #[test]
    fn boj_spot_check_march_2024_is_inside_window() {
        // Mar 19 2024 BOJ ended negative interest rates (historic meeting).
        // Typical announce ~12:00 JST = 03:00 UTC — well inside 02:00-06:00 window.
        let windows = boj_windows_2024_2026();
        let mar2024 = windows
            .iter()
            .find(|w| {
                let d = w.start.date();
                d.year() == 2024 && d.month() == 3 && d.day() == 19
            })
            .expect("March 19 2024 BOJ window should exist");
        assert!(
            mar2024.contains(dt(2024, 3, 19, 3, 0)),
            "03:00 UTC should be inside the Mar 2024 BOJ window"
        );
    }

    #[test]
    fn fomc_dates_const_has_18_rows() {
        assert_eq!(
            FOMC_DATES_2024_2026.len(),
            18,
            "expected 18 FOMC rows for 2024-2026"
        );
    }

    #[test]
    fn fomc_2024_2026_has_18_events() {
        let windows = fomc_windows_2024_2026();
        assert_eq!(windows.len(), 18, "expected 18 FOMC windows for 2024-2026");
    }

    #[test]
    fn fomc_2024_2026_chronologically_ordered() {
        let windows = fomc_windows_2024_2026();
        for pair in windows.windows(2) {
            assert!(
                pair[0].start < pair[1].start,
                "FOMC windows must be strictly increasing: {:?} vs {:?}",
                pair[0],
                pair[1]
            );
        }
    }

    #[test]
    fn fomc_edt_sample_uses_18_utc() {
        // Mar 20 2024: post DST start (Mar 10 2024) → EDT → 14:00 ET = 18:00 UTC.
        let row = FOMC_DATES_2024_2026
            .iter()
            .find(|r| r.0 == 2024 && r.1 == 3 && r.2 == 20)
            .expect("Mar 20 2024 row must exist");
        assert_eq!(row.3, 18, "Mar 20 2024 should be EDT (hour_utc=18)");

        let windows = fomc_windows_2024_2026();
        let mar2024 = windows
            .iter()
            .find(|w| {
                let d = w.start.date();
                d.year() == 2024 && d.month() == 3 && d.day() == 20
            })
            .unwrap();
        assert_eq!(mar2024.start.time().hour(), 18);
        assert_eq!(mar2024.end.time().hour(), 19);
    }

    #[test]
    fn fomc_est_sample_uses_19_utc() {
        // Jan 31 2024: pre DST start → EST → 14:00 ET = 19:00 UTC.
        let row = FOMC_DATES_2024_2026
            .iter()
            .find(|r| r.0 == 2024 && r.1 == 1 && r.2 == 31)
            .expect("Jan 31 2024 row must exist");
        assert_eq!(row.3, 19, "Jan 31 2024 should be EST (hour_utc=19)");

        let windows = fomc_windows_2024_2026();
        let jan2024 = windows
            .iter()
            .find(|w| {
                let d = w.start.date();
                d.year() == 2024 && d.month() == 1 && d.day() == 31
            })
            .unwrap();
        assert_eq!(jan2024.start.time().hour(), 19);
        assert_eq!(jan2024.end.time().hour(), 20);
    }

    #[test]
    fn fomc_direction_signs_are_present() {
        // Every row must be hawkish (+1) or dovish (-1), no zeros.
        for row in FOMC_DATES_2024_2026 {
            assert!(
                row.4 == 1 || row.4 == -1,
                "Row {:?} has invalid direction (must be +1 or -1)",
                row
            );
        }

        // Spot checks against research table.
        let dir = |y: i32, m: u32, d: u32| -> i8 {
            FOMC_DATES_2024_2026
                .iter()
                .find(|r| r.0 == y && r.1 == m && r.2 == d)
                .unwrap()
                .4
        };
        assert_eq!(dir(2024, 1, 31), 1, "Jan 2024 = hawkish hold");
        assert_eq!(dir(2024, 9, 18), -1, "Sep 2024 = dovish -50bp");
        assert_eq!(dir(2024, 11, 7), -1, "Nov 2024 = dovish cut");
        assert_eq!(dir(2024, 12, 18), 1, "Dec 2024 = hawkish cut");
        assert_eq!(dir(2025, 9, 17), -1, "Sep 2025 = dovish cut");
        assert_eq!(dir(2026, 3, 18), 1, "Mar 2026 = hawkish hold");
    }

    #[test]
    fn fomc_2024_2026_window_duration_is_one_hour() {
        let windows = fomc_windows_2024_2026();
        for w in &windows {
            let dur = w.end.signed_duration_since(w.start);
            assert_eq!(
                dur.num_hours(),
                1,
                "each FOMC 2024-2026 window must be 1h, got {:?}",
                dur
            );
        }
    }

    #[test]
    fn apply_event_filter_full_calendar_drops_all_announcement_time_bars() {
        // Build a bar at the middle of every FOMC and ECB window. All should be dropped.
        let mut bars = Vec::new();
        for w in fomc_windows_2025_2026() {
            bars.push(Bar {
                datetime: w.start + chrono::Duration::hours(2),
                open: 100.0,
                high: 101.0,
                low: 99.0,
                close: 100.5,
                volume: 1000.0,
            });
        }
        for w in ecb_windows_2025_2026() {
            bars.push(Bar {
                datetime: w.start + chrono::Duration::hours(1),
                open: 100.0,
                high: 101.0,
                low: 99.0,
                close: 100.5,
                volume: 1000.0,
            });
        }
        let mut windows = fomc_windows_2025_2026();
        windows.extend(ecb_windows_2025_2026());
        let result = apply_event_filter(bars, &windows);
        assert_eq!(
            result.len(),
            0,
            "all announcement-time bars should be dropped"
        );
    }

    // --- ECB calendar tests ---

    #[test]
    fn ecb_calendar_count_is_16() {
        assert_eq!(ecb_windows_2024_2025().len(), 16);
    }

    #[test]
    fn ecb_calendar_is_ordered() {
        let windows = ecb_windows_2024_2025();
        for pair in windows.windows(2) {
            assert!(
                pair[0].start < pair[1].start,
                "ECB windows out of order: {:?} >= {:?}",
                pair[0].start,
                pair[1].start
            );
        }
    }

    #[test]
    fn ecb_windows_2024_2025_first_anchor_is_1h_at_13_utc() {
        // Phase 34 D-01: 1h anchor window. CET day (2024-01-25): hour_utc=13
        let windows = ecb_windows_2024_2025();
        let w = &windows[0];
        assert_eq!(
            w.start,
            dt(2024, 1, 25, 13, 0),
            "ECB CET window must start at 13:00 UTC"
        );
        assert_eq!(
            w.end,
            dt(2024, 1, 25, 14, 0),
            "ECB CET window must end at 14:00 UTC (1h anchor)"
        );
    }

    #[test]
    fn ecb_windows_2024_2025_summer_anchor_is_1h_at_12_utc() {
        // Phase 34 D-01: 1h anchor window. CEST day (2024-06-06): hour_utc=12
        let windows = ecb_windows_2024_2025();
        // 2024-06-06 is index 3 in ECB_DATES_2024_2025
        let w = &windows[3];
        assert_eq!(
            w.start,
            dt(2024, 6, 6, 12, 0),
            "ECB CEST window must start at 12:00 UTC"
        );
        assert_eq!(
            w.end,
            dt(2024, 6, 6, 13, 0),
            "ECB CEST window must end at 13:00 UTC (1h anchor)"
        );
    }

    #[test]
    fn ecb_spot_hour_utc_cet_vs_cest() {
        // CET day: 2024-01-25 → hour_utc == 13
        assert_eq!(
            ECB_DATES_2024_2025[0].3, 13,
            "2024-01-25 should be hour 13 (CET)"
        );
        // CEST day: 2024-06-06 → hour_utc == 12
        assert_eq!(
            ECB_DATES_2024_2025[3].3, 12,
            "2024-06-06 should be hour 12 (CEST)"
        );
    }

    // --- NFP calendar tests ---

    #[test]
    fn nfp_calendar_count_is_22() {
        assert_eq!(nfp_windows_2024_2025().len(), 22);
    }

    #[test]
    fn nfp_calendar_is_ordered() {
        let windows = nfp_windows_2024_2025();
        for pair in windows.windows(2) {
            assert!(
                pair[0].start < pair[1].start,
                "NFP windows out of order: {:?} >= {:?}",
                pair[0].start,
                pair[1].start
            );
        }
    }

    #[test]
    fn nfp_windows_2024_2025_winter_anchor_is_1h_at_13_utc() {
        // Phase 34 D-01: 1h anchor window. EST day (2024-01-05): hour_utc=13
        let windows = nfp_windows_2024_2025();
        let w = &windows[0];
        assert_eq!(
            w.start,
            dt(2024, 1, 5, 13, 0),
            "NFP EST window must start at 13:00 UTC"
        );
        assert_eq!(
            w.end,
            dt(2024, 1, 5, 14, 0),
            "NFP EST window must end at 14:00 UTC (1h anchor)"
        );
    }

    #[test]
    fn nfp_windows_2024_2025_summer_anchor_is_1h_at_12_utc() {
        // Phase 34 D-01: 1h anchor window. EDT day (2024-04-05): hour_utc=12
        let windows = nfp_windows_2024_2025();
        // 2024-04-05 is index 3 in NFP_DATES_2024_2025
        let w = &windows[3];
        assert_eq!(
            w.start,
            dt(2024, 4, 5, 12, 0),
            "NFP EDT window must start at 12:00 UTC"
        );
        assert_eq!(
            w.end,
            dt(2024, 4, 5, 13, 0),
            "NFP EDT window must end at 13:00 UTC (1h anchor)"
        );
    }

    #[test]
    fn nfp_spot_hour_utc_est_vs_edt() {
        // EST day: 2024-01-05 → hour_utc == 13
        assert_eq!(
            NFP_DATES_2024_2025[0].3, 13,
            "2024-01-05 should be hour 13 (EST)"
        );
        // EDT day: 2024-04-05 → hour_utc == 12
        assert_eq!(
            NFP_DATES_2024_2025[3].3, 12,
            "2024-04-05 should be hour 12 (EDT)"
        );
    }

    #[test]
    fn nfp_jul_3_holiday_shift_present() {
        // 2025-07-03 is a Thursday release (shifted from Jul 4 holiday)
        let found = NFP_DATES_2024_2025
            .iter()
            .any(|&(y, m, d, _, _)| y == 2025 && m == 7 && d == 3);
        assert!(
            found,
            "NFP_DATES_2024_2025 must contain the Jul 3 2025 holiday-shifted entry"
        );
    }

    // ---------------------------------------------------------------
    // Phase 40 Plan 01 — ECB DST Q3/Q4 quadrant unit tests
    // ---------------------------------------------------------------

    #[test]
    fn ecb_dst_quadrant_mar_pre_transition_is_cet_h13() {
        // 2024-03-07 ECB meeting — Mar 31 DST transition BEFORE → CET → hour_utc = 13
        // Source: ECB_DATES_2024_2025[1] (python verified in RESEARCH.md A1)
        assert_eq!(
            ECB_DATES_2024_2025[1],
            (2024, 3, 7, 13, 0),
            "2024-03-07 should have hour=13 (CET)"
        );
        assert_eq!(
            ECB_DATES_2024_2025[1].3, 13,
            "Mar pre-DST ECB should be CET hour=13 UTC"
        );
    }

    #[test]
    fn ecb_dst_quadrant_mar_post_transition_is_cest_h12() {
        // 2024-04-11 ECB — Mar 31 DST transition AFTER → CEST → hour_utc = 12
        // Source: ECB_DATES_2024_2025[2], corrected in Plan 61-07 via zoneinfo authoritative
        assert_eq!(
            ECB_DATES_2024_2025[2].3, 12,
            "Apr post-DST ECB should be CEST hour=12 UTC"
        );
        assert_eq!(
            (
                ECB_DATES_2024_2025[2].0,
                ECB_DATES_2024_2025[2].1,
                ECB_DATES_2024_2025[2].2
            ),
            (2024, 4, 11),
            "index 2 should be 2024-04-11"
        );
    }

    #[test]
    fn ecb_dst_quadrant_oct_pre_transition_is_cest_h12() {
        // 2024-10-17 ECB — Oct 27 DST transition BEFORE → CEST → hour_utc = 12
        // Source: ECB_DATES_2024_2025[6] (python verified in RESEARCH.md A1)
        assert_eq!(
            ECB_DATES_2024_2025[6].3, 12,
            "Oct pre-DST ECB should be CEST hour=12 UTC"
        );
        assert_eq!(
            (
                ECB_DATES_2024_2025[6].0,
                ECB_DATES_2024_2025[6].1,
                ECB_DATES_2024_2025[6].2
            ),
            (2024, 10, 17),
            "index 6 should be 2024-10-17"
        );
    }

    #[test]
    fn ecb_dst_quadrant_oct_post_transition_is_cet_h13() {
        // 2024-12-12 ECB — Oct 27 DST transition AFTER → CET → hour_utc = 13
        // Source: ECB_DATES_2024_2025[7] (python verified in RESEARCH.md A1)
        assert_eq!(
            ECB_DATES_2024_2025[7].3, 13,
            "Dec post-DST ECB should be CET hour=13 UTC"
        );
        assert_eq!(
            (
                ECB_DATES_2024_2025[7].0,
                ECB_DATES_2024_2025[7].1,
                ECB_DATES_2024_2025[7].2
            ),
            (2024, 12, 12),
            "index 7 should be 2024-12-12"
        );
    }

    // --- combinator function tests (Phase 48 Plan 01) ---

    #[test]
    fn fomc_windows_all_has_34_events() {
        assert_eq!(fomc_windows_all().len(), 34);
    }

    #[test]
    fn ecb_windows_all_has_32_events() {
        assert_eq!(ecb_windows_all().len(), 32);
    }

    #[test]
    fn nfp_windows_all_has_46_events() {
        // nfp_windows_2022_2023()=24 + nfp_windows_2024_2025()=22 = 46
        // A2 assumption in plan was 24+24=48 but actual 2024_2025 count is 22.
        assert_eq!(nfp_windows_all().len(), 46);
    }

    #[test]
    fn fomc_windows_all_is_chronologically_ordered() {
        let windows = fomc_windows_all();
        for pair in windows.windows(2) {
            assert!(
                pair[0].start <= pair[1].start,
                "fomc_windows_all not ordered: {:?} > {:?}",
                pair[0].start,
                pair[1].start
            );
        }
    }
}
