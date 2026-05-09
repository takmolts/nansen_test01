# Discord Embed設計

## 全体方針

- 1コマンドにつき複数のEmbedをサブスレッド内に投稿する
- 最初は**総合Embed**、ボタンでカテゴリ別詳細・バンドル詳細・類似Tokenを展開
- プログレスバーはブロック文字(`█▓▒░`など)で擬似的に表現
- 色は判定バンドに応じて変更(緑/黄/赤)

## 色コード(Embed左側のカラーバー)

| 判定 | 16進 | 色名 |
|---|---|---|
| STRONG BUY (80+) | `0x00FF00` | 明るい緑 |
| BUY (60-79) | `0x7FFF00` | 黄緑 |
| CAUTION (40-59) | `0xFFD700` | 金色(黄) |
| AVOID (0-39) | `0xFF4444` | 赤 |
| Bundle Risk検出時 | `0xFF8C00` | 強調オレンジ(上書き) |

---

## 総合Embed(Main Embed)

サブスレッド作成直後に最初に投稿されるEmbed。

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 $PEPE2 Analysis Report
Chain: Solana | Age: 5d | MCap: $4.2M

Total Score: 68/100  🟢 BUY
🎖️ α-Token (元祖) | 💎 Hidden Gem候補 | 🚨 軽度Bundle

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧠 Smart Money       78/100  ████████░░
📈 Momentum          55/100  █████▌░░░░
💧 Liquidity         72/100  ███████▎░░
👥 Distribution      58/100  █████▊░░░░
🛡️ Risk              62/100  ██████▏░░░
🔍 Deployer Trust    78/100  ████████░░
📦 Bundle Safety     55/100  █████▌░░░░  ⚠️
🌊 Narrative         72/100  ███████▎░░  🎖️

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Insight:
"○○系ミーム" の元祖で、現在8個の追随トークン発生中。
Smart Moneyの買いは強く、流行初期の優位性があります。
ただしローンチ時のバンドル配布痕跡(3人クラスタ)あり。
仕込みなら少額推奨、α優位性があるうちがチャンス。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Thread TTL: 60分後に自動削除
[📊 詳細] [🔍 Bundle] [🎖️ 類似Token] [📉 Chart] [🗑️ 削除]
```

### 構造詳細

| 要素 | 配置 | 実装 |
|---|---|---|
| タイトル `🎯 $SYMBOL Analysis Report` | Embed title | `embed.title` |
| チェーン・経過日数・MCap | Embed description(1行目) | `embed.description` |
| Total Score バンド絵文字 | description 2行目 | 同上 |
| 特別フラグ群 | description 3行目 | 同上 |
| 8カテゴリの横棒グラフ | description 4行目以降 | 同上、等幅で整列 |
| Insight(2〜4行の自然言語解説) | description 末尾 | 同上 |
| TTL表示 | footer | `embed.footer` |
| ボタン群 | ActionRow(メッセージ付属) | discord.py View |

### プログレスバー文字列の生成

```python
def make_bar(score, width=10):
    filled = score / 100 * width
    full = int(filled)
    partial_chars = " ░▒▓█"  # 4段階
    partial = partial_chars[int((filled - full) * len(partial_chars)) % len(partial_chars)]
    empty = "░" * (width - full - (1 if partial != " " else 0))
    return "█" * full + (partial if partial != " " else "") + empty
```

または単純化して `int(score/10)` 個の`█` + 残りを `░` で埋めるだけでもOK。

### Insight文の生成ロジック

AIを使わないので、スコアパターンに応じたテンプレを用意:

```python
def generate_insight(scores, flags):
    lines = []

    # αβ判定
    if "α-Token" in flags:
        lines.append(f'"{symbol_base}系ミーム"の元祖で、現在{similar_week_count}個の追随トークン発生中。')
    elif "β-Token" in flags:
        lines.append(f'"{symbol_base}系"の後発組。元祖は別トークンです。')

    # SM状況
    if scores.sm >= 70:
        lines.append("Smart Moneyの買いは強く、")
    elif scores.sm <= 30:
        lines.append("Smart Moneyは積極的には買っていません。")

    # Bundle警告
    if "Bundle Risk" in flags or scores.bundle < 50:
        lines.append("ローンチ時のバンドル配布痕跡があります。")

    # 結論
    if total >= 70:
        lines.append("全体として有望で、攻めて良い水準です。")
    elif total >= 50:
        lines.append("仕込みなら少額推奨、要素を見極めて判断。")
    else:
        lines.append("複数の警告サインがあり、慎重に。")

    return "\n".join(lines)
