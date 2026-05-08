# Nansen Discord Bot

Solana ミームコインを **Nansen** + **Helius** + **DexScreener** で多角的に追跡する Discord bot。

## 機能概要

大きく 4 系統:

1. **`/analyze` (オンデマンド分析)**
   ミーム CA を渡すと、 銘柄詳細 / Smart Money / Holders / Bundle 検出 / (オプション) ローカル LLM 総括 を Embed で返す。
2. **`/digest` (時系列ダイジェスト)**
   Nansen Token Screener から `momentum / sm / hot` の 3 観点で TOP token を Embed 投稿。 4h / 24h の自動 loop も持つ (デフォは無効)。
3. **Smart Money 監視ロスター → Helius webhook**
   Nansen `/smart-money/dex-trades` を 1 日 1 回叩き、 Fund + 180D Smart Trader 等を `sm_roster` テーブルに蓄積 → Helius webhook に登録 → 該当 wallet の取引を **リアルタイムでスレッド通知**。
4. **毎時集計通知**
   蓄積された SM SWAP イベントを毎正時に集計し、 「群衆 ≥ 2 wallet が買った銘柄」 をスコア順に Embed リストで投稿 (Nansen クレジット消費なし)。

## アーキテクチャ (SM 監視パイプライン)

```
[Nansen /smart-money/dex-trades]
  └─ 日次取得 (sm_roster cog)
        └─ sm_roster テーブル (上限 500 で LRU prune)
              └─ /sm-helius-sync で Helius webhook に PUT
                     │
[Helius] ── enhanced webhook POST ──→ aiohttp.web (sm_signal cog)
                                          ├─ classify_swap (sm_signal_classifier)
                                          ├─ 連発抑制 / 群衆検出 / 大口判定
                                          ├─ sm_signal_events テーブルに蓄積
                                          └─ SM_SIGNAL_THREAD_ID に Embed 投稿

毎正時 0 分:
[sm_summary cog] ── sm_signal_events を集計 → SM_SUMMARY_CHANNEL_ID へ TOP_N
```

## 前提

- Python 3.10 以上 (本番は 3.12 で動作確認)
- Discord Bot アプリ (Bot Token 取得済み)
- Nansen API Key (`nsn_...`)
- (任意) Helius API Key — webhook 利用時のみ
- (任意) Helius が POST できる公開 URL — webhook 利用時のみ (例: `http://your.host:50150/helius-webhook`)

## セットアップ

`.env` を用意すれば、 起動スクリプト [run.sh](run.sh) が `.venv` 作成 + 依存インストールを自動で行う。

```bash
cd /home/tasato/develop_bb/nansen_test01

cp .env.example .env
# .env を開いて DISCORD_BOT_TOKEN / NANSEN_API_KEY / 必要な ID を埋める

./run.sh
```

systemd で常駐させる場合 (任意):

```bash
# 例: ~/.config/systemd/user/nansen-bot.service
systemctl --user start nansen-bot
journalctl --user -u nansen-bot -f
```

## Slash コマンド一覧

| コマンド | 用途 |
|---|---|
| `/analyze ca:<addr>` | CA を渡すと銘柄詳細・SM 動向・ホルダー・バンドル検出を Embed で返す |
| `/digest [timeframe]` | 勢い / SM / 急流入の 3 観点で TOP token をダイジェスト投稿 |
| `/wallet-rank` | digest 連動で蓄積した高勝率 wallet をランキング表示 |
| `/sm-roster-fetch` | 今すぐ Nansen から SM wallet を取得して `sm_roster` に upsert |
| `/sm-roster-list` | 蓄積済 roster を一覧表示 (sort 切替・Helius 未登録だけ表示など) |
| `/sm-helius-sync` | `sm_roster` 全 wallet を Helius webhook に同期 (新規 POST or 更新 PUT) |
| `/sm-summary` | 過去 N 分の SM SWAP を即集計して Embed リスト投稿 |

## SM 監視パイプラインの始め方

