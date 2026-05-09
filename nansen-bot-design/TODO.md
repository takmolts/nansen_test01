# 実装TODOリスト

段階的に実装していくためのタスクリスト。チェックボックスで進捗管理。

---

## Phase 0: セットアップ

- [ ] GitHubでリポジトリ作成(private推奨)
- [ ] VSCodeでプロジェクトclone、仮想環境作成(`python -m venv .venv`)
- [ ] `requirements.txt`作成、`pip install -r requirements.txt`
- [ ] `.gitignore`作成(`.env`, `.venv`, `__pycache__`, `data/` 等)
- [ ] Discord Developer Portalでbotアプリケーション作成
  - [ ] Bot Token取得
  - [ ] Application ID取得
  - [ ] Slash Commandsのpermissionを有効化
  - [ ] Message Content Intentは不要(Slash commandsのみ使うため)
- [ ] Nansen APIのFreeプランにサインアップ、APIキー取得
- [ ] CoinGecko Demoプランにサインアップ、APIキー取得
- [ ] `.env`作成(`.env.example`をコピーして値を埋める)
- [ ] テストサーバ(Discord)作成、botを招待

---

## Phase 1: MVP(最小動作)

目標: `/analyze <token> <chain>` を叩くとサブスレッドが作られ、
Nansen + DexScreenerの情報をもとに最小限のEmbedが投稿される。

### 1.1 スケルトン

- [ ] `bot/main.py` でbot起動できる
- [ ] `bot/config.py` で`.env`の読み込みができる
- [ ] Slash command `/analyze` が登録され、起動時にguildに反映される

### 1.2 APIクライアント最小実装

- [ ] `bot/clients/nansen.py`
  - [ ] 基底クラス(認証ヘッダ、タイムアウト、リトライ)
  - [ ] `tgm.token_information(address, chain)`
  - [ ] `tgm.holders(address, chain)`
- [ ] `bot/clients/dexscreener.py`
  - [ ] `tokens(address)`
  - [ ] `search(query)`

### 1.3 サブスレッド作成

- [ ] `bot/thread_manager/manager.py`
  - [ ] `create_analysis_thread(channel, token_symbol)` でスレッド生成
  - [ ] 手動削除ボタンのハンドラ

### 1.4 最小スコアリング

- [ ] `bot/scoring/engine.py` で3カテゴリだけ計算
  - [ ] Smart Money(Nansen holdersから簡略版)
  - [ ] Liquidity(DexScreenerから)
  - [ ] Momentum(DexScreenerから)
- [ ] 総合スコア算出(上記3つのみ、重みを一時的に再配分)

### 1.5 最小Embed

- [ ] `bot/embeds/main_embed.py`
  - [ ] タイトル・description・総合スコア表示
  - [ ] プログレスバー文字列生成ユーティリティ
  - [ ] 簡単なinsight(スコア値に応じたテンプレート)

### 1.6 動作確認

- [ ] 有名なSolanaミームコイン(例: BONK)でコマンド実行
- [ ] スレッド作成→Embed表示→削除ボタン動作確認

---

## Phase 2: TTL削除・キャッシュ

- [ ] `bot/cache/memory_cache.py` 実装
  - [ ] `get(key)`, `set(key, value, ttl)` のシンプルなTTLキャッシュ
  - [ ] NansenクライアントとDexScreenerクライアントに組み込み
- [ ] `bot/thread_manager/scheduler.py` 実装
  - [ ] `data/threads.db`(SQLite)にスレッド情報を永続化
  - [ ] 1分おきに削除予定スレッドをチェック
  - [ ] 時刻経過したら`thread.delete()`
- [ ] bot起動時に既存のスケジュールを復元
- [ ] `.env`の`THREAD_TTL_MINUTES`で制御できることを確認

---

## Phase 3: 残りのスコアリング(5カテゴリ追加)

- [ ] `bot/scoring/distribution.py`
  - [ ] トップ10集中度計算(Nansen holders)
  - [ ] 新規ホルダー増加率
- [ ] `bot/scoring/risk.py`
  - [ ] `tgm/nansen-indicators` 呼び出し
  - [ ] **実レスポンス確認して正規化式を確定**
  - [ ] トークン年齢計算
  - [ ] CEX流入比率計算
- [ ] `bot/scoring/deployer.py`
  - [ ] `profiler/address/labels` で警告ラベル検出
  - [ ] `profiler/address/transactions` で最古tx→年齢計算
  - [ ] `profiler/address/pnl-summary` で実績確認
  - [ ] `profiler/address/related-wallets` でクリーン度判定
- [ ] `bot/scoring/bundle.py`
  - [ ] 3%超ホルダー抽出
  - [ ] 各whaleの`related-wallets`取得
  - [ ] First Funderでクラスタ検出
  - [ ] 警告ラベル混入度計算
  - [ ] Insider Ratio計算
- [ ] `bot/scoring/engine.py`
  - [ ] 8カテゴリの並列呼び出し(`asyncio.gather`)
  - [ ] 重み設定(DESIGN.md参照)で総合スコア算出