```

---

## カテゴリ別詳細Embed(Detail Embed)

「📊 詳細」ボタンクリックで表示。各カテゴリの内訳を示す。1Embedに4カテゴリずつ入れて2Embed展開、または8つの小Embedを一気に投稿する(好みで)。

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧠 Smart Money Score: 78/100

✅ 保有SMウォレット: 15個 (38/40pt)
✅ ネットフロー: +$320K (22/35pt)
✅ 新規買い(24h): 7人 (18/25pt)

プロのウォレットが積極的に保有・買い増ししています。
Nansen上で「Smart Money」ラベルのウォレットが
複数このトークンに注目している状態です。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 Momentum Score: 55/100

✅ Buy/Sell比: 1.8倍 (25/30pt)
✅ 買い手優勢: 1.5倍 (19/25pt)
⚠️ 価格変動24h: +8% (15/25pt)
✅ 出来高伸長: 2.1倍 (14/20pt)

買い圧力は強いが、価格上昇はこれから。仕込み期の可能性。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💧 Liquidity Score: 72/100

✅ 流動性: $680K (40/50pt)
✅ 出来高/流動性比: 2.3倍 (30/30pt) — 健全な回転
⚠️ 時価総額: $4.2M (15/20pt)

$680Kの流動性があるので数十万ドル規模のトレードは可能。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👥 Distribution Score: 58/100

✅ 総ホルダー: 2,340人 (25/35pt)
⚠️ トップ10集中度: 42% (20/40pt) — やや集中
✅ 新規増加24h: +3.2% (16/25pt)
```

同様に Risk / Deployer Trust / Bundle Safety / Narrative の4Embedが続く。

---

## Bundle詳細Embed

「🔍 Bundle」ボタンクリックで表示。検出されたクラスタの詳細を可視化。

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 Bundle Detection Details

検出クラスタ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Cluster A (3wallets, 合計保有11.2%)
└─ First Funder: 0xFund...111

  ├─ 0xAbc...123 (保有3.8%) [Nansenで表示]
  ├─ 0xDef...456 (保有3.9%) [Nansenで表示]
  └─ 0xGhi...789 (保有3.5%) [Nansenで表示]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
その他3%超ホルダー:

  ├─ 0xJkl...012 (保有5.1%) — 独立
  ├─ 0xMno...345 (保有3.2%) — 独立
  └─ 0xPqr...678 (保有3.0%) — 独立

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 解釈:
3人のウォレットが同じ資金源(First Funder)から
資金を受け取っており、ローンチ時の
バンドル配布の痕跡が疑われます。
ただし合計保有率は11%で、致命的な規模ではありません。
```

各ウォレットアドレスは Nansen の該当URLへのハイパーリンク化すると便利。

---

## 類似Token Embed

「🎖️ 類似Token」ボタンクリックで表示。αβ判定の裏付けを見せる。

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎖️ Similar Tokens Analysis

α-Token: $PEPE2 (本トークン) ← これが元祖
Deploy: 5 days ago
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
追随トークン(7d以内):

1. $PEPE3     4d前  MCap $820K   流動性$45K  ⚠️
2. $PEPE4     3d前  MCap $1.2M   流動性$120K
3. $PEPE2X    2d前  MCap $340K   流動性$28K  ⚠️
4. $PEPEV2    2d前  MCap $890K   流動性$85K
5. $NEWPEPE   1d前  MCap $2.1M   流動性$220K  🔥
6. $PEPEKING  1d前  MCap $150K   流動性$18K  ⚠️
7. $PEPE2026  6h前  MCap $95K    流動性$12K  ⚠️
8. $MEGAPEPE  3h前  MCap $45K    流動性$5K   ⚠️

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
合計MCap: $9.8M(元祖$4.2M含む)
→ "PEPE2系ナラティブ"が現在活発
→ 元祖優位性により本トークンが流行の中心

⚠️ = 低流動性、🔥 = 出来高急増
```

---

## Chart Embed

「📉 Chart」ボタンクリック時。matplotlib / plotly 等でPNG生成して添付。

### チャート要素

- 価格ライン(24h or 7d)
- 出来高の棒グラフ(下段)
- Smart Moneyの買い/売りマーカー(可能なら)
- 主要なイベント点(大口取引、deployer動き等)

MVPでは単純な価格+出来高の2段チャートで十分。

---

## Delete Confirmation

「🗑️ 削除」ボタンクリック時:

```
本当にこのスレッドを削除しますか?
[はい、削除する] [キャンセル]
```

「はい」クリックで`thread.delete()`を実行。

---

## 自動削除の通知

TTL削除の5分前にスレッド内に通知を投稿(オプション):

```
⏰ このスレッドは5分後に自動削除されます
分析結果を保存したい場合はスクリーンショットを取ってください。
[削除をキャンセル(+30分延長)] [今すぐ削除]
```

延長機能は便利だがMVPでは省略可能。
