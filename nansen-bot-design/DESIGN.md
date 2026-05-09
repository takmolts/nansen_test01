# スコアリング設計

8カテゴリの計算式・閾値・重みを定義する。

## 総合スコア

```python
total_score = (
    sm_score          * 0.20 +
    momentum_score    * 0.12 +
    liquidity_score   * 0.12 +
    distribution_score* 0.08 +
    risk_score        * 0.12 +
    deployer_score    * 0.08 +
    bundle_score      * 0.13 +
    narrative_score   * 0.15
)
```

## 判定バンド

| スコア | 判定 | 絵文字 | 解説 |
|---|---|---|---|
| 80〜100 | STRONG BUY | 🟢🟢 | 全指標で高評価。ミームとしては稀 |
| 60〜79 | BUY | 🟢 | 攻めて良い水準。一部弱点はあり許容範囲 |
| 40〜59 | CAUTION | 🟡 | リスク要素あり。少額 or 見送り |
| 0〜39 | AVOID | 🔴 | 複数の警告サイン。触らない方が安全 |

## 特別フラグ(バンドルに上乗せ表示)

| フラグ | 条件 | 意味 |
|---|---|---|
| 🚨 Rug Risk | Distribution < 30 かつ Liquidity < $100K | ラグプル警戒 |
| 💎 Hidden Gem候補 | SM Score >= 70 かつ Momentum < 50 | プロは買ってるが価格未反応 |
| 🔥 Overheated | Momentum >= 85 | 過熱・FOMO圏 |
| 🎖️ α-Token | Narrative内のα判定成立 | 元祖・流行の先頭 |
| ⚠️ β-Token | Narrative内のβ判定成立 | 二番煎じ |
| 🚨 Bundle Risk | Bundle Safety < 40 | バンドル疑惑 |

---

## カテゴリ1: Smart Money Score (重み20%)

**使用エンドポイント**: `tgm/flow-intelligence`, `tgm/holders`, `smart-money/holdings`

### 計算式

```python
# SM保有ウォレット数スコア(満点40)
sm_holder_score = min(sm_holder_count / 20, 1.0) * 40
# → 20ウォレット以上保有で満点

# SMネットフロースコア(±35)
if netflow_usd > 0:
    sm_flow_score = min(netflow_usd / 500000, 1.0) * 35
elif netflow_usd < 0:
    sm_flow_score = max(-35, netflow_usd / 500000 * 35)  # マイナスは減点
else:
    sm_flow_score = 0
# → +$500K以上の流入で満点

# SM新規買いスコア(満点25)
sm_new_buyer_score = min(sm_unique_buyers_24h / 10, 1.0) * 25
# → 24時間で10人以上の新規SM買いで満点

sm_score = max(0, min(100, sm_holder_score + sm_flow_score + sm_new_buyer_score))
```

### Embed解説(例)

```
🧠 Smart Money Score: 78/100

✅ 保有SMウォレット: 15個 (38/40pt)
✅ ネットフロー: +$320K (22/35pt)
✅ 新規買い(24h): 7人 (18/25pt)

プロのウォレットが積極的に保有・買い増ししています。
```

---

## カテゴリ2: Momentum Score (重み12%)

**使用エンドポイント**: `tgm/token-information`, `tgm/price-ohlcv`(またはDexScreener), `tgm/who-bought-sold`

### 計算式

