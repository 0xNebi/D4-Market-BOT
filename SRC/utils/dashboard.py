import asyncio
import os
import shutil
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from SRC.managers.account_manager import AccountConfig, AccountManager
from SRC.managers.session_manager import SessionManager

from rich.console import Console, Group
from SRC.utils.logger import log
from rich.layout import Layout
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

_LOG_BUFFER: deque = deque(maxlen=200)

def log_sink(message) -> None:

    record   = message.record

    if record["extra"].get("ai_log"):
        return
    time_str = record["time"].strftime("%H:%M:%S")
    level    = record["level"].name

    text = record["message"].replace("\n", " ↵ ").replace("\r", "")

    if len(text) > 200:
        text = text[:197] + "..."

    if _LOG_BUFFER and _LOG_BUFFER[-1][2] == text:
        ts0, lvl0, txt0, cnt0 = _LOG_BUFFER[-1]
        _LOG_BUFFER[-1] = (time_str, lvl0, txt0, cnt0 + 1)
    else:
        _LOG_BUFFER.append((time_str, level, text, 1))

_LEVEL_COLORS = {
    "SUCCESS":  "bright_green",
    "INFO":     "bright_white",
    "WARNING":  "yellow",
    "ERROR":    "bright_red",
    "DEBUG":    "grey50",
    "CRITICAL": "bold red",
}

_STATUS_COLORS = {
    "STARTING": "yellow",
    "RUNNING":  "bright_green",
    "ERROR":    "bright_red",
    "STOPPED":  "dim",
}

_ACCOUNT_FIELDS = [
    ("id",              "Account ID",        "str"),
    ("username",        "Display Name",      "str"),
    ("player_tag",      "BattleTag",         "str"),
    ("proxy",           "Proxy URL",         "str"),
    ("cdp_url",         "CDP URL",           "str"),
    ("cdp_port",        "CDP Port",          "int"),
    ("discord_channel_id", "Discord Channel ID", "int"),
    ("use_ai",          "Use AI",            "bool"),
    ("use_templates",   "Use Templates",     "bool"),
    ("ai_model",        "AI Model",          "str"),
    ("bot_enabled",     "Autostart",         "bool"),
]

_AUTO_FIELDS = {"username", "player_tag"}

_ACCOUNT_ACTIONS = [
    ("setup_session",  "🌐 Setup Session (Login)",  "Opens a visible browser for manual login"),
    ("preview_session","👁  Preview Session",        "Open a visible browser with this account's saved cookies"),
    ("start_bot",      "▶  Start Bot",              "Launch headless browser + start bot loop"),
    ("stop_bot",       "⏹  Stop Bot",               "Stop the bot loop and kill browser"),
    ("delete_account", "🗑  Delete Account",         "Delete account, session data and all references"),
]

