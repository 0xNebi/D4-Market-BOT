import os
import yaml
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

_SRC_DIR = Path(__file__).parent.parent
_ROOT_DIR = _SRC_DIR.parent
_ENV_FILE = _ROOT_DIR / "CONFIG" / ".env"
_YAML_FILE = _ROOT_DIR / "CONFIG" / "config.yaml"
_DEFAULTS_FILE = _ROOT_DIR / "CONFIG" / "defaults.yaml"

class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Secrets (loaded from .env) ──────────────────────────────────────────
    discord_bot_token: str = ""
    google_api_key: str = ""

    # ── Per-account fields (populated by build_account_settings) ────────────
    battletag: str = "YourTag#1234"
    my_user_id: str = ""
    username: str = ""
    cdp_url: str = "http://localhost:9222"

    # ── Global settings (loaded from config.yaml) ───────────────────────────
    discord_channel_id: int = 0

    gemini_model: str = "gemini-2.0-flash-lite"

    target_url: str = "https://diablo.trade"

    check_interval: int = 10
    restart_on_error: bool = True
    max_retries: int = 3
    debug_mode: bool = False

    auto_accept_bnet_reveal: bool = True

    message_batch_window: float = 5.0

    hold_expiry_seconds: int = 7200

    inventory_refresh_interval: int = 300

    use_ai: bool = False
    use_templates: bool = True
    ai_threshold: str = "complex"
    min_delay_ms: int = 800
    max_delay_ms: int = 2500

    template_ready_to_buy: str = (
        "yeah item is ready, add me {battletag}"
    )
    template_price_inquiry: str = (
        "looking for {price} gold for that one. {battletag} if interested"
    )
    template_still_available: str = (
        "yep still available, {battletag}"
    )
    template_lowball_decline: str = (
        "nah sorry cant go that low, price is firm on that one"
    )
    template_unknown: str = (
        "hey, lmk what item ur looking at and ill check. {battletag}"
    )
    template_item_reserved: str = (
        "that one is reserved for someone rn, ill msg u if it falls through"
    )

    control_server_enabled: bool = True
    control_server_port: int = 8080

    log_level: str = "INFO"
    log_rotation: str = "1 day"
    log_retention: str = "7 days"
    log_dir: str = str(_ROOT_DIR / "DATA" / "logs")

    discord_enabled: bool = True
    discord_notify_on_offer: bool = True
    discord_daily_summary: bool = True
    summary_time: str = "23:59"

    accounts: List[dict] = []

    db_path: str = str(_ROOT_DIR / "DATA" / "bot.db")

    price_auto_decline_below_pct: float = 50.0
    price_auto_accept_above_pct: float = 90.0

