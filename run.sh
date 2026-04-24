#!/usr/bin/env bash
#
# Nansen Discord Bot 起動スクリプト。
#
# - .venv が無ければ作成して requirements.txt を install する
# - .venv の python で bot を起動する
#
set -euo pipefail

# スクリプトのあるディレクトリに移動(どこから呼んでも動くように)
cd "$(dirname "$(readlink -f "$0")")"

VENV_DIR=".venv"
PYTHON_BIN="python3"

# python3 の存在確認
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "ERROR: $PYTHON_BIN が見つかりません。Python 3.10 以上をインストールしてください。" >&2
    exit 1
fi

# 仮想環境の準備
if [ ! -d "$VENV_DIR" ]; then
    echo "[run.sh] 仮想環境 $VENV_DIR が無いので作成します"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    echo "[run.sh] 依存パッケージをインストールします"
    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install -r requirements.txt
elif [ "requirements.txt" -nt "$VENV_DIR/pyvenv.cfg" ]; then
    # requirements.txt が venv より新しい場合は追従インストール
    echo "[run.sh] requirements.txt が更新されているので再インストールします"
    "$VENV_DIR/bin/pip" install -r requirements.txt
    touch "$VENV_DIR/pyvenv.cfg"
fi

# .env の存在確認(無くても起動は試みる。Config.load で分かりやすくエラーになる)
if [ ! -f ".env" ]; then
    echo "[run.sh] WARNING: .env が存在しません。.env.example をコピーして値を埋めてください。" >&2
fi

echo "[run.sh] bot を起動します (Ctrl+C で停止)"
exec "$VENV_DIR/bin/python" -m bot.main