```python
# Buy/Sell比率スコア(満点30)
ratio = buy_volume_usd / (sell_volume_usd + 1)
if ratio >= 2.0:     bs_score = 30
elif ratio >= 1.5:   bs_score = 25
elif ratio >= 1.2:   bs_score = 20
elif ratio >= 1.0:   bs_score = 10
else:                bs_score = 0

# ユニーク買い手比率スコア(満点25)
buyer_ratio = unique_buyers / (unique_sellers + 1)
buyer_score = min(buyer_ratio / 2.0, 1.0) * 25

# 価格モメンタムスコア(満点25)
if price_change_24h > 50:   price_score = 25   # 過熱気味でも一旦満点
elif price_change_24h > 20: price_score = 25
elif price_change_24h > 0:  price_score = 15
else:                        price_score = 0

# 出来高伸び率スコア(満点20)
volume_growth = volume_24h / (volume_prev_24h + 1)
vol_score = min(volume_growth / 3.0, 1.0) * 20
# → 3倍成長で満点

momentum_score = bs_score + buyer_score + price_score + vol_score
```

---

## カテゴリ3: Liquidity Score (重み12%)

**使用エンドポイント**: DexScreener `/latest/dex/tokens/{addr}`(優先) または Nansen `tgm/token-information`

### 計算式

```python
# 流動性絶対額スコア(満点50)
if liquidity_usd >= 1_000_000:   liq_score = 50
elif liquidity_usd >= 500_000:   liq_score = 40
elif liquidity_usd >= 100_000:   liq_score = 25
elif liquidity_usd >= 50_000:    liq_score = 10
else:                            liq_score = 0

# 出来高/流動性比スコア(満点30)
vol_liq_ratio = volume_24h / (liquidity_usd + 1)
if 0.5 <= vol_liq_ratio <= 5.0:  vlr_score = 30  # 健全
elif vol_liq_ratio <= 10.0:       vlr_score = 20  # 過熱
elif vol_liq_ratio > 10.0:        vlr_score = 5   # 異常
else:                             vlr_score = 10  # 閑散

# 時価総額適正スコア(満点20)
if market_cap_usd > 10_000_000:   mc_score = 20
elif market_cap_usd >= 1_000_000: mc_score = 15
elif market_cap_usd >= 100_000:   mc_score = 10
else:                              mc_score = 5

liquidity_score = liq_score + vlr_score + mc_score
```

---

## カテゴリ4: Distribution Score (重み8%)

**使用エンドポイント**: `tgm/holders`, `tgm/token-information`

### 計算式

```python
# ホルダー総数スコア(満点35)
if total_holders >= 5000:    th_score = 35
elif total_holders >= 1000:  th_score = 25
elif total_holders >= 500:   th_score = 15
elif total_holders >= 100:   th_score = 8
else:                         th_score = 2

# トップ10集中度スコア(満点40、逆転)
# deployer・既知CEX/bridgeは除外してカウント
if top10_pct <= 20:           t10_score = 40  # 非常に分散
elif top10_pct <= 35:         t10_score = 30
elif top10_pct <= 50:         t10_score = 20
elif top10_pct <= 70:         t10_score = 10
else:                          t10_score = 0   # 集中リスク

# 新規ホルダー増加スコア(満点25)
new_holders_ratio = new_holders_24h / max(total_holders, 1)
nh_score = min(new_holders_ratio / 0.05, 1.0) * 25
# → 24時間で5%増加で満点

distribution_score = th_score + t10_score + nh_score
```

---

## カテゴリ5: Risk Score (重み12%)

**スコアの意味: 高いほど安全**

**使用エンドポイント**: `tgm/nansen-indicators`, `tgm/flow-intelligence`, `tgm/token-information`

### 計算式

```python
# BTC連動リスクスコア(満点30)
btc_signal = token_info.btc_drop_reflexivity.signal  # low/medium/high
if btc_signal == "low":    btc_score = 30
elif btc_signal == "medium": btc_score = 20
else:                       btc_score = 5

# 取引所流入リスクスコア(満点25、売り圧検出)
cex_inflow_ratio = cex_inflow_24h / max(total_volume_24h, 1)
if cex_inflow_ratio <= 0.05:  cex_score = 25  # 売り圧なし
elif cex_inflow_ratio <= 0.15: cex_score = 15
elif cex_inflow_ratio <= 0.30: cex_score = 5
else:                          cex_score = 0   # 大量売り圧

# トークン年齢スコア(満点20、古いほど安全)
age_days = (今日 - token_deployment_date).days
if age_days >= 180:    age_score = 20
elif age_days >= 90:   age_score = 15
elif age_days >= 30:   age_score = 10
elif age_days >= 7:    age_score = 5
else:                   age_score = 0   # 極新規=高リスク

# Nansen独自リスク指標(満点25)
# 実データのレンジを見てから正規化式を確定する
ni_score = normalize_nansen_indicators(nansen_indicators)  # 0〜25

risk_score = btc_score + cex_score + age_score + ni_score
```

