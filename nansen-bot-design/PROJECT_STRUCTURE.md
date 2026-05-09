# プロジェクト構造

discord.py (Python) ベースを推奨。Node.js派なら discord.js でも同等に実装可能。

## ディレクトリ構成(推奨)

```
nansen-bot/
├── .env                          # APIキー・設定(gitignore対象)
├── .env.example                  # 公開用テンプレート
├── .gitignore
├── README.md
├── requirements.txt              # Python依存
├── pyproject.toml                # またはこちらで管理
│
├── docs/                         # 設計書(この設計書群をそのまま配置)
│   ├── CONVERSATION_HISTORY.md
│   ├── DESIGN.md
│   ├── API_MAPPING.md
│   ├── EMBED_DESIGN.md
│   ├── PROJECT_STRUCTURE.md
│   ├── TODO.md
│   └── COST_ESTIMATE.md
│
├── bot/                          # Bot本体
│   ├── __init__.py
│   ├── main.py                   # エントリポイント(bot.run())
│   ├── config.py                 # .env読み込み、設定管理
│   │
│   ├── cogs/                     # Discord commands (discord.py's Cog)
│   │   ├── __init__.py
│   │   ├── analyze.py            # /analyze コマンド
│   │   └── admin.py              # 管理系(キャッシュクリア等)
│   │
│   ├── clients/                  # 外部APIクライアント
│   │   ├── __init__.py
│   │   ├── nansen.py             # Nansen APIラッパ
│   │   ├── dexscreener.py        # DexScreener APIラッパ
│   │   └── coingecko.py          # CoinGecko APIラッパ
│   │
│   ├── scoring/                  # スコア計算ロジック
│   │   ├── __init__.py
│   │   ├── engine.py             # 総合スコア計算のエントリ
│   │   ├── smart_money.py        # SM Score
│   │   ├── momentum.py           # Momentum Score
│   │   ├── liquidity.py          # Liquidity Score
│   │   ├── distribution.py       # Distribution Score
│   │   ├── risk.py               # Risk Score
│   │   ├── deployer.py           # Deployer Trust Score
│   │   ├── bundle.py             # Bundle Safety Score
│   │   └── narrative.py          # Narrative Score
│   │
│   ├── embeds/                   # Embed生成
│   │   ├── __init__.py
│   │   ├── main_embed.py         # 総合Embed
│   │   ├── detail_embed.py       # カテゴリ別詳細
│   │   ├── bundle_embed.py       # Bundle詳細
│   │   ├── similar_embed.py      # 類似Token
│   │   ├── chart_embed.py        # Chart(画像生成含む)
│   │   └── insights.py           # Insightテンプレ文生成
│   │
│   ├── views/                    # discord.py UI Views(ボタン)
│   │   ├── __init__.py
│   │   └── analysis_view.py      # 詳細/Bundle/類似/Chart/削除ボタン
│   │
│   ├── thread_manager/           # サブスレッド管理(TTL削除含む)
│   │   ├── __init__.py
│   │   ├── manager.py            # スレッド作成・削除・登録
│   │   └── scheduler.py          # TTL監視のバックグラウンドタスク
│   │
│   ├── cache/                    # レスポンスキャッシュ(APIコスト削減)
│   │   ├── __init__.py
│   │   └── memory_cache.py       # TTL付きインメモリキャッシュ
│   │
│   ├── models/                   # データクラス(dataclass / pydantic)
│   │   ├── __init__.py
│   │   ├── token.py              # トークン情報
│   │   ├── holder.py             # ホルダー情報
│   │   ├── deployer.py           # Deployer情報
│   │   └── scores.py             # スコア構造体
│   │
│   └── utils/                    # ユーティリティ
│       ├── __init__.py
│       ├── progress_bar.py       # プログレスバー文字列生成
│       ├── formatters.py         # 数値フォーマット($等)
│       └── logger.py             # ロギング設定
│
├── data/                         # 永続データ(gitignore対象)
│   └── threads.db                # スレッドTTL情報(SQLite)
│
└── tests/                        # テスト
    ├── __init__.py
    ├── test_scoring/
    │   ├── test_smart_money.py
    │   ├── test_momentum.py
    │   └── ...
    ├── test_clients/
    │   └── test_nansen.py
    └── fixtures/                 # APIレスポンスのモックデータ
        ├── nansen_holders.json
        ├── dexscreener_tokens.json
        └── ...
```

