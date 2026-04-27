"""discord.py UI コンポーネント (ボタン群)。"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot.scoring.types import TotalScore

logger = logging.getLogger(__name__)


class ResultView(discord.ui.View):
    """結果メッセージにスコア根拠表示と削除ボタンを付ける View。

    - ヘルプ (📖 スコア根拠): 誰でも押せる、ephemeral で計算根拠を表示
    - 削除 (🗑️): コマンド実行者 + Manage Messages 権限保持者のみ
        - thread モード: スレッド削除を試み、駄目なら bot 投稿だけ削除
        - inline モード: ボタンが付いたメッセージそのものを削除
    """

    def __init__(
        self,
        *,
        owner_id: int,
        target_thread: discord.Thread | None = None,
        target_anchor: discord.Message | None = None,
        scores: "TotalScore | None" = None,
    ):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.target_thread = target_thread
        self.target_anchor = target_anchor
        self.scores = scores
        if scores is None:
            # スコアが無い時はヘルプボタンを除去
            self.remove_item(self.help_button)

    # ----- ヘルプ -----
    @discord.ui.button(label="📖 スコア根拠", style=discord.ButtonStyle.secondary)
    async def help_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        embed = self._build_help_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _build_help_embed(self) -> discord.Embed:
        s = self.scores
        embed = discord.Embed(
            title="📖 スコア計算の根拠",
            color=0x4B9CD3,
        )
        if s is None:
            embed.description = "スコア情報がありません。"
            return embed

        embed.description = (
            f"**Total: {s.total:.1f} / 100  {s.band_emoji} {s.band}**\n"
            "各カテゴリ 0..100 点に正規化したのち、設計書の重みを 5 カテゴリで再配分して合算。"
        )

        for c in s.categories:
            value = _format_breakdown(c.name, c.breakdown, c.note)
            embed.add_field(
                name=f"{c.emoji} {c.name}: {c.score:.1f} / 100  (重み {c.weight*100:.1f}%)",
                value=value or "-",
                inline=False,
            )

        embed.add_field(
            name="判定バンド",
            value=(
                "🟢🟢 80+: STRONG BUY\n"
                "🟢 60-79: BUY\n"
                "🟡 40-59: CAUTION\n"
                "🔴 0-39: AVOID"
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                "フェーズB2: 7カテゴリ暫定算出。 "
                "Narrative は別 API 連携待ち"
            )
        )
        return embed

    # ----- 削除 -----
    @discord.ui.button(label="🗑️ 削除", style=discord.ButtonStyle.danger)
    async def delete_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not self._can_delete(interaction):
            await interaction.response.send_message(
                "削除はコマンド実行者のみ可能です。", ephemeral=True
            )
            return

        if self.target_thread is not None:
            if await self._try_delete_thread():
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

    def _can_delete(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        perms = (
            interaction.channel.permissions_for(interaction.user)
            if hasattr(interaction.channel, "permissions_for")
            else None
        )
        return perms is not None and perms.manage_messages

    async def _try_delete_thread(self) -> bool:
        try:
            await self.target_thread.delete()
        except discord.Forbidden:
            logger.warning("スレッド削除権限不足 → メッセージ削除に fallback")
            return False
        except Exception:
            logger.exception("スレッド削除失敗 → メッセージ削除に fallback")
            return False

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


# 後方互換のためのエイリアス
DeleteResultView = ResultView


def _format_breakdown(name: str, breakdown: dict, note: str) -> str:
    if not breakdown:
        return note or ""

    lines: list[str] = []
    if name == "Smart Money":
        bc = breakdown.get("buy_count")
        ba = breakdown.get("buy_amount_usd")
        cs = breakdown.get("count_score")
        as_ = breakdown.get("amount_score")
        if bc is not None:
            lines.append(f"・SM 買い件数 7d: {int(bc)}人 → {_fmt(cs)} / 50pt")
        if ba is not None:
            lines.append(f"・SM 買い額 7d: {_fmt_usd(ba)} → {_fmt(as_)} / 50pt")
        lines.append("(簡略: 保有SM数とネットフローは未対応)")

    elif name == "Momentum":
        bsr = breakdown.get("buy_sell_ratio")
        br = breakdown.get("buyer_ratio")
        bs = breakdown.get("bs_score")
        ber = breakdown.get("buyer_score")
        if bsr is not None:
            lines.append(f"・Buy/Sell 比: {bsr:.2f}x → {_fmt(bs)} / 30pt")
        if br is not None:
            lines.append(f"・買い手/売り手 比: {br:.2f}x → {_fmt(ber)} / 25pt")
        lines.append("(簡略: 価格変動・出来高伸長は未対応 → 取得可能 55pt を 100pt 換算)")

    elif name == "Liquidity":
        liq = breakdown.get("liquidity_usd")
        vlr = breakdown.get("vol_liq_ratio")
        mcap = breakdown.get("market_cap_usd")
        ls = breakdown.get("liq_score")
        vs = breakdown.get("vlr_score")
        ms = breakdown.get("mc_score")
        if liq is not None:
            lines.append(f"・流動性: {_fmt_usd(liq)} → {_fmt(ls)} / 50pt")
        if vlr is not None:
            lines.append(f"・出来高/流動性 比: {vlr:.2f} → {_fmt(vs)} / 30pt")
        if mcap is not None:
            lines.append(f"・MCap: {_fmt_usd(mcap)} → {_fmt(ms)} / 20pt")

    elif name == "Distribution":
        th = breakdown.get("total_holders")
        top10 = breakdown.get("top10_concentration_pct")
        ts = breakdown.get("th_score")
        t10s = breakdown.get("t10_score")
        if th is not None:
            lines.append(f"・総ホルダー: {int(th):,} → {_fmt(ts)} / 35pt")
        if top10 is not None:
            lines.append(f"・Top10 集中度: {top10:.2f}% → {_fmt(t10s)} / 40pt")
        lines.append("(簡略: 新規ホルダー増加率は未対応 → 75pt を 100pt 換算)")

    elif name == "Bundle Safety":
        wc = breakdown.get("whale_count")
        cc = breakdown.get("cluster_count")
        mcs = breakdown.get("max_cluster_size")
        ws = breakdown.get("wc_score")
        bs = breakdown.get("bd_score")
        if wc is not None:
            lines.append(f"・3%超ホルダー数: {wc} → {_fmt(ws)} / 30pt")
        if mcs is not None:
            lines.append(f"・最大クラスタサイズ: {mcs} (検出 {cc} 件) → {_fmt(bs)} / 40pt")
        lines.append("(簡略: 警告ラベル混入・Insider 比は未対応 → 70pt を 100pt 換算)")

    elif name == "Risk":
        btc_label = breakdown.get("btc_signal")
        bs = breakdown.get("btc_score")
        cex_ratio = breakdown.get("cex_inflow_ratio")
        cs = breakdown.get("cex_score")
        ad = breakdown.get("age_days")
        ags = breakdown.get("age_score")
        ni_summary = breakdown.get("ni_summary") or {}
        nis = breakdown.get("ni_score")
        if btc_label is not None:
            lines.append(f"・BTC 連動 (btc-reflexivity): `{btc_label}` → {_fmt(bs)} / 30pt")
        else:
            lines.append(f"・BTC 連動: 取得不可 → {_fmt(bs)} / 30pt")
        if cex_ratio is not None:
            lines.append(f"・CEX 流入比: {cex_ratio*100:.2f}% → {_fmt(cs)} / 25pt")
        else:
            lines.append(f"・CEX 流入比: 取得不可 → {_fmt(cs)} / 25pt")
        if ad is not None:
            lines.append(f"・トークン年齢: {ad}日 → {_fmt(ags)} / 20pt")
        else:
            lines.append(f"・トークン年齢: 不明 → {_fmt(ags)} / 20pt")
        ni_parts = [f"{k}={v or '?'}" for k, v in ni_summary.items()]
        lines.append(f"・Nansen 独自リスク ({', '.join(ni_parts) or '取得不可'}) → {_fmt(nis)} / 25pt")

    elif name == "Deployer Trust":
        if not breakdown.get("fetched", True):
            lines.append("・Solana RPC からの取得失敗 → スコア計上不可")
        elif breakdown.get("renounced"):
            lines.append("・Mint Authority Renounced (deployer 不明)")
            lines.append("・通常はラグ抑止効果ありとして 70pt 付与")
        else:
            addr = breakdown.get("deployer_address")
            labels = breakdown.get("labels") or []
            ws = breakdown.get("warn_score")
            ages = breakdown.get("age_score")
            ds = breakdown.get("days_active")
            ps = breakdown.get("pnl_score")
            wr = breakdown.get("win_rate")
            tt = breakdown.get("total_trades")
            rs = breakdown.get("rel_score")
            if addr:
                lines.append(f"・deployer: `{addr[:6]}...{addr[-4:]}`")
            if labels:
                lines.append(f"・ラベル: {', '.join(labels[:5]) or 'なし'}")
            lines.append(f"・警告ラベル判定 → {_fmt(ws)} / 40pt")
            if ds is not None:
                lines.append(f"・アカウント年齢: {ds}日 → {_fmt(ages)} / 25pt")
            else:
                lines.append(f"・アカウント年齢: 不明 → {_fmt(ages)} / 25pt")
            if tt is not None:
                wr_str = f"{wr*100:.1f}%" if isinstance(wr, (int, float)) else "?"
                lines.append(f"・PnL: 取引{tt}回 / 勝率{wr_str} → {_fmt(ps)} / 20pt")
            else:
                lines.append(f"・PnL: 取得不可 → {_fmt(ps)} / 20pt")
            lines.append(f"・関連ウォレット: フェーズB MVP → 一律 {_fmt(rs)} / 15pt")

    else:
        for k, v in breakdown.items():
            lines.append(f"・{k}: {v}")

    if note:
        lines.append(f"※ {note}")
    return "\n".join(lines)


def _fmt(v) -> str:
    if v is None:
        return "?"
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_usd(v) -> str:
    if v is None:
        return "N/A"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    a = abs(n)
    if a >= 1_000_000_000:
        return f"${n/1_000_000_000:.2f}B"
    if a >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if a >= 1_000:
        return f"${n/1_000:.2f}K"
    return f"${n:.2f}"
