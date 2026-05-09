# Nansen Token Analysis Discord Bot — 設計書

ミームコインを主なターゲットとした、Discord上でのオンチェーントークン分析botの設計書一式。

## ドキュメント構成

| ファイル | 内容 |
|---|---|
| [CONVERSATION_HISTORY.md](./CONVERSATION_HISTORY.md) | この設計に至った議論の経緯・決定事項 |
| [DESIGN.md](./DESIGN.md) | スコアリング設計(8カテゴリ、計算式、重み) |
| [API_MAPPING.md](./API_MAPPING.md) | 各評価項目とAPI/エンドポイントの対応表 |
| [EMBED_DESIGN.md](./EMBED_DESIGN.md) | Discord Embedのビジュアル設計 |
| [PROJECT_STRUCTURE.md](./PROJECT_STRUCTURE.md) | 推奨ファイル構成 |
| [TODO.md](./TODO.md) | 実装タスクリスト(MVP〜v2) |
| [COST_ESTIMATE.md](./COST_ESTIMATE.md) | API利用料の見積もり |

## クイックサマリ

### 何を作るか

Discord上で `/analyze <token>` のようなコマンドを叩くと、サブスレッドが作られ、そこにNansen・DexScreener・CoinGeckoのデータを統合した8カテゴリのスコアリングレポートが投稿されるbot。スレッドは`.env`で設定した時間で自動削除、または手動削除ボタンで即削除可能。

### 方針

- **AIは使わない**(コスト予測困難、ローカル実行は非力)
- **ルールベースのスコアリング**でAIなしでも十分な判定品質を確保
- **無料APIを最大活用**(DexScreener/CoinGecko)、Nansenは「Nansenじゃないと取れないもの」だけに使う
- **ミームコイン特有の観点**を重視(バンドル検出、αβ判定、deployer信頼度)

### 8カテゴリスコア

1. Smart Money (20%) — Nansen
2. Momentum (12%) — Nansen + DexScreener
3. Liquidity (12%) — DexScreener + Nansen
4. Distribution (8%) — Nansen
5. Risk (12%) — Nansen
6. Deployer Trust (8%) — Nansen
7. Bundle Safety (13%) — Nansen
8. Narrative (15%) — DexScreener + CoinGecko

### コスト目安

月100コマンド実行で **$5〜15/月** 程度。Pro Plan $49/月は最初は不要。
