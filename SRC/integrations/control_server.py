import json
import time
from pathlib import Path
from typing import Optional

from aiohttp import web

from ..utils.logger import log
from ..storage.repository import Repository

_DASHBOARD_PATH = Path(__file__).parent.parent / "web_ui" / "frontend" / "dist" / "index.html"
try:
    _HTML_PAGE = _DASHBOARD_PATH.read_text(encoding="utf-8")
except FileNotFoundError:
    log.warning(f"Dashboard HTML not found at {_DASHBOARD_PATH} — using fallback")
    _HTML_PAGE = "<html><body><h1>D4-Market Dashboard</h1><p>dashboard.html not found. Check SRC/web_ui/dashboard.html</p></body></html>"

# aiohttp server: exposes REST endpoints for the React dashboard and serves the built static files.
# Multiple bots register themselves so the server can dispatch sold/release actions to the right account.
class ControlServer:

    def __init__(self, repo, settings, notification_queue=None):
        self.repo               = repo
        self.settings           = settings
        self.notification_queue = notification_queue
        self.listing_manager    = None
        self._bot_ref           = None
        self._bots: dict        = {}
        self._runner: Optional[web.AppRunner] = None

    def register_bot(self, bot) -> None:

        self._bots[bot.account_id] = bot
        if self._bot_ref is None:
            self._bot_ref = bot

    def _get_repo_for(self, account_id: str) -> 'Repository':

        if account_id:
            return Repository(db_path=self.repo.db_path, account_id=account_id)
        # No account_id = MAIN overview: return unfiltered repo so all sessions show
        return Repository(db_path=self.repo.db_path, account_id="")

    def _get_bot_for(self, account_id: str):

        if account_id:
            return self._bots.get(account_id, None)
        return self._bot_ref

    async def start(self) -> None:

        app = web.Application()
        app.router.add_get("/",                    self._handle_index)
        app.router.add_get("/api/status",          self._handle_status)
        app.router.add_get("/api/inventory",       self._handle_inventory)
        app.router.add_get("/api/activity",        self._handle_activity)
        app.router.add_post("/api/sold/{uuid}",    self._handle_sold)
        app.router.add_post("/api/release/{uuid}", self._handle_release)

        dist_path = Path(__file__).parent.parent / "web_ui" / "frontend" / "dist"
        if dist_path.exists():
            app.router.add_static("/assets", dist_path / "assets", name="assets")

            app.router.add_get("/icon.svg", lambda request: web.FileResponse(dist_path / "icon.svg"))

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self.settings.control_server_port)
        await site.start()
        log.success(
            f"Control dashboard running at "
            f"http://localhost:{self.settings.control_server_port}"
        )

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    async def _handle_index(self, _request: web.Request) -> web.Response:
        dist_index = Path(__file__).parent.parent / "web_ui" / "frontend" / "dist" / "index.html"
        if dist_index.exists():
            return web.Response(content_type="text/html", text=dist_index.read_text(encoding="utf-8"))
        return web.Response(content_type="text/html", text="<html><body><h1>Build not found</h1><p>Please run <b>npm run build</b> inside <code>SRC/web_ui/frontend</code></p></body></html>")

    async def _handle_status(self, _request: web.Request) -> web.Response:
        account_id = _request.query.get("account_id", "")
        repo = self._get_repo_for(account_id)
        bot = self._get_bot_for(account_id)

        stats = await repo.get_daily_stats()
        convs = await repo.get_all_conversations()
        holds = await repo.get_active_holds()
        sold  = await repo.get_sold_items(limit=50)

        poll_stats = {}
        if bot and hasattr(bot, "_poll_stats"):
            poll_stats = bot._poll_stats
        if bot and hasattr(bot, "cycle"):
            poll_stats["cycle"] = bot.cycle

        accounts_data = []
        try:
            import yaml
            import pathlib
            p = pathlib.Path(__file__).parent.parent.parent / "CONFIG" / "accounts.yaml"
            with open(p, "r", encoding="utf-8") as file:
                accts = yaml.safe_load(file).get("accounts", [])
                accounts_data = accts
        except Exception:
            accounts_data = getattr(self.settings, "accounts", [])

        return web.Response(
            content_type="application/json",
            text=json.dumps({
                "stats":         stats,
                "conversations": convs,
                "holds":         holds,
                "sold":          sold,
                "poll_stats":    poll_stats,
                "server_ts":     int(time.time()),
                "accounts":      accounts_data,
            }, default=str),
        )

    async def _handle_inventory(self, _request: web.Request) -> web.Response:

        account_id = _request.query.get("account_id", "")
        items = []

        if account_id:

            bot = self._get_bot_for(account_id)
            if bot and hasattr(bot, "inventory"):
                for item in bot.inventory.active_items:
                    items.append({
                        "id":               item.id,
                        "name":             item.name,
                        "price":            item.price,
                        "quantity":         getattr(item, "quantity", 1),
                        "game_mode":        item.game_mode,
                        "item_type":        getattr(item, "item_type", ""),
                        "rarity":           getattr(item, "rarity", ""),
                        "material_type":    getattr(item, "material_type", ""),
                        "is_ancestral":     getattr(item, "is_ancestral", False),
                        "greater_affix_count": getattr(item, "greater_affix_count", 0),
                        "affixes":          getattr(item, "affixes", []),
                        "power":            getattr(item, "power", 0),
                    })
        else:

            seen_ids = set()
            for bot in self._bots.values():
                if hasattr(bot, "inventory"):
                    for item in bot.inventory.active_items:
                        if item.id not in seen_ids:
                            seen_ids.add(item.id)
                            items.append({
                                "id":               item.id,
                                "name":             item.name,
                                "price":            item.price,
                                "quantity":         getattr(item, "quantity", 1),
                                "game_mode":        item.game_mode,
                                "item_type":        getattr(item, "item_type", ""),
                                "rarity":           getattr(item, "rarity", ""),
                                "material_type":    getattr(item, "material_type", ""),
                                "is_ancestral":     getattr(item, "is_ancestral", False),
                                "greater_affix_count": getattr(item, "greater_affix_count", 0),
                                "affixes":          getattr(item, "affixes", []),
                                "power":            getattr(item, "power", 0),
                            })

        return web.Response(
            content_type="application/json",
            text=json.dumps({"items": items}, default=str),
        )

    async def _handle_activity(self, _request: web.Request) -> web.Response:

        account_id = _request.query.get("account_id", "")
        repo = self._get_repo_for(account_id)
        actions = await repo.get_recent_actions(limit=50)
        return web.Response(
            content_type="application/json",
            text=json.dumps({"actions": actions}, default=str),
        )

    async def _handle_sold(self, request: web.Request) -> web.Response:

        item_uuid = request.match_info["uuid"]
        hold = await self.repo.get_item_hold(item_uuid)
        if not hold:
            return web.Response(
                status=404,
                content_type="application/json",
                text=json.dumps({"message": "No active hold found for this item."}),
            )
        await self.repo.mark_item_sold(item_uuid)
        log.info(f"[control] Item {item_uuid[:12]} marked as SOLD (was held by {hold['player_name']})")

        owner_bot = None
        for bot in self._bots.values():
            if hasattr(bot, "inventory") and bot.inventory.get_item(item_uuid):
                owner_bot = bot
                break
        if owner_bot is None:
            owner_bot = self._bot_ref

        listing_result = None
        lm = owner_bot.listing_manager if (owner_bot and hasattr(owner_bot, "listing_manager")) else self.listing_manager
        if lm:

            sold_price = 0
            if owner_bot and hasattr(owner_bot, "inventory"):
                cached = owner_bot.inventory.get_item(item_uuid)
                if cached and cached.price:
                    sold_price = cached.price
            sold_qty = hold.get("quantity") or 1
            listing_result = await lm.mark_as_sold_by_id(
                item_uuid, sold_price=sold_price, quantity=sold_qty
            )
            if listing_result.get("success"):
                log.success(f"[control] Item {item_uuid[:12]}... marked as sold via API")
                if self._bot_ref and hasattr(self._bot_ref, "inventory"):
                    self._bot_ref.inventory.mark_sold_locally(item_uuid)
            else:
                log.warning(f"[control] API mark_as_sold failed: {listing_result.get('error')}")

        await self.repo.log_action(
            "SOLD", hold["conv_id"],
            f"item={item_uuid[:16]} player={hold['player_name']}"
        )

        site_status = ""
        if listing_result:
            site_status = " Also marked sold on diablo.trade." if listing_result.get("success") else f" Site update failed: {listing_result.get('error')}"

        return web.Response(
            content_type="application/json",
            text=json.dumps({"message": f"Item marked as sold. Was held by {hold['player_name']}.{site_status}"}),
        )

    async def _handle_release(self, request: web.Request) -> web.Response:

        item_uuid = request.match_info["uuid"]
        released = await self.repo.release_item_hold(item_uuid)
        if not released:
            return web.Response(
                status=404,
                content_type="application/json",
                text=json.dumps({"message": "No active hold found for this item."}),
            )
        waitlist = await self.repo.get_waitlist_for_item(item_uuid)
        log.info(
            f"[control] Item {item_uuid[:12]} hold RELEASED "
            f"(was held by {released['player_name']}, {len(waitlist)} in waitlist)"
        )
        await self.repo.log_action(
            "RELEASE", released["conv_id"],
            f"item={item_uuid[:16]} waitlist={len(waitlist)}"
        )

        if self.notification_queue is not None and waitlist:
            await self.notification_queue.put({
                "type":      "item_released",
                "item_uuid": item_uuid,
                "waitlist":  waitlist,
            })
        return web.Response(
            content_type="application/json",
            text=json.dumps({
                "message": (
                    f"Hold released. "
                    f"{len(waitlist)} buyer(s) on waitlist — "
                    f"they will be notified on the next bot poll."
                ),
                "waitlist": waitlist,
            }, default=str),
        )
