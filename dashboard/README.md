# SM 速報 BUY ダッシュボード

bot に蓄積された `sm_signal_events` を期間別 JSON にエクスポートし、
別の **public な git リポジトリ** に定期 push して GitHub Pages 上で
公開するための一式。 cron は使わず、 bot プロセス内の loop が
一定間隔で `git add / commit / push` を実行する。

```
[bot プロセス]
  └─ DashboardPublisherCog (5 分間隔)
       ├─ export_signals.export_all() で SQLite → JSON
       ├─ <DASHBOARD_REPO_PATH>/data/*.json を更新
       └─ data/ に差分があれば git push (公開リポへ)
                                   │
                                   ▼
                        GitHub Pages 自動再ビルド
                                   │
                                   ▼
                  https://<user>.github.io/<dashboard-repo>/
```

## 構成ファイル

| パス | 役割 |
|---|---|
| `dashboard/exporter/export_signals.py` | bot SQLite を読み window 別 JSON を出力 (CLI / API 両対応) |
| `dashboard/site/{index.html,app.js,style.css}` | 公開リポの中身として置く静的サイトテンプレ |
| `bot/cogs/dashboard_publisher.py` | bot 起動時に loop で定期 push する cog |

## セットアップ (一回だけ)

### 1. 公開リポを作る

GitHub で `nansen-sm-dashboard` などの **public** リポを新規作成 (空で OK)。

### 2. リポを bot ホストに clone

```sh
cd ~
git clone git@github.com:<user>/nansen-sm-dashboard.git dashboard-repo
cd dashboard-repo
```

SSH 鍵か PAT で **bot 実行ユーザのまま push が通る** 状態にしておく。

### 3. site テンプレを公開リポに配置

bot リポから一度コピーする (以後は触る必要なし)。

```sh
# bot リポを ~/develop_bb/nansen_test01 とした場合
cp ~/develop_bb/nansen_test01/dashboard/site/* ~/dashboard-repo/
cd ~/dashboard-repo
git add index.html app.js style.css
git commit -m "init: dashboard site"
git push
```

### 4. GitHub Pages 有効化

リポの Settings → Pages →
- Source: Deploy from a branch
- Branch: `main` / root

数分で `https://<user>.github.io/nansen-sm-dashboard/` が開く。
この時点では `data/` がまだ無いのでリストは空。

### 5. bot 側 .env に設定

```ini
DASHBOARD_PUBLISH_ENABLED=true
DASHBOARD_REPO_PATH=/home/<user>/dashboard-repo
DASHBOARD_PUBLISH_INTERVAL_MIN=5
DASHBOARD_GIT_BRANCH=main
# Discord 速報 BUY に Link ボタンを足したい場合のみ (空でも可)
DASHBOARD_PUBLIC_URL=https://<user>.github.io/<dashboard-repo>/
```

bot を再起動すると、 起動直後に 1 回 export → push が走る。
うまくいけば `data/signals_*.json` がリポに commit される。

`DASHBOARD_PUBLIC_URL` を設定した場合、 速報 BUY の Discord 通知に
「📊 dashboard」 リンクボタンが追加され、 押すと `<URL>?mint=<CA>` の形で
ブラウザが開き、 ダッシュボードがその銘柄を自動選択した状態で立ち上がる
(24h → 7d → 6h → 1h の順で銘柄を検索)。

## 手動エクスポート (デバッグ用)

bot を起動せずに動作確認するには:

```sh
cd ~/develop_bb/nansen_test01
source .venv/bin/activate
python -m dashboard.exporter.export_signals \
    --out ~/dashboard-repo/data \
    --db data/wallets.db
```

その後 `python -m http.server -d ~/dashboard-repo` でローカル確認可能。

## 出力 JSON の形 (signals_24h.json)

```json
{
  "window": "24h",
  "window_hours": 24,
  "since_ts": 1747000000,
  "now_ts": 1747086400,
  "generated_at": "2026-05-11T02:30:00Z",
  "total_events_in_window": 1234,
  "min_distinct_buyers": 2,
  "tokens": [
    {
      "mint": "...",
      "symbol": "FOO",
      "name": "Foo Token",
      "image_url": "...",
      "market_cap": 1234567.0,
      "price_usd": 0.001234,
      "distinct_buyers": 7,
      "distinct_sellers": 1,
      "buy_trades": 9,
      "sum_buy_sol": 12.4,
      "sum_buy_stable": 850.0,
      "sum_sell_sol": 0.0,
      "sum_sell_stable": 0.0,
      "n_large_buys": 1,
      "max_buy_quote": 5.2,
      "first_seen_ts": 1747000000,
      "last_seen_ts": 1747005000,
      "buyers": [
        {"wallet": "abc...", "label": "Fund", "trades": 1,
         "sum_sol": 1.2, "sum_stable": 0.0, "last_ts": 1747005000}
      ]
    }
  ]
}
```

