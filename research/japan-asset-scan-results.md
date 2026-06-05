# Japan Asset Discovery Scan — 2026-03-21

## Summary

714 WFD work units (17 asset/TF × 7 strategies × 2 modes × 3 exits) completed in 2.5min.
63 discovery passes found, 10 validated in robustness check.

### Discovery Config (relaxed for exploration)
- IS PF >= 1.2 (standard: 1.5)
- OOS PF >= 1.1 (standard: 2.0)
- Annual trades >= 15 (standard: 50)
- OOS win rate >= 60% (standard: 80%)
- WFE >= 0.3 (standard: 0.5)

## TIER 1: Robust Results (2+/3 WFD configs PASS, params stable)

| Asset | Strategy | TF | Mode | OOS PF | Trades | Robust | Params |
|---|---|---|---|---|---|---|---|
| **9984.T** (SBG) | **Keltner** | **1d** | **long_only** | **1.84** | **16** | **2/3 STABLE** | EMA10, ATR14, m=1.5 |
| **9983.T** (FastRetail) | **DualMomentum** | **1d** | **both** | **1.63** | **13** | **2/3 STABLE** | ROC10, th=7.0 |

## TIER 2: High-Trade Discovery Passes (trades >= 15, PF >= 1.2)

| Asset | Strategy | TF | Mode | Exit | OOS PF | Trades | WFE |
|---|---|---|---|---|---|---|---|
| 9984.T | Keltner | 1h | long_only | PCT_2_4 | 1.24 | 37 | 0.96 |
| **8306.T** (MUFG) | **EmaAtr** | **1h** | **long_only** | **baseline** | **1.59** | **36** | **1.14** |
| 8306.T | Keltner | 1h | long_only | ATR_2_4 | 1.30 | 23 | 1.15 |
| 8306.T | Keltner | 1h | long_only | PCT_2_4 | 1.36 | 22 | 1.34 |
| 8306.T | SmaCross | 1h | long_only | ATR_2_4 | 1.26 | 23 | 0.81 |

## Asset Ranking (passes with trades >= 10)

| Asset | Name | Passes | Best Strategy |
|---|---|---|---|
| **8306.T** | 三菱UFJ FG | 11 | EmaAtr 1h long_only PF=1.59 |
| **9984.T** | ソフトバンクG | 8 | Keltner 1d long_only PF=1.84 |
| **1570.T** | 日経レバETF | 6 | DualMom 1d both PF=3.71 |
| **6861.T** | キーエンス | 4 | DualMom 1h long_only PF=2.88 |
| 7203.T | トヨタ | 3 | Keltner 1d long_only PF=1.78 |
| 9983.T | ファストリ | 3 | DualMom 1d both PF=1.63 |
| ^N225 | 日経225 | 2 | RSI 1h both PF=1.22 |
| 8035.T | 東京エレクトロン | 1 | RSI 1d both PF=4.41 |
| JPY=X | USD/JPY | 1 | Keltner 4h both PF=1.13 |

## Key Insights

1. **8306.T (三菱UFJ) が最も幅広いエッジ** — 11 passes、複数の戦略/exit で一貫。
   金融株で流動性が高く、トレンドフォロー系が有効。
   S&P500 EmaAtr との類似性が高い（同じ EMA + ATR パターン）。

2. **9984.T (SBG) Keltner 1d が最も堅牢** — PF=1.84、パラメータ安定、2/3 robustness PASS。
   ボラが大きい半面、1d TF でノイズが平滑化される。

3. **日本株は 1d > 1h** — 1d の方がPF が高い傾向。
   取引時間が短い（6h/日）ため、1h では信号/ノイズ比が低い。

4. **JPY=X はほぼエッジなし** — PF ≈ 1.0。FX は暗号資産と同様、効率的。

5. **^N225 指数よりも個別株にエッジ** — 個別株の方がボラ/非効率が残存。

## Comparison with S&P500 Results

| Metric | S&P500 (Session 3) | Japan Best (8306.T) |
|---|---|---|
| Strategy | EmaAtr | EmaAtr |
| TF | 1h | 1h |
| Mode | long_only | long_only |
| OOS PF | 1.24-1.26 | 1.59 |
| Trades | 22-26 | 36 |
| Param Stability | YES (EMA12/50) | TBD (needs robustness) |

## Next Steps

1. **8306.T EmaAtr 1h long_only のロバストネス検証** — 複数WFD configで確認
2. **9984.T Keltner 1d long_only の深掘り** — パラメータ近傍チェック
3. **日本株ポートフォリオ構成** — 8306.T + 9984.T + S&P500 で分散
4. **取引コスト検証** — 日本株の取引手数料は S&P500 より低い可能性
