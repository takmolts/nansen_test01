"""環境変数 (.env) から bot 設定を読み込む。"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


RESPONSE_MODE_INLINE = "inline"
RESPONSE_MODE_THREAD = "thread"
_VALID_RESPONSE_MODES = {RESPONSE_MODE_INLINE, RESPONSE_MODE_THREAD}


@dataclass(frozen=True)
class Config:
    discord_bot_token: str
    nansen_api_key: str
    nansen_base_url: str
    solana_rpc_url: str
    coingecko_api_key: str | None
    helius_api_key: str | None
    enable_helius: bool
    enable_coingecko: bool
    digest_channel_id: int | None
    digest_archive_thread_id: int | None
    digest_auto_4h_enabled: bool
    digest_auto_daily_enabled: bool
    sm_roster_auto_enabled: bool
    sm_roster_fetch_time_jst: str
    sm_roster_notify_channel_id: int | None
    sm_roster_max_wallets: int
    helius_webhook_url: str | None
    helius_webhook_type: str
    helius_webhook_transaction_types: tuple[str, ...]
    helius_webhook_auth_header: str | None
    helius_webhook_auto_sync: bool
    allowed_channel_ids: frozenset[int]
    dev_guild_id: int | None
    response_mode: str
    log_level: str

    @property
    def helius_active(self) -> bool:
        return self.enable_helius and bool(self.helius_api_key)

    @property
    def coingecko_active(self) -> bool:
        return self.enable_coingecko and bool(self.coingecko_api_key)

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()

        token = _require("DISCORD_BOT_TOKEN")
        nansen_key = _require("NANSEN_API_KEY")
        base_url = os.getenv("NANSEN_BASE_URL", "https://api.nansen.ai").rstrip("/")
        solana_rpc = os.getenv(
            "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
        ).rstrip("/")

        coingecko_key_raw = os.getenv("COINGECKO_API_KEY", "").strip()
        coingecko_key = coingecko_key_raw or None

        helius_key_raw = os.getenv("HELIUS_API_KEY", "").strip()
        helius_key = helius_key_raw or None

        enable_helius = _parse_bool(os.getenv("ENABLE_HELIUS"), default=True)
        enable_coingecko = _parse_bool(os.getenv("ENABLE_COINGECKO"), default=True)

        raw_digest_ch = os.getenv("DIGEST_CHANNEL_ID", "").strip()
        digest_channel_id = int(raw_digest_ch) if raw_digest_ch else None

        raw_archive_thread = os.getenv("DIGEST_ARCHIVE_THREAD_ID", "").strip()
        digest_archive_thread_id = int(raw_archive_thread) if raw_archive_thread else None

        # digest 自動投稿の個別 ON/OFF。 デフォは両方 OFF (手動 /digest のみ)。
        digest_auto_4h_enabled = _parse_bool(os.getenv("DIGEST_AUTO_4H_ENABLED"), default=False)
        digest_auto_daily_enabled = _parse_bool(
            os.getenv("DIGEST_AUTO_DAILY_ENABLED"), default=False
        )

        # SM roster の自動取得 (1 日 1 回、 JST 指定時刻)。
        sm_roster_auto_enabled = _parse_bool(os.getenv("SM_ROSTER_AUTO_ENABLED"), default=True)
        sm_roster_fetch_time_jst = os.getenv("SM_ROSTER_FETCH_TIME_JST", "00:30").strip() or "00:30"
        raw_roster_notify = os.getenv("SM_ROSTER_NOTIFY_CHANNEL_ID", "").strip()
        sm_roster_notify_channel_id = int(raw_roster_notify) if raw_roster_notify else None

        # roster の保持上限 (last_seen 古い順に超過分を prune)。 0 以下で無制限。
        raw_max = os.getenv("SM_ROSTER_MAX_WALLETS", "500").strip()
        try:
            sm_roster_max_wallets = int(raw_max) if raw_max else 500
        except ValueError:
            raise RuntimeError(
                f"SM_ROSTER_MAX_WALLETS は整数で指定してください (現在: {raw_max!r})"
            )

        # Helius webhook (sm_roster と Helius を繋ぐ送信先設定)。
        helius_webhook_url = (os.getenv("HELIUS_WEBHOOK_URL", "").strip() or None)
        helius_webhook_type = (
            os.getenv("HELIUS_WEBHOOK_TYPE", "enhanced").strip().lower() or "enhanced"
        )
        if helius_webhook_type not in ("enhanced", "raw"):
            raise RuntimeError(
                f"HELIUS_WEBHOOK_TYPE は 'enhanced' / 'raw' のいずれか (現在: {helius_webhook_type!r})"
            )
        raw_tx_types = os.getenv("HELIUS_WEBHOOK_TRANSACTION_TYPES", "SWAP").strip()
        helius_webhook_transaction_types = tuple(
            x.strip().upper() for x in raw_tx_types.split(",") if x.strip()
        ) or ("SWAP",)
        helius_webhook_auth_header = os.getenv("HELIUS_WEBHOOK_AUTH_HEADER", "").strip() or None
        helius_webhook_auto_sync = _parse_bool(
            os.getenv("HELIUS_WEBHOOK_AUTO_SYNC"), default=False
        )

        raw_channels = os.getenv("ALLOWED_CHANNEL_IDS", "").strip()
        channels: frozenset[int] = frozenset()
        if raw_channels:
            channels = frozenset(
                int(x.strip()) for x in raw_channels.split(",") if x.strip()
            )

        raw_guild = os.getenv("DEV_GUILD_ID", "").strip()
        dev_guild_id = int(raw_guild) if raw_guild else None

        raw_mode = os.getenv("RESPONSE_MODE", RESPONSE_MODE_INLINE).strip().lower()
        if raw_mode not in _VALID_RESPONSE_MODES:
            raise RuntimeError(
                f"RESPONSE_MODE は {sorted(_VALID_RESPONSE_MODES)} のいずれかにしてください (現在値: {raw_mode!r})"
            )

        log_level = os.getenv("LOG_LEVEL", "INFO").upper()

        return cls(
            discord_bot_token=token,
            nansen_api_key=nansen_key,
            nansen_base_url=base_url,
            solana_rpc_url=solana_rpc,
            coingecko_api_key=coingecko_key,
            helius_api_key=helius_key,
            enable_helius=enable_helius,
            enable_coingecko=enable_coingecko,
            digest_channel_id=digest_channel_id,
            digest_archive_thread_id=digest_archive_thread_id,
            digest_auto_4h_enabled=digest_auto_4h_enabled,
            digest_auto_daily_enabled=digest_auto_daily_enabled,
            sm_roster_auto_enabled=sm_roster_auto_enabled,
            sm_roster_fetch_time_jst=sm_roster_fetch_time_jst,
            sm_roster_notify_channel_id=sm_roster_notify_channel_id,
            sm_roster_max_wallets=sm_roster_max_wallets,
            helius_webhook_url=helius_webhook_url,
            helius_webhook_type=helius_webhook_type,
            helius_webhook_transaction_types=helius_webhook_transaction_types,
            helius_webhook_auth_header=helius_webhook_auth_header,
            helius_webhook_auto_sync=helius_webhook_auto_sync,
            allowed_channel_ids=channels,
            dev_guild_id=dev_guild_id,
            response_mode=raw_mode,
            log_level=log_level,
        )


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"環境変数 {key} が設定されていません (.env を確認してください)")
    return value


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    s = raw.strip().lower()
    if not s:
        return default
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return default
