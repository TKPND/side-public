"""FX Cross-Asset Signal Research Script.

Tests cross-asset predictive signals for FX:
1. Interest rate differential (carry) proxy via bond yields
2. Equity market → FX spillover (SPY/VIX → FX)
3. Commodity → commodity currency (Gold/Oil → AUD/CAD)
4. Cross-currency momentum
5. Volatility regime (VIX level) → FX carry
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# FX pairs
FX_PAIRS = {
    "EURUSD=X": "EURUSD",
    "GBPUSD=X": "GBPUSD",
    "USDJPY=X": "USDJPY",
    "AUDUSD=X": "AUDUSD",
    "USDCAD=X": "USDCAD",
    "NZDUSD=X": "NZDUSD",
}

# Cross-asset tickers
CROSS_ASSETS = {
    "^VIX": "VIX",
    "^GSPC": "SP500",       # S&P 500
    "^N225": "Nikkei",      # Nikkei 225
    "GC=F": "Gold",
    "CL=F": "Oil_WTI",
    "^TNX": "US10Y",        # US 10Y yield
    "^TYX": "US30Y",        # US 30Y yield
    "DX-Y.NYB": "DXY",      # Dollar index
}


def fetch_data(ticker: str, period: str = "10y", interval: str = "1d") -> pd.DataFrame:
    """Fetch data from yfinance with caching."""
    import yfinance as yf

    cache_dir = Path("data/cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_name = ticker.replace("=", "_").replace("^", "_").replace(".", "_")
    cache_file = cache_dir / f"{safe_name}_{interval}_{period}.csv"

    if cache_file.exists():
        df = pd.read_csv(cache_file)
        # Normalize date column name and strip time for daily data
        for col in ["Date", "Datetime"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True).dt.tz_localize(None).dt.normalize()
                if col != "Date":
                    df = df.rename(columns={col: "Date"})
                break
        return df

    t = yf.Ticker(ticker)
    df = t.history(period=period, interval=interval)
    df = df.reset_index()
    if "Datetime" in df.columns:
        df = df.rename(columns={"Datetime": "Date"})
    df.to_csv(cache_file, index=False)
    return df


def align_series(*series_list: pd.Series) -> list[pd.Series]:
    """Align multiple series by their common dates."""
    common_idx = series_list[0].index
    for s in series_list[1:]:
        common_idx = common_idx.intersection(s.index)
    return [s.loc[common_idx] for s in series_list]


def test_vix_fx_relationship(fx_data: dict, vix_data: pd.DataFrame) -> dict:
    """Test VIX level → FX carry/risk-off relationship."""
    results = {}
    vix_ret = np.log(vix_data.set_index("Date")["Close"]).diff().dropna()
    vix_level = vix_data.set_index("Date")["Close"]

    for ticker, label in FX_PAIRS.items():
        df = fx_data.get(ticker)
        if df is None or len(df) < 100:
            continue

        fx_ret = np.log(df.set_index("Date")["Close"]).diff().dropna()

        # Align
        common = fx_ret.index.intersection(vix_ret.index)
        if len(common) < 50:
            continue

        fx_r = fx_ret.loc[common]
        vix_r = vix_ret.loc[common]
        vix_l = vix_level.loc[common]

        # 1. Contemporaneous correlation
        corr, corr_p = stats.pearsonr(fx_r.values, vix_r.values)

        # 2. VIX regime: high vs low (median split)
        vix_median = vix_l.median()
        high_vix = fx_r[vix_l > vix_median]
        low_vix = fx_r[vix_l <= vix_median]

        if len(high_vix) > 10 and len(low_vix) > 10:
            regime_t, regime_p = stats.ttest_ind(high_vix.values, low_vix.values)
        else:
            regime_t, regime_p = 0.0, 1.0

        # 3. Predictive: VIX change today → FX return tomorrow
        vix_lag = vix_r.shift(1).dropna()
        fx_lead = fx_r.loc[vix_lag.index]
        if len(vix_lag) > 50:
            pred_corr, pred_p = stats.pearsonr(vix_lag.values, fx_lead.values)
        else:
            pred_corr, pred_p = 0.0, 1.0

        results[label] = {
            "contemporaneous_corr": float(corr),
            "contemporaneous_p": float(corr_p),
            "vix_regime_high_mean": float(high_vix.mean()),
            "vix_regime_low_mean": float(low_vix.mean()),
            "vix_regime_p": float(regime_p),
            "predictive_corr": float(pred_corr),
            "predictive_p": float(pred_p),
            "significant_contemp": corr_p < 0.05,
            "significant_regime": regime_p < 0.05,
            "significant_predictive": pred_p < 0.05,
        }

    return results


def test_equity_fx_spillover(fx_data: dict, equity_data: dict) -> dict:
    """Test equity market → FX spillover."""
    results = {}
    sp500 = equity_data.get("^GSPC")
    nikkei = equity_data.get("^N225")

    if sp500 is None:
        return results

    sp_ret = np.log(sp500.set_index("Date")["Close"]).diff().dropna()

    for ticker, label in FX_PAIRS.items():
        df = fx_data.get(ticker)
        if df is None or len(df) < 100:
            continue

        fx_ret = np.log(df.set_index("Date")["Close"]).diff().dropna()

        # Align
        common = fx_ret.index.intersection(sp_ret.index)
        if len(common) < 50:
            continue

        fx_r = fx_ret.loc[common]
        sp_r = sp_ret.loc[common]

        # 1. Same-day correlation
        corr, corr_p = stats.pearsonr(fx_r.values, sp_r.values)

        # 2. S&P500 lag-1 → FX
        sp_lag = sp_r.shift(1).dropna()
        fx_lead = fx_r.loc[sp_lag.index]
        if len(sp_lag) > 50:
            lag_corr, lag_p = stats.pearsonr(sp_lag.values, fx_lead.values)
        else:
            lag_corr, lag_p = 0.0, 1.0

        # 3. Big moves: S&P500 > 2 std → FX next day
        sp_std = sp_r.std()
        big_up = sp_r[sp_r > 2 * sp_std].index
        big_down = sp_r[sp_r < -2 * sp_std].index

        next_day_after_up = fx_r.loc[fx_r.index.isin([(d + pd.Timedelta(days=1)) for d in big_up]) |
                                      fx_r.index.isin([(d + pd.Timedelta(days=2)) for d in big_up]) |
                                      fx_r.index.isin([(d + pd.Timedelta(days=3)) for d in big_up])]
        next_day_after_down = fx_r.loc[fx_r.index.isin([(d + pd.Timedelta(days=1)) for d in big_down]) |
                                        fx_r.index.isin([(d + pd.Timedelta(days=2)) for d in big_down]) |
                                        fx_r.index.isin([(d + pd.Timedelta(days=3)) for d in big_down])]

        results[label] = {
            "sp500_contemp_corr": float(corr),
            "sp500_contemp_p": float(corr_p),
            "sp500_lag1_corr": float(lag_corr),
            "sp500_lag1_p": float(lag_p),
            "significant_contemp": corr_p < 0.05,
            "significant_lag": lag_p < 0.05,
            "fx_after_sp_big_up_mean": float(next_day_after_up.mean()) if len(next_day_after_up) > 0 else 0.0,
            "fx_after_sp_big_down_mean": float(next_day_after_down.mean()) if len(next_day_after_down) > 0 else 0.0,
            "n_big_up": len(big_up),
            "n_big_down": len(big_down),
        }

    return results


def test_commodity_currency(fx_data: dict, commodity_data: dict) -> dict:
    """Test commodity → commodity currency relationship."""
    results = {}

    # Gold → AUD, Oil → CAD
    commodity_fx_pairs = [
        ("GC=F", "AUDUSD=X", "Gold_AUD"),
        ("CL=F", "USDCAD=X", "Oil_CAD"),
        ("GC=F", "NZDUSD=X", "Gold_NZD"),
    ]

    for comm_ticker, fx_ticker, name in commodity_fx_pairs:
        comm_df = commodity_data.get(comm_ticker)
        fx_df = fx_data.get(fx_ticker)

        if comm_df is None or fx_df is None or len(comm_df) < 100 or len(fx_df) < 100:
            continue

        comm_ret = np.log(comm_df.set_index("Date")["Close"]).diff().dropna()
        fx_ret = np.log(fx_df.set_index("Date")["Close"]).diff().dropna()

        common = comm_ret.index.intersection(fx_ret.index)
        if len(common) < 50:
            continue

        c_r = comm_ret.loc[common]
        f_r = fx_ret.loc[common]

        # Same-day correlation
        corr, corr_p = stats.pearsonr(c_r.values, f_r.values)

        # Lag-1 predictive
        c_lag = c_r.shift(1).dropna()
        f_lead = f_r.loc[c_lag.index]
        if len(c_lag) > 50:
            lag_corr, lag_p = stats.pearsonr(c_lag.values, f_lead.values)
        else:
            lag_corr, lag_p = 0.0, 1.0

        # Rolling correlation stability
        rolling_corr = c_r.rolling(60).corr(f_r).dropna()

        results[name] = {
            "contemp_corr": float(corr),
            "contemp_p": float(corr_p),
            "lag1_corr": float(lag_corr),
            "lag1_p": float(lag_p),
            "rolling_corr_mean": float(rolling_corr.mean()),
            "rolling_corr_std": float(rolling_corr.std()),
            "significant_contemp": corr_p < 0.05,
            "significant_lag": lag_p < 0.05,
            "n_observations": len(common),
        }

    return results


def test_cross_currency_momentum(fx_data: dict) -> dict:
    """Test cross-currency momentum (time-series momentum in FX)."""
    results = {}
    lookbacks = [5, 10, 20, 60]

    for ticker, label in FX_PAIRS.items():
        df = fx_data.get(ticker)
        if df is None or len(df) < 100:
            continue

        close = df.set_index("Date")["Close"]
        ret = np.log(close).diff().dropna()

        pair_results = {}
        for lb in lookbacks:
            # Past N-day return as signal
            past_ret = ret.rolling(lb).sum().shift(1).dropna()
            future_ret = ret.loc[past_ret.index]

            if len(past_ret) < 50:
                continue

            # Correlation between past and future
            corr, corr_p = stats.pearsonr(past_ret.values, future_ret.values)

            # Strategy: long if past > 0, else flat
            signal = (past_ret > 0).astype(int)
            strat_ret = signal * future_ret
            sharpe = float(strat_ret.mean() / strat_ret.std() * np.sqrt(252)) if strat_ret.std() > 0 else 0.0

            pair_results[f"lb{lb}"] = {
                "momentum_corr": float(corr),
                "momentum_p": float(corr_p),
                "strategy_sharpe": sharpe,
                "strategy_annual_ret": float(strat_ret.mean() * 252),
                "significant": corr_p < 0.05,
            }

        results[label] = pair_results

    return results


def test_dxy_mean_reversion(fx_data: dict, dxy_data: pd.DataFrame | None) -> dict:
    """Test DXY mean reversion → individual FX pairs."""
    results = {}
    if dxy_data is None or len(dxy_data) < 100:
        return results

    dxy_close = dxy_data.set_index("Date")["Close"]
    dxy_ret = np.log(dxy_close).diff().dropna()

    for ticker, label in FX_PAIRS.items():
        df = fx_data.get(ticker)
        if df is None or len(df) < 100:
            continue

        fx_ret = np.log(df.set_index("Date")["Close"]).diff().dropna()
        common = fx_ret.index.intersection(dxy_ret.index)
        if len(common) < 50:
            continue

        fx_r = fx_ret.loc[common]
        dxy_r = dxy_ret.loc[common]

        # DXY z-score (20-day) → FX next day
        dxy_ma = dxy_close.rolling(20).mean()
        dxy_std = dxy_close.rolling(20).std()
        dxy_z = ((dxy_close - dxy_ma) / dxy_std).dropna()

        common2 = fx_r.index.intersection(dxy_z.index)
        if len(common2) < 50:
            continue

        dxy_z_aligned = dxy_z.loc[common2].shift(1).dropna()
        fx_aligned = fx_r.loc[dxy_z_aligned.index]

        corr, corr_p = stats.pearsonr(dxy_z_aligned.values, fx_aligned.values)

        # Strategy: sell DXY extreme (z > 2) → buy FX
        extreme_high = dxy_z_aligned[dxy_z_aligned > 1.5]
        extreme_low = dxy_z_aligned[dxy_z_aligned < -1.5]

        fx_after_high = fx_aligned.loc[extreme_high.index]
        fx_after_low = fx_aligned.loc[extreme_low.index]

        results[label] = {
            "dxy_z_fx_corr": float(corr),
            "dxy_z_fx_p": float(corr_p),
            "significant": corr_p < 0.05,
            "fx_mean_after_dxy_high": float(fx_after_high.mean()) if len(fx_after_high) > 0 else 0.0,
            "fx_mean_after_dxy_low": float(fx_after_low.mean()) if len(fx_after_low) > 0 else 0.0,
            "n_extreme_high": len(extreme_high),
            "n_extreme_low": len(extreme_low),
        }

    return results


def main():
    print("=" * 80)
    print("FX CROSS-ASSET SIGNAL RESEARCH")
    print("=" * 80)

    # Fetch all data
    print("\nFetching FX data...")
    fx_data = {}
    for ticker, label in FX_PAIRS.items():
        try:
            df = fetch_data(ticker, period="10y", interval="1d")
            if df is not None and len(df) > 100:
                fx_data[ticker] = df
                print(f"  {label}: {len(df)} rows")
        except Exception as e:
            print(f"  {label}: ERROR {e}")

    print("\nFetching cross-asset data...")
    cross_data = {}
    for ticker, label in CROSS_ASSETS.items():
        try:
            df = fetch_data(ticker, period="10y", interval="1d")
            if df is not None and len(df) > 100:
                cross_data[ticker] = df
                print(f"  {label}: {len(df)} rows")
        except Exception as e:
            print(f"  {label}: ERROR {e}")

    all_results = {}

    # 1. VIX → FX
    print("\n\n--- VIX → FX ---")
    vix_df = cross_data.get("^VIX")
    if vix_df is not None:
        vix_results = test_vix_fx_relationship(fx_data, vix_df)
        all_results["vix_fx"] = vix_results
        for pair, r in vix_results.items():
            sig = []
            if r["significant_contemp"]:
                sig.append(f"contemp r={r['contemporaneous_corr']:.3f}")
            if r["significant_regime"]:
                sig.append(f"regime p={r['vix_regime_p']:.4f}")
            if r["significant_predictive"]:
                sig.append(f"predictive r={r['predictive_corr']:.3f}")
            print(f"  {pair}: {', '.join(sig) if sig else 'not significant'}")

    # 2. Equity → FX
    print("\n--- Equity → FX ---")
    equity_results = test_equity_fx_spillover(fx_data, cross_data)
    all_results["equity_fx"] = equity_results
    for pair, r in equity_results.items():
        sig = []
        if r["significant_contemp"]:
            sig.append(f"contemp r={r['sp500_contemp_corr']:.3f}")
        if r["significant_lag"]:
            sig.append(f"lag1 r={r['sp500_lag1_corr']:.3f}")
        print(f"  {pair}: {', '.join(sig) if sig else 'not significant'}")

    # 3. Commodity → Currency
    print("\n--- Commodity → Currency ---")
    commodity_results = test_commodity_currency(fx_data, cross_data)
    all_results["commodity_currency"] = commodity_results
    for name, r in commodity_results.items():
        sig = []
        if r["significant_contemp"]:
            sig.append(f"contemp r={r['contemp_corr']:.3f}")
        if r["significant_lag"]:
            sig.append(f"lag1 r={r['lag1_corr']:.3f}")
        print(f"  {name}: {', '.join(sig) if sig else 'not significant'}")

    # 4. Cross-currency momentum
    print("\n--- Cross-Currency Momentum ---")
    momentum_results = test_cross_currency_momentum(fx_data)
    all_results["momentum"] = momentum_results
    for pair, lookbacks in momentum_results.items():
        best_lb = max(lookbacks, key=lambda k: lookbacks[k].get("strategy_sharpe", 0))
        best = lookbacks[best_lb]
        sig = "***" if best["significant"] else ""
        print(f"  {pair}: best={best_lb}, sharpe={best['strategy_sharpe']:.2f}, corr={best['momentum_corr']:.3f} {sig}")

    # 5. DXY mean reversion
    print("\n--- DXY Mean Reversion ---")
    dxy_df = cross_data.get("DX-Y.NYB")
    dxy_results = test_dxy_mean_reversion(fx_data, dxy_df)
    all_results["dxy_mean_reversion"] = dxy_results
    for pair, r in dxy_results.items():
        sig = "***" if r["significant"] else ""
        print(f"  {pair}: corr={r['dxy_z_fx_corr']:.3f}, p={r['dxy_z_fx_p']:.4f} {sig}")

    # Summary
    print("\n\n" + "=" * 80)
    print("CROSS-ASSET SIGNAL RANKING")
    print("=" * 80)

    signal_scores = []
    # VIX
    if "vix_fx" in all_results:
        contemp = sum(1 for r in all_results["vix_fx"].values() if r["significant_contemp"])
        pred = sum(1 for r in all_results["vix_fx"].values() if r["significant_predictive"])
        signal_scores.append(("VIX→FX (contemporaneous)", contemp, len(all_results["vix_fx"])))
        signal_scores.append(("VIX→FX (predictive lag-1)", pred, len(all_results["vix_fx"])))

    # Equity
    if "equity_fx" in all_results:
        contemp = sum(1 for r in all_results["equity_fx"].values() if r["significant_contemp"])
        lag = sum(1 for r in all_results["equity_fx"].values() if r["significant_lag"])
        signal_scores.append(("SP500→FX (contemporaneous)", contemp, len(all_results["equity_fx"])))
        signal_scores.append(("SP500→FX (predictive lag-1)", lag, len(all_results["equity_fx"])))

    # Commodity
    if "commodity_currency" in all_results:
        contemp = sum(1 for r in all_results["commodity_currency"].values() if r["significant_contemp"])
        lag = sum(1 for r in all_results["commodity_currency"].values() if r["significant_lag"])
        signal_scores.append(("Commodity→Currency (contemp)", contemp, len(all_results["commodity_currency"])))
        signal_scores.append(("Commodity→Currency (lag-1)", lag, len(all_results["commodity_currency"])))

    # Momentum
    if "momentum" in all_results:
        for lb_name in ["lb5", "lb10", "lb20", "lb60"]:
            sig = sum(1 for pair_data in all_results["momentum"].values()
                      if lb_name in pair_data and pair_data[lb_name]["significant"])
            total = sum(1 for pair_data in all_results["momentum"].values() if lb_name in pair_data)
            signal_scores.append((f"Momentum {lb_name}", sig, total))

    # DXY
    if "dxy_mean_reversion" in all_results:
        sig = sum(1 for r in all_results["dxy_mean_reversion"].values() if r["significant"])
        signal_scores.append(("DXY z-score→FX", sig, len(all_results["dxy_mean_reversion"])))

    signal_scores.sort(key=lambda x: x[1] / max(x[2], 1), reverse=True)
    for name, sig, total in signal_scores:
        print(f"  {name:40s}: {sig}/{total} pairs significant")

    # Save
    output_path = Path("data/fx-cross-asset-results.json")

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

    json_results = json.loads(json.dumps(all_results, default=make_serializable))
    output_path.write_text(json.dumps(json_results, indent=2))
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
