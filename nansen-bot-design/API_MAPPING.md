# API対応表

各評価項目と使用するAPI/エンドポイントの対応関係。

## 3つのAPIの役割分担

| API | 役割 | 料金 | レート制限 |
|---|---|---|---|
| **Nansen** | オンチェーン解析の核(SM/ホルダー/deployer/バンドル) | 従量課金 | Pro: 多くが1 credit/call |
| **DexScreener** | 検索・類似トークン発掘・ソーシャルリンク | 無料・APIキー不要 | 300 req/min(ペア系)、60 req/min(プロフィール系) |
| **CoinGecko(GeckoTerminal)** | 広範なトークンメタ・カテゴリ・トレンディング | 無料Demo: 10,000 call/月 | Demo枠 |

---

## 評価項目 → API対応表

| 評価項目 | 使用API | エンドポイント | クレジット(Pro) |
|---|---|---|---|
| 基本メタ(価格・流動性・出来高) | DexScreener | `GET /latest/dex/tokens/{address}` | 無料 |
| チャート(OHLCV) | CoinGecko or DexScreener | GeckoTerminal OHLCV / DS pairs | 無料 |
| ソーシャルリンク | DexScreener | 上記のresponse内`info.socials` | 無料 |
| カテゴリ/ナラティブタグ | CoinGecko | `GET /coins/{id}` の `categories` | 無料 |
| Smart Money保有・フロー | Nansen | `POST /tgm/flow-intel` | 1 |
| SMの買い/売り履歴 | Nansen | `POST /tgm/who-bought-sold` | 1 |
| Nansen独自指標 | Nansen | `POST /tgm/nansen-indicators` | 1 |
| ホルダー分布(トップ10集中度) | Nansen | `POST /tgm/holders` | 5 |
| Deployerラベル | Nansen | `POST /profiler/address/labels` | 1 |
| Deployer PnL | Nansen | `POST /profiler/address/pnl-summary` | 1 |
| Deployer関連ウォレット | Nansen | `POST /profiler/address/related-wallets` | 1 |
| 3%超ホルダーの関連ウォレット(×N) | Nansen | 同上 | 1 each |
| 3%超ホルダーのラベル(×N) | Nansen | `POST /profiler/address/labels` | 1 each |
| 類似ミーム検索(αβ判定) | DexScreener | `GET /latest/dex/search?q={symbol}` | 無料 |
| Token Boost状況 | DexScreener | `GET /token-boosts/latest/v1` | 無料 |
| トレンディング | CoinGecko | `GET /search/trending` | 無料 |

---

## Nansen APIの注意点

### ベースURL
```
https://api.nansen.ai
```

### 認証
全エンドポイントで `apiKey: YOUR_API_KEY` ヘッダーが必要。

### Freeプランの制限
`chain="all"`を使える一部エンドポイントのみ利用可能、クレジットコストもProの10倍。本番運用には Pro Plan または x402 都度課金が必要。

### Profiler関連のchain指定
`related-wallets`エンドポイントは `chain="all"` が使えないため、個別にチェーンを指定する必要がある。Solanaトークンなら`"solana"`。

### "Special Connections"が返す種類
`related-wallets` は以下の関係を返す:
- First Funder
- Signer / Previous Signer
- Multisig Signer of / Previous Multisig Signer of
- Deployed via / Deployed by
- Deployed Contract / Created Contract / Created by

**バンドル検出では主に"First Funder"を使う**(同じfunderから資金供給を受けたウォレット群を同一クラスタ扱い)。

---

## DexScreener APIの主要エンドポイント

### トークン情報取得
```
GET https://api.dexscreener.com/latest/dex/tokens/{tokenAddress}
```
レスポンス例:
```json
{
  "pairs": [
    {
      "chainId": "solana",
      "dexId": "raydium",
      "pairAddress": "...",
      "baseToken": {"address": "...", "name": "...", "symbol": "..."},
      "priceUsd": "0.00012",
      "liquidity": {"usd": 680000},
      "volume": {"h24": 1500000, "h6": 400000},
      "priceChange": {"h24": 8.5},
      "txns": {"h24": {"buys": 820, "sells": 540}},
      "info": {
        "socials": [
          {"type": "twitter", "url": "..."},
          {"type": "telegram", "url": "..."}
        ],
        "websites": [{"url": "..."}]
      },
      "pairCreatedAt": 1709000000000
    }
  ]
}
```

### シンボル検索(αβ判定に使用)
```
GET https://api.dexscreener.com/latest/dex/search?q={query}
```
部分一致で同名/類似シンボルのトークンが返る。

### Token Boost状況
```
GET https://api.dexscreener.com/token-boosts/latest/v1
```
現在boostされているトークン一覧。対象トークンのaddressが含まれるかで「ブースト中か」を判定。

---

## CoinGecko API(Demo枠)の主要エンドポイント

### トークンIDの取得(アドレス→ID)
```
GET /api/v3/coins/{asset_platform}/contract/{contract_address}
```
例: Solana上のトークンなら `asset_platform=solana`

### カテゴリ情報
上記レスポンス内の `categories` フィールドに `["Meme", "Solana Ecosystem", ...]` のように分類が入る。

### トレンディング
```
GET /api/v3/search/trending
```
現在トレンドのコインとNFTが返る。

### Demo API Key
無料Demoプランにはサインアップでキー発行。ヘッダー `x-cg-demo-api-key` で認証。

---

## 1コマンドあたりのAPI呼び出しフロー

```
[ユーザー] /analyze token:<address> chain:solana
    ↓
[Step 1: 並列実行]
  DexScreener  → トークン基本情報・類似検索・Boost
  CoinGecko    → カテゴリ・Trending
  Nansen       → token-information, nansen-indicators, flow-intel,
                 who-bought-sold, holders
    ↓
[Step 2: holders結果からwhale・deployerを抽出]
    ↓
[Step 3: 並列実行]
  Nansen       → deployerの labels / pnl-summary / related-wallets
  Nansen       → 3%超ホルダー(whales)各々の related-wallets / labels
    ↓
[Step 4: スコア計算]
    ↓
[Step 5: Embed投稿]
```

### 並列化の注意点

- Nansenのレート制限を超えないよう`asyncio.Semaphore`などで同時実行数を制御
- DexScreenerは300req/minだが、ペアエンドポイントと検索エンドポイントでバケットが別
- CoinGecko Demoは10,000 call/月を1日単位で均等割り計算すると333 call/日なので注意

---

## エラーハンドリング方針

### Nansen
- 401/403: API Keyエラー → ログに記録してユーザーに再認証を促すEmbed
- 429: レート制限 → 指数バックオフで3回まで再試行
- 404: データなし → スコアリングでそのカテゴリを「N/A」にしつつ、重みを再配分

### DexScreener
- 404: トークン未登録 → αβ判定をスキップ、Embedで警告
- 429: レート制限 → 30秒待機後に再試行

### CoinGecko
- 404: トークン未登録 → カテゴリ情報なしでも動作継続
- 429: Demo枠超過 → その日はCoinGecko機能無効化

### フォールバック
Nansenのtoken-informationが取れない場合、DexScreenerから価格・流動性を取る。この冗長性によりコストも削減可能。
