# Nansen Discord Bot (MVP)

Solana ミームコインの CA を受け取り、Nansen API から以下を取得して Discord に Embed で返す最小構成の bot。

- 銘柄詳細 (token-information)
- Smart Money の直近売買 (who-bought-sold)
- ホルダー分布 (holders)
- バンドル検出 (3%超 whale × related-wallets の First Funder クラスタ)

消費クレジット数は設計書の目安値を自前で加算し、最後の Embed のフッタに「消費クレジット: N(目安)」として表示する。

## 前提

- Python 3.10 以上
- Discord Bot アプリを作成済み (Bot Token を取得済み)
- Nansen API Key (`nsn_...`) を取得済み

## セットアップ

`.env` だけ用意すれば、あとは起動スクリプト [run.sh](run.sh) が `.venv` の作成と依存インストールを自動で行う。

```bash
cd /home/tasato/develop_bb/nansen_test01

# 1. .env を作成して値を埋める
cp .env.example .env
# エディタで .env を開き、DISCORD_BOT_TOKEN / NANSEN_API_KEY / ALLOWED_CHANNEL_IDS を設定

# 2. 起動(初回は自動で .venv 作成 + pip install まで実行される)
./run.sh
```

### 手動セットアップする場合

```bash
# 仮想環境を作成
python3 -m venv .venv

# 仮想環境を有効化 (Linux/macOS)
source .venv/bin/activate
# (Windows PowerShell の場合)
# .venv\Scripts\Activate.ps1

# 依存パッケージをインストール
pip install --upgrade pip
pip install -r requirements.txt
```

### .env の項目

| 変数 | 必須 | 内容 |
|---|---|---|
| `DISCORD_BOT_TOKEN` | ✓ | Discord Bot Token |
| `NANSEN_API_KEY` | ✓ | Nansen API Key (`nsn_...`) |
| `NANSEN_BASE_URL` | - | 既定 `https://api.nansen.ai` |
| `ALLOWED_CHANNEL_IDS` | - | bot が反応するチャネル ID (カンマ区切り)。空にすると全チャネル許可 |
| `DEV_GUILD_ID` | - | 開発用ギルド ID。設定するとこのギルドにだけ Slash コマンドを即時同期する。空にすると全ギルドへグローバル同期 (反映まで最大1時間) |
| `LOG_LEVEL` | - | `INFO` / `DEBUG` など。API payload を見たい場合は `DEBUG` |

## 実行

もっとも簡単な方法は起動スクリプトを使うこと。初回は `.venv` の作成と依存インストールも自動で行う:

```bash
./run.sh
```

手動で実行する場合(仮想環境が有効な状態で):

```bash
python -m bot.main
```

起動ログに `Slash コマンド N 件を ... 同期しました` が出れば OK。

- `DEV_GUILD_ID` を設定している場合 → そのギルドで即反映
- 未設定の場合 → グローバル同期となり、全ギルドに反映されるまで最大1時間程度かかる

## 使い方

Discord 上でコマンドを実行する:

```
/check ca:<Solanaのトークンアドレス>
```

例: BONK のアドレスなどを渡す。結果は4つの Embed (銘柄詳細 / Smart Wallets / Holders / Bundle) として返信される。

## 停止

ターミナルで `Ctrl+C`。

## 仮想環境を抜ける

```bash
deactivate
```

## トラブルシュート

- **`環境変数 DISCORD_BOT_TOKEN が設定されていません`**
  → `.env` が存在しないか値が空。`.env.example` からコピーして値を埋めたか確認。
- **Slash コマンドが Discord に出てこない**
  → Bot を招待する際の OAuth2 URL で `applications.commands` スコープが付与されているか確認。
- **Nansen API 400/422**
  → `LOG_LEVEL=DEBUG` にして payload とレスポンスをログ確認。[bot/nansen_client.py](bot/nansen_client.py) のリクエスト body を調整する。
- **Embed の値が `N/A` ばかり**
  → レスポンスのキー名が想定と違う。ログを見て [bot/embeds.py](bot/embeds.py) の `_first(...)` 候補キーに追加する。

## 設計ドキュメント

詳細な設計・将来計画は [nansen-bot-design/](nansen-bot-design/) 配下を参照。