---

## Phase 4: Narrative + DexScreener/CoinGecko連携

- [ ] `bot/clients/coingecko.py`
  - [ ] `/coins/{asset_platform}/contract/{address}` でカテゴリ取得
  - [ ] `/search/trending` 取得
- [ ] `bot/clients/dexscreener.py` 追加機能
  - [ ] `/token-boosts/latest/v1` で対象address検索
  - [ ] search結果からdeploy日時を抽出
- [ ] `bot/scoring/narrative.py`
  - [ ] シンボル部分一致での類似検索
  - [ ] α/β/early判定ロジック
  - [ ] Trending/Boost確認
  - [ ] ソーシャル充実度計算
- [ ] 特別フラグ(α-Token / β-Token / Hidden Gem / Rug Risk / Bundle Risk / Overheated)の判定実装

---

## Phase 5: 詳細Embed

- [ ] `bot/views/analysis_view.py`
  - [ ] 「📊 詳細」ボタン
  - [ ] 「🔍 Bundle」ボタン
  - [ ] 「🎖️ 類似Token」ボタン
  - [ ] 「📉 Chart」ボタン
  - [ ] 「🗑️ 削除」ボタン
- [ ] `bot/embeds/detail_embed.py` — カテゴリ別8Embed
- [ ] `bot/embeds/bundle_embed.py` — クラスタ可視化
- [ ] `bot/embeds/similar_embed.py` — 類似Token一覧
- [ ] `bot/embeds/insights.py` — Insightテンプレ文生成

---

## Phase 6: Chart

- [ ] `bot/embeds/chart_embed.py`
  - [ ] matplotlibで価格+出来高の2段チャート生成
  - [ ] PNGをメモリ上で生成(一時ファイル不要)
  - [ ] Embedに添付して投稿
- [ ] CoinGecko OHLCV または DexScreener から履歴データ取得

---

## Phase 7: 品質向上

- [ ] エラーハンドリング強化
  - [ ] Nansen 401/403/429/500 の適切な処理
  - [ ] DexScreener 404(未登録トークン)の処理
  - [ ] タイムアウト処理
- [ ] ロギング整備(`bot/utils/logger.py`)
  - [ ] APIコール数・クレジット消費の記録
  - [ ] エラーログ
- [ ] テスト
  - [ ] モックレスポンスでスコアリングの単体テスト
  - [ ] APIクライアントの接続テスト
- [ ] レート制限対応
  - [ ] Nansen: `asyncio.Semaphore`で同時実行数制御
  - [ ] DexScreener: 300req/min制限の管理

---

## Phase 8: デプロイ

- [ ] デプロイ先決定
  - Oracle Cloud Always Free(1vCPU/1GB)推奨
  - または Railway.app / Fly.io 等の無料枠
  - 自宅PCでも可
- [ ] systemd サービス化 または Docker コンテナ化
- [ ] 自動再起動設定
- [ ] ログローテーション設定
- [ ] モニタリング(Uptimeチェック程度でOK)

---

## 将来の拡張(v2以降)

- [ ] 複数トークン比較コマンド `/compare token1 token2`
- [ ] ウォッチリスト機能(特定トークンのスコア変化を通知)
- [ ] アラート機能(Smart Moneyが大量購入したときに通知)
- [ ] 日次/週次レポート機能
- [ ] ユーザー設定の永続化(閾値カスタマイズなど)
- [ ] EVMチェーン対応(Ethereum/Base/BNB等)
- [ ] Telegram版の横展開(clients層はそのまま使える)

---

## Wallet DB (digest 連動) — 残課題

`/digest` 実行時に `tgm/pnl-leaderboard` の上位 wallet を `data/wallets.db`
(SQLite, テーブル `wallet_appearances`) に蓄積している。 当面はテスト運用で
蓄積のみ実施し、 以下は別途検討する:

- [ ] **蓄積上限がない**
  - `wallet_appearances` は毎回 `INSERT` のみで cutoff / 重複制限なし
  - 4h loop ×6/日 で月 ~10,000 件、 年 ~130,000 件のペースで増加
  - SQLite サイズ・パフォーマンス的には当面問題ないが、 古いレコードの cutoff
    (例: 90 日以前を削除) や `UNIQUE(wallet, token, DATE(detected_at))`
    による日次デデュープを検討する
- [ ] **ウォレットのスコアリングロジック未実装**
  - 現状は raw record を蓄積するだけで、 「高勝率 wallet」 を確定させる
    判定式が無い
  - 候補: `unique_tokens >= N` & `sum_pnl_usd >= X` で確定、 直近 30 日で
    複数 token 出現で重み付け、 PnL/trades 比 (1 取引あたりの平均利益) も加味、
    など
  - スコアリングを実装すれば `/wallet-rank` は「高勝率扱いの wallet のみ」
    に絞れるし、 後続の Helius webhook で監視対象を選定する元データになる
