# 戦略探索・最適化ツール調査レポート

調査日: 2026-03-21

## 目的

ブルートフォース型の戦略x資産xタイムフレーム探索を加速するためのPythonライブラリ・フレームワークの調査。
自前WFDパイプラインとの統合可能性を重視。

---

## 1. 戦略探索・最適化フレームワーク

### 1.1 vectorbt / vectorbtpro

| 項目 | 内容 |
|------|------|
| GitHub | [polakowo/vectorbt](https://github.com/polakowo/vectorbt) ~6.8k stars |
| 概要 | NumPy/Numbaベースのベクトル化バックテストエンジン。数千のパラメータ組み合わせを秒単位でテスト可能 |
| 最新リリース | v0.28.4 (2026-01) |
| PRO版 | $20/月。WFA/CV、並列化、purging付きCombinatorial CV (de Prado方式) をネイティブサポート |

**自前WFDとの統合可能性:**
- OSS版: パラメータグリッド生成+高速バックテストの前段として有用。シグナル生成部分を流用できる
- PRO版: WFA/CVが組み込まれているが、自前WFDと重複する。シグナル生成ライブラリとしての利用が現実的
- **評価: シグナル生成の高速化に有用。WFD自体は自前の方が柔軟**

### 1.2 Optuna

| 項目 | 内容 |
|------|------|
| GitHub | [optuna/optuna](https://github.com/optuna/optuna) ~11k stars |
| 概要 | Define-by-Run型のハイパーパラメータ最適化フレームワーク。TPE、CMA-ES、NSGA-III等のサンプラー内蔵 |
| 最新版 | v4.8.0 |
| メンテ | 非常に活発 (Preferred Networks開発) |

**核心的な価値:**
- **多目的最適化**: `directions=["maximize", "maximize", "minimize"]` でPF, Sharpe, MaxDDを同時最適化し、Pareto frontを可視化
- **枝刈り (Pruning)**: 途中経過が悪いトライアルを早期打ち切り → 探索時間を最大4x短縮
- **分散実行**: RDB StorageでPostgreSQL/MySQL経由の並列ワーカー実行
- **SMAC3統合**: OptunaHub経由でSMAC3サンプラーも利用可能
- **評価: 自前WFDのラッパーとして最も統合しやすい。グリッドサーチからの移行が容易**

### 1.3 Freqtrade

| 項目 | 内容 |
|------|------|
| GitHub | [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) ~35k stars |
| 概要 | 暗号資産特化のトレーディングBot。Optunaベースのhyperopt内蔵 |
| 最新版 | 2026.2 (Python 3.11-3.14対応) |
| 戦略ライブラリ | [freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies) に多数のコミュニティ戦略 |

**注目点:**
- NSGA-IIIサンプラーでエントリ/エグジット/ストップロス/ROIを同時最適化
- 戦略テンプレートが豊富 → アイデアの参照元として有用
- **評価: 暗号資産以外では直接使いにくい。戦略アイデアの参照元として価値あり**

### 1.4 Jesse

| 項目 | 内容 |
|------|------|
| GitHub | [jesse-ai/jesse](https://github.com/jesse-ai/jesse) ~7.6k stars |
| 概要 | 暗号資産特化のアルゴトレーディングフレームワーク。300+指標内蔵 |
| 特徴 | マルチタイムフレーム/マルチシンボル、look-ahead bias防止機能 |

**評価: 暗号資産限定。戦略記述の参考にはなるが統合メリットは低い**

### 1.5 NautilusTrader

| 項目 | 内容 |
|------|------|
| GitHub | [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) ~17k stars |
| 概要 | Rust+Pythonのプロダクショングレードトレーディングエンジン。イベント駆動、ナノ秒精度 |
| パフォーマンス | 500万行/秒のストリーミング |

**評価: 本番デプロイ向け。探索フェーズでの利用はオーバーキル。将来のライブトレーディング基盤候補**

### 1.6 Backtrader

| 項目 | 内容 |
|------|------|
| GitHub | ~14k stars |
| 概要 | 老舗のイベント駆動バックテストライブラリ。`cerebro.optstrategy()`でパラメータ最適化内蔵 |
| メンテ | 開発停滞気味 (最終コミットが古い) |

**評価: vectorbtの方が高速。既存プロジェクトでは積極的に採用する理由なし**

### 1.7 QuantConnect LEAN

| 項目 | 内容 |
|------|------|
| GitHub | [QuantConnect/Lean](https://github.com/QuantConnect/Lean) ~10k stars |
| 概要 | C#/Python対応のアルゴトレーディングエンジン。Grid Search, Euler Search, WFO対応 |
| 戦略ライブラリ | 数百の公開戦略あり (Quant League → Strategies) |

**評価: C#中心のエコシステム。Pythonからの統合コストが高い。戦略アイデアの参照元として有用**

---

## 2. 戦略ライブラリ・コレクション

### 2.1 テクニカル分析ライブラリ

| ライブラリ | 指標数 | 特徴 | 統合容易性 |
|-----------|--------|------|-----------|
| **pandas-ta** | 150+ | Pandas Extension、マルチプロセス対応、Strategy Class内蔵 | 高 (DataFrameベース) |
| **pandas-ta-classic** | 200+ | pandas-taのコミュニティ版、ローソク足パターン60種 | 高 |
| **TA-Lib** (Python wrapper) | 150+ | C実装で高速、業界標準 | 中 (インストールが面倒) |
| **ta** (bukosabino) | 40+ | 軽量、Pandas統合 | 高 |

**活用方針:** pandas-taのStrategy Classを使って全指標を一括計算し、組み合わせをWFDに投入するパイプラインが構築可能

### 2.2 戦略コレクション

| リポジトリ | 内容 |
|-----------|------|
| [awesome-systematic-trading](https://github.com/wangzhe3224/awesome-systematic-trading) | ライブラリ・書籍・戦略のキュレーションリスト |
| [paperswithbacktest/awesome-systematic-trading](https://github.com/paperswithbacktest/awesome-systematic-trading) | 論文付き戦略コレクション |
| [best-of-algorithmic-trading](https://github.com/merovinh/best-of-algorithmic-trading) | 97ライブラリのランク付きリスト |
| [freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies) | Freqtrade用コミュニティ戦略集 |
| GitHub Topics: [trading-strategies](https://github.com/topics/trading-strategies) | 1,673リポジトリ (Python 657) |

**評価: 「戦略Zoo」として体系化されたものは存在しない。各所から手動収集が必要**

---

## 3. Walk-Forward Analysis ツール

### 3.1 既存WFA/WFD実装

| ツール | WFA対応 | 特徴 |
|--------|---------|------|
| **PyBroker** | ネイティブ | NumPy+Numbaで高速。`windows`パラメータでWFA分割。ML統合が強み |
| **vectorbtpro** | ネイティブ | Purging/Embargo付きCombinatorial CV。de Prado方式準拠 |
| **walk-forward-backtester** | 専用ツール | [GitHub](https://github.com/TonyMa1/walk-forward-backtester) Bayesian Opt + WFO |
| **QuantConnect LEAN** | ネイティブ | クラウド上でのWFO実行 |
| **Backtrader** | プラグイン | コミュニティ実装あり |

**評価: 自前WFDは十分に差別化されている (WFD = WFAの発展形)。PyBrokerのアーキテクチャは参考になる**

---

## 4. メタ最適化・ベイジアンアプローチ

### 4.1 Optuna (再掲 - 多目的最適化)

```python
# 多目的最適化の例
study = optuna.create_study(
    directions=["maximize", "maximize", "minimize"],  # PF, Sharpe, MaxDD
    sampler=optuna.samplers.NSGAIIISampler()
)
study.optimize(wfd_objective, n_trials=500)
pareto_trials = study.best_trials
```

- Pareto front可視化: `optuna.visualization.plot_pareto_front(study)`
- 分散実行: RDBバックエンドで複数マシンから並列探索

### 4.2 SMAC3

| 項目 | 内容 |
|------|------|
| GitHub | [automl/SMAC3](https://github.com/automl/SMAC3) ~1.1k stars |
| 概要 | ランダムフォレストベースのベイジアン最適化。多目的、多忠実度、並列対応 |
| 統合 | OptunaHubに登録済み → Optunaのサンプラーとして利用可能 |

**評価: Optunaのサンプラーとして使えるため、別途導入不要**

### 4.3 Ax (Meta)

| 項目 | 内容 |
|------|------|
| 概要 | Meta開発の適応的実験プラットフォーム。v1.0 (2025-11) |
| 特徴 | Gaussian Process + Expected Improvement、SAASBO (高次元ベイジアン) |
| 多目的 | NEHVI (Noisy Expected Hypervolume Improvement) でPareto最適化 |

**評価: 高次元パラメータ空間でOptuna+TPEより優位な場面がある。ただしトレーディング特化ではない**

### 4.4 グリッドサーチ vs ベイジアン最適化の比較

| 手法 | 探索効率 | パラメータ空間 | 並列性 | 実装コスト |
|------|----------|--------------|--------|-----------|
| グリッドサーチ | 低 (全探索) | 低次元のみ現実的 | 高 | 最低 |
| ランダムサーチ | 中 | 中次元まで | 高 | 低 |
| TPE (Optuna) | 高 | 中~高次元 | 中 | 低 |
| GP-BO (Ax) | 最高 | 高次元 | 低 | 中 |
| NSGA-III (Optuna) | 高 (多目的) | 中~高次元 | 中 | 低 |

---

## 5. Feature/Signal Discovery

### 5.1 遺伝的プログラミング

#### gplearn

| 項目 | 内容 |
|------|------|
| GitHub | [trevorstephens/gplearn](https://github.com/trevorstephens/gplearn) ~1.8k stars |
| 概要 | scikit-learn互換のGP。SymbolicRegressor, SymbolicClassifier, SymbolicTransformer |
| メンテ | 開発停滞気味 (安定版 0.4.3) |
| 用途 | アルファ式の自動発見。`SymbolicTransformer`で特徴量エンジニアリング |

**活用例:** OHLCVから数式的アルファ(例: `(close - open) / sqrt(volume)`)を自動探索

#### DEAP

| 項目 | 内容 |
|------|------|
| GitHub | [DEAP/deap](https://github.com/DEAP/deap) ~5.8k stars |
| 概要 | 汎用進化計算フレームワーク。GA, GP, ES, PSO, DE等をサポート |
| メンテ | 活発 |
| 用途 | カスタムGPでトレーディング戦略の木構造を進化 |

**gplearn vs DEAP:**
- gplearn: すぐ使える、scikit-learn互換、ただし柔軟性は低い
- DEAP: 低レベルで柔軟、カスタム適応度関数(=WFD結果)を定義可能、実装コスト高

**評価: DEAP + 自前WFDの組み合わせが最も強力。WFDのPF値を適応度関数にしてGPで戦略式を進化させる**

### 5.2 Symbolic Regression

#### PySR

| 項目 | 内容 |
|------|------|
| GitHub | [MilesCranmer/PySR](https://github.com/MilesCranmer/PySR) ~2.5k stars |
| 概要 | Julia backend + Python frontend。多目的最適化(精度 vs 簡潔さ)のPareto front |
| 特徴 | SRBenchベンチマーク勝者。科学的発見向けだがアルファ式発見にも応用可能 |

**評価: gplearnより高性能。OHLCV→アルファ式の探索に使えるが、トレーディング特化の事例は少ない**

### 5.3 RL/LLMベースのアルファ生成

#### AlphaGen

| 項目 | 内容 |
|------|------|
| GitHub | [RL-MLDM/alphagen](https://github.com/RL-MLDM/alphagen) |
| 概要 | 強化学習でシナジーのある数式的アルファ集合を生成 (KDD 2023) |
| 特徴 | qlib統合、LLMクライアント対応 |

#### Alpha-GFN

| 項目 | 内容 |
|------|------|
| GitHub | [nshen7/alpha-gfn](https://github.com/nshen7/alpha-gfn) |
| 概要 | GFlowNetベースのアルファファクター探索 |

**評価: 学術的に興味深いが、株式市場特化で暗号資産/FXには直接適用しにくい。qlib依存も制約**

---

## 6. 推奨アクションプラン

### 即座に導入可能 (低コスト・高リターン)

1. **Optuna統合** - 自前WFDのobjectiveをOptuna studyでラップ
   - グリッドサーチ → TPE/NSGA-IIIに切り替え
   - 多目的最適化 (PF + Sharpe + MaxDD) でPareto front取得
   - 枝刈りで探索時間短縮
   - 推定工数: 1-2日

2. **pandas-ta導入** - 全指標一括計算 → 組み合わせ探索の入力
   - 150+指標をDataFrame列として追加
   - 推定工数: 0.5日

### 中期検討 (1-2週間)

3. **DEAP + WFD** - 遺伝的プログラミングでシグナル式を自動進化
   - 適応度関数 = WFDのPF値
   - OHLCVの演算木を進化させてアルファ式を発見
   - 推定工数: 3-5日

4. **PySR試行** - Symbolic Regressionでアルファ式探索
   - gplearnより高速・高精度
   - 推定工数: 1-2日

### 長期検討

5. **NautilusTrader** - ライブトレーディング基盤として
6. **AlphaGen** - RL/LLMベースのアルファ発見 (実験的)

---

## 7. アーキテクチャ統合案

```
[pandas-ta: 指標生成]
        |
        v
[DEAP/PySR: シグナル式探索] ──or── [手動戦略定義]
        |                              |
        v                              v
[Optuna: パラメータ最適化 (TPE/NSGA-III)]
        |
        v
[自前WFD: Walk-Forward Decomposition検証]
        |
        v
[Pareto Front分析: PF x Sharpe x MaxDD]
        |
        v
[NautilusTrader: ライブデプロイ (将来)]
```

**核心思想:** 自前WFDは最も重要な差別化要素。上流(シグナル生成)と最適化層(Optuna)の強化が最もROIが高い。

---

## Sources

- [vectorbt GitHub](https://github.com/polakowo/vectorbt)
- [VectorBT PRO](https://vectorbt.pro/)
- [Optuna](https://optuna.org/)
- [Optuna Multi-objective Tutorial](https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/002_multi_objective.html)
- [SMAC3 on OptunaHub](https://hub.optuna.org/samplers/smac_sampler/)
- [Freqtrade](https://www.freqtrade.io/en/stable/)
- [Freqtrade Strategies](https://github.com/freqtrade/freqtrade-strategies)
- [NautilusTrader](https://nautilustrader.io/)
- [NautilusTrader GitHub](https://github.com/nautechsystems/nautilus_trader)
- [Jesse](https://jesse.trade/)
- [Jesse GitHub](https://github.com/jesse-ai/jesse)
- [QuantConnect LEAN](https://github.com/QuantConnect/Lean)
- [PyBroker](https://www.pybroker.com/en/latest/)
- [PyBroker GitHub](https://github.com/edtechre/pybroker)
- [walk-forward-backtester](https://github.com/TonyMa1/walk-forward-backtester)
- [gplearn](https://github.com/trevorstephens/gplearn)
- [DEAP](https://github.com/DEAP/deap)
- [PySR](https://github.com/MilesCranmer/PySR)
- [AlphaGen](https://github.com/RL-MLDM/alphagen)
- [Alpha-GFN](https://github.com/nshen7/alpha-gfn)
- [Ax Platform (Meta)](https://ax.dev/)
- [SMAC3 GitHub](https://github.com/automl/SMAC3)
- [pandas-ta-classic](https://github.com/xgboosted/pandas-ta-classic)
- [awesome-systematic-trading](https://github.com/wangzhe3224/awesome-systematic-trading)
- [best-of-algorithmic-trading](https://github.com/merovinh/best-of-algorithmic-trading)
