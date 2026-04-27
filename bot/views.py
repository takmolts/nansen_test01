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
    @discord.ui.button(label="📖 スコア詳細", style=discord.ButtonStyle.secondary)
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
            value = _format_breakdown(c.name, c.breakdown, c.note) or "-"
            value = _truncate(value, 1024)
            embed.add_field(
                name=f"{c.emoji} {c.name}: {c.score:.1f} / 100  (重み {c.weight*100:.1f}%)",
                value=value,
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
            text="スコアはあくまで参考値です。積極的な投資を推奨するものではありません"
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
        sh = breakdown.get("sm_holder_count")
        shs = breakdown.get("sm_holder_score")
        nf = breakdown.get("smart_trader_net_flow_usd")
        nfs = breakdown.get("sm_flow_score")
        nb = breakdown.get("new_buyers_count")
        nbs = breakdown.get("sm_new_buyer_score")
        if sh is not None:
            lines.append(f"・SM 保有ウォレット: {sh}件 → {_fmt(shs)} / 40pt")
        else:
            lines.append(f"・SM 保有ウォレット: 取得不可 → {_fmt(shs)} / 40pt")
        if nf is not None:
            sign = "+" if nf > 0 else ""
            lines.append(f"・SM ネットフロー: {sign}{_fmt_usd(nf)} → {_fmt(nfs)} / ±35pt")
        else:
            lines.append(f"・SM ネットフロー: 取得不可 → {_fmt(nfs)} / ±35pt")
        if nb is not None:
            lines.append(f"・SM 新規買い (直近 7d): {nb}件 → {_fmt(nbs)} / 25pt")
        else:
            lines.append(f"・SM 新規買い: 取得不可 → {_fmt(nbs)} / 25pt")

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
        ng = breakdown.get("holder_growth_24h_ratio")
        nhs = breakdown.get("nh_score")
        if th is not None:
            lines.append(f"・総ホルダー: {int(th):,} → {_fmt(ts)} / 35pt")
        if top10 is not None:
            lines.append(f"・Top10 集中度: {top10:.2f}% → {_fmt(t10s)} / 40pt")
        if ng is not None:
            sign = "+" if ng >= 0 else ""
            lines.append(f"・24h ホルダー増加率: {sign}{ng*100:.2f}% → {_fmt(nhs)} / 25pt")
        else:
            lines.append(f"・24h ホルダー増加率: 取得不可 → {_fmt(nhs)} / 25pt")

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

    elif name == "Narrative":
        n = breakdown.get("similar_recent_count")
        status = breakdown.get("status")
        sim = breakdown.get("sim_score")
        is_oldest = breakdown.get("is_oldest")
        elder = breakdown.get("elder_score")
        ds_b = breakdown.get("is_dexscreener_boosted")
        cg_t = breakdown.get("is_coingecko_trending")
        ts = breakdown.get("trend_score")
        sc = breakdown.get("social_count")
        ss = breakdown.get("social_score")

        lines.append(f"・類似トークン (7日内): {n} 件 / status=`{status}` → {_fmt(sim)} / 40pt")
        similar_tokens = breakdown.get("similar_tokens") or []
        if similar_tokens:
            lines.append(_format_similar_tokens(similar_tokens, limit=5))
        oldest_label = "✅" if is_oldest else "❌"
        lines.append(f"・自トークンが最古か: {oldest_label} → {_fmt(elder)} / 25pt")
        ds_label = "✅" if ds_b else ("?" if ds_b is None else "❌")
        cg_label = "✅" if cg_t else ("? (CoinGecko key 未設定)" if cg_t is None else "❌")
        lines.append(f"・DexScreener Boost: {ds_label} / CoinGecko Trending: {cg_label} → {_fmt(ts)} / 25pt")
        lines.append(f"・ソーシャル ({sc}/3 link) → {_fmt(ss)} / 10pt")

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
        ni_parts = [f"{k}={v}" for k, v in ni_summary.items() if v is not None]
        ni_label = ", ".join(ni_parts) if ni_parts else "Nansen 側で未計算"
        lines.append(f"・Nansen 独自リスク ({ni_label}) → {_fmt(nis)} / 25pt")

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
            cdc = breakdown.get("creator_deploy_count")
            sp = breakdown.get("serial_penalty") or 0
            if cdc is not None:
                serial_note = f" (シリアル -{sp}pt)" if sp else ""
                lines.append(f"・creator の他 deploy 数: {cdc} 件{serial_note}")
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


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _format_similar_tokens(tokens: list[dict], *, limit: int = 10) -> str:
    """類似トークンを ↳ プレフィクス付きで列挙する (Discord の bullet 解釈を回避)。"""
    lines: list[str] = []
    for t in tokens[:limit]:
        sym = (t.get("symbol") or "?").upper()
        addr = t.get("address") or ""
        short = f"{addr[:4]}...{addr[-4:]}" if len(addr) > 8 else addr
        url = f"https://solscan.io/token/{addr}" if addr else None
        link = f"[{short}]({url})" if url else short
        lines.append(f"　↳ `${sym}` {link}")
    if len(tokens) > limit:
        lines.append(f"　↳ ...他 {len(tokens) - limit} 件")
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