## 設計のポイント

### 1. clientsとscoring分離

- `clients/` は**純粋なAPIラッパ**(認証・リトライ・キャッシュのみ)
- `scoring/` は**純粋な計算ロジック**(APIを知らない、データクラスだけを受け取る)

これにより、将来別のデータソースに切り替えても`scoring/`はそのまま使える。

### 2. cogs/ はDiscord依存部分

Discord側の関心(コマンド登録、メッセージ送信、ボタンハンドラ)は`cogs/`と`views/`に隔離。将来的にTelegram botにも展開する場合、`cogs/`だけ差し替えればいい。

### 3. 並列化しやすい構造

`scoring/engine.py` が各カテゴリモジュールを`asyncio.gather()`で並列呼び出し。各モジュールが独立してAPIコールする設計。

### 4. キャッシュ戦略

`cache/memory_cache.py` でシンプルなTTL付きインメモリキャッシュを実装。キーは`(api_name, endpoint, params_hash)`、TTLはデフォルト5分。

同じトークンへの連続問い合わせでコスト爆発を防ぐ目的。永続化は不要(botが再起動したらリセットでOK)。

### 5. SQLite for thread management

TTL削除機能だけは永続化が必要(botが再起動しても覚えている必要がある)。`data/threads.db`にSQLite1枚で足りる。テーブル例:

```sql
CREATE TABLE scheduled_deletes (
    thread_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL,
    delete_at TIMESTAMP NOT NULL,
    created_by INTEGER NOT NULL
);
```

---

## .env.example

```bash
# === Discord ===
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_APPLICATION_ID=your_application_id_here

# === Nansen ===
NANSEN_API_KEY=your_nansen_api_key_here
NANSEN_BASE_URL=https://api.nansen.ai

# === CoinGecko (Demo API Key推奨) ===
COINGECKO_API_KEY=your_coingecko_demo_key_here

# === DexScreener は APIキー不要 ===

# === Bot Settings ===
THREAD_TTL_MINUTES=60
CACHE_TTL_SECONDS=300
MAX_CONCURRENT_NANSEN_CALLS=5

# === Logging ===
LOG_LEVEL=INFO
LOG_FILE=logs/bot.log
```

---

## requirements.txt(最小構成)

```
discord.py>=2.4.0
aiohttp>=3.9.0
python-dotenv>=1.0.0
pydantic>=2.0.0
aiosqlite>=0.19.0
matplotlib>=3.8.0     # チャート用
```

## 追加推奨

```
pytest>=8.0.0
pytest-asyncio>=0.23.0
respx>=0.20.0         # aiohttpのモック
ruff>=0.1.0           # linter/formatter
```

---

## 起動フロー

```python
# bot/main.py の概要
import discord
from discord.ext import commands
from bot.config import load_config
from bot.thread_manager.scheduler import ThreadDeletionScheduler

async def main():
    config = load_config()
    intents = discord.Intents.default()
    intents.message_content = False  # Slash commandのみ使うなら不要

    bot = commands.Bot(command_prefix="/", intents=intents)

    # cogs読み込み
    await bot.load_extension("bot.cogs.analyze")
    await bot.load_extension("bot.cogs.admin")

    # TTLスケジューラ起動
    scheduler = ThreadDeletionScheduler(bot)
    bot.loop.create_task(scheduler.run_forever())

    await bot.start(config.DISCORD_BOT_TOKEN)
```

---

## 段階的実装推奨

いきなり全部実装しようとせず、MVP→段階拡張が現実的:

1. **MVP**: `/analyze` → 総合スコアのみの1 Embed、TTL削除なし、手動削除ボタンだけ
2. **v0.2**: TTL削除追加、キャッシュ追加
3. **v0.3**: カテゴリ別詳細Embed追加
4. **v0.4**: Bundle詳細Embed追加
5. **v0.5**: 類似Token Embed + Narrative Score 追加
6. **v1.0**: Chartボタン追加、テスト整備、エラーハンドリング強化

詳細は TODO.md を参照。