`meta.json` には全 window の件数 + 生成時刻のサマリが入る。

## チューニング: 「速報条件」 と 「ダッシュボード集計」 の関係

Discord の **速報通知** (sm_summary realtime) と、 ダッシュボードの **集計 JSON** は
完全に独立している。 速報通知の閾値を満たさなかった BUY も、 すべて
`sm_signal_events` テーブルに保存されており、 dashboard exporter は
そのテーブルから自分の閾値で抽出する。

```
[Helius webhook] ─► sm_signal_events  (BUY/SELL/suppressed 全部)
                          │
       ┌──────────────────┼──────────────────┐
       ▼                                     ▼
  Discord 速報 / 集計 (sm_summary)     dashboard exporter
   閾値: 速報 3 wallets / 集計 2        閾値: DASHBOARD_MIN_DISTINCT_BUYERS
                                            DASHBOARD_TOP_N
                                            DASHBOARD_BUYERS_PER_TOKEN
```

UI の `Min buyers` は **JSON にある銘柄を絞り込む** だけなので、
JSON に来ていない銘柄は UI で 1 にしても出ない。 「全部見たい」 場合は
exporter 側の閾値を下げる必要がある。

### よく使う設定例

**(a) デフォルト**: 速報的な「2 人以上が同 mint を BUY した銘柄」 のみ。 軽い。

```ini
DASHBOARD_MIN_DISTINCT_BUYERS=2
DASHBOARD_TOP_N=50
DASHBOARD_BUYERS_PER_TOKEN=30
```

**(b) ロングテール込みで全部見たい**: 1 人だけが買った銘柄も全部出す。
JSON が太る (24h で数百 KB) し git の履歴も嵩むが、 観測網としては最強。

```ini
DASHBOARD_MIN_DISTINCT_BUYERS=1
DASHBOARD_TOP_N=200
DASHBOARD_BUYERS_PER_TOKEN=30
```

**(c) 重要案件のみ厳選**: 群衆性の高い銘柄だけ。 通知に近い体験。

```ini
DASHBOARD_MIN_DISTINCT_BUYERS=3
DASHBOARD_TOP_N=30
DASHBOARD_BUYERS_PER_TOKEN=50
```

### 手動エクスポートで一度試す

`.env` を変える前に、 同じパラメータを CLI で渡して JSON 内容を確認できる。

```sh
# (b) 全部入りで一度出してみる
python -m dashboard.exporter.export_signals \
    --out /tmp/dash_check/data \
    --db data/wallets.db \
    --min-buyers 1 --top-n 200

# サイズ確認
du -h /tmp/dash_check/data/*.json

# ローカル確認
cp dashboard/site/* /tmp/dash_check/
python -m http.server -d /tmp/dash_check 8765
```

### 補足: 集計に含まれる/含まれないもの

`bot.wallet_db.aggregate_sm_signals` の挙動:

| 種類 | 集計に含まれるか |
|---|---|
| `direction='BUY'` の event | ○ |
| `direction='SELL'` の event | ○ (sell 列としてカウント、 `sum_sell_*` に反映) |
| `is_suppressed=1` (連発抑制された event) | ○ (フィルタしていない) |
| BUY 0 件で SELL のみの mint | × (`HAVING distinct_buyers >= 1` で除外) |
| `top_n` で打ち切られた tail | × |

「suppressed も含めるか」 を変えたい場合は exporter 側で別途フィルタ追加が必要
(現状 `aggregate_sm_signals` には引数がない)。

## トラブルシュート

| 症状 | 確認点 |
|---|---|
| 起動時に "DASHBOARD_REPO_PATH 未設定" のログ | `.env` の `DASHBOARD_REPO_PATH=` が空、 または bot 再起動忘れ |
| "repo path は git リポではありません" | clone 済みかどうか / `.git/` が存在するか |
| `git push 失敗 rc=128` | SSH 鍵 or PAT が bot 実行ユーザに無い。 手動で `git push` できるか確認 |
| Pages で 404 | Pages Source 未設定、 または初回 push がまだ届いていない |
| 銘柄リストが出ない | `data/signals_24h.json` が public で取得できるか直接 fetch して確認 (CORS は同一 origin なので問題なし) |
| チャートが灰色 / 表示されない | DexScreener / Birdeye 側が iframe ブロックしている。 右上の "↗ 新規タブ" で開く |