> **TODO**: `tgm/nansen-indicators`の実レスポンスを1回叩いて、どの値をどう正規化するか決める。

---

## カテゴリ6: Deployer Trust Score (重み8%)

**使用エンドポイント**: `profiler/address/labels`, `profiler/address/transactions`, `profiler/address/pnl-summary`, `profiler/address/related-wallets`

### 計算式

```python
# 警告ラベル検出(満点40、最重要)
labels = profiler.labels(deployer_address)
label_names = [l.label.lower() for l in labels]
danger_words = ["scam", "hacker", "mixer", "sanctioned"]
warn_words = ["suspicious", "phishing"]

if any(w in " ".join(label_names) for w in danger_words):
    warn_score = 0   # 即0点(ここで早期リターンも検討)
elif any(w in " ".join(label_names) for w in warn_words):
    warn_score = 10
else:
    warn_score = 40

# Deployerアカウント年齢スコア(満点25)
first_tx = profiler.transactions(deployer, order="asc", limit=1)[0]
days_active = (今日 - first_tx.timestamp).days
if days_active >= 365:   age_score = 25
elif days_active >= 180: age_score = 18
elif days_active >= 90:  age_score = 10
elif days_active >= 30:  age_score = 5
else:                     age_score = 0   # 捨てアカ疑惑

# Deployer実績スコア(満点20)
pnl = profiler.pnl_summary(deployer)
if pnl.win_rate >= 0.5 and pnl.total_trades >= 20:
    pnl_score = 20   # 実際にトレードしている
elif pnl.total_trades < 5:
    pnl_score = 0    # トレード履歴なし=deploy専用アカ疑惑
else:
    pnl_score = 10

# 関連ウォレットクリーン度スコア(満点15)
related = profiler.related_wallets(deployer)
related_labels = [profiler.labels(r.address) for r in related]
warn_ratio = count_warnings(related_labels) / max(len(related), 1)
if warn_ratio == 0:         rel_score = 15
elif warn_ratio < 0.05:     rel_score = 10
elif warn_ratio < 0.20:     rel_score = 5
else:                        rel_score = 0

deployer_score = warn_score + age_score + pnl_score + rel_score
```

---

## カテゴリ7: Bundle Safety Score (重み13%)

**スコアの意味: 高いほど安全**

**使用エンドポイント**: `tgm/holders`, `profiler/address/related-wallets`, `profiler/address/labels`

### 計算式

