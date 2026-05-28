"""Discord Bot エントリポイント。"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from bot.config import Config
from bot.views import RateWalletButton, ScoreWalletButton

logger = logging.getLogger(__name__)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


async def _run() -> None:
    config = Config.load()
    _setup_logging(config.log_level)

    intents = discord.Intents.default()  # Slash Command だけなので message_content は不要
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
    bot.config = config  # type: ignore[attr-defined]

    @bot.event
    async def on_ready() -> None:
        logger.info("Logged in as %s (id=%s)", bot.user, getattr(bot.user, "id", None))
        try:
            if config.dev_guild_id is not None:
                # 開発ギルドなら即反映
                guild = discord.Object(id=config.dev_guild_id)
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                logger.info(
                    "Slash コマンド %d 件を guild=%s に同期しました (即反映)",
                    len(synced), config.dev_guild_id,
                )
            else:
                synced = await bot.tree.sync()
                logger.info(
                    "Slash コマンド %d 件をグローバル同期しました (反映まで最大1時間)",
                    len(synced),
                )
        except Exception:
            logger.exception("Slash コマンドの同期に失敗しました")

    # 通知のスコアボタンを再起動後も有効化 (custom_id でハンドリング)。
    # ScoreWalletButton=新カテゴリ式、 RateWalletButton=旧★通知の後方互換。
    bot.add_dynamic_items(ScoreWalletButton)
    bot.add_dynamic_items(RateWalletButton)

    await bot.load_extension("bot.cogs.check")
    await bot.load_extension("bot.cogs.digest")
    await bot.load_extension("bot.cogs.sm_roster")
    await bot.load_extension("bot.cogs.sm_signal")
    await bot.load_extension("bot.cogs.sm_summary")
    await bot.load_extension("bot.cogs.dashboard_publisher")
    await bot.start(config.discord_bot_token)


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
