"""SM 速報 BUY をダッシュボード用 JSON にエクスポートし、 別 git リポに push する cog。

役割:
- bot プロセス内で定期 (DASHBOARD_PUBLISH_INTERVAL_MIN 分間隔) に
  dashboard.exporter.export_signals.export_all() を呼ぶ
- 出力先は <DASHBOARD_REPO_PATH>/data/ (git clone 済みの公開リポ)
- 差分があれば `git add data/ && git commit && git push` する
- 失敗時は logger.exception で記録するが bot 本体は止めない

事前準備 (運用者側):
1. 公開リポ (例: nansen-sm-dashboard) を GitHub に作る
2. その中に dashboard/site/ の中身を index.html / app.js / style.css として置く
3. GitHub Pages を有効化 (main / root)
4. bot ホストで `git clone git@github.com:<user>/nansen-sm-dashboard.git`
5. 必要なら `git config user.name/email` を local 設定
6. SSH 鍵 (or PAT) を bot 実行ユーザに用意し、 push が通る状態にしておく
7. .env に DASHBOARD_PUBLISH_ENABLED=true / DASHBOARD_REPO_PATH=/path/to/clone

cron は使わない。 bot 同居 loop なので、 bot を落とせば push も止まる。
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from discord.ext import commands

from bot.config import Config
from dashboard.exporter.export_signals import export_all

logger = logging.getLogger(__name__)


class DashboardPublisherCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config):
        self.bot = bot
        self.config = config
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def cog_load(self) -> None:
        if not self.config.dashboard_publish_enabled:
            logger.info("dashboard_publisher は DISABLED (DASHBOARD_PUBLISH_ENABLED=false)")
            return
        repo = self.config.dashboard_repo_path
        if not repo:
            logger.warning(
                "dashboard_publisher: DASHBOARD_REPO_PATH 未設定のため起動しません"
            )
            return
        if not Path(repo).is_dir():
            logger.warning(
                "dashboard_publisher: repo path が存在しません path=%s", repo
            )
            return
        if not (Path(repo) / ".git").exists():
            logger.warning(
                "dashboard_publisher: repo path は git リポではありません path=%s", repo
            )
            return
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "dashboard_publisher 起動 (interval=%d 分 / repo=%s / branch=%s)",
            self.config.dashboard_publish_interval_min,
            repo,
            self.config.dashboard_git_branch,
        )

    def cog_unload(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    # ---- メインループ ----

    async def _loop(self) -> None:
        try:
            await self.bot.wait_until_ready()
        except asyncio.CancelledError:
            return

        # 起動直後に 1 回実行
        await self._run_once_safe(tag="boot")

        interval_sec = max(60, self.config.dashboard_publish_interval_min * 60)
        while not self.bot.is_closed():
            try:
                await asyncio.sleep(interval_sec)
            except asyncio.CancelledError:
                return
            await self._run_once_safe(tag="periodic")

    async def _run_once_safe(self, *, tag: str) -> None:
        if self._lock.locked():
            logger.info("[dashboard:%s] 前回実行中のためスキップ", tag)
            return
        async with self._lock:
            t0 = time.time()
            try:
                await self._run_once(tag=tag)
            except Exception:
                logger.exception("[dashboard:%s] 失敗 (継続)", tag)
            finally:
                logger.info(
                    "[dashboard:%s] 1 周期 完了 (elapsed=%.1fs)", tag, time.time() - t0
                )

    async def _run_once(self, *, tag: str) -> None:
        repo = Path(self.config.dashboard_repo_path)
        out_dir = repo / "data"

        counts = await export_all(
            out_dir=out_dir,
            min_distinct_buyers=self.config.dashboard_min_distinct_buyers,
            top_n=self.config.dashboard_top_n,
            buyers_per_token=self.config.dashboard_buyers_per_token,
            enrich=self.config.dashboard_enrich,
        )
        logger.info("[dashboard:%s] export 完了 counts=%s", tag, counts)

        # git status で差分判定
        rc, stdout, _ = await _run_git(repo, ["status", "--porcelain", "--", "data/"])
        if rc != 0:
            logger.warning("[dashboard:%s] git status 失敗 rc=%d", tag, rc)
            return
        if not stdout.strip():
            logger.info("[dashboard:%s] data/ に差分なし → push スキップ", tag)
            return

        rc, _, err = await _run_git(repo, ["add", "data/"])
        if rc != 0:
            logger.warning("[dashboard:%s] git add 失敗 rc=%d err=%s", tag, rc, err)
            return

        msg = f"auto: signals snapshot @ {int(time.time())}"
        rc, _, err = await _run_git(repo, ["commit", "-m", msg])
        if rc != 0:
            logger.warning("[dashboard:%s] git commit 失敗 rc=%d err=%s", tag, rc, err)
            return

        branch = self.config.dashboard_git_branch
        rc, _, err = await _run_git(repo, ["push", "origin", branch])
        if rc != 0:
            logger.warning("[dashboard:%s] git push 失敗 rc=%d err=%s", tag, rc, err)
            return

        logger.info("[dashboard:%s] push 完了 (msg=%s)", tag, msg)


async def _run_git(repo: Path, args: list[str]) -> tuple[int, str, str]:
    """指定リポで git コマンドを実行し (rc, stdout, stderr) を返す。"""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(repo),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    return proc.returncode or 0, out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace")


async def setup(bot: commands.Bot) -> None:
    config: Config = bot.config  # type: ignore[attr-defined]
    await bot.add_cog(DashboardPublisherCog(bot, config))
