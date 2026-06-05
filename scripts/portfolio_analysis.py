"""Portfolio Analysis for WFD-passed strategies.

Analyzes triple-filter passes (WFD + DSR + no MC cliff):
1. Strategy-asset correlation matrix
2. Diversification scoring
3. Portfolio construction recommendations
"""

import json
from collections import defaultdict
from pathlib import Path

import numpy as np


def load_all_passes() -> list[dict]:
    """Load WFD passes from all scan result files."""
    scan_files = [
        "data/scan-results-20260321-223754.json",  # Japan stocks
        "data/scan-results-multi-20260321-232604.json",  # Multi-asset 1h
    ]

    all_passes = []
    for f in scan_files:
        if not Path(f).exists():
            continue
        with open(f) as fh:
            _scan_data = json.load(fh)
        data = (
            _scan_data["results"]
            if isinstance(_scan_data, dict) and "results" in _scan_data
            else _scan_data
        )
        for cell in data:
            if cell.get("wfd_pass"):
                all_passes.append(cell)

    return all_passes


def classify_passes(passes: list[dict]) -> dict:
    """Classify passes by quality tier."""
    tiers = {"gold": [], "silver": [], "bronze": []}

    for p in passes:
        mc = p.get("monte_carlo", {})
        cliff = mc.get("cliff_detected", True) if isinstance(mc, dict) else True
        dsr = p.get("dsr_significant", False)

        if dsr and not cliff:
            tiers["gold"].append(p)
        elif dsr or not cliff:
            tiers["silver"].append(p)
        else:
            tiers["bronze"].append(p)

    return tiers


def correlation_analysis(passes: list[dict]) -> None:
    """Analyze correlation between strategy-asset pairs."""
    # Group by (asset, strategy) for unique entries
    unique = {}
    for p in passes:
        key = f"{p['asset']}_{p['strategy']}"
        if key not in unique or p["oos_pf"] > unique[key]["oos_pf"]:
            unique[key] = p

    print(f"\n{'=' * 80}")
    print("PORTFOLIO ANALYSIS")
    print(f"{'=' * 80}")

    # Asset diversification
    asset_strats = defaultdict(list)
    strat_assets = defaultdict(list)
    for key, p in unique.items():
        asset_strats[p["asset"]].append(p)
        strat_assets[p["strategy"]].append(p)

    print("\n--- Asset Coverage ---")
    for asset, strats in sorted(asset_strats.items()):
        avg_pf = np.mean([s["oos_pf"] for s in strats])
        avg_sharpe = np.mean([s["oos_sharpe"] for s in strats])
        print(
            f"  {asset:12s}: {len(strats)} strategies, avg PF={avg_pf:.2f}, avg Sharpe={avg_sharpe:.2f}"
        )

    print("\n--- Strategy Coverage ---")
    for strat, assets in sorted(strat_assets.items()):
        avg_pf = np.mean([a["oos_pf"] for a in assets])
        n_assets = len(set(a["asset"] for a in assets))
        print(f"  {strat:18s}: {n_assets} assets, avg PF={avg_pf:.2f}")

    return unique


def portfolio_recommendations(unique: dict) -> None:
    """Generate portfolio recommendations."""
    passes = list(unique.values())

    # Score each entry: PF * Sharpe / abs(MaxDD)
    for p in passes:
        dd = abs(p["oos_max_dd"]) if p["oos_max_dd"] != 0 else 0.01
        mc = p.get("monte_carlo", {})
        cliff = mc.get("cliff_detected", True) if isinstance(mc, dict) else True
        dsr = p.get("dsr_significant", False)

        quality = 1.0
        if dsr:
            quality *= 1.5
        if not cliff:
            quality *= 1.3

        p["score"] = p["oos_pf"] * max(p["oos_sharpe"], 0.1) * quality / dd

    # Sort by score
    ranked = sorted(passes, key=lambda x: x["score"], reverse=True)

    print(f"\n{'=' * 80}")
    print("TOP RANKED STRATEGY-ASSET PAIRS")
    print(f"{'=' * 80}")

    seen_assets = set()
    diversified = []

    print("\n--- Top 20 by Score ---")
    for i, p in enumerate(ranked[:20]):
        mc = p.get("monte_carlo", {})
        cliff = mc.get("cliff_detected", True) if isinstance(mc, dict) else True
        dsr = p.get("dsr_significant", False)
        tier = (
            "GOLD" if dsr and not cliff else "SILVER" if dsr or not cliff else "BRONZE"
        )
        print(
            f"  {i + 1:2d}. {p['asset']:12s} {p['strategy']:18s} "
            f"PF={p['oos_pf']:5.2f} Sharpe={p['oos_sharpe']:6.2f} MaxDD={p['oos_max_dd']:7.2%} "
            f"[{tier}] Score={p['score']:.1f}"
        )

    # Diversified portfolio: pick best per asset
    print("\n--- Diversified Portfolio (best per asset) ---")
    for p in ranked:
        if p["asset"] not in seen_assets:
            seen_assets.add(p["asset"])
            diversified.append(p)
            mc = p.get("monte_carlo", {})
            cliff = mc.get("cliff_detected", True) if isinstance(mc, dict) else True
            dsr = p.get("dsr_significant", False)
            tier = (
                "GOLD"
                if dsr and not cliff
                else "SILVER"
                if dsr or not cliff
                else "BRONZE"
            )
            print(
                f"  {p['asset']:12s} {p['strategy']:18s} "
                f"PF={p['oos_pf']:5.2f} Sharpe={p['oos_sharpe']:6.2f} MaxDD={p['oos_max_dd']:7.2%} [{tier}]"
            )

    # Strategy diversification
    print("\n--- Strategy-Diversified Portfolio (max 2 per strategy) ---")
    strat_count = defaultdict(int)
    strat_div = []
    for p in ranked:
        if strat_count[p["strategy"]] < 2:
            strat_count[p["strategy"]] += 1
            strat_div.append(p)
            if len(strat_div) >= 10:
                break

    for p in strat_div:
        print(
            f"  {p['asset']:12s} {p['strategy']:18s} "
            f"PF={p['oos_pf']:5.2f} Sharpe={p['oos_sharpe']:6.2f} MaxDD={p['oos_max_dd']:7.2%}"
        )


def main():
    passes = load_all_passes()
    print(f"Total WFD passes: {len(passes)}")

    tiers = classify_passes(passes)
    print(f"  Gold (DSR + no cliff): {len(tiers['gold'])}")
    print(f"  Silver (DSR or no cliff): {len(tiers['silver'])}")
    print(f"  Bronze: {len(tiers['bronze'])}")

    unique = correlation_analysis(passes)
    portfolio_recommendations(unique)


if __name__ == "__main__":
    main()
