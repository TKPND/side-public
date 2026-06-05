# kenzan/shikake 設計分析（2026-03-18）

## 引き継ぐべき良い設計

### kenzan
- **プラグイン戦略パターン**: `__init_subclass__` 自動登録。戦略追加 = ファイル追加
- **StrategyParams**: Pydantic frozen modelでパラメータ検証・不変性担保
- **Optuna多目的最適化**: TPE + NSGA-II、Pareto最適解から加重スコア選出
- **WFD**: 非重複ローリングウィンドウ、WFE ≥ 50% 合格基準
- **コストモデル段階化**: spread + slippage → session-aware拡張可能

### shikake
- **Thin Bridge**: MT5側ロジックなし、全判断Linux側（→ 暗号資産では不要に）
- **WebSocket JSON-RPC**: デバッグ可能、request-response + push両対応
- **asyncio.Lock + to_thread**: MT5スレッド分離（→ 暗号資産API用にasync化）
- **FastAPI + lifespan**: 依存管理、自動再接続、structlogで観察性高い

## 新システムで解消される課題（暗号資産移行により）
- MT5 Windows依存 → 全部Linux/Docker
- WebSocket bridge → 取引所APIと直接通信
- Terminal crash → 消滅
- 市場クローズ処理 → 24/7（流動性差は残る）
- MT5サイレント失敗 → 取引所APIの明示的エラー

## 残存する課題（新システムでも対処必要）
- WFE > 50%でもfragile → パラメータ安定性スコア追加
- Swap/Financing未計算 → 保有時間ベースのコスト統合
- BT→ライブ乖離 → 3コストシナリオ評価
- ポジション状態管理 → 定期reconciliation

## 新アーキテクチャの方向性
```
[戦略定義] → [データ取得] → [バックテスト] → [WFD] → [ライブ執行]
  Plugin       CCXT/REST     kenzan流       kenzan流     CCXT統一API
  Registry     ローカルDB    Optuna最適化   WFE≥50%     取引所差し替え可能
                              3コスト評価    安定性検証   Discord通知
```