1. `.env` の `HELIUS_WEBHOOK_URL` (公開 URL) と `SM_SIGNAL_THREAD_ID` (通知先スレッド) を設定
2. ファイアウォール (UFW など) で `WEBHOOK_BIND_PORT` (既定 50150) を開ける
3. bot 起動後、 Discord で `/sm-roster-fetch` 実行 (Nansen 1 call ≒ 5 credit)
4. `/sm-helius-sync` で Helius に webhook 登録
5. 実 SM の取引が発生すると `SM_SIGNAL_THREAD_ID` のスレッドにリアルタイム Embed
6. 毎正時 0 分に `SM_SUMMARY_CHANNEL_ID` (空なら `DIGEST_CHANNEL_ID`) に集計通知

## .env の主な項目

### Discord / 共通

| 変数 | 必須 | 内容 |
|---|---|---|
| `DISCORD_BOT_TOKEN` | ✓ | Discord Bot Token |
| `ALLOWED_CHANNEL_IDS` | - | カンマ区切り。 空で全チャネル許可 |
| `DEV_GUILD_ID` | - | 設定すると即時 Slash 同期 |
| `RESPONSE_MODE` | - | `inline` / `thread` |
| `LOG_LEVEL` | - | `INFO` / `DEBUG` |

### Nansen / digest

| 変数 | 内容 |
|---|---|
| `NANSEN_API_KEY` | Nansen API Key (必須) |
| `NANSEN_BASE_URL` | 既定 `https://api.nansen.ai` |
| `DIGEST_CHANNEL_ID` | digest 自動投稿先 |
| `DIGEST_AUTO_4H_ENABLED` | 4h loop ON/OFF (既定 false) |
| `DIGEST_AUTO_DAILY_ENABLED` | 24h loop ON/OFF (既定 false) |
| `DIGEST_ARCHIVE_THREAD_ID` | アーカイブスレッド ID |

### SM roster (Nansen 取得条件、 ソース変更不要で env 上書き可)

| 変数 | 既定 | 内容 |
|---|---|---|
| `SM_ROSTER_AUTO_ENABLED` | true | 日次自動取得 ON/OFF |
| `SM_ROSTER_FETCH_TIME_JST` | `00:30` | 取得時刻 (HH:MM) |
| `SM_ROSTER_NOTIFY_CHANNEL_ID` | - | サマリ通知先 (空で通知なし) |
| `SM_ROSTER_MAX_WALLETS` | 500 | 上限。 超過は last_seen 古い順に prune |
| `SM_ROSTER_CHAIN` | `solana` | チェーン |
| `SM_ROSTER_INCLUDE_LABELS` | `Fund,180D Smart Trader` | カンマ区切り |
| `SM_ROSTER_EXCLUDE_LABELS` | `30D Smart Trader` | カンマ区切り |
| `SM_ROSTER_TOKEN_AGE_MIN` | 1 | token age 下限 (日) |
| `SM_ROSTER_TOKEN_AGE_MAX` | 30 | token age 上限 (日) |
| `SM_ROSTER_TRADE_VALUE_USD_MIN` | 200 | trade_value_usd 下限 |
| `SM_ROSTER_PER_PAGE` | 500 | per_page (1〜1000) |

### Helius (DAS RPC + Webhook)

| 変数 | 内容 |
|---|---|
| `HELIUS_API_KEY` | Helius API Key |
| `ENABLE_HELIUS` | DAS 機能 ON/OFF (既定 true) |
| `HELIUS_WEBHOOK_URL` | Helius が POST する公開 URL |
| `HELIUS_WEBHOOK_TYPE` | `enhanced` / `raw` (既定 enhanced) |
| `HELIUS_WEBHOOK_TRANSACTION_TYPES` | カンマ区切り (既定 `SWAP`) |
| `HELIUS_WEBHOOK_AUTH_HEADER` | 受信側に渡す Authorization 値 |
| `HELIUS_WEBHOOK_AUTO_SYNC` | 日次 fetch 後に自動 sync (既定 false) |

### Webhook 受信サーバ + SM Signal

