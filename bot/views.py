"""discord.py UI コンポーネント (ボタン群)。"""
from __future__ import annotations

import logging

import discord

logger = logging.getLogger(__name__)


class DeleteResultView(discord.ui.View):
    """結果メッセージ/スレッドを削除する 🗑 ボタン付き View。

    - inline モード時: ボタンが付いた Embed メッセージそのものを削除
    - thread モード時: スレッド全体の削除を試み、駄目なら bot の投稿だけ削除
    - 操作可能なのはコマンド実行者 + Manage Messages 権限保持者
    """

    def __init__(
        self,
        *,
        owner_id: int,
        target_thread: discord.Thread | None = None,
        target_anchor: discord.Message | None = None,
    ):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.target_thread = target_thread
        self.target_anchor = target_anchor

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        perms = (
            interaction.channel.permissions_for(interaction.user)
            if hasattr(interaction.channel, "permissions_for")
            else None
        )
        if perms is not None and perms.manage_messages:
            return True
        await interaction.response.send_message(
            "削除はコマンド実行者のみ可能です。", ephemeral=True
        )
        return False

    @discord.ui.button(label="🗑️ 削除", style=discord.ButtonStyle.danger)
    async def delete_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.target_thread is not None:
            if await self._try_delete_thread(interaction):
                return

        # inline / fallback: ボタンが付いたメッセージを削除
        try:
            if interaction.message is not None:
                await interaction.message.delete()
            if not interaction.response.is_done():
                await interaction.response.defer()
        except discord.Forbidden:
            logger.exception("メッセージ削除権限不足")
            await self._safe_reply(interaction, "メッセージ削除権限がありません。")
        except Exception:
            logger.exception("メッセージ削除失敗")
            await self._safe_reply(interaction, "削除に失敗しました。")

    async def _try_delete_thread(self, interaction: discord.Interaction) -> bool:
        """スレッド削除を試みる。成功時 True。"""
        try:
            await self.target_thread.delete()
        except discord.Forbidden:
            logger.warning("スレッド削除権限不足 → メッセージ削除に fallback")
            return False
        except Exception:
            logger.exception("スレッド削除失敗 → メッセージ削除に fallback")
            return False

        # スレッドの起点メッセージ(親チャンネル側)も併せて削除
        if self.target_anchor is not None:
            try:
                await self.target_anchor.delete()
            except Exception:
                logger.exception("起点メッセージ削除失敗 (スレッドは削除済み)")
        return True

    async def _safe_reply(self, interaction: discord.Interaction, text: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except Exception:
            logger.exception("エラー応答送信失敗")