# Rich TUI: full-screen Live layout at 4fps. Keyboard input via msvcrt (Windows-only non-blocking).
# Tabs map to individual accounts; the last tab is a global stats/logs view.
# Account config can be edited in-place and written back to accounts.yaml.
class BotDashboard:

    def __init__(self, repo, settings):
        self.repo     = repo
        self.settings = settings
        self._status  = "STARTING"
        self._running = True
        self._console = Console()

        self._acct_mgr = AccountManager()
        self._acct_mgr.load()
        self.accounts: List[AccountConfig] = self._acct_mgr.accounts or []
        self._selected_tab: int = 0

        self._editing: bool = False
        self._field_editing: bool = False
        self._edit_field: int = 0
        self._current_edit_account: Optional[AccountConfig] = None
        self._edit_buffer: str = ""
        self._status_msg: str = ""
        self._creating_account: bool = False

        self._quit_confirm: bool = False
        self._quit_selection: int = 0

        self._bot_ref = None
        self._bot_task = None

        self._session_mgr = SessionManager()

        self._bot_tasks: dict = {}
        self._bg_tasks: set = set()

        log.info(f"Dashboard: {len(self.accounts)} account(s) loaded")

        try:
            import msvcrt
            self._input_task = asyncio.create_task(self._input_loop())
        except ImportError:
            self._input_task = None

    def set_status(self, status: str) -> None:
        self._status = status

    def stop(self) -> None:
        self._running = False

        try:
            self._session_mgr.kill_all()
        except Exception:
            pass

    def _build_header(self) -> Panel:
        now   = datetime.now().strftime("%H:%M:%S")
        color = _STATUS_COLORS.get(self._status, "white")
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right")
        grid.add_row(
            Text("  🎮  D4-Market Bot", style="bold bright_magenta"),
            Text(f"● {self._status}   {now}  ", style=f"bold {color}"),
        )
        return Panel(grid, style="bold", border_style="magenta")

    @staticmethod
    def _tab_label(acct: AccountConfig) -> str:

        if acct.username:
            return acct.username

        if len(acct.id) > 12:
            return acct.id[:10] + "…"
        return acct.id

    def _build_tabs(self) -> Panel:
        if not self.accounts and not self._creating_account:
            return Panel("[dim]No accounts — press Enter on +Create[/dim]",
                         title="[bold]Accounts[/bold]", border_style="magenta")

        total_tabs = len(self.accounts) + 1
        tabs = Table.grid(expand=True, pad_edge=False)
        for _ in range(total_tabs):
            tabs.add_column(justify="center")

        cells: list = []
        for idx, acct in enumerate(self.accounts):
            label = self._tab_label(acct)

            browser_up = self._session_mgr.is_browser_running(acct.id)
            if browser_up:
                indicator = "● "
                ind_style = "bright_green"
            elif acct.bot_enabled:
                indicator = "○ "
                ind_style = "yellow"
            else:
                indicator = ""
                ind_style = ""

            if idx == self._selected_tab and not self._editing:
                cell = Text(f" [{indicator}{label}] ", style="bold black on bright_white")
            elif idx == self._selected_tab and self._editing:
                cell = Text(f" [{indicator}{label}] ", style="bold black on bright_green")
            else:
                cell = Text("")
                if indicator:
                    cell.append(f" {indicator}", style=ind_style)
                cell.append(f" {label}  ")
            cells.append(cell)

        plus_idx = len(self.accounts)
        if self._selected_tab == plus_idx and not self._editing:
            cells.append(Text(" [+ Create] ", style="bold black on bright_white"))
        else:
            cells.append(Text("  +Create  "))
        tabs.add_row(*cells)

        if self._editing:
            hint = Text("  ▲▼ navigate  Enter edit  Backspace back", style="dim")
        elif self._creating_account:
            hint = Text(f"  Display Name: {self._edit_buffer}█  (Enter confirm · Esc cancel)", style="bold bright_cyan")
        else:
            hint = Text("  ◄► switch tabs   Enter open config   ● = bot running", style="dim")

        return Panel(Group(tabs, hint), title="[bold]Accounts[/bold]",
                     style="bold", border_style="magenta")

    async def _build_stats(self) -> Panel:
        try:
            s = await self.repo.get_daily_stats()
        except Exception:
            s = {"total_offers": 0, "replied": 0, "pending": 0, "items_on_hold": 0}

        poll = {}
        if self._bot_ref and hasattr(self._bot_ref, "_poll_stats"):
            poll = self._bot_ref._poll_stats

        grid = Table.grid(expand=True, padding=(0, 4))
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="center", ratio=1)

        def _stat(value, label, color="bright_white"):
            t = Text()
            t.append(str(value), style=f"bold {color}")
            t.append(f"  {label}", style="dim white")
            return t

        grid.add_row(
            _stat(s.get("total_offers",  0), "Offers",  "bright_cyan"),
            _stat(s.get("replied",       0), "Replied", "bright_green"),
            _stat(s.get("pending",       0), "Pending", "yellow"),
            _stat(s.get("items_on_hold", 0), "On Hold", "bright_magenta"),
            _stat(poll.get("unread",     0), "Unread",  "bright_red" if poll.get("unread", 0) else "dim"),
            _stat(f"#{poll.get('cycle', 0)}", "Poll",    "dim"),
        )
        return Panel(grid, title="[bold]Stats (24h)[/bold]", border_style="blue")

    async def _build_holds(self) -> Panel:
        try:
            holds = await self.repo.get_all_holds()
        except Exception:
            holds = []
        t = Table(box=box.SIMPLE, expand=True, header_style="dim", padding=(0, 1))
        t.add_column("Item",    style="dim cyan",     max_width=14)
        t.add_column("Held by", style="bright_white", max_width=18)
        t.add_column("Since",   style="dim",          max_width=10)
        t.add_column("Status",  justify="center",     max_width=10)
        _hold_style = {
            "holding":  "[bold magenta]holding[/bold magenta]",
            "sold":     "[bold green]sold[/bold green]",
            "released": "[red]released[/red]",
        }
        if not holds:
            t.add_row("—", "[dim]No active holds[/dim]", "", "")
        else:
            for h in holds:
                uid   = (h.get("item_uuid") or "")[:12] + "…"
                ts    = h.get("held_at", 0) or 0
                since = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "—"
                st    = h.get("status", "")
                t.add_row(uid, h.get("player_name", "?"), since, _hold_style.get(st, st))
        return Panel(t, title="[bold]Active Holds[/bold]", border_style="magenta")

    async def _build_convs(self) -> Panel:
        try:
            convs = (await self.repo.get_all_conversations())[:10]
        except Exception:
            convs = []
        t = Table(box=box.SIMPLE, expand=True, header_style="dim", padding=(0, 1))
        t.add_column("Player",     style="bright_white", max_width=16)
        t.add_column("Intent",     style="cyan",         max_width=16)
        t.add_column("Status",     justify="center",     max_width=10)
        t.add_column("Time",       style="dim",          max_width=10)
        t.add_column("Reply sent", style="dim",          ratio=1)
        _conv_style = {
            "replied": "[green]replied[/green]",
            "pending": "[yellow]pending[/yellow]",
            "on_hold": "[blue]on_hold[/blue]",
            "error":   "[red]error[/red]",
        }
        if not convs:
            t.add_row("—", "—", "—", "—", "[dim]No conversations yet[/dim]")
        else:
            for c in convs:
                ra   = c.get("replied_at") or 0
                ra_s = datetime.fromtimestamp(ra).strftime("%H:%M:%S") if ra else "—"
                st   = c.get("status", "")
                rp   = (c.get("reply_text") or "")[:55]
                t.add_row(
                    c.get("player_name", "?"),
                    c.get("intent", "—"),
                    _conv_style.get(st, st),
                    ra_s, rp,
                )
        return Panel(t, title="[bold]Recent Conversations[/bold]", border_style="blue")

    def _build_log(self) -> Panel:
        t = Table(box=None, expand=True, show_header=False, padding=(0, 1))
        t.add_column("Time",    style="dim",   width=8,  no_wrap=True)
        t.add_column("Level",                  width=7,  no_wrap=True)
        t.add_column("Message",                ratio=1,  no_wrap=True, overflow="ellipsis")
        lines = list(_LOG_BUFFER)[-14:]
        if not lines:
            t.add_row("", "", "[dim]Waiting for events…[/dim]")
        else:
            for entry in lines:
                ts, level, msg, *rest = entry
                count = rest[0] if rest else 1
                color = _LEVEL_COLORS.get(level, "white")
                suffix = f" [dim](×{count})[/dim]" if count > 1 else ""

                display_msg = msg.replace("\n", " ↵ ").replace("\r", "")
                t.add_row(ts, f"[{color}]{level[:4]}[/{color}]", display_msg + suffix)
        return Panel(t, title="[bold]Log[/bold]", border_style="dim")

    def _build_config(self) -> Panel:

        acct = self._current_edit_account
        if acct is None:
            return Panel("[dim]No account selected[/dim]", title="Config")

        t = Table(box=None, expand=True, show_header=True, padding=(0, 1))
        t.add_column("Field",  style="bright_cyan", width=22, no_wrap=True)
        t.add_column("Value",  ratio=1)

        for idx, (attr, label, ftype) in enumerate(_ACCOUNT_FIELDS):
            raw_val = getattr(acct, attr, None)
            is_auto = attr in _AUTO_FIELDS

            # Show temp IDs (starting with _new_) as blank — user must set Account ID
            if attr == "id" and isinstance(raw_val, str) and raw_val.startswith("_new_"):
                display_val = "[dim]— (must be set)[/dim]"
            elif attr == "player_tag" and (raw_val is None or raw_val == ""):
                display_val = "[dim]— (must be set)[/dim]"
            elif attr == "discord_channel_id" and (not raw_val or raw_val == 0):
                display_val = "[dim]— (uses global default from config.yaml)[/dim]"
            elif raw_val == "auto":
                display_val = "[bright_magenta]auto[/bright_magenta] [dim](detected at startup)[/dim]"
            elif ftype == "bool":
                display_val = "[green]yes[/green]" if raw_val else "[red]no[/red]"
            elif ftype == "int":
                display_val = str(raw_val) if raw_val else "[dim]—[/dim]"
            elif raw_val is None or raw_val == "":
                display_val = "[dim]—[/dim]"
            else:
                display_val = str(raw_val)

            if self._field_editing and idx == self._edit_field:
                buf_display = self._edit_buffer + "█"
                if ftype == "bool":
                    buf_display = self._edit_buffer + "█  [dim](y/n)[/dim]"
                elif is_auto:
                    buf_display = self._edit_buffer + "█  [dim](type 'auto' to auto-detect)[/dim]"
                t.add_row(
                    Text(f"▸ {label}", style="bold bright_green"),
                    Text.from_markup(f"[bold bright_white on blue] {buf_display} [/bold bright_white on blue]"),
                )
            elif idx == self._edit_field and not self._field_editing:
                t.add_row(
                    Text(f"▸ {label}", style="bold bright_white"),
                    Text.from_markup(display_val),
                )
            else:
                t.add_row(Text(f"  {label}"), Text.from_markup(display_val))

        t.add_row(Text(""), Text(""))

        field_count = len(_ACCOUNT_FIELDS)
        for act_idx, (act_id, act_label, act_desc) in enumerate(_ACCOUNT_ACTIONS):
            item_idx = field_count + act_idx

            extra = ""
            if act_id == "setup_session":
                has_session = self._session_mgr.session_exists(acct)
                extra = "  [green]✓ ready[/green]" if has_session else "  [yellow]not set up[/yellow]"
            elif act_id == "preview_session":
                has_session = self._session_mgr.session_exists(acct)
                preview_open = self._session_mgr.is_preview_running(acct.id)
                if preview_open:
                    extra = "  [bright_cyan]● open[/bright_cyan]"
                elif has_session:
                    extra = "  [green]✓ session ready[/green]"
                else:
                    extra = "  [yellow]no saved session[/yellow]"
            elif act_id == "start_bot":
                running = self._session_mgr.is_browser_running(acct.id)
                if running:
                    extra = "  [green]● running[/green]"
            elif act_id == "stop_bot":
                running = self._session_mgr.is_browser_running(acct.id)
                if not running:
                    extra = "  [dim]not running[/dim]"
            elif act_id == "delete_account":
                extra = "  [red]⚠ permanent[/red]"

            if item_idx == self._edit_field and not self._field_editing:
                t.add_row(
                    Text(f"▸ {act_label}", style="bold bright_yellow"),
                    Text.from_markup(f"[dim]{act_desc}[/dim]{extra}"),
                )
            else:
                t.add_row(
                    Text(f"  {act_label}"),
                    Text.from_markup(f"[dim]{act_desc}[/dim]{extra}"),
                )

        if self._field_editing:
            hint = "[dim]Type or paste value · Enter confirm · Esc cancel[/dim]"
        else:
            hint = "[dim]▲▼ navigate  Enter edit/run  Backspace back to dashboard[/dim]"

        status_line = ""
        if self._status_msg:
            status_line = f"\n[bright_green]{self._status_msg}[/bright_green]"
            self._status_msg = ""

        content = Group(t, Text.from_markup(f"\n{hint}{status_line}"))
        title = f"[bold]⚙  {acct.id} — Account Configuration[/bold]"
        return Panel(content, title=title, border_style="bright_green", padding=(1, 2))

    def _build_quit_dialog(self) -> Panel:
        grid = Table.grid(expand=True)
        grid.add_column(justify="center")
        grid.add_row(Text("\n  Are you sure you want to quit?\n", style="bold bright_white"))

        btn = Table.grid(padding=(0, 4))
        btn.add_column(justify="center")
        btn.add_column(justify="center")
        yes_style = "bold black on bright_green" if self._quit_selection == 0 else "dim"
        no_style  = "bold black on bright_red"   if self._quit_selection == 1 else "dim"
        btn.add_row(Text("  Yes  ", style=yes_style), Text("  No  ", style=no_style))
        grid.add_row(btn)
        grid.add_row(Text("\n◄► switch   Enter confirm   Esc cancel", style="dim", justify="center"))

        return Panel(grid, title="[bold]⏻  Quit[/bold]", border_style="bright_red",
                     padding=(2, 4))

    async def _render(self) -> Layout:
        layout = Layout()

        if self._quit_confirm:

            layout.split_column(
                Layout(name="header", size=3),
                Layout(name="quit",   ratio=1),
            )
            layout["header"].update(self._build_header())
            layout["quit"].update(self._build_quit_dialog())
            return layout

        if self._editing:

            layout.split_column(
                Layout(name="header", size=3),
                Layout(name="tabs",   size=5),
                Layout(name="config", ratio=1),
                Layout(name="log",    size=8),
            )
            layout["header"].update(self._build_header())
            layout["tabs"].update(self._build_tabs())
            layout["config"].update(self._build_config())
            layout["log"].update(self._build_log())
        else:

            layout.split_column(
                Layout(name="header", size=3),
                Layout(name="tabs",   size=5),
                Layout(name="stats",  size=4),
                Layout(name="middle", minimum_size=6),
                Layout(name="log",    size=16),
            )
            layout["middle"].split_row(
                Layout(name="holds", ratio=1),
                Layout(name="convs", ratio=2),
            )
            layout["header"].update(self._build_header())
            layout["tabs"].update(self._build_tabs())
            layout["stats"].update(await self._build_stats())
            layout["middle"]["holds"].update(await self._build_holds())
            layout["middle"]["convs"].update(await self._build_convs())
            layout["log"].update(self._build_log())

        return layout

    # Hot-reloads accounts.yaml each cycle unless in edit mode to avoid clobbering in-progress edits.
    async def run(self) -> None:
        self._status = "STARTING"
        with Live(
            console=self._console,
            refresh_per_second=4.0,
            screen=True,
        ) as live:
            self._editing = False
            self._field_editing = False
            while self._running:
                try:

                    if not self._editing and not self._creating_account:
                        self._acct_mgr.load()
                        if self._acct_mgr.accounts:
                            self.accounts = self._acct_mgr.accounts
                        if self._selected_tab > len(self.accounts):
                            self._selected_tab = len(self.accounts)

                    layout = await self._render()
                    live.update(layout)
                except Exception:
                    pass
                await asyncio.sleep(0.15)

    def _save_accounts(self) -> None:

        self._acct_mgr.accounts = list(self.accounts)
        self._acct_mgr.save()

    def _enter_config(self, acct: AccountConfig) -> None:

        self._current_edit_account = acct
        self._edit_field = 0
        self._editing = True
        self._field_editing = False
        self._edit_buffer = ""
        log.debug(f"Opened config for account [{acct.id}]")

    def _exit_config(self) -> None:

        if self._field_editing:

            self._field_editing = False
            self._edit_buffer = ""
        self._editing = False
        self._current_edit_account = None
        self._save_accounts()
        log.debug("Exited account config → saved")

    def _start_field_edit(self) -> None:

        if self._current_edit_account is None:
            return

        field_count = len(_ACCOUNT_FIELDS)

        if self._edit_field < field_count:

            attr, _, ftype = _ACCOUNT_FIELDS[self._edit_field]
            current = getattr(self._current_edit_account, attr, None)
            if ftype == "bool":
                self._edit_buffer = "y" if current else "n"
            elif ftype == "int":
                self._edit_buffer = str(current) if current else "0"
            elif attr == "id" and isinstance(current, str) and current.startswith("_new_"):
                self._edit_buffer = ""
            else:
                self._edit_buffer = str(current) if current else ""
            self._field_editing = True
        else:

            act_idx = self._edit_field - field_count
            if act_idx < len(_ACCOUNT_ACTIONS):
                act_id, _, _ = _ACCOUNT_ACTIONS[act_idx]
                self._execute_action(act_id)

    def _execute_action(self, act_id: str) -> None:

        acct = self._current_edit_account
        if acct is None:
            return
        idx = next((i for i, a in enumerate(self.accounts) if a.id == acct.id), 0)

        if act_id == "setup_session":
            ok = self._session_mgr.launch_setup(acct, index=idx)
            if ok:
                self._status_msg = "🌐 Browser opened — log in, then close it"
                self._save_accounts()
            else:
                self._status_msg = "⚠ Could not launch browser (already running?)"

        elif act_id == "start_bot":
            if not acct.id or acct.id.startswith("_new_"):
                self._status_msg = "⚠ Set Account ID before starting the bot"
                return
            if not acct.player_tag:
                self._status_msg = "⚠ Set BattleTag before starting the bot"
                return
            if not self._session_mgr.session_exists(acct):
                self._status_msg = "⚠ No session — run Setup Session first"
                return
            if acct.id in self._bot_tasks and not self._bot_tasks[acct.id].done():
                self._status_msg = "⚠ Bot already running for this account"
                return
            ok = self._session_mgr.launch_headless(acct, index=idx)
            if ok or self._session_mgr.is_browser_running(acct.id):
                self._status_msg = "▶ Starting bot…"
                self._save_accounts()

                task = asyncio.create_task(self._launch_bot(acct))
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
            else:
                self._status_msg = "⚠ Could not start browser"

        elif act_id == "stop_bot":
            bot_task = self._bot_tasks.get(acct.id)
            had_task = bot_task and not bot_task.done()
            was_running = had_task or self._session_mgr.is_browser_running(acct.id)

            # Send Discord notification BEFORE stopping (while Discord is still connected)
            if was_running:
                task = asyncio.create_task(self._notify_account_status(acct, is_active=False))
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)

            # Cancel the bot task
            if had_task:
                bot_task.cancel()
                del self._bot_tasks[acct.id]

            # Stop any bot instance
            bots = getattr(self, "_bots", [])
            for b in bots:
                if getattr(b, "account_id", "") == acct.id:
                    b.stop()

            killed = self._session_mgr.kill_browser(acct)
            self._save_accounts()
            if killed or had_task:
                self._status_msg = "⏹ Bot stopped, browser closed"
            else:
                self._status_msg = "No bot/browser to stop"

        elif act_id == "preview_session":
            if self._session_mgr.is_preview_running(acct.id):
                self._status_msg = "👁 Preview already open — check your taskbar"
                return
            ok = self._session_mgr.launch_preview(acct)
            if ok:
                self._status_msg = "👁 Preview browser opened — inspect cookies/session, then close it"
            else:
                self._status_msg = "⚠ Could not open preview browser (already open or Chrome not found)"

        elif act_id == "delete_account":
            self._delete_account(acct)

    async def _notify_account_status(self, acct: AccountConfig, is_active: bool) -> None:
        status = "started" if is_active else "stopped"
        display = acct.username or acct.id
        log.info(f"[notify] Account [{acct.id}] session {status} ({display})")

        try:
            if self._bot_ref and hasattr(self._bot_ref, "discord"):
                discord_bot = self._bot_ref.discord
                channel_id = getattr(acct, "discord_channel_id", 0) or 0
                await discord_bot.send_account_status(acct.id, display, is_active, channel_id=channel_id or None)
        except Exception:
            pass

    async def _launch_bot(self, acct: AccountConfig) -> None:
        """Create and run a D4MarketBot for the given account."""
        from SRC.config.settings import load_settings, build_account_settings
        from SRC.core.bot import D4MarketBot
        from SRC.storage.db import init_db

        try:
            settings = load_settings()
            acct_settings = build_account_settings(settings, acct)

            await init_db(settings.db_path, primary_account_id=acct.id)

            # Determine if we have a usable Discord client to share
            shared_discord = None
            needs_services = True
            if self._bot_ref and hasattr(self._bot_ref, 'discord'):
                discord_bot = self._bot_ref.discord
                if discord_bot and getattr(discord_bot, '_ready', None) and discord_bot._ready.is_set():
                    shared_discord = discord_bot
                    needs_services = False

            bot = D4MarketBot(
                settings=acct_settings,
                account_id=acct.id,
                start_services=needs_services,
                shared_discord=shared_discord,
            )
            bot.dashboard = self

            if not hasattr(self, "_bots"):
                self._bots = []
            self._bots.append(bot)

            if self._bot_ref is None:
                self._bot_ref = bot
            if self.repo is None:
                self.repo = bot.repo

            bot_task = asyncio.create_task(bot.run())
            self._bot_tasks[acct.id] = bot_task

            self._status_msg = f"▶ Bot started for {acct.username or acct.id}"

            # Send Discord notification after Discord has connected
            async def _delayed_notify():
                discord_bot = getattr(bot, 'discord', None)
                if discord_bot and hasattr(discord_bot, 'wait_ready'):
                    await discord_bot.wait_ready(timeout=30.0)
                await self._notify_account_status(acct, is_active=True)
            notify_task = asyncio.create_task(_delayed_notify())
            self._bg_tasks.add(notify_task)
            notify_task.add_done_callback(self._bg_tasks.discard)

            await bot_task
        except asyncio.CancelledError:
            log.info(f"Bot task cancelled for [{acct.id}]")
        except Exception as e:
            log.error(f"Bot error for [{acct.id}]: {e}")
            self._status_msg = f"⚠ Bot error: {e}"

    def _delete_account(self, acct: AccountConfig) -> None:

        self._session_mgr.kill_browser(acct)

        session_dir = self._session_mgr.session_dir(acct)
        if session_dir.exists():
            try:
                shutil.rmtree(str(session_dir))
                log.info(f"Deleted session directory: {session_dir}")
            except Exception as e:
                log.warning(f"Could not delete session dir: {e}")

        account_id = acct.id
        self.accounts = [a for a in self.accounts if a.id != account_id]
        self._save_accounts()

        self._editing = False
        self._field_editing = False
        self._current_edit_account = None
        if self._selected_tab >= len(self.accounts):
            self._selected_tab = max(0, len(self.accounts) - 1)

        self._status_msg = f"🗑 Account [{account_id}] deleted"
        log.info(f"Account [{account_id}] deleted — session data and references removed")

    def _confirm_field_edit(self) -> None:

        if self._current_edit_account is None:
            return
        attr, label, ftype = _ACCOUNT_FIELDS[self._edit_field]
        value = self._edit_buffer.strip()

        old_id = self._current_edit_account.id

        if ftype == "bool":
            setattr(self._current_edit_account, attr,
                    value.lower() in ("y", "yes", "true", "1"))
        elif ftype == "int":
            try:
                setattr(self._current_edit_account, attr, int(value))
            except ValueError:
                self._status_msg = f"⚠ Invalid number for {label}"
                self._field_editing = False
                self._edit_buffer = ""
                return
        else:
            setattr(self._current_edit_account, attr, value if value else None)

        # When Account ID changes from temp _new_ to a real ID, update process tracking
        if attr == "id" and old_id.startswith("_new_") and value and not value.startswith("_new_"):
            proc = self._session_mgr._processes.pop(old_id, None)
            if proc is not None:
                self._session_mgr._processes[value] = proc

        self._field_editing = False
        self._edit_buffer = ""
        self._status_msg = f"✓ {label} updated"
        self._save_accounts()

    def _cancel_field_edit(self) -> None:

        self._field_editing = False
        self._edit_buffer = ""

    def _create_new_account_inline(self) -> None:

        self._creating_account = True
        self._edit_buffer = ""

    def _finish_create_account(self) -> None:

        display_name = self._edit_buffer.strip()
        self._creating_account = False
        self._edit_buffer = ""
        if not display_name:
            return
        if any(a.username == display_name for a in self.accounts):
            log.warning(f"Account with name '{display_name}' already exists")
            return
        acct = self._acct_mgr.create_account(display_name)
        self.accounts = self._acct_mgr.accounts
        self._selected_tab = len(self.accounts) - 1
        log.info(f"Created account '{display_name}' [{acct.id}]")

        self._enter_config(acct)

    async def _input_loop(self) -> None:

        import msvcrt

        while self._running:
            if not msvcrt.kbhit():
                await asyncio.sleep(0.01)
                continue

            ch = msvcrt.getwch()

            if self._creating_account:
                if ch == "\r":
                    self._finish_create_account()
                elif ch == "\x1b":
                    self._creating_account = False
                    self._edit_buffer = ""
                elif ch == "\x08":
                    self._edit_buffer = self._edit_buffer[:-1]
                elif ch in ("\x00", "\xe0"):
                    msvcrt.getwch()
                elif ch >= " ":
                    self._edit_buffer += ch
                await asyncio.sleep(0.01)
                continue

            if self._field_editing:
                if ch == "\r":
                    self._confirm_field_edit()
                elif ch == "\x1b":
                    self._cancel_field_edit()
                elif ch == "\x08":
                    self._edit_buffer = self._edit_buffer[:-1]
                elif ch == "\x16":
                    try:
                        import ctypes
                        CF_UNICODETEXT = 13
                        u32 = ctypes.windll.user32
                        k32 = ctypes.windll.kernel32
                        if u32.OpenClipboard(0):
                            try:
                                h = u32.GetClipboardData(CF_UNICODETEXT)
                                if h:
                                    ptr = k32.GlobalLock(h)
                                    if ptr:
                                        clip = ctypes.c_wchar_p(ptr).value or ""
                                        self._edit_buffer += clip.replace("\r", "").replace("\n", "")
                                        k32.GlobalUnlock(h)
                            finally:
                                u32.CloseClipboard()
                    except Exception:
                        pass
                elif ch in ("\x00", "\xe0"):
                    msvcrt.getwch()
                elif ch >= " ":
                    self._edit_buffer += ch
                await asyncio.sleep(0.01)
                continue

            if self._editing:
                total_items = len(_ACCOUNT_FIELDS) + len(_ACCOUNT_ACTIONS)
                if ch in ("\x00", "\xe0"):
                    nxt = msvcrt.getwch()
                    if nxt == "H":
                        self._edit_field = max(0, self._edit_field - 1)
                    elif nxt == "P":
                        self._edit_field = min(total_items - 1, self._edit_field + 1)
                elif ch == "\r":
                    self._start_field_edit()
                elif ch == "\x08":
                    self._exit_config()
                elif ch == "\x1b":
                    self._exit_config()
                await asyncio.sleep(0.01)
                continue

            if self._quit_confirm:
                if ch in ("\x00", "\xe0"):
                    nxt = msvcrt.getwch()
                    if nxt == "K":
                        self._quit_selection = 0
                    elif nxt == "M":
                        self._quit_selection = 1
                elif ch == "\r":
                    if self._quit_selection == 0:
                        self._running = False

                        tasks = getattr(self, "_bot_tasks", [])
                        if not tasks and getattr(self, "_bot_task", None):
                            tasks = [self._bot_task]
                            
                        bots = getattr(self, "_bots", [])
                        if not bots and getattr(self, "_bot_ref", None):
                            bots = [self._bot_ref]
                            
                        for b in bots:
                            b.stop()
                            
                        for t in tasks:
                            if t and not t.done():
                                t.cancel()
                                
                        if hasattr(self, "_session_mgr") and self._session_mgr:
                            self._session_mgr.kill_all()
                            
                        async def _force_exit() -> None:
                            await asyncio.sleep(1.0)
                            os._exit(0)
                        _exit_task = asyncio.create_task(_force_exit())
                        self._bg_tasks.add(_exit_task)
                        _exit_task.add_done_callback(self._bg_tasks.discard)
                    else:
                        self._quit_confirm = False
                elif ch == "\x1b":
                    self._quit_confirm = False
                await asyncio.sleep(0.01)
                continue

            if ch in ("\x00", "\xe0"):
                nxt = msvcrt.getwch()
                if nxt == "K":
                    self._selected_tab = max(0, self._selected_tab - 1)
                elif nxt == "M":
                    self._selected_tab = min(len(self.accounts), self._selected_tab + 1)
            elif ch == "\x1b":
                self._quit_confirm = True
                self._quit_selection = 0
            elif ch == "\r":
                if self._selected_tab >= len(self.accounts):
                    self._create_new_account_inline()
                elif self.accounts:
                    self._enter_config(self.accounts[self._selected_tab])

            await asyncio.sleep(0.01)