| 変数 | 既定 | 内容 |
|---|---|---|
| `WEBHOOK_BIND_HOST` | `0.0.0.0` | bind ホスト |
| `WEBHOOK_BIND_PORT` | `50150` | bind ポート |
| `WEBHOOK_PATH` | `/helius-webhook` | パス |
| `SM_SIGNAL_THREAD_ID` | - | リアルタイム通知先スレッド ID |
| `SM_SIGNAL_INCLUDE_SELL` | true | SELL も通知するか |
| `SM_SIGNAL_LARGE_SOL_MIN` | 2.0 | 🐋 大口 ラベル閾値 (SOL) |
| `SM_SIGNAL_LARGE_STABLE_MIN` | 200 | 🐋 大口 ラベル閾値 (USD) |
| `SM_SIGNAL_DEDUP_WINDOW_MIN` | 30 | 連発抑制ウィンドウ |
| `SM_SIGNAL_GROUP_WINDOW_MIN` | 30 | 群衆判定ウィンドウ |

### SM Summary (毎時集計通知)

| 変数 | 既定 | 内容 |
|---|---|---|
| `SM_SUMMARY_ENABLED` | true | 毎時 loop ON/OFF |
| `SM_SUMMARY_WINDOW_MIN` | 60 | 集計対象期間 |
| `SM_SUMMARY_MIN_WALLETS` | 2 | 通知 gate (distinct buyers ≥ N) |
| `SM_SUMMARY_TOP_N` | 10 | Embed 件数 (Discord 上限 10) |
| `SM_SUMMARY_CHANNEL_ID` | - | 投稿先。 空で `DIGEST_CHANNEL_ID` フォールバック |

### その他 (オプショナル)

| 変数 | 内容 |
|---|---|
| `COINGECKO_API_KEY` | Trending 判定用 (空で機能 skip) |
| `ENABLE_COINGECKO` | true/false |
| `SOLANA_RPC_URL` | Deployer Trust 用 |
| `LLM_HOST` / `LLM_MODEL` | rkllama / Ollama 互換 LLM (空で /analyze の総括 skip) |

## 実行とログ

```bash
# 起動
./run.sh
# または systemd 経由
systemctl --user start nansen-bot

# ライブログ
journalctl --user -u nansen-bot -f

# Helius 受信状況だけ追う
journalctl --user -u nansen-bot -f | grep -iE "sm_signal|webhook"

# 健康チェック (外部到達確認)
curl -s http://localhost:50150/health
# {"ok":true,"sm_wallets":28}
```

## データストア

`data/wallets.db` (SQLite) に以下を保存:

- `wallet_appearances` (digest 連動の wallet 出現履歴)
- `sm_roster` (SM 監視候補 wallet。 last_label / observation count / Helius 登録フラグ)
- `sm_signal_events` (Helius 受信した全 SWAP。 集計通知の母数)

## トラブルシュート

| 症状 | 確認ポイント |
|---|---|
| Bot 起動失敗 (cog ロード時) | `journalctl ...` で Python traceback 確認。 `bot.loop` 系エラーなら discord.py のバージョンと `cog_load` の使用 |
| Slash コマンドが Discord に出ない | OAuth2 招待 URL に `applications.commands` スコープがあるか確認 |
| Nansen 4xx | `LOG_LEVEL=DEBUG` で payload を確認、 `bot/nansen_client.py` のリクエスト body を調整 |
| Helius webhook が届かない | (1) `curl http://localhost:50150/health` (2) UFW で `WEBHOOK_BIND_PORT` 許可済か (3) NAT/DDNS で公開 URL 到達するか |
| signal が来ているが Discord 投稿されない | `SM_SIGNAL_THREAD_ID` が正しい thread id か / bot に送信権限あるか |
| 集計通知が空 | `SM_SUMMARY_MIN_WALLETS=2` の gate に達していない可能性。 `/sm-summary` で即時実行して確認 |

## 検証スクリプト

`scripts/` 配下に Nansen API の挙動確認用 probe を置いてある:

```bash
# Smart Money DEX trades の 3 構成比較
.venv/bin/python -m scripts.probe_smart_money

# 確定済 filter 構成で roster 取得をシミュレート (DB には書かない)
.venv/bin/python -m scripts.probe_sm_roster
.venv/bin/python -m scripts.probe_sm_roster --raw 2     # raw JSON 確認
.venv/bin/python -m scripts.probe_sm_roster --csv out.csv
```

## 設計ドキュメント

詳細な設計・将来計画は [nansen-bot-design/](nansen-bot-design/) 配下を参照。