def load_settings() -> Settings:

    settings = Settings()

    if _YAML_FILE.exists():
        with open(_YAML_FILE, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        bot     = cfg.get("bot", {})
        browser = cfg.get("browser", {})
        reply   = cfg.get("reply", {})
        logging = cfg.get("logging", {})
        notif   = cfg.get("notifications", {})
        store   = cfg.get("storage", {})
        prices  = cfg.get("price_rules", {})
        accounts = cfg.get("accounts") or []

        settings.check_interval    = bot.get("check_interval",    settings.check_interval)
        settings.restart_on_error  = bot.get("restart_on_error",  settings.restart_on_error)
        settings.max_retries       = bot.get("max_retries",        settings.max_retries)
        settings.debug_mode        = bot.get("debug_mode",         settings.debug_mode)

        settings.auto_accept_bnet_reveal = bot.get("auto_accept_bnet_reveal", settings.auto_accept_bnet_reveal)

        settings.message_batch_window = bot.get("message_batch_window", settings.message_batch_window)

        settings.hold_expiry_seconds = bot.get("hold_expiry_seconds", settings.hold_expiry_seconds)

        settings.inventory_refresh_interval = bot.get("inventory_refresh_interval", settings.inventory_refresh_interval)

        settings.target_url = browser.get("target_url", settings.target_url)

        settings.use_ai         = reply.get("use_ai",         settings.use_ai)
        settings.use_templates  = reply.get("use_templates",  settings.use_templates)
        settings.ai_threshold   = reply.get("ai_threshold",   settings.ai_threshold)
        settings.gemini_model   = reply.get("gemini_model",   settings.gemini_model)
        settings.min_delay_ms   = reply.get("min_delay_ms",   settings.min_delay_ms)
        settings.max_delay_ms   = reply.get("max_delay_ms",   settings.max_delay_ms)
        settings.template_ready_to_buy    = reply.get("template_ready_to_buy",    settings.template_ready_to_buy)
        settings.template_price_inquiry   = reply.get("template_price_inquiry",   settings.template_price_inquiry)
        settings.template_still_available = reply.get("template_still_available", settings.template_still_available)
        settings.template_lowball_decline = reply.get("template_lowball_decline", settings.template_lowball_decline)
        settings.template_unknown         = reply.get("template_unknown",         settings.template_unknown)
        settings.template_item_reserved   = reply.get("template_item_reserved",   settings.template_item_reserved)

        ctrl = cfg.get("control_server", {})
        settings.control_server_enabled = ctrl.get("enabled", settings.control_server_enabled)
        settings.control_server_port    = ctrl.get("port",    settings.control_server_port)

        settings.log_level     = logging.get("level",     settings.log_level)
        settings.log_rotation  = logging.get("rotation",  settings.log_rotation)
        settings.log_retention = logging.get("retention", settings.log_retention)
        _raw_log_dir = logging.get("log_dir", "")
        if _raw_log_dir:
            _p = Path(_raw_log_dir)
            settings.log_dir = str(_p if _p.is_absolute() else _ROOT_DIR / _p)

        settings.discord_enabled          = notif.get("discord_enabled",          settings.discord_enabled)
        settings.discord_notify_on_offer  = notif.get("discord_notify_on_offer",  settings.discord_notify_on_offer)
        settings.discord_daily_summary    = notif.get("discord_daily_summary",    settings.discord_daily_summary)
        settings.summary_time             = notif.get("summary_time",             settings.summary_time)

        discord_cfg = cfg.get("discord_bot", {})

        _yaml_ch_id = discord_cfg.get("channel_id", 0)
        if _yaml_ch_id:
            settings.discord_channel_id = _yaml_ch_id

        parsed_accounts = []
        for a in accounts:
            try:
                parsed_accounts.append(
                    {
                        "id": a.get("id", ""),
                        "username": a.get("username"),
                        "proxy": a.get("proxy"),
                    }
                )
            except Exception:
                pass

        settings.accounts = parsed_accounts

        _raw_db = store.get("db_path", "")
        if _raw_db:
            _p = Path(_raw_db)
            settings.db_path = str(_p if _p.is_absolute() else _ROOT_DIR / _p)

        settings.price_auto_decline_below_pct = prices.get("auto_decline_below_pct", settings.price_auto_decline_below_pct)
        settings.price_auto_accept_above_pct  = prices.get("auto_accept_above_pct",  settings.price_auto_accept_above_pct)

    return settings

# Per-account override: copies base settings and applies account-level fields.
# Keeps the global config as the source of truth for everything not overridden.
def build_account_settings(base: Settings, acct) -> Settings:

    overrides: dict = {}
    if getattr(acct, 'username', None):
        overrides['username'] = acct.username
    if getattr(acct, 'cdp_url', None):
        overrides['cdp_url'] = acct.cdp_url
    if getattr(acct, 'player_tag', None) and acct.player_tag != 'auto':
        overrides['battletag'] = acct.player_tag
    if getattr(acct, 'use_ai', None) is not None:
        overrides['use_ai'] = acct.use_ai
    if getattr(acct, 'use_templates', None) is not None:
        overrides['use_templates'] = acct.use_templates
    if getattr(acct, 'ai_model', None):
        overrides['gemini_model'] = acct.ai_model
    if getattr(acct, 'discord_channel_id', None):
        overrides['discord_channel_id'] = acct.discord_channel_id
    return base.model_copy(update=overrides)


def load_defaults() -> dict:
    """Load new-account default values from CONFIG/defaults.yaml."""
    if _DEFAULTS_FILE.exists():
        with open(_DEFAULTS_FILE, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("new_account_defaults", {})
    return {}
