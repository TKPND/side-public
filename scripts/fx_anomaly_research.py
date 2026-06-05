"""FX Calendar Anomaly Research Script.

Analyzes calendar-based anomalies in FX data:
1. Day-of-week effect
2. Month-end / turn-of-month effect
3. Time-of-day (session) effect
4. Monthly seasonality
5. Quarter-end effect
6. NFP week effect (first Friday of month)

Uses yfinance for FX data (OHLCV).
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# FX pairs to test (yfinance format)
FX_PAIRS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X",
    "USDCAD=X", "USDCHF=X", "NZDUSD=X", "EURJPY=X",
    "GBPJPY=X", "EURGBP=X", "AUDJPY=X", "CADJPY=X",
]

PAIR_LABELS = {p: p.replace("=X", "") for p in FX_PAIRS}


def fetch_fx_data(pair: str, period: str = "2y", interval: str = "1h") -> pd.DataFrame:
    """Fetch FX data from yfinance with caching."""
    import yfinance as yf

    cache_dir = Path("data/cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{pair.replace('=', '_')}_{interval}_{period}.csv"

    if cache_file.exists():
        df = pd.read_csv(cache_file, parse_dates=["Datetime"])
        return df

    ticker = yf.Ticker(pair)
    df = ticker.history(period=period, interval=interval)
    df = df.reset_index()
    if "Date" in df.columns:
        df = df.rename(columns={"Date": "Datetime"})
    df.to_csv(cache_file, index=False)
    return df


def compute_returns(df: pd.DataFrame) -> pd.Series:
    """Compute log returns from Close."""
    return np.log(df["Close"] / df["Close"].shift(1)).dropna()


# ===================== Anomaly Tests =====================

def test_day_of_week(df: pd.DataFrame, pair: str) -> dict:
    """Test day-of-week effect."""
    dt_col = "Datetime" if "Datetime" in df.columns else df.index.name or "index"
    if dt_col == "index":
        df = df.copy()
        df["Datetime"] = df.index
        dt_col = "Datetime"

    df = df.copy()
    df["returns"] = compute_returns(df)
    df["dow"] = pd.to_datetime(df[dt_col]).dt.dayofweek  # 0=Mon, 4=Fri

    groups = {dow: df[df["dow"] == dow]["returns"].dropna() for dow in range(5)}
    means = {dow: float(g.mean()) for dow, g in groups.items()}
    stds = {dow: float(g.std()) for dow, g in groups.items()}
    counts = {dow: len(g) for dow, g in groups.items()}

    # Kruskal-Wallis test (non-parametric ANOVA)
    samples = [g.values for g in groups.values() if len(g) > 0]
    if len(samples) >= 2:
        kw_stat, kw_p = stats.kruskal(*samples)
    else:
        kw_stat, kw_p = 0.0, 1.0

    # Best and worst day
    best_dow = max(means, key=means.get)
    worst_dow = min(means, key=means.get)

    # T-test: best day vs rest
    best_returns = groups[best_dow].values
    rest_returns = pd.concat([groups[d] for d in range(5) if d != best_dow]).values
    if len(best_returns) > 1 and len(rest_returns) > 1:
        t_stat, t_p = stats.ttest_ind(best_returns, rest_returns)
    else:
        t_stat, t_p = 0.0, 1.0

    dow_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
    return {
        "pair": pair,
        "anomaly": "day_of_week",
        "kruskal_wallis_p": float(kw_p),
        "significant_kw": kw_p < 0.05,
        "best_day": dow_names[best_dow],
        "worst_day": dow_names[worst_dow],
        "best_vs_rest_t_p": float(t_p),
        "means_by_day": {dow_names[k]: float(v) for k, v in means.items()},
        "annualized_spread_bps": float((means[best_dow] - means[worst_dow]) * 252 * 10000),
        "n_observations": counts,
    }


def test_month_end(df: pd.DataFrame, pair: str, window: int = 3) -> dict:
    """Test turn-of-month effect (last N and first N trading days)."""
    df = df.copy()
    df["returns"] = compute_returns(df)
    dt_col = "Datetime" if "Datetime" in df.columns else df.index.name
    df["date"] = pd.to_datetime(df[dt_col]).dt.date
    df["month"] = pd.to_datetime(df[dt_col]).dt.to_period("M")

    # Identify turn-of-month days
    daily = df.groupby("date").agg({"returns": "sum", "month": "first"}).reset_index()
    daily["td_in_month"] = daily.groupby("month").cumcount()
    daily["td_from_end"] = daily.groupby("month")["td_in_month"].transform("max") - daily["td_in_month"]

    # Turn-of-month: last `window` days of month + first `window` days of next
    tom = daily[(daily["td_from_end"] < window) | (daily["td_in_month"] < window)]
    rest = daily[~daily.index.isin(tom.index)]

    tom_mean = float(tom["returns"].mean())
    rest_mean = float(rest["returns"].mean())

    if len(tom) > 1 and len(rest) > 1:
        t_stat, t_p = stats.ttest_ind(tom["returns"].values, rest["returns"].values)
        mw_stat, mw_p = stats.mannwhitneyu(tom["returns"].values, rest["returns"].values, alternative="two-sided")
    else:
        t_stat, t_p = 0.0, 1.0
        mw_stat, mw_p = 0.0, 1.0

    return {
        "pair": pair,
        "anomaly": "month_end",
        "window_days": window,
        "tom_mean_daily_ret": tom_mean,
        "rest_mean_daily_ret": rest_mean,
        "ratio": tom_mean / rest_mean if rest_mean != 0 else float("inf"),
        "t_test_p": float(t_p),
        "mann_whitney_p": float(mw_p),
        "significant": t_p < 0.05 or mw_p < 0.05,
        "tom_n": len(tom),
        "rest_n": len(rest),
        "annualized_spread_bps": float((tom_mean - rest_mean) * 252 * 10000),
    }


def test_monthly_seasonality(df: pd.DataFrame, pair: str) -> dict:
    """Test monthly seasonality (which months outperform)."""
    df = df.copy()
    df["returns"] = compute_returns(df)
    dt_col = "Datetime" if "Datetime" in df.columns else df.index.name
    df["month"] = pd.to_datetime(df[dt_col]).dt.month

    groups = {m: df[df["month"] == m]["returns"].dropna() for m in range(1, 13)}
    means = {m: float(g.mean()) for m, g in groups.items()}

    samples = [g.values for g in groups.values() if len(g) > 0]
    if len(samples) >= 2:
        kw_stat, kw_p = stats.kruskal(*samples)
    else:
        kw_stat, kw_p = 0.0, 1.0

    best_month = max(means, key=means.get)
    worst_month = min(means, key=means.get)

    month_names = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
                   7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}

    return {
        "pair": pair,
        "anomaly": "monthly_seasonality",
        "kruskal_wallis_p": float(kw_p),
        "significant": kw_p < 0.05,
        "best_month": month_names[best_month],
        "worst_month": month_names[worst_month],
        "means_by_month": {month_names[k]: float(v) for k, v in means.items()},
        "annualized_spread_bps": float((means[best_month] - means[worst_month]) * 252 * 10000),
    }


def test_quarter_end(df: pd.DataFrame, pair: str, window: int = 5) -> dict:
    """Test quarter-end rebalancing effect."""
    df = df.copy()
    df["returns"] = compute_returns(df)
    dt_col = "Datetime" if "Datetime" in df.columns else df.index.name
    df["date"] = pd.to_datetime(df[dt_col]).dt.date
    df["quarter"] = pd.to_datetime(df[dt_col]).dt.to_period("Q")

    daily = df.groupby("date").agg({"returns": "sum", "quarter": "first"}).reset_index()
    daily["td_in_q"] = daily.groupby("quarter").cumcount()
    daily["td_from_q_end"] = daily.groupby("quarter")["td_in_q"].transform("max") - daily["td_in_q"]

    qe = daily[daily["td_from_q_end"] < window]
    rest = daily[~daily.index.isin(qe.index)]

    qe_mean = float(qe["returns"].mean())
    rest_mean = float(rest["returns"].mean())

    if len(qe) > 1 and len(rest) > 1:
        t_stat, t_p = stats.ttest_ind(qe["returns"].values, rest["returns"].values)
    else:
        t_stat, t_p = 0.0, 1.0

    return {
        "pair": pair,
        "anomaly": "quarter_end",
        "window_days": window,
        "qe_mean_daily_ret": qe_mean,
        "rest_mean_daily_ret": rest_mean,
        "t_test_p": float(t_p),
        "significant": t_p < 0.05,
        "annualized_spread_bps": float((qe_mean - rest_mean) * 252 * 10000),
    }


def test_session_effect(df: pd.DataFrame, pair: str) -> dict:
    """Test trading session effect (Asian/London/NY)."""
    df = df.copy()
    df["returns"] = compute_returns(df)
    dt_col = "Datetime" if "Datetime" in df.columns else df.index.name
    hours = pd.to_datetime(df[dt_col]).dt.hour

    # Sessions (UTC approximation)
    sessions = {
        "Asian": (0, 8),    # 00:00-08:00 UTC
        "London": (8, 13),  # 08:00-13:00 UTC
        "Overlap": (13, 17), # 13:00-17:00 UTC (London+NY)
        "NY_late": (17, 22), # 17:00-22:00 UTC
    }

    results = {}
    session_returns = {}
    for name, (start, end) in sessions.items():
        mask = (hours >= start) & (hours < end)
        rets = df.loc[mask, "returns"].dropna()
        session_returns[name] = rets
        results[name] = {
            "mean_ret": float(rets.mean()) if len(rets) > 0 else 0.0,
            "std_ret": float(rets.std()) if len(rets) > 0 else 0.0,
            "sharpe": float(rets.mean() / rets.std() * np.sqrt(252 * 5)) if len(rets) > 0 and rets.std() > 0 else 0.0,
            "n": len(rets),
        }

    samples = [v.values for v in session_returns.values() if len(v) > 0]
    if len(samples) >= 2:
        kw_stat, kw_p = stats.kruskal(*samples)
    else:
        kw_stat, kw_p = 0.0, 1.0

    best_session = max(results, key=lambda k: results[k]["mean_ret"])

    return {
        "pair": pair,
        "anomaly": "session_effect",
        "kruskal_wallis_p": float(kw_p),
        "significant": kw_p < 0.05,
        "best_session": best_session,
        "sessions": results,
    }


def test_nfp_week(df: pd.DataFrame, pair: str) -> dict:
    """Test NFP (Non-Farm Payrolls) week effect.
    NFP is released first Friday of each month.
    """
    df = df.copy()
    df["returns"] = compute_returns(df)
    dt_col = "Datetime" if "Datetime" in df.columns else df.index.name
    dates = pd.to_datetime(df[dt_col])
    df["date"] = dates.dt.date
    df["dow"] = dates.dt.dayofweek
    df["day"] = dates.dt.day

    # First Friday: day <= 7 and dow == 4
    df["is_nfp_day"] = (df["day"] <= 7) & (df["dow"] == 4)
    # NFP week: same week as NFP day
    df["week"] = dates.dt.isocalendar().week.values
    df["year"] = dates.dt.year

    nfp_weeks = df[df["is_nfp_day"]][["year", "week"]].drop_duplicates()
    df["is_nfp_week"] = df.apply(
        lambda r: any((nfp_weeks["year"] == r["year"]) & (nfp_weeks["week"] == r["week"])),
        axis=1,
    )

    nfp = df[df["is_nfp_week"]]["returns"].dropna()
    rest = df[~df["is_nfp_week"]]["returns"].dropna()

    if len(nfp) > 1 and len(rest) > 1:
        # Compare volatility
        vol_ratio = float(nfp.std() / rest.std()) if rest.std() > 0 else 1.0
        t_stat, t_p = stats.ttest_ind(nfp.values, rest.values)
        # Levene test for variance difference
        lev_stat, lev_p = stats.levene(nfp.values, rest.values)
    else:
        vol_ratio = 1.0
        t_stat, t_p = 0.0, 1.0
        lev_stat, lev_p = 0.0, 1.0

    return {
        "pair": pair,
        "anomaly": "nfp_week",
        "nfp_mean_ret": float(nfp.mean()),
        "rest_mean_ret": float(rest.mean()),
        "nfp_vol": float(nfp.std()),
        "rest_vol": float(rest.std()),
        "vol_ratio": vol_ratio,
        "mean_t_test_p": float(t_p),
        "levene_p": float(lev_p),
        "significant_mean": t_p < 0.05,
        "significant_vol": lev_p < 0.05,
    }


def run_all_tests(pairs: list[str] | None = None) -> dict:
    """Run all anomaly tests on all FX pairs."""
    if pairs is None:
        pairs = FX_PAIRS

    all_results = {}

    for pair in pairs:
        label = PAIR_LABELS[pair]
        print(f"\n{'='*60}")
        print(f"Testing {label}...")
        print(f"{'='*60}")

        try:
            df = fetch_fx_data(pair)
            if df is None or len(df) < 100:
                print(f"  Insufficient data for {label}")
                continue
        except Exception as e:
            print(f"  Error fetching {label}: {e}")
            continue

        print(f"  Data: {len(df)} rows, {df.iloc[0]['Datetime'] if 'Datetime' in df.columns else 'N/A'} to {df.iloc[-1]['Datetime'] if 'Datetime' in df.columns else 'N/A'}")

        pair_results = {}

        # 1. Day of week
        try:
            r = test_day_of_week(df, label)
            pair_results["day_of_week"] = r
            sig = "***" if r["significant_kw"] else ""
            print(f"  DOW: best={r['best_day']}, worst={r['worst_day']}, KW p={r['kruskal_wallis_p']:.4f} {sig}")
        except Exception as e:
            print(f"  DOW error: {e}")

        # 2. Month-end
        try:
            r = test_month_end(df, label)
            pair_results["month_end"] = r
            sig = "***" if r["significant"] else ""
            print(f"  Month-end: TOM={r['tom_mean_daily_ret']:.6f}, rest={r['rest_mean_daily_ret']:.6f}, p={r['t_test_p']:.4f} {sig}")
        except Exception as e:
            print(f"  Month-end error: {e}")

        # 3. Monthly seasonality
        try:
            r = test_monthly_seasonality(df, label)
            pair_results["monthly_seasonality"] = r
            sig = "***" if r["significant"] else ""
            print(f"  Seasonality: best={r['best_month']}, worst={r['worst_month']}, KW p={r['kruskal_wallis_p']:.4f} {sig}")
        except Exception as e:
            print(f"  Seasonality error: {e}")

        # 4. Quarter-end
        try:
            r = test_quarter_end(df, label)
            pair_results["quarter_end"] = r
            sig = "***" if r["significant"] else ""
            print(f"  Quarter-end: QE={r['qe_mean_daily_ret']:.6f}, rest={r['rest_mean_daily_ret']:.6f}, p={r['t_test_p']:.4f} {sig}")
        except Exception as e:
            print(f"  Quarter-end error: {e}")

        # 5. Session effect
        try:
            r = test_session_effect(df, label)
            pair_results["session_effect"] = r
            sig = "***" if r["significant"] else ""
            print(f"  Session: best={r['best_session']}, KW p={r['kruskal_wallis_p']:.4f} {sig}")
        except Exception as e:
            print(f"  Session error: {e}")

        # 6. NFP week
        try:
            r = test_nfp_week(df, label)
            pair_results["nfp_week"] = r
            sig_m = "***" if r["significant_mean"] else ""
            sig_v = "***" if r["significant_vol"] else ""
            print(f"  NFP: vol_ratio={r['vol_ratio']:.2f}, mean_p={r['mean_t_test_p']:.4f}{sig_m}, vol_p={r['levene_p']:.4f}{sig_v}")
        except Exception as e:
            print(f"  NFP error: {e}")

        all_results[label] = pair_results

    return all_results


def summarize_results(results: dict) -> str:
    """Create a summary of significant anomalies."""
    lines = ["\n" + "=" * 80]
    lines.append("FX CALENDAR ANOMALY SUMMARY")
    lines.append("=" * 80)

    anomaly_types = ["day_of_week", "month_end", "monthly_seasonality",
                     "quarter_end", "session_effect", "nfp_week"]

    for anomaly in anomaly_types:
        lines.append(f"\n--- {anomaly.upper()} ---")
        sig_pairs = []
        for pair, pair_results in results.items():
            if anomaly in pair_results:
                r = pair_results[anomaly]
                is_sig = False
                if anomaly == "day_of_week":
                    is_sig = r.get("significant_kw", False)
                elif anomaly == "nfp_week":
                    is_sig = r.get("significant_mean", False) or r.get("significant_vol", False)
                else:
                    is_sig = r.get("significant", False)

                if is_sig:
                    sig_pairs.append(pair)

        if sig_pairs:
            lines.append(f"  Significant in: {', '.join(sig_pairs)}")
            for pair in sig_pairs:
                r = results[pair][anomaly]
                if anomaly == "day_of_week":
                    lines.append(f"    {pair}: best={r['best_day']}, spread={r['annualized_spread_bps']:.0f}bps/yr")
                elif anomaly == "month_end":
                    lines.append(f"    {pair}: spread={r['annualized_spread_bps']:.0f}bps/yr, p={r['t_test_p']:.4f}")
                elif anomaly == "monthly_seasonality":
                    lines.append(f"    {pair}: best={r['best_month']}, worst={r['worst_month']}, spread={r['annualized_spread_bps']:.0f}bps/yr")
                elif anomaly == "quarter_end":
                    lines.append(f"    {pair}: spread={r['annualized_spread_bps']:.0f}bps/yr, p={r['t_test_p']:.4f}")
                elif anomaly == "session_effect":
                    lines.append(f"    {pair}: best_session={r['best_session']}")
                elif anomaly == "nfp_week":
                    lines.append(f"    {pair}: vol_ratio={r['vol_ratio']:.2f}")
        else:
            lines.append("  No significant effects found.")

    # Overall ranking
    lines.append(f"\n{'='*80}")
    lines.append("ANOMALY RANKING BY SIGNIFICANCE COUNT")
    lines.append("=" * 80)
    counts = {}
    for anomaly in anomaly_types:
        count = 0
        for pair, pair_results in results.items():
            if anomaly in pair_results:
                r = pair_results[anomaly]
                if anomaly == "day_of_week":
                    count += r.get("significant_kw", False)
                elif anomaly == "nfp_week":
                    count += r.get("significant_mean", False) or r.get("significant_vol", False)
                else:
                    count += r.get("significant", False)
        counts[anomaly] = count

    for anomaly, count in sorted(counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {anomaly:25s}: {count}/{len(results)} pairs significant")

    return "\n".join(lines)


def run_daily_tests(pairs: list[str] | None = None) -> dict:
    """Run anomaly tests on daily FX data (longer history)."""
    if pairs is None:
        pairs = FX_PAIRS

    all_results = {}

    for pair in pairs:
        label = PAIR_LABELS[pair]
        print(f"\n{'='*60}")
        print(f"Testing {label} (DAILY)...")
        print(f"{'='*60}")

        try:
            df = fetch_fx_data(pair, period="10y", interval="1d")
            if df is None or len(df) < 100:
                print(f"  Insufficient data for {label}")
                continue
        except Exception as e:
            print(f"  Error fetching {label}: {e}")
            continue

        dt_col = "Datetime" if "Datetime" in df.columns else "Date"
        print(f"  Data: {len(df)} rows")

        pair_results = {}

        # Day of week
        try:
            r = test_day_of_week(df, label)
            pair_results["day_of_week"] = r
            sig = "***" if r["significant_kw"] else ""
            print(f"  DOW: best={r['best_day']}, worst={r['worst_day']}, KW p={r['kruskal_wallis_p']:.4f} {sig}")
        except Exception as e:
            print(f"  DOW error: {e}")

        # Month-end
        try:
            r = test_month_end(df, label)
            pair_results["month_end"] = r
            sig = "***" if r["significant"] else ""
            print(f"  Month-end: TOM={r['tom_mean_daily_ret']:.6f}, rest={r['rest_mean_daily_ret']:.6f}, p={r['t_test_p']:.4f} {sig}")
        except Exception as e:
            print(f"  Month-end error: {e}")

        # Monthly seasonality
        try:
            r = test_monthly_seasonality(df, label)
            pair_results["monthly_seasonality"] = r
            sig = "***" if r["significant"] else ""
            print(f"  Seasonality: best={r['best_month']}, worst={r['worst_month']}, KW p={r['kruskal_wallis_p']:.4f} {sig}")
        except Exception as e:
            print(f"  Seasonality error: {e}")

        # Quarter-end
        try:
            r = test_quarter_end(df, label)
            pair_results["quarter_end"] = r
            sig = "***" if r["significant"] else ""
            print(f"  Quarter-end: QE={r['qe_mean_daily_ret']:.6f}, rest={r['rest_mean_daily_ret']:.6f}, p={r['t_test_p']:.4f} {sig}")
        except Exception as e:
            print(f"  Quarter-end error: {e}")

        all_results[label] = pair_results

    return all_results


if __name__ == "__main__":
    print("=" * 80)
    print("PHASE 1: Hourly data (2 years)")
    print("=" * 80)
    results_1h = run_all_tests()

    print("\n\n")
    print("=" * 80)
    print("PHASE 2: Daily data (10 years)")
    print("=" * 80)
    results_daily = run_daily_tests()

    results = {"hourly_2y": results_1h, "daily_10y": results_daily}

    # Save raw results
    output_path = Path("data/fx-anomaly-results.json")

    # Convert for JSON serialization
    def make_serializable(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        return obj

    json_results = json.loads(
        json.dumps(results, default=make_serializable)
    )
    output_path.write_text(json.dumps(json_results, indent=2))
    print(f"\nResults saved to {output_path}")

    # Print summaries
    for label, res in [("HOURLY (2y)", results_1h), ("DAILY (10y)", results_daily)]:
        print(f"\n\n{'#'*80}")
        print(f"# {label}")
        print(f"{'#'*80}")
        summary = summarize_results(res)
        print(summary)

    # Save summary
    all_summary = ""
    for label, res in [("HOURLY (2y)", results_1h), ("DAILY (10y)", results_daily)]:
        all_summary += f"\n{'#'*80}\n# {label}\n{'#'*80}\n"
        all_summary += summarize_results(res) + "\n"

    summary_path = Path("data/fx-anomaly-summary.txt")
    summary_path.write_text(all_summary)
    print(f"\nSummary saved to {summary_path}")
