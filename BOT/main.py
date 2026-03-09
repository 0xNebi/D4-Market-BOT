import sys
import asyncio
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=ResourceWarning)

sys.path.insert(0, str(Path(__file__).parent.parent))

from SRC.config.settings import load_settings, build_account_settings
from SRC.utils.logger import setup_logger, log
from SRC.core.bot import D4MarketBot
from SRC.managers.account_manager import AccountManager
from SRC.managers.session_manager import SessionManager
from SRC.storage.repository import Repository
from SRC.integrations.control_server import ControlServer
from SRC.storage.db import init_db

# Entry point. Reads enabled accounts from accounts.yaml, launches a headless Chrome
# per account (if session exists), then spawns one D4MarketBot task per account.
async def main() -> None:
    use_dashboard = "--no-dashboard" not in sys.argv

    settings = load_settings()

    setup_logger(
        log_dir=settings.log_dir,
        level=settings.log_level,
        dashboard_mode=use_dashboard,
    )

    acct_mgr = AccountManager()
    acct_mgr.load()
    enabled_accounts = [a for a in acct_mgr.accounts
                        if a.bot_enabled and not a.id.startswith("_new_")]
    log.info(f"Loaded {len(acct_mgr.accounts)} account(s), {len(enabled_accounts)} enabled")

    session_mgr = SessionManager()

    dashboard = None
    if use_dashboard:
        from SRC.utils.dashboard import BotDashboard
        dashboard = BotDashboard(repo=None, settings=settings)
        _dash_task = asyncio.create_task(dashboard.run())

    log.info("=" * 55)
    log.info("  D4-MARKET BOT  STARTING")
    log.info("=" * 55)

    if len(enabled_accounts) >= 1:

        primary_acct_id = enabled_accounts[0].id if enabled_accounts else ""
        await init_db(settings.db_path, primary_account_id=primary_acct_id)

        global_repo = Repository(db_path=settings.db_path)

        for i, acct in enumerate(enabled_accounts):
            if session_mgr.session_exists(acct):
                ok = session_mgr.launch_headless(acct, index=i)
                if ok:
                    log.info(f"  [autostart] Launched headless Chrome for {acct.username} (port {session_mgr.cdp_port(acct, i)})")
                else:
                    log.warning(f"  [autostart] Chrome already running or failed for {acct.username}")
            else:
                log.warning(f"  [autostart] No session data for {acct.username} — run Setup Session first")

        shared_control_server = None
        if settings.control_server_enabled:
            shared_control_server = ControlServer(
                repo=global_repo,
                settings=settings,
            )
            try:
                await shared_control_server.start()
            except Exception as e:
                log.warning(f"Control server could not start (non-fatal): {e}")
                shared_control_server = None

        bots: list[D4MarketBot] = []
        shared_discord = None

        for i, acct in enumerate(enabled_accounts):
            is_primary = (i == 0)
            acct_settings = build_account_settings(settings, acct)

            log.info(f"  Account #{i+1}: {acct.username}  CDP: {acct_settings.cdp_url}  AI: {acct_settings.use_ai}")

            bot = D4MarketBot(
                settings=acct_settings,
                account_id=acct.id,
                start_services=is_primary,
                shared_discord=shared_discord,
                shared_control_server=shared_control_server,
            )
            bots.append(bot)

            if is_primary:
                shared_discord = bot.discord

                bot._all_account_names = [a.username for a in enabled_accounts]

        if shared_discord is not None:
            shared_discord._all_bots = bots

        log.info("=" * 55)

        if dashboard is not None:
            for bot in bots:
                bot.dashboard = dashboard
            dashboard._bot_ref = bots[0]
            dashboard._bots = bots
            dashboard.repo = global_repo
            dashboard._session_mgr = session_mgr

        bot_tasks = []
        for bot in bots:
            task = asyncio.create_task(bot.run())
            bot_tasks.append(task)

        if dashboard is not None:
            dashboard._bot_task = bot_tasks[0]
            dashboard._bot_tasks = bot_tasks

        try:
            await asyncio.gather(*bot_tasks)
        except (KeyboardInterrupt, asyncio.CancelledError):
            log.info("Stopped.")
        finally:
            if shared_discord is not None:
                await shared_discord.send_bot_offline_notice()
            for bot in bots:
                bot.stop()
            if dashboard is not None:
                dashboard.stop()
            await asyncio.sleep(0.5)
            session_mgr.kill_all()
    else:

        log.info("  No enabled accounts — use the dashboard to create and configure accounts")
        log.info("=" * 55)

        if dashboard is not None:
            dashboard._session_mgr = session_mgr

            try:
                await _dash_task
            except (KeyboardInterrupt, asyncio.CancelledError):
                log.info("Stopped.")
            finally:
                dashboard.stop()
                session_mgr.kill_all()
        else:
            log.info("  No accounts to run and no dashboard. Exiting.")
            log.info("  (Run without --no-dashboard to access the account management UI)")

if __name__ == "__main__":
    asyncio.run(main())
