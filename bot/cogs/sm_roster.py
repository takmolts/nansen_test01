"""Smart Money roster の日次取得 cog。

役割:
- bot 内 asyncio タスクで JST 指定時刻に 1 日 1 回 `/smart-money/dex-trades` を叩く
- 結果を `sm_roster` テーブルに upsert (Helius 登録候補ロスター)
- /sm-roster-fetch (今すぐ取得) と /sm-roster-list (DB 表示) の手動コマンド

確定済 filter 構成 (会話で合意済、 1 call = 推定 5 credit):
    chains              : ["solana"]
    include             : ["Fund", "180D Smart Trader"]
    exclude             : ["30D Smart Trader"]
    token_bought_age    : 1〜30 日
    trade_value_usd     : >= 300
    per_page            : 100  (24h 全件で 100 未満なので 1 call 完結を確認済)
    order               : block_timestamp DESC
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Config
from bot.helius_webhook import HeliusWebhookClient, HeliusWebhookError
from bot.nansen_client import NansenClient
from bot.wallet_db import WalletDB

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")

ROSTER_CHAIN = "solana"
ROSTER_INCLUDE_LABELS = ["Fund", "180D Smart Trader"]
ROSTER_EXCLUDE_LABELS = ["30D Smart Trader"]
ROSTER_TOKEN_AGE_MIN = 1
ROSTER_TOKEN_AGE_MAX = 30
ROSTER_TRADE_VALUE_USD_MIN = 300
ROSTER_PER_PAGE = 100


def _parse_hhmm(raw: str) -> time:
    """'00:30' のような文字列を JST 付き time に変換。 不正なら 00:30 にフォールバック。"""
    try:
        parts = raw.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError
        return time(h, m, tzinfo=JST)
    except Exception:
        logger.warning("SM_ROSTER_FETCH_TIME_JST=%r が不正なので 00:30 を使用", raw)
        return time(0, 30, tzinfo=JST)


def _short(addr: str | None) -> str:
    if not isinstance(addr, str) or not addr:
        return "-"
    if len(addr) <= 10:
        return addr
    return f"{addr[:4]}…{addr[-4:]}"


def _aggregate_roster(data: list[dict]) -> list[dict]:
    """trader_address ごとに 24h trade を集計。 scripts/probe_sm_roster.py と同形。"""
    by_wallet: dict[str, dict] = {}
    for r in data:
        if not isinstance(r, dict):
            continue
        w = r.get("trader_address")
        if not isinstance(w, str) or not w:
            continue
        slot = by_wallet.setdefault(
            w,
            {
                "wallet_address": w,
                "trade_count": 0,
                "sum_trade_value_usd": 0.0,
                "max_trade_value_usd": 0.0,
                "last_seen": "",
                "first_seen": "",
                "label": "",
                "bought_tokens": defaultdict(int),
            },
        )
        slot["trade_count"] += 1
        v = r.get("trade_value_usd")
        try:
            v_f = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            v_f = 0.0
        slot["sum_trade_value_usd"] += v_f
        if v_f > slot["max_trade_value_usd"]:
            slot["max_trade_value_usd"] = v_f
        ts = r.get("block_timestamp") or ""
        if not slot["last_seen"] or ts > slot["last_seen"]:
            slot["last_seen"] = ts
        if not slot["first_seen"] or ts < slot["first_seen"]:
            slot["first_seen"] = ts
        lbl = r.get("trader_address_label")
        if isinstance(lbl, str) and lbl and not slot["label"]:
            slot["label"] = lbl
        bs = r.get("token_bought_symbol")
        if isinstance(bs, str) and bs:
            slot["bought_tokens"][bs] += 1
    rows = list(by_wallet.values())
    rows.sort(key=lambda x: (x["trade_count"], x["sum_trade_value_usd"]), reverse=True)
    return rows


class SmRosterCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config):
        self.bot = bot
        self.config = config
        self._auto_task: asyncio.Task | None = None
        self._fetch_lock = asyncio.Lock()
        self._fetch_time: time = _parse_hhmm(config.sm_roster_fetch_time_jst)

        if config.sm_roster_auto_enabled:
            self._auto_task = bot.loop.create_task(self._daily_loop())
            logger.info(
                "sm_roster auto-loop 起動 (JST %02d:%02d)",
                self._fetch_time.hour, self._fetch_time.minute,
            )
        else:
            logger.info("sm_roster auto-loop は DISABLED (SM_ROSTER_AUTO_ENABLED=false)")

    def cog_unload(self) -> None:
        if self._auto_task and not self._auto_task.done():
            self._auto_task.cancel()

    # ---- 日次自動 loop ----

    async def _daily_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.now(JST)
            target = now.replace(
                hour=self._fetch_time.hour,
                minute=self._fetch_time.minute,
                second=0,
                microsecond=0,
            )
            if target <= now:
                target += timedelta(days=1)
            sleep_sec = (target - now).total_seconds()
            logger.info(
                "[sm_roster] 次回自動取得まで %.0f 秒待機 (target=%s)",
                sleep_sec, target.isoformat(timespec="seconds"),
            )
            try:
                await asyncio.sleep(sleep_sec)
            except asyncio.CancelledError:
                logger.info("[sm_roster] auto-loop キャンセル")
                return

            try:
                summary = await self._fetch_and_store(tag="auto")
                await self._notify_auto(summary)
            except Exception:
                logger.exception("[sm_roster] 自動取得失敗")
                continue

            if self.config.helius_webhook_auto_sync:
                try:
                    sync_summary = await self._sync_helius(tag="auto")
                    await self._notify_helius_auto(sync_summary)
                except Exception:
                    logger.exception("[sm_roster] Helius 自動 sync 失敗")

    async def _notify_auto(self, summary: dict[str, Any]) -> None:
        await self._notify_to_channel(_build_summary_embed(summary, title_suffix="(自動)"))

    async def _notify_helius_auto(self, sync_summary: dict[str, Any]) -> None:
        await self._notify_to_channel(
            _build_helius_sync_embed(sync_summary, title_suffix="(自動)")
        )

    async def _notify_to_channel(self, embed: discord.Embed) -> None:
        ch_id = self.config.sm_roster_notify_channel_id
        if not ch_id:
            return
        channel = self.bot.get_channel(ch_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ch_id)
            except Exception:
                logger.exception("[sm_roster] notify channel 取得失敗 id=%s", ch_id)
                return
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            await channel.send(embed=embed)
        except Exception:
            logger.exception("[sm_roster] 通知投稿失敗")

    # ---- 共通 fetch ----

    async def _fetch_and_store(self, *, tag: str) -> dict[str, Any]:
        """API → 集計 → DB upsert。 サマリ dict を返す。"""
        async with self._fetch_lock:
            async with NansenClient(
                self.config.nansen_api_key,
                self.config.nansen_base_url,
                chain=ROSTER_CHAIN,
            ) as client:
                resp = await client.smart_money_dex_trades(
                    chains=[ROSTER_CHAIN],
                    include_labels=ROSTER_INCLUDE_LABELS,
                    exclude_labels=ROSTER_EXCLUDE_LABELS,
                    per_page=ROSTER_PER_PAGE,
                    order_field="block_timestamp",
                    order_direction="DESC",
                    extra_filters={
                        "token_bought_age_days": {
                            "min": ROSTER_TOKEN_AGE_MIN,
                            "max": ROSTER_TOKEN_AGE_MAX,
                        },
                        "trade_value_usd": {"min": ROSTER_TRADE_VALUE_USD_MIN},
                    },
                )
                credits_used = client.credits_used

            data = resp.get("data") if isinstance(resp, dict) else None
            valid = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
            pagination = resp.get("pagination") if isinstance(resp, dict) else None
            is_last = bool(pagination.get("is_last_page")) if isinstance(pagination, dict) else False

            rows = _aggregate_roster(valid)
            inserted = 0
            updated = 0
            evicted = 0
            total = 0
            unregistered = 0
            async with WalletDB() as db:
                if rows:
                    inserted, updated = await db.upsert_sm_roster(rows)
                evicted = await db.prune_sm_roster(self.config.sm_roster_max_wallets)
                total, unregistered = await db.sm_roster_count()

            logger.info(
                "[sm_roster:%s] records=%d uniq=%d insert=%d update=%d evict=%d "
                "total=%d unregistered=%d credit=%d is_last=%s max=%d",
                tag, len(valid), len(rows), inserted, updated, evicted,
                total, unregistered, credits_used, is_last,
                self.config.sm_roster_max_wallets,
            )
            return {
                "tag": tag,
                "records": len(valid),
                "unique_wallets": len(rows),
                "inserted": inserted,
                "updated": updated,
                "evicted": evicted,
                "total": total,
                "unregistered": unregistered,
                "credits_used": credits_used,
                "is_last_page": is_last,
                "max_wallets": self.config.sm_roster_max_wallets,
                "top_rows": rows[:10],
            }

    # ---- Helius sync ----

    async def _sync_helius(self, *, tag: str) -> dict[str, Any]:
        """sm_roster の全 wallet を Helius webhook の accountAddresses として同期する。

        既存 webhook (URL マッチ) があれば PUT、 無ければ POST。
        成功後、 全 wallet を helius_registered=1 にマーク。
        """
        if not self.config.helius_api_key:
            raise RuntimeError("HELIUS_API_KEY が未設定です")
        if not self.config.helius_webhook_url:
            raise RuntimeError("HELIUS_WEBHOOK_URL が未設定です")

        async with WalletDB() as db:
            wallets = await db.list_all_sm_wallets()
            unregistered_before = await db.list_unregistered_sm_wallets()

        if not wallets:
            logger.info("[sm_roster:%s] sm_roster が空。 Helius sync スキップ", tag)
            return {
                "tag": tag,
                "action": "skipped",
                "reason": "roster empty",
                "wallet_count": 0,
                "newly_registered": 0,
                "added": [],
                "removed": [],
                "webhook_id": None,
                "webhook_url": self.config.helius_webhook_url,
            }

        async with HeliusWebhookClient(self.config.helius_api_key) as client:
            existing = await client.find_by_url(self.config.helius_webhook_url)
            existing_addrs: set[str] = set()
            if existing:
                aa = existing.get("accountAddresses")
                if isinstance(aa, list):
                    existing_addrs = {a for a in aa if isinstance(a, str)}

            new_addrs = set(wallets)
            added = sorted(new_addrs - existing_addrs)
            removed = sorted(existing_addrs - new_addrs)

            result, action, _ = await client.upsert_webhook(
                webhook_url=self.config.helius_webhook_url,
                account_addresses=wallets,
                webhook_type=self.config.helius_webhook_type,
                transaction_types=list(self.config.helius_webhook_transaction_types),
                auth_header=self.config.helius_webhook_auth_header,
            )

        webhook_id = (
            result.get("webhookID") or result.get("webhook_id") or result.get("id")
        )

        async with WalletDB() as db:
            newly_registered = await db.mark_sm_helius_registered(unregistered_before)

        logger.info(
            "[sm_roster:%s] Helius %s ok wallets=%d added=%d removed=%d "
            "newly_registered=%d webhook_id=%s",
            tag, action, len(wallets), len(added), len(removed),
            newly_registered, webhook_id,
        )
        return {
            "tag": tag,
            "action": action,
            "reason": None,
            "wallet_count": len(wallets),
            "newly_registered": newly_registered,
            "added": added,
            "removed": removed,
            "webhook_id": webhook_id,
            "webhook_url": self.config.helius_webhook_url,
        }

    # ---- Slash commands ----

    @app_commands.command(
        name="sm-roster-fetch",
        description="Smart Money roster を今すぐ Nansen から取得して DB に蓄積します",
    )
    async def sm_roster_fetch(self, interaction: discord.Interaction):
        if (
            self.config.allowed_channel_ids
            and interaction.channel_id not in self.config.allowed_channel_ids
        ):
            await interaction.response.send_message(
                "このチャネルではコマンドが許可されていません。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)
        try:
            summary = await self._fetch_and_store(tag="manual")
        except Exception:
            logger.exception("/sm-roster-fetch 失敗")
            await interaction.followup.send(
                "SM roster 取得でエラーが発生しました。 ログを確認してください。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(embed=_build_summary_embed(summary, title_suffix="(手動)"))

    @app_commands.command(
        name="sm-helius-sync",
        description="sm_roster の全 wallet を Helius webhook に同期します",
    )
    async def sm_helius_sync(self, interaction: discord.Interaction):
        if (
            self.config.allowed_channel_ids
            and interaction.channel_id not in self.config.allowed_channel_ids
        ):
            await interaction.response.send_message(
                "このチャネルではコマンドが許可されていません。",
                ephemeral=True,
            )
            return
        if not self.config.helius_api_key or not self.config.helius_webhook_url:
            await interaction.response.send_message(
                "HELIUS_API_KEY か HELIUS_WEBHOOK_URL が未設定です。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)
        try:
            sync_summary = await self._sync_helius(tag="manual")
        except HeliusWebhookError as e:
            logger.warning("/sm-helius-sync Helius API エラー: %s", e)
            await interaction.followup.send(f"Helius API エラー: `{e}`", ephemeral=True)
            return
        except Exception as e:
            logger.exception("/sm-helius-sync 失敗")
            await interaction.followup.send(
                f"想定外のエラーが発生しました: `{e!r}`",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=_build_helius_sync_embed(sync_summary, title_suffix="(手動)")
        )

    @app_commands.command(
        name="sm-roster-list",
        description="蓄積済の Smart Money roster を表示します",
    )
    @app_commands.describe(
        order_by="ソート軸",
        limit="表示件数 (1〜25、 省略時 15)",
        only_unregistered="Helius 未登録のみ表示するか (省略時 false)",
    )
    @app_commands.choices(
        order_by=[
            app_commands.Choice(name="last_seen 最新順", value="last_seen_at"),
            app_commands.Choice(name="観測回数 多い順", value="total_observations"),
            app_commands.Choice(name="直近 24h sum_usd 大きい順", value="last_trade_sum_usd"),
        ]
    )
    async def sm_roster_list(
        self,
        interaction: discord.Interaction,
        order_by: app_commands.Choice[str] | None = None,
        limit: int = 15,
        only_unregistered: bool = False,
    ):
        if (
            self.config.allowed_channel_ids
            and interaction.channel_id not in self.config.allowed_channel_ids
        ):
            await interaction.response.send_message(
                "このチャネルではコマンドが許可されていません。",
                ephemeral=True,
            )
            return
        order = order_by.value if order_by else "last_seen_at"
        limit = max(1, min(int(limit), 25))
        await interaction.response.defer(thinking=True)
        try:
            async with WalletDB() as db:
                rows = await db.list_sm_roster(
                    order_by=order, limit=limit, only_unregistered=only_unregistered
                )
                total, unregistered = await db.sm_roster_count()
        except Exception:
            logger.exception("/sm-roster-list 失敗")
            await interaction.followup.send(
                "DB アクセスでエラーが発生しました。",
                ephemeral=True,
            )
            return
        if not rows:
            await interaction.followup.send(
                f"roster はまだ蓄積されていません (total={total})。",
                ephemeral=True,
            )
            return
        embed = _build_list_embed(
            rows=rows,
            order_by=order,
            total=total,
            unregistered=unregistered,
            only_unregistered=only_unregistered,
        )
        await interaction.followup.send(embed=embed)


def _build_summary_embed(summary: dict[str, Any], *, title_suffix: str = "") -> discord.Embed:
    color = 0x4CAF50 if summary["unique_wallets"] > 0 else 0x9E9E9E
    embed = discord.Embed(
        title=f"🛰️ SM roster 取得結果 {title_suffix}".strip(),
        color=color,
    )
    embed.add_field(
        name="今回の取得",
        value=(
            f"trades: **{summary['records']}** / unique wallet: **{summary['unique_wallets']}**\n"
            f"insert: **{summary['inserted']}** / update: **{summary['updated']}** / "
            f"evict: **{summary.get('evicted', 0)}**\n"
            f"is_last_page: `{summary['is_last_page']}` / credit (推定): +{summary['credits_used']}"
        ),
        inline=False,
    )
    max_w = summary.get("max_wallets", 0)
    cap_str = f"{max_w}" if max_w and max_w > 0 else "無制限"
    embed.add_field(
        name="DB 累計",
        value=(
            f"total: **{summary['total']}** / 上限: **{cap_str}** / "
            f"Helius 未登録: **{summary['unregistered']}**"
        ),
        inline=False,
    )
    top = summary.get("top_rows") or []
    if top:
        lines = []
        for i, r in enumerate(top, 1):
            bought = ",".join(
                f"{sym}×{n}"
                for sym, n in sorted(r["bought_tokens"].items(), key=lambda x: -x[1])[:2]
            )
            lines.append(
                f"`{i:>2}` `{_short(r['wallet_address'])}` "
                f"cnt={r['trade_count']} "
                f"sum=${r['sum_trade_value_usd']:,.0f} "
                f"{bought}"
            )
        embed.add_field(name="今回 top 10", value="\n".join(lines), inline=False)
    return embed


def _build_helius_sync_embed(summary: dict[str, Any], *, title_suffix: str = "") -> discord.Embed:
    action = summary.get("action", "?")
    color = {
        "created": 0x4CAF50,
        "updated": 0x2196F3,
        "skipped": 0x9E9E9E,
    }.get(action, 0xFFC107)
    embed = discord.Embed(
        title=f"🛰️ Helius webhook sync {title_suffix}".strip(),
        color=color,
    )
    embed.add_field(
        name="アクション",
        value=f"**{action}**" + (
            f"\n理由: {summary.get('reason')}" if summary.get("reason") else ""
        ),
        inline=False,
    )
    embed.add_field(
        name="状態",
        value=(
            f"webhook_id: `{summary.get('webhook_id') or '-'}`\n"
            f"target URL: `{summary.get('webhook_url') or '-'}`\n"
            f"登録 wallet 数: **{summary.get('wallet_count', 0)}**\n"
            f"今回 newly_registered: **{summary.get('newly_registered', 0)}**"
        ),
        inline=False,
    )
    added = summary.get("added") or []
    removed = summary.get("removed") or []
    diff_lines = [
        f"➕ added: **{len(added)}**" + (
            "" if not added else f" (例: {', '.join(_short(a) for a in added[:5])}{'…' if len(added) > 5 else ''})"
        ),
        f"➖ removed: **{len(removed)}**" + (
            "" if not removed else f" (例: {', '.join(_short(a) for a in removed[:5])}{'…' if len(removed) > 5 else ''})"
        ),
    ]
    embed.add_field(name="差分 (前回 webhook 比)", value="\n".join(diff_lines), inline=False)
    return embed


def _build_list_embed(
    *,
    rows,
    order_by: str,
    total: int,
    unregistered: int,
    only_unregistered: bool,
) -> discord.Embed:
    label = {
        "last_seen_at": "最新観測順",
        "total_observations": "観測回数",
        "last_trade_sum_usd": "直近 sum_usd",
    }.get(order_by, order_by)
    title = f"🗂️ SM roster ({label})"
    desc_lines = [f"DB total: **{total}** / Helius 未登録: **{unregistered}**"]
    if only_unregistered:
        desc_lines.append("(Helius 未登録のみ)")
    embed = discord.Embed(title=title, description="\n".join(desc_lines), color=0x2196F3)

    for i, r in enumerate(rows, start=1):
        addr = r["wallet_address"]
        nansen_url = f"https://app.nansen.ai/profiler/{addr}?chain=solana"
        solscan_url = f"https://solscan.io/account/{addr}"
        last_seen = (r["last_seen_at"] or "")[:16]
        last_label = r["last_label"] or "-"
        obs = r["total_observations"]
        cnt = r["last_trade_count_24h"]
        sum_usd = r["last_trade_sum_usd"]
        max_usd = r["last_trade_max_usd"]
        bought = r["last_bought_top"] or "-"
        registered = "✅" if r["helius_registered"] else "⏳"
        head = f"#{i} {_short(addr)} {registered}"
        value_lines = [
            f"📍 [solscan]({solscan_url}) · [Nansen]({nansen_url})",
            f"🪪 {last_label} | 🔁 obs: **{obs}**",
            f"📊 24h: cnt={cnt} sum=${(sum_usd or 0):,.0f} max=${(max_usd or 0):,.0f}",
            f"🛒 buy: {bought}",
            f"🕒 last seen: {last_seen}",
        ]
        embed.add_field(name=head, value="\n".join(value_lines), inline=False)
    return embed


async def setup(bot: commands.Bot) -> None:
    config: Config = bot.config  # type: ignore[attr-defined]
    await bot.add_cog(SmRosterCog(bot, config))