```python
# 3%超ホルダー数スコア(満点30、逆転)
# deployerと既知CEX/bridgeは除外
whales = [h for h in holders if h.pct >= 3.0 and not is_known_entity(h.address)]
whale_count = len(whales)
if whale_count == 0:     wc_score = 30
elif whale_count <= 2:   wc_score = 25
elif whale_count <= 5:   wc_score = 15
elif whale_count <= 10:  wc_score = 5
else:                     wc_score = 0

# バンドル検出スコア(満点40)
# 3%超ホルダー同士のrelated-walletsを取得し、共通のFirst Funderを探す
funders_map = {}  # funder_address -> [holder_addresses]
for whale in whales:
    rel = profiler.related_wallets(whale.address)
    for r in rel:
        if r.type == "First Funder":
            funders_map.setdefault(r.address, []).append(whale.address)

max_cluster = max([len(v) for v in funders_map.values()] or [1])
if max_cluster <= 1:   bd_score = 40   # バンドルなし
elif max_cluster == 2: bd_score = 25   # 軽微
elif max_cluster == 3: bd_score = 10   # 疑わしい
else:                   bd_score = 0   # 明確なバンドル

# 警告ラベル混入度スコア(満点20)
whale_labels = [profiler.labels(w.address) for w in whales]
warn_pct_sum = sum(
    w.pct for w, labels in zip(whales, whale_labels)
    if has_warning_label(labels)
)
if warn_pct_sum == 0:       wl_score = 20
elif warn_pct_sum <= 5:     wl_score = 15
elif warn_pct_sum <= 15:    wl_score = 8
else:                        wl_score = 0

# Insider Ratio(deployer系保有割合)スコア(満点10)
deployer_cluster = {deployer_address} | {r.address for r in deployer_related}
insider_pct = sum(h.pct for h in holders if h.address in deployer_cluster)
if insider_pct <= 5:    ir_score = 10
elif insider_pct <= 15: ir_score = 5
else:                    ir_score = 0

bundle_score = wc_score + bd_score + wl_score + ir_score
```

---

## カテゴリ8: Narrative Score (重み15%)

**使用API**: DexScreener + CoinGecko(Nansen不使用)

### 計算式

```python
# 類似トークン数スコア(満点40、α/β判定用)
similar = dexscreener.search(symbol_base)  # シンボル部分一致
similar_week = [t for t in similar if t.deploy_days_ago <= 7]
similar_day = [t for t in similar if t.deploy_days_ago <= 1]

# 対象トークンが最古か判定
target_deploy = target_token.deploy_date
is_oldest = all(t.deploy_date >= target_deploy for t in similar_week)
is_early_half = (
    len([t for t in similar_week if t.deploy_date < target_deploy])
    < len(similar_week) / 2
)

if len(similar_week) == 0:
    # 誰も追随していない=話題になっていない
    sim_score = 0
    status = "isolated"
elif 1 <= len(similar_week) <= 3 and is_oldest:
    sim_score = 40
    status = "α"
elif 1 <= len(similar_week) <= 3:
    sim_score = 20
    status = "β"
elif 4 <= len(similar_week) <= 10:
    sim_score = 30
    status = "hot"
else:  # 11個以上
    sim_score = 15
    status = "saturated"

# α/β詳細判定スコア(満点25)
if is_oldest:               elder_score = 25   # α
elif is_early_half:         elder_score = 15   # early
else:                        elder_score = 5    # β

# Trending/Boost検出スコア(満点25)
is_ds_boosted = dexscreener.is_token_boosted(target_address)
is_cg_trending = coingecko.is_trending(target_address)
if is_ds_boosted and is_cg_trending:  trend_score = 25
elif is_ds_boosted or is_cg_trending: trend_score = 20
else:                                  trend_score = 0

# ソーシャル充実度スコア(満点10)
socials = dexscreener.get_socials(target_address)
social_count = sum(1 for s in [socials.twitter, socials.telegram, socials.website] if s)
social_score = {3: 10, 2: 6, 1: 3, 0: 0}[social_count]

narrative_score = sim_score + elder_score + trend_score + social_score
```

### αβステータスのフラグ

`status` は総合Embedに「🎖️ α-Token」「⚠️ β-Token」として表示。

---

## 閾値チューニングについて

上記の数値は設計の出発点であり、実装後のテストで調整が必須:

1. 既知の有名ミームコイン数種類で実際にスコアを計算
2. 「良い」とされるトークンで60点以下が連発するなら閾値を緩める
3. 「怪しい」とされるトークンで40点以上出るなら閾値を厳しくする
4. 特に`tgm/nansen-indicators`は実データのレンジ次第で正規化式を変える必要がある

この作業は実装後の第2フェーズとして実施。
