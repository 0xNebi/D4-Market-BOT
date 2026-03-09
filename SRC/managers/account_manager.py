from __future__ import annotations

import uuid
import yaml
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

from SRC.config.settings import load_settings, load_defaults
from SRC.utils.logger import log

_BASE_CDP_PORT = 9222

class AccountConfig(BaseModel):
    id: str
    username: Optional[str] = None
    proxy: Optional[str] = None

    # Optional so accounts without these set don't override the global config.yaml values.
    use_ai: Optional[bool] = None
    use_templates: Optional[bool] = None
    ai_model: Optional[str] = None
    player_tag: Optional[str] = None
    cdp_url: Optional[str] = None

    discord_channel_id: Optional[int] = None

    cdp_port: int = 0
    session_dir: Optional[str] = None
    bot_enabled: bool = False

# Loads accounts from CONFIG/accounts.yaml. Hot-reload is detected by
# comparing string signatures of account ID lists, not timestamps.
class AccountManager:

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or (Path(__file__).parent.parent.parent / "CONFIG" / "accounts.yaml")
        self.accounts: List[AccountConfig] = []
        self._last_signature: str = ""

    def load(self) -> None:

        if self.config_path.exists():
            with open(self.config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            raw = data.get("accounts", [])
        else:

            settings = load_settings()
            raw = settings.accounts

        self.accounts = []
        for a in raw:
            try:

                if isinstance(a, AccountConfig):
                    self.accounts.append(a)
                else:
                    self.accounts.append(AccountConfig(**a))
            except Exception:
                continue

        new_sig = str([a.id for a in self.accounts])
        if new_sig != self._last_signature:
            self._last_signature = new_sig
            log.debug(f"AccountManager.load: {len(self.accounts)} account(s) loaded from {self.config_path}: {[a.id for a in self.accounts]}")

    def save(self) -> None:

        data = {"accounts": [a.model_dump() for a in self.accounts]}
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

    def get_proxy(self, account_id: str) -> Optional[str]:
        for a in self.accounts:
            if a.id == account_id:
                return a.proxy
        return None

    def next_available_port(self) -> int:
        """Return the next CDP port not already used by an existing account."""
        used = {a.cdp_port for a in self.accounts if a.cdp_port and a.cdp_port > 0}
        port = _BASE_CDP_PORT
        while port in used:
            port += 1
        return port

    def create_account(self, display_name: str) -> AccountConfig:
        """Create a new account with defaults from defaults.yaml.
        Account ID is left blank (temp internal UUID) — user must set it in config editor."""
        defaults = load_defaults()

        # Temp internal ID so saving/loading works; user must replace it before starting bot.
        temp_id = f"_new_{uuid.uuid4().hex[:8]}"

        # Resolve CDP port
        cdp_port_val = defaults.get("cdp_port", "auto")
        if cdp_port_val == "auto" or cdp_port_val is None:
            cdp_port = self.next_available_port()
        else:
            cdp_port = int(cdp_port_val)

        # Resolve CDP URL
        cdp_url_val = defaults.get("cdp_url", "auto")
        if cdp_url_val == "auto" or cdp_url_val is None:
            cdp_url = f"http://localhost:{cdp_port}"
        else:
            cdp_url = str(cdp_url_val)

        # Session dir is named after the display name and stays permanent
        _root = Path(__file__).parent.parent.parent
        session_dir = str(_root / "DATA" / "sessions" / display_name)

        acct = AccountConfig(
            id=temp_id,
            username=display_name,
            proxy=defaults.get("proxy"),
            cdp_port=cdp_port,
            cdp_url=cdp_url,
            discord_channel_id=defaults.get("discord_channel_id", 0) or None,
            use_ai=defaults.get("use_ai", False),
            use_templates=defaults.get("use_templates", True),
            ai_model=defaults.get("ai_model", "gemini-2.0-flash-lite"),
            bot_enabled=defaults.get("bot_enabled", False),
            session_dir=session_dir,
        )

        self.accounts.append(acct)
        self.save()
        log.info(f"Created account [{acct.id}] with display name '{display_name}'")
        return acct
