from SRC.utils.price_parser import parse_offered_price, parse_buyer_quantity, parse_trade_prices

import asyncio
import re
import time
from datetime import datetime
from typing import Optional

from playwright.async_api import async_playwright, Page, Browser

from ..config.settings import Settings
from ..config.templates import TemplateEngine, MessageIntent
from ..utils.logger import log, add_session_logger
from ..utils.delays import human_delay, short_delay, page_load_delay
from ..utils.item_parser import extract_item_uuid
from ..ai.classifier import classify_intent, MessageIntent as Intent
from ..ai.gemini import GeminiClient
from ..integrations.discord_bot import D4DiscordBot
from ..integrations.control_server import ControlServer
from ..core.listing_manager import ListingManager
from ..core.ws_client import DiabloWebSocket
from ..core.diablo_api import DiabloAPI
from ..core.inventory import InventoryCache
from ..storage.db import init_db
from ..storage.repository import Repository

# Returns "accept" / "decline" / "counter" / "skip" based on how the offered price
# compares against the listed price using the configured thresholds.
def _evaluate_price(offered: Optional[int], listed: Optional[int], settings: Settings) -> str:

    if not offered or not listed or listed == 0:
        return "skip"
    offer_pct = (offered / listed) * 100
    if offer_pct >= settings.price_auto_accept_above_pct:
        return "accept"
    if offer_pct < settings.price_auto_decline_below_pct:
        return "decline"
    return "counter"

# Core trading bot — drives the polling loop, classifies buyer messages,
# picks replies (template or AI), and coordinates all integrations.
class D4MarketBot:
    def __init__(
        self,
        settings: Settings,
        account_id: str = "",
        start_services: bool = True,
        shared_discord: Optional[D4DiscordBot] = None,
        shared_control_server = None,
    ):
        self.settings  = settings
        self.account_id = account_id
        self._start_services = start_services
        _display = settings.username or (account_id[:8] if account_id else "")
        self._label = f"[{_display}] " if _display else ""
        self.repo      = Repository(db_path=settings.db_path, account_id=account_id)
        self._shared_control_server = shared_control_server

        if shared_discord is not None:
            self.discord = shared_discord
        else:
            self.discord = D4DiscordBot(
                token=settings.discord_bot_token,
                channel_id=settings.discord_channel_id,
                repo=self.repo,
                enabled=settings.discord_enabled,
            )

        self.gemini    = GeminiClient(api_key=settings.google_api_key,
                                      model=settings.gemini_model)
        self.templates = TemplateEngine(settings)

        self.notification_queue: asyncio.Queue = asyncio.Queue()
        self.my_id: str = ""
        self.cycle: int = 0
        self._running   = False
        self.listing_manager: Optional[ListingManager] = None

        self.ws = DiabloWebSocket()
        self._ws_enabled: bool = True

        self.api = DiabloAPI(ws=self.ws)

        self.inventory = InventoryCache(api=self.api)

        self._pending_messages: dict[str, dict] = {}
        self._batch_window: float = settings.message_batch_window

        self._poll_stats: dict = {"cycle": 0, "actionable": 0, "unread": 0, "total": 0}
        self._last_settings_reload: float = 0.0

    async def _reload_settings_from_file(self) -> None:
        # Re-reads config.yaml + accounts.yaml and updates mutable settings fields.
        # Avoids constant hot-reload by enforcing a minimum interval of 60 seconds.
        import time as _time
        if _time.time() - self._last_settings_reload < 60:
            return
        self._last_settings_reload = _time.time()
        try:
            from ..config.settings import load_settings, build_account_settings
            from ..managers.account_manager import AccountManager
            base = load_settings()
            new_s = base
            if self.account_id:
                acct_mgr = AccountManager()
                acct_mgr.load()
                acct = next((a for a in acct_mgr.accounts if a.id == self.account_id), None)
                if acct:
                    new_s = build_account_settings(base, acct)

            changed = []
            for field in (
                'use_ai', 'use_templates', 'ai_threshold', 'gemini_model',
                'battletag', 'check_interval', 'message_batch_window',
                'hold_expiry_seconds', 'min_delay_ms', 'max_delay_ms',
                'price_auto_accept_above_pct', 'price_auto_decline_below_pct',
                'template_ready_to_buy', 'template_price_inquiry',
                'template_still_available', 'template_lowball_decline',
                'template_unknown', 'template_item_reserved',
                'auto_accept_bnet_reveal',
            ):
                old_val = getattr(self.settings, field, None)
                new_val = getattr(new_s, field, None)
                if old_val != new_val:
                    setattr(self.settings, field, new_val)
                    changed.append(f"{field}={new_val!r}")

            if changed:
                log.info(f"[config] Hot-reloaded: {', '.join(changed)}")
                self.templates = TemplateEngine(self.settings)
                self._batch_window = self.settings.message_batch_window
                if self.gemini.model_id != self.settings.gemini_model:
                    self.gemini = GeminiClient(
                        api_key=new_s.google_api_key or self.settings.google_api_key,
                        model=self.settings.gemini_model,
                    )
        except Exception as e:
            log.debug(f"[config] Settings reload skipped: {e}")

    # Attaches to an already-running Chrome via CDP. Retries until Chrome is up.
    async def _connect_browser(self, proxy: Optional[str] = None) -> tuple[Browser, Page]:

        playwright = await async_playwright().start()
        log.info(f"{self._label}Connecting to Chrome at {self.settings.cdp_url} ...")

        _retry_secs = 5
        _warned     = False
        browser = None
        while browser is None and self._running:
            try:
                browser = await playwright.chromium.connect_over_cdp(self.settings.cdp_url)
            except Exception as e:
                if not _warned:
                    log.warning(f"Chrome not yet available — retrying every {_retry_secs}s until it starts...")
                    if hasattr(self, 'dashboard'):
                        self.dashboard.set_status("STARTING")
                    _warned = True
                await asyncio.sleep(_retry_secs)

        if browser is None:
            raise RuntimeError("Bot was stopped while waiting for Chrome.")

        if proxy:

            try:
                context = await browser.new_context(proxy={"server": proxy})
                page = await context.new_page()
                log.info(f"Created new context with proxy {proxy}")
            except Exception as e:
                log.warning(f"Failed to create proxied context ({proxy}): {e}")
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else await context.new_page()
        else:
            context = browser.contexts[0]
            page    = context.pages[0] if context.pages else await context.new_page()

        needs_nav = self.settings.target_url not in page.url
        if not needs_nav:

            try:
                title = await page.title()
                if "404" in title or "not found" in title.lower():
                    log.warning(f"Page is on 404 ('{page.url}') — navigating to homepage.")
                    needs_nav = True
            except Exception:
                pass
        if needs_nav:
            log.info(f"Navigating to {self.settings.target_url} ...")
            try:
                await page.goto(self.settings.target_url, wait_until="load", timeout=20_000)
            except Exception as e:

                log.debug(f"goto completed with warning: {e}")
            await page_load_delay()

        log.success(f"{self._label}Browser connected — active page: {page.url}")
        return browser, page

    async def _fetch_my_id(self, page: Page) -> str:

        user_data = await self.api.get_user_id()
        return user_data.get("user_id", "")

    async def _auto_accept_bnet_reveal(self, conv_id: str) -> None:

        try:
            ok = await self.api.accept_bnet_reveal(conv_id)
            if ok:
                log.success(f"[bnet] Auto-accepted BNet reveal for {conv_id[:30]}...")
                await self.repo.log_action("BNET_ACCEPT", conv_id, "Auto-accepted BNet tag reveal")

                await asyncio.sleep(2)
                await self._fetch_revealed_tags(conv_id)
            else:
                log.warning(f"[bnet] Failed to auto-accept BNet reveal for {conv_id[:30]}...")
        except Exception as e:
            log.warning(f"[bnet] Auto-accept error: {e}")

    async def _check_and_accept_bnet_reveal(self, conv_id: str) -> None:

        if not self.settings.auto_accept_bnet_reveal:
            return
        try:
            status = await self.api.get_bnet_reveal_status(conv_id)
            if status:
                reveal_status = status.get("status", "")
                log.debug(f"[bnet] Reveal status for {conv_id[:20]}...: {status}")
                if status.get("ownTag") or status.get("otherTag") or str(reveal_status).lower() in ("locked", "finalized"):
                    log.info(f"[bnet] Reveal already available for {conv_id[:30]}... — fetching tags")
                    await self._fetch_revealed_tags(conv_id)
                    return

                if reveal_status in ("pending", "requested", "waiting") or\
                   status.get("state") in ("pending", "requested"):
                    initiated_by = status.get("initiatedBy", "")
                    if initiated_by and initiated_by == self.my_id:
                        log.debug(f"[bnet] Pending reveal for {conv_id[:30]}... was initiated by us — not accepting")
                        return
                    log.info(f"[bnet] Found pending reveal for {conv_id[:30]}... — auto-accepting")
                    await self._auto_accept_bnet_reveal(conv_id)
        except Exception as e:
            log.debug(f"[bnet] Check reveal status failed (non-critical): {e}")

    async def _reconcile_bnet_reveal_request(self, conv_id: str, player: str) -> None:

        try:
            accepted = await self.api.accept_bnet_reveal(conv_id)
            if accepted:
                log.info(f"[bnet] Accepted reveal request for {player} via direct accept call")
                await self.repo.log_action("BNET_ACCEPT", conv_id, "Accepted reveal during reconcile flow")
                await asyncio.sleep(2)
                await self._fetch_revealed_tags(conv_id)
                return
        except Exception as e:
            log.debug(f"[bnet] Direct accept attempt failed during reconcile flow: {e}")

        try:
            status = await self.api.get_bnet_reveal_status(conv_id)
            if status:
                reveal_status = str(status.get("status", "")).lower()
                initiated_by = status.get("initiatedBy", "")

                if status.get("ownTag") or status.get("otherTag") or reveal_status in ("locked", "finalized"):
                    log.info(f"[bnet] Reveal already available for {player} — fetching tags")
                    await self._fetch_revealed_tags(conv_id)
                    return

                if reveal_status in ("pending", "requested", "waiting") or status.get("state") in ("pending", "requested"):
                    if initiated_by and initiated_by != self.my_id:
                        log.info(f"[bnet] Buyer {player} already initiated reveal — accepting instead of starting")
                        await self._auto_accept_bnet_reveal(conv_id)
                        return
                    if initiated_by and initiated_by == self.my_id:
                        log.debug(f"[bnet] Reveal already pending from our side for {player}")
                        return
        except Exception as e:
            log.debug(f"[bnet] Reveal reconciliation status check failed: {e}")

        log.info(f"[bnet] Buyer {player} requested tag reveal — starting proactively")
        await self._start_bnet_reveal(conv_id)

    async def _fetch_revealed_tags(self, conv_id: str) -> None:

        try:
            status = await self.api.get_bnet_reveal_status(conv_id)
            if status:

                log.debug(f"[bnet] Full reveal status: {status}")

                buyer_tag = (
                    status.get("otherTag")
                    or status.get("partnerBattleTag")
                    or status.get("recipientTag")
                    or status.get("otherBattleTag")
                    or status.get("partnerTag")
                    or status.get("battleTag")
                )
                seller_tag = (
                    status.get("ownTag")
                    or status.get("myBattleTag")
                    or status.get("initiatorTag")
                    or status.get("myTag")
                )

                if not buyer_tag:
                    users = status.get("users", [])
                    for u in (users if isinstance(users, list) else []):
                        tag = u.get("battleTag") or u.get("battleNetTag") or u.get("bnetTag") or ""
                        if tag and tag.lower() != self.settings.battletag.lower():
                            buyer_tag = tag
                        elif tag:
                            seller_tag = tag

                if not buyer_tag:
                    initiator = status.get("initiator", {})
                    recipient = status.get("recipient", {})
                    for obj in [initiator, recipient]:
                        if isinstance(obj, dict):
                            tag = obj.get("battleTag") or obj.get("tag") or ""
                            if tag and tag.lower() != self.settings.battletag.lower():
                                buyer_tag = tag
                            elif tag:
                                seller_tag = tag

                if buyer_tag:
                    log.success(f"[bnet] ★ BUYER TAG REVEALED: {buyer_tag} (conv: {conv_id[:20]}...)")
                if seller_tag:
                    log.info(f"[bnet] Seller tag: {seller_tag}")
                if not buyer_tag and not seller_tag:
                    log.info(f"[bnet] Reveal finalized but could not extract tags — response: {status}")

                tag_info = f"buyer={buyer_tag or '?'} seller={seller_tag or '?'}"
                await self.repo.log_action("BNET_REVEALED", conv_id, tag_info)

                if buyer_tag:
                    await self._store_buyer_tag(conv_id, buyer_tag)
        except Exception as e:
            log.warning(f"[bnet] Failed to fetch revealed tags: {e}")

    async def _store_buyer_tag(self, conv_id: str, buyer_tag: str) -> None:

        try:
            holds = await self.repo.get_all_holds()
            for hold in holds:
                if hold.get("conv_id") == conv_id:
                    await self.repo.log_action(
                        "BUYER_TAG", conv_id,
                        f"item={hold.get('item_uuid', '?')[:12]} tag={buyer_tag}"
                    )
                    break
        except Exception as e:
            log.debug(f"[bnet] Could not store buyer tag: {e}")

    async def _start_bnet_reveal(self, conv_id: str) -> None:

        try:
            ok = await self.api.start_bnet_reveal(conv_id)
            if ok:
                log.success(f"[bnet] Started tag reveal for {conv_id[:30]}...")
                await self.repo.log_action("BNET_START", conv_id, "Initiated tag reveal")
            else:
                log.debug(f"[bnet] Tag reveal start returned false for {conv_id[:30]}... (may already be revealed)")
        except Exception as e:
            log.debug(f"[bnet] Start reveal error: {e}")

    # Injects a browser-side WS listener so we get real-time messages without polling.
    async def _init_websocket(self, page: Page) -> bool:

        try:
            token, user_id, _ = await self.api.fetch_credentials()
            if not token:
                log.warning("[ws] No realtime token — WS messaging disabled")
                self._ws_enabled = False
                return False

            if user_id and not self.my_id:
                self.my_id = user_id
                log.info(f"[ws] Auto-detected user ID: {user_id[:20]}...")

            self.ws.set_message_handler(self._on_realtime_message)

            ok = await self.ws.start_browser_listener(page)
            if ok:
                log.success("[ws] Browser-side WebSocket listener active")
            else:
                log.warning("[ws] Browser listener failed to start — using GUI fallback")
            return ok

        except Exception as e:
            log.warning(f"[ws] Init failed ({e}) — using GUI fallback")
            self._ws_enabled = False
            return False

    # Handler for messages pushed from the browser WS listener.
    # Text messages are handled in the poll loop; system events (bnet reveal) are handled here.
    async def _on_realtime_message(self, data: dict) -> None:

        conv_id  = data.get("conversationId", "")
        msg_id   = data.get("messageId", "")
        msg_type = data.get("type", "")

        if conv_id and self.ws._page:

            asyncio.create_task(
                self.ws.subscribe_conv_via_browser(self.ws._page, conv_id)
            )

        if msg_type != "text":

            system_code = data.get("meta", {}).get("system", {}).get("code", "")
            snippet = data.get("snippet", "")

            if self.settings.auto_accept_bnet_reveal:

                if system_code == "bnet-reveal-start-recipient" or\
                   (snippet and "battle.net reveal" in snippet.lower()):
                    log.info(f"[bnet] Incoming reveal request for conv {conv_id[:30]}... — auto-accepting")
                    asyncio.create_task(self._auto_accept_bnet_reveal(conv_id))

                elif system_code == "bnet-reveal-finalized" or\
                     (snippet and "tags are now visible" in snippet.lower()):
                    log.info(f"[bnet] Tags revealed for conv {conv_id[:30]}... — fetching status")
                    asyncio.create_task(self._fetch_revealed_tags(conv_id))

            last_msg = data.get("lastMessage") or {}
            if conv_id and (snippet or last_msg):
                sender_id = last_msg.get("userId", "")

                if not sender_id and "|" in conv_id:
                    sender_id = next(
                        (p for p in conv_id.split("|") if p != self.ws.user_id),
                        ""
                    )
                ts_ms   = last_msg.get("ts", 0) or data.get("ts", 0)
                ts_str  = datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M:%S") if ts_ms else "--:--:--"
                content = snippet or last_msg.get("content", "")
                log.info(
                    f"[ws-rt] INBOX  {ts_str} | from {sender_id[:20] or '?'} "
                    f"| conv {conv_id[:18]}... | {content}"
                )
            return

        sender_id = data.get("userId", "")
        content   = data.get("content", "")
        ts_ms     = data.get("ts", 0)
        ts_str    = datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M:%S") if ts_ms else "--:--:--"

        log.info(
            f"[ws-rt] MSG    {ts_str} | from {sender_id[:20] or '?'} "
            f"| conv {conv_id[:18]}... | {content}"
        )

        if msg_id and conv_id:
            await self.api.mark_as_read(conv_id, msg_id)

    # Sends via WS API with a simulated typing delay. Falls back to GUI if WS fails.
    async def _send_message_ws(
        self, page: Page, conv_id: str, player_name: str, text: str
    ) -> bool:

        asyncio.create_task(self.api.send_typing(conv_id, is_typing=True))

        chars = len(text)
        type_time = max(0.5, min(chars / 40.0, 4.0))
        await asyncio.sleep(type_time)

        ok = await self.api.send_message(conv_id, text)

        asyncio.create_task(self.api.send_typing(conv_id, is_typing=False))

        if ok:
            log.debug(f"[ws] Message delivered to {player_name}")
            return True

        log.warning(f"[ws] API send failed for {player_name} — falling back to GUI")
        return await self._send_message_gui(
            page=page, conv_id=conv_id, player_id="",
            player_name=player_name, text=text,
        )

    async def _resolve_auto_fields(self, page: Page) -> None:

        identity = await self.api.auto_detect_identity()
        if not identity:
            return

        detected_user = identity.get("username", "")
        detected_id = identity.get("user_id", "")

        if detected_user:
            log.info(f"[auto] Detected username: {detected_user}")
        if detected_id:
            log.info(f"[auto] Detected user_id:  {detected_id[:20]}...")

        if self.settings.battletag in ("auto", "YourTag#1234", ""):
            if detected_user:
                self.settings.battletag = detected_user
                log.info(f"[auto] battletag → {detected_user}")

        if not self.settings.my_user_id or self.settings.my_user_id == "auto":
            if detected_id:
                self.settings.my_user_id = detected_id
                self.my_id = detected_id

        if hasattr(self, 'dashboard') and getattr(self.dashboard, 'accounts', None):
            for acct in self.dashboard.accounts:
                if getattr(acct, 'username', None) == "auto" and detected_user:
                    acct.username = detected_user
                    log.info(f"[auto] Account [{acct.id}] username → {detected_user}")
                if getattr(acct, 'player_tag', None) == "auto" and detected_user:
                    acct.player_tag = detected_user
                    log.info(f"[auto] Account [{acct.id}] player_tag → {detected_user}")

    async def _fetch_conversations(self, page: Page) -> list[dict]:

        result = await self.api.get_conversations()
        if self.settings.debug_mode and result:
            log.debug(f"[api] Raw conversations sample: {str(result[:1])[:300]}")
        return result

    async def _fetch_listing(self, page: Page, uuid: str) -> Optional[dict]:

        result = await self.api.get_listing(uuid)
        if result:
            log.debug(f"[api] Listing keys: {list(result.keys())[:15]} | data: {str(result)[:300]}")
        return result

    async def _open_messages_panel(self, page: Page) -> bool:

        import re as _re

        current_url = page.url
        log.debug(f"[chat] Page before panel open: {current_url} | title: {await page.title()}")
        try:
            await page.goto(
                self.settings.target_url, wait_until="load", timeout=20_000
            )
            await page_load_delay()
            log.debug(f"[chat] Navigated to homepage: {page.url}")
        except Exception as e:
            log.debug(f"[chat] Homepage navigation warning: {e}")

        btn = page.get_by_role("button", name=_re.compile(r"^Messages", _re.IGNORECASE)).first
        try:
            await btn.wait_for(state="visible", timeout=15_000)
            await btn.click()
            await short_delay()
            log.debug("[chat] Messages panel opened")
            return True
        except Exception as e:
            log.warning(f"[chat] Could not open Messages panel: {e}")
            return False

    async def _click_conversation_in_panel(
        self, page: Page, player_name: str, _msg_preview: str
    ) -> bool:

        candidates = [
            page.get_by_text(player_name, exact=True).first,
            page.get_by_text(player_name, exact=False).first,
        ]
        for el in candidates:
            try:
                if await el.is_visible(timeout=3_000):
                    await el.click()
                    await page_load_delay()
                    log.debug(f"[chat] Opened conversation with {player_name}")
                    return True
            except Exception:
                continue
        log.warning(f"[chat] Conversation row not found for {player_name}")
        return False

    async def _send_message_gui(
        self, page: Page, conv_id: str, player_id: str,
        player_name: str, text: str, msg_preview: str = ""
    ) -> bool:

        import re as _re
        log.debug(f"[send] GUI send → {player_name}")

        success = False
        try:

            if not await self._open_messages_panel(page):
                return False

            if not await self._click_conversation_in_panel(page, player_name, msg_preview):
                return False

            try:
                message_row = page.locator(".relative.flex.min-w-0").last
                if await message_row.is_visible(timeout=2_000):
                    await message_row.hover()
                    await short_delay()
                    reply_btn = message_row.locator(".rounded-md").first
                    if await reply_btn.is_visible(timeout=1_000):
                        await reply_btn.click()
                        log.debug("[send] Clicked Reply button")
                        await short_delay()
            except Exception:
                log.debug("[send] No Reply hover found — proceeding to textbox")

            textbox = page.get_by_role(
                "textbox",
                name=_re.compile(f"Message {_re.escape(player_name)}", _re.IGNORECASE)
            )
            try:
                await textbox.wait_for(state="visible", timeout=8_000)
            except Exception:
                log.warning("[send] Textbox not found by player name, trying generic fallback")
                textbox = page.locator('[contenteditable="true"]').last
                try:
                    await textbox.wait_for(state="visible", timeout=5_000)
                except Exception as e:
                    log.error(f"[send] No message input found — {e}")
                    return False

            await textbox.click()
            await short_delay()

            try:
                await textbox.fill(text)
            except Exception:
                await textbox.press_sequentially(text)

            log.debug(f"[send] Filled {len(text)} chars")
            await short_delay()

            sent = False
            try:
                send_btn = page.locator(".flex.items-end > div:nth-child(3)")
                if await send_btn.is_visible(timeout=2_000):
                    await send_btn.click()
                    sent = True
                    log.debug("[send] Clicked send button")
            except Exception:
                pass
            if not sent:
                await textbox.press("Enter")
                log.debug("[send] Pressed Enter to send")

            await short_delay()

            log.debug("[send] GUI send completed")
            success = True
        finally:

            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

        return success

    async def _send_reply(
        self, page: Page, conv_id: str, player_id: str,
        player_name: str, text: str, msg_preview: str = ""
    ) -> bool:

        if self._ws_enabled:
            ok = await self._send_message_ws(page, conv_id, player_name, text)
            if ok:
                return True
            log.info("[send] WS send failed — falling back to GUI")

        return await self._send_message_gui(
            page=page, conv_id=conv_id, player_id=player_id,
            player_name=player_name, text=text, msg_preview=msg_preview,
        )

    def _should_use_ai(self, intent: Intent) -> bool:
        if not self.settings.use_ai or not self.gemini.is_ready:
            return False
            
        if not getattr(self.settings, 'use_templates', True):
            return True
            
        threshold = self.settings.ai_threshold
        if threshold == "simple":
            return False

        # Always use AI for inventory queries and counter-offers — templates can't handle these well
        if intent in (Intent.INVENTORY_QUERY, Intent.COUNTER_OFFER):
            return True

        return True

    # Resolves item info, classifies intent, checks price, and returns the reply text.
    # Returns (reply, ai_used, item_uuid, item_name, listed_price, intent_str, quantity).
    async def _choose_reply(
        self,
        page:        Page,
        conv_id:     str,
        player:      str,
        message:     str,
        my_id:       str,
        prefetched_history: Optional[list] = None,
    ) -> tuple[str, bool, Optional[str], Optional[str], Optional[int], str, int]:

        item_uuid    = extract_item_uuid(message)
        item_name    = "the item"
        listed_price = None
        item_status  = "unknown"
        item_quantity = 1
        message_offer_price, message_listed_price = parse_trade_prices(message)

        if not item_uuid:
            try:
                # reuse already-fetched history from _merge_recent_buyer_messages if available
                raw_history = prefetched_history if prefetched_history is not None else \
                    await self.api.get_conversation_messages(conv_id, limit=20)
                buyer_history = [
                    msg for msg in raw_history
                    if msg.get("userId", "") != self.my_id
                ]
                for msg in reversed(buyer_history or raw_history):
                    found = extract_item_uuid(msg.get("content", ""))
                    if found:
                        item_uuid = found
                        log.debug(f"[resolve] Item UUID found in conversation history: {item_uuid[:12]}...")
                        break
            except Exception as e:
                log.debug(f"Could not scan history for item UUID: {e}")

        if item_uuid:

            cached = self.inventory.get_item(item_uuid)
            if cached:
                item_name    = cached.name or "the item"
                listed_price = cached.price or None
                item_quantity = getattr(cached, "quantity", 1) or 1
                if cached.is_sold:
                    item_status = "sold"
                else:
                    item_status = "available"
                log.debug(f"[inv] Item from cache: {item_name} | Status: {item_status}")
            else:

                listing = await self._fetch_listing(page, item_uuid)
                if listing and not listing.get("__error"):
                    # Verify ownership — only trust listings that belong to this account.
                    # A UUID might come from a buyer clicking another user's item in chat,
                    # giving us foreign listing data that would confuse the bot.
                    listing_owner = (
                        listing.get("userId")
                        or listing.get("sellerId")
                        or (listing.get("user") or {}).get("id", "")
                    )
                    if listing_owner and str(listing_owner) != str(self.my_id):
                        log.warning(
                            f"[resolve] Item {item_uuid[:12]}... belongs to user "
                            f"{listing_owner} not us ({self.my_id}) — ignoring foreign listing"
                        )
                        item_uuid = None
                        item_name = "the item"
                        listed_price = None
                        item_status = "unknown"
                    else:
                        item_name    = listing.get("name") or listing.get("title") or "the item"

                        raw_price = (
                            listing.get("price")
                            or listing.get("listPrice")
                            or listing.get("goldPrice")
                            or listing.get("soldPrice")
                            or listing.get("totalPrice")
                        )
                        if raw_price:
                            try:
                                listed_price = int(raw_price)
                            except (ValueError, TypeError):
                                listed_price = None

                        item_quantity = listing.get("quantity") or listing.get("qty") or 1
                        try:
                            item_quantity = int(item_quantity)
                        except (ValueError, TypeError):
                            item_quantity = 1
                        item_status = "available"
                log.debug(f"[api] Item: {item_name} | Price: {listed_price}")

            if message_listed_price and (not listed_price or message_listed_price > listed_price):
                listed_price = message_listed_price
                log.debug(f"[price] Using structured listed price from chat context: {listed_price}")

            hold = await self.repo.get_item_hold(item_uuid)
            if hold and hold.get("status") == "holding":
                if hold.get("conv_id") == conv_id:

                    item_status = "pending_trade"
                    log.info(f"[hold] Item on hold for THIS buyer ({conv_id[:20]}...) — status=pending_trade")
                else:
                    item_status = "on_hold"
                    log.info(f"[hold] Item on hold for ANOTHER buyer ({hold.get('player_name')}) — status=on_hold")
            elif hold:
                log.debug(f"[hold] Hold exists but status={hold.get('status')} (not 'holding') — ignoring")
            else:
                log.debug(f"[hold] No hold found for item {item_uuid[:12]}...")

            log.info(f"[decide] Item: {item_name} | Status: {item_status} | Price: {listed_price} | Conv: {conv_id[:20]}...")

        intent = classify_intent(message)

        if intent == Intent.UNKNOWN and item_uuid:
            try:
                hist_msgs = await self.api.get_conversation_messages(conv_id, limit=10)
                for hmsg in hist_msgs:
                    if hmsg.get("userId", "") != self.my_id:
                        hist_intent = classify_intent(hmsg.get("content", ""))
                        if hist_intent != Intent.UNKNOWN:
                            log.debug(f"[intent] Upgraded from UNKNOWN → {hist_intent.value} via history")
                            intent = hist_intent
                            break
            except Exception:
                pass

        log.debug(f"Intent classified: {intent.value}")

        if item_status == "sold" and intent in (Intent.READY_TO_BUY, Intent.STILL_AVAIL, Intent.UNKNOWN):

            log.info(f"Item {item_uuid[:12] if item_uuid else '?'} is sold — adjusting reply")

        if intent == Intent.COUNTER_OFFER:
            offered = parse_offered_price(message) or message_offer_price
            if offered and listed_price:
                decision = _evaluate_price(offered, listed_price, self.settings)
                log.info(f"Price decision: {decision} (offered={offered:,}, listed={listed_price:,})")
                if decision == "accept":
                    intent = Intent.READY_TO_BUY
                if decision == "decline":
                    intent = Intent.LOWBALL

        conversation_history = []
        try:
            # reuse already-fetched history; trim to last 12 messages for AI context
            raw_history = prefetched_history if prefetched_history is not None else \
                await self.api.get_conversation_messages(conv_id, limit=12)
            for msg in raw_history[-12:]:
                conversation_history.append({
                    "content": msg.get("content", ""),
                    "is_mine": msg.get("userId", "") == self.my_id,
                    "ts": msg.get("ts", 0),
                })
        except Exception as e:
            log.debug(f"Could not fetch conversation history: {e}")

        inventory_summary = self.inventory.get_inventory_summary()

        buyer_qty = parse_buyer_quantity(message)
        if buyer_qty and buyer_qty > 1:
            log.info(f"[qty] Buyer requested quantity: {buyer_qty} (listing has {item_quantity})")

        ai_used = False
        fallback_allowed = getattr(self.settings, 'use_templates', True)
        
        if self._should_use_ai(intent) and (not fallback_allowed or intent != Intent.LOWBALL):

            async def _ai_tool_executor(name: str, args: dict) -> dict:
                if name == "accept_tag_reveal":

                    try:
                        status = await self.api.get_bnet_reveal_status(conv_id)
                        if status and status.get("status") == "revealed":
                            return {"success": True, "message": "Tags already revealed — no action needed"}
                    except Exception:
                        pass
                    ok = await self.api.accept_bnet_reveal(conv_id)
                    return {"success": ok, "message": "Tag reveal accepted" if ok else "Failed to accept reveal"}
                elif name == "request_tag_reveal":

                    try:
                        status = await self.api.get_bnet_reveal_status(conv_id)
                        if status and status.get("status") == "revealed":
                            return {"success": True, "message": "Tags already revealed — no action needed"}
                    except Exception:
                        pass
                    ok = await self.api.start_bnet_reveal(conv_id)
                    return {"success": ok, "message": "Tag reveal requested" if ok else "Failed to request reveal"}
                elif name == "check_item_status":
                    matches = self.inventory.find_by_name(args.get("item_name", ""))
                    if matches:
                        found = matches[0]
                        return {
                            "found": True,
                            "name": found.name,
                            "price": found.price,
                            "quantity": found.quantity,
                            "is_sold": found.is_sold,
                        }
                    return {"found": False, "message": "Item not found in inventory"}
                elif name == "get_full_inventory":
                    return {"summary": self.inventory.get_inventory_summary()}
                return {"error": f"Unknown tool: {name}"}

            ai_reply, tokens_used, actions = await self.gemini.generate_reply(
                player=player,
                item_name=item_name,
                price=listed_price or 0,
                message=message,
                battletag=self.settings.battletag,
                conversation_history=conversation_history,
                inventory_summary=inventory_summary,
                item_status=item_status,
                tool_executor=_ai_tool_executor,
                item_quantity=item_quantity,
                buyer_quantity=buyer_qty,
            )

            for act in actions:
                log.info(f"{self._label}[ai-action] {act['action']} → {act.get('result', {})}")
                await self.repo.log_action(
                    "AI_TOOL",
                    conv_id,
                    f"{act['action']}({act.get('args', {})}) → {act.get('result', {})}",
                )

            if ai_reply:

                await self.repo.log_action("AI_METRICS", conv_id, f"tokens={tokens_used}")
                return ai_reply, True, item_uuid, item_name, listed_price, intent.value, item_quantity

        if item_status == "sold":
            if not getattr(self.settings, 'use_templates', True):
                log.warning(f"[{conv_id[:8]}] Item sold. Templates disabled. Ignoring message.")
                return "", False, item_uuid, item_name, listed_price, "sold", item_quantity
            reply = "sold already sorry"
            return reply, False, item_uuid, item_name, listed_price, "sold", item_quantity

        if not getattr(self.settings, 'use_templates', True):
            log.warning(f"[{conv_id[:8]}] Templates are disabled and AI failed to produce a reply. Ignoring message.")
            return "", ai_used, item_uuid, item_name, listed_price, intent.value, item_quantity

        reply = self.templates.render(
            intent=intent,
            player=player,
            item_name=item_name,
            price=listed_price or 0,
        )
        return reply, ai_used, item_uuid, item_name, listed_price, intent.value, item_quantity

    # Groups rapid multi-message bursts from the same buyer before we reply.
    def _batch_add(self, conv_id: str, conv: dict) -> bool:

        now = time.time()
        msg = conv.get("lastMessage", {})
        msg_id = msg.get("id", "")
        msg_ts = msg.get("ts", 0)
        entry = {
            "content": msg.get("content", ""),
            "ts": msg_ts,
            "id": msg_id,
            "userId": msg.get("userId", ""),
        }

        if conv_id in self._pending_messages:

            existing_ids = {
                (m["id"], m["ts"]) for m in self._pending_messages[conv_id]["messages"]
            }
            if (msg_id, msg_ts) in existing_ids:
                return False
            self._pending_messages[conv_id]["messages"].append(entry)
            return False
        else:
            self._pending_messages[conv_id] = {
                "conv": conv,
                "messages": [entry],
                "first_seen": now,
            }
            return True

    def _batch_ready(self, conv_id: str) -> bool:

        batch = self._pending_messages.get(conv_id)
        if not batch:
            return False
        elapsed = time.time() - batch["first_seen"]
        return elapsed >= self._batch_window

    def _batch_pop(self, conv_id: str) -> Optional[dict]:

        return self._pending_messages.pop(conv_id, None)

    # Pulls any buyer messages since our last reply and merges them so the AI
    # sees the full context, not just the latest single line.
    async def _merge_recent_buyer_messages(
        self,
        conv_id: str,
        msg_content: str,
        msg_ts: int,
        msg_id: str,
    ) -> tuple[str, int, str, list]:

        try:
            record = await self.repo.get_conversation_record(conv_id)
            last_processed_ts = int((record or {}).get("last_msg_ts") or 0)
            raw_history = await self.api.get_conversation_messages(conv_id, limit=20)
            sorted_history = sorted(raw_history, key=lambda item: item.get("ts", 0))

            recent_buyer_messages = []
            for hist_msg in reversed(sorted_history):
                hist_ts = int(hist_msg.get("ts", 0) or 0)
                if last_processed_ts and hist_ts <= last_processed_ts:
                    break
                if hist_msg.get("userId", "") == self.my_id:
                    if recent_buyer_messages:
                        break
                    continue
                if hist_msg.get("content"):
                    recent_buyer_messages.append(hist_msg)

            if not recent_buyer_messages:
                return msg_content, msg_ts, msg_id, raw_history

            recent_buyer_messages.reverse()
            latest = recent_buyer_messages[-1]
            if len(recent_buyer_messages) == 1:
                return latest.get("content", msg_content), latest.get("ts", msg_ts), latest.get("id", msg_id), raw_history

            combined = " | ".join(m.get("content", "") for m in recent_buyer_messages if m.get("content"))
            log.info(f"[context] Merged {len(recent_buyer_messages)} recent buyer messages for {conv_id[:20]}...")
            return combined, latest.get("ts", msg_ts), latest.get("id", msg_id), raw_history
        except Exception as e:
            log.debug(f"[context] Could not merge recent buyer messages: {e}")
            return msg_content, msg_ts, msg_id, []

    # Entry point per conversation in a poll cycle.
    # De-duplication, bnet reveal, batching, reply dispatch, and hold management all live here.
    async def _process_conversation(self, page: Page, conv: dict) -> None:

        conv_id     = conv.get("id", "")
        last_msg    = conv.get("lastMessage", {})
        msg_content = last_msg.get("content", "")
        msg_user_id = last_msg.get("userId", "")
        msg_ts      = last_msg.get("ts", 0)
        msg_id      = last_msg.get("id", "")

        if msg_user_id == self.my_id:
            return

        _SYSTEM_SKIP_PHRASES = (
            "battle.net tags are now visible",
            "both users have consented to reveal",
            "tags are now visible",
            "battle.net reveal request was cancelled",
            "reveal request was cancelled",
            "wants to reveal battle.net",
            "battle.net tag reveal",
            "has requested to reveal",
            "requested a battle.net reveal",
            "battle.net reveal requested",
            "waiting for user to consent",
            "use the toolbar to accept or decline",
            "item stats may differ",
        )
        if any(phrase in msg_content.lower() for phrase in _SYSTEM_SKIP_PHRASES):
            log.debug(f"[skip] System message in {conv_id[:20]}...: {msg_content[:60]}")

            if msg_id and conv_id:
                await self.api.mark_as_read(conv_id, msg_id)
            return

        if not await self.repo.needs_reply(conv_id, msg_ts, msg_user_id, self.my_id):
            return

        if self._batch_window > 0:
            is_first = self._batch_add(conv_id, conv)
            if is_first:
                log.debug(f"[batch] Started batch for {conv_id[:20]}... — waiting {self._batch_window}s")
            if not self._batch_ready(conv_id):

                return

            batch = self._batch_pop(conv_id)
            if batch and len(batch["messages"]) > 1:

                combined = " | ".join(m["content"] for m in batch["messages"] if m["content"])
                msg_content = combined
                log.info(f"[batch] Merged {len(batch['messages'])} messages from {conv_id[:20]}...")

        player = next(
            (u["name"] for u in conv.get("users", []) if u.get("id") != self.my_id),
            "Unknown",
        )
        player_id = next(
            (u["id"] for u in conv.get("users", []) if u.get("id") != self.my_id),
            "",
        )

        log.info(f"New message from {player}: {msg_content[:80]}")

        await self._check_and_accept_bnet_reveal(conv_id)

        _REVEAL_REQUEST_PHRASES = (
            "show me your tag", "reveal tag", "reveal your tag",
            "show tag", "share tag", "your battletag", "your battle tag",
            "bnet tag", "show btag", "share btag", "reveal btag",
            "add me", "whats your tag", "what's your tag",
        )
        msg_lower = msg_content.lower()
        if any(phrase in msg_lower for phrase in _REVEAL_REQUEST_PHRASES):
            asyncio.create_task(self._reconcile_bnet_reveal_request(conv_id, player))

        await human_delay(self.settings.min_delay_ms, self.settings.max_delay_ms)

        msg_content, msg_ts, msg_id, prefetched_history = await self._merge_recent_buyer_messages(
            conv_id=conv_id,
            msg_content=msg_content,
            msg_ts=msg_ts,
            msg_id=msg_id,
        )

        item_uuid = extract_item_uuid(msg_content)
        if item_uuid:
            active_hold = await self.repo.get_item_hold(item_uuid)
            if active_hold and active_hold["conv_id"] != conv_id:
                log.info(
                    f"Item {item_uuid[:12]} is on hold for {active_hold['player_name']}"
                    f" — sending reserved reply to {player}"
                )
                reserved_reply = self.settings.template_item_reserved
                ok = await self._send_reply(
                    page=page,
                    conv_id=conv_id,
                    player_id=player_id,
                    player_name=player,
                    text=reserved_reply,
                    msg_preview=msg_content,
                )
                if ok:
                    await self.repo.record_reply(
                        conv_id=conv_id,
                        player=player,
                        player_id=player_id,
                        item_uuid=item_uuid,
                        raw_message=msg_content,
                        reply=reserved_reply,
                        intent="reserved",
                        last_msg_ts=msg_ts,
                        status="on_hold",
                    )
                    await self.repo.log_action("SKIP", conv_id, f"Item reserved for {active_hold['player_name']}")
                return

        reply, ai_used, item_uuid_out, item_name, listed_price, resolved_intent, item_quantity = await self._choose_reply(
            page=page,
            conv_id=conv_id,
            player=player,
            message=msg_content,
            my_id=self.my_id,
            prefetched_history=prefetched_history,
        )

        if item_uuid is None:
            item_uuid = item_uuid_out

        ok = await self._send_reply(
            page=page,
            conv_id=conv_id,
            player_id=player_id,
            player_name=player,
            text=reply,
            msg_preview=msg_content,
        )

        if ok:
            mode = "Gemini AI" if ai_used else "Template"
            offered_price = parse_offered_price(msg_content)
            log.success(f"Reply sent to {player} via {mode}: {reply}")

            if msg_id and conv_id:
                await self.api.mark_as_read(conv_id, msg_id)

            await self.repo.record_reply(
                conv_id=conv_id,
                player=player,
                player_id=player_id,
                reply=reply,
                item_uuid=item_uuid,
                item_name=item_name,
                listed_price=listed_price,
                raw_message=msg_content,
                intent=resolved_intent,
                ai_used=ai_used,
                last_msg_ts=msg_ts,
            )
            await self.repo.log_action("REPLY", conv_id,
                                       f"player={player} mode={mode} item={item_name}")

            if item_uuid:
                if resolved_intent in ("ready_to_buy", "still_available", "unknown"):
                    hold_qty = parse_buyer_quantity(msg_content) or 1
                    await self.repo.set_item_hold(item_uuid, conv_id, player, quantity=hold_qty)
                    log.info(f"Item {item_uuid[:12]} placed on hold for {player} (qty={hold_qty})")

            if self.settings.battletag.lower() in reply.lower():
                asyncio.create_task(self._start_bnet_reveal(conv_id))

            buyer_qty_alert = parse_buyer_quantity(msg_content)
            await self.discord.send_offer_alert(
                player=player,
                item_name=item_name or "Unknown",
                message_preview=msg_content,
                reply_sent=reply,
                intent=resolved_intent,
                ai_used=ai_used,
                listed_price=listed_price,
                offered_price=offered_price,
                item_uuid=item_uuid,
                quantity=item_quantity,
                buyer_quantity=buyer_qty_alert,
                caller_bot=self,
            )

        else:
            log.error(f"Failed to send reply to {player}")
            await self.repo.log_action("ERROR", conv_id, f"Send failed for {player}")

    # One full poll iteration: expire stale holds, refresh inventory,
    # fetch conversations, process actionable ones, then drain notifications.
    async def _poll_cycle(self, page: Page) -> None:

        self.cycle += 1
        await self._reload_settings_from_file()

        try:
            expired = await self.repo.expire_stale_holds(self.settings.hold_expiry_seconds)
            if expired:
                log.info(f"[holds] Auto-released {expired} stale hold(s)")
        except Exception as e:
            log.debug(f"[holds] Expire check failed: {e}")

        try:
            await self.inventory.refresh()
        except Exception as e:
            log.debug(f"[inventory] Refresh failed: {e}")

        conversations = await self._fetch_conversations(page)

        my_id = self.my_id
        actionable = [
            c for c in conversations
            if c.get("lastMessage", {}).get("userId") != my_id
        ]

        unread_count = sum(1 for c in conversations if c.get("unreadCount", 0) > 0)

        self._poll_stats = {
            "cycle": self.cycle,
            "actionable": len(actionable),
            "unread": unread_count,
            "total": len(conversations),
        }

        if actionable:
            log.debug(
                f"Poll #{self.cycle} — {len(actionable)} actionable, {unread_count} unread"
            )
        elif self.cycle % 50 == 0:

            log.debug(f"Poll #{self.cycle} — idle")

        for conv in actionable:
            try:
                await self._process_conversation(page, conv)
            except Exception as e:
                log.opt(exception=True).error(f"Error processing conversation: {e}")
                await self.repo.log_action("ERROR", conv.get("id"), str(e))

        try:
            if page.url != self.settings.target_url and page.url != self.settings.target_url + "/":
                await page.goto(self.settings.target_url, wait_until="load", timeout=15_000)
        except Exception as e:
            log.debug(f"[poll] Homepage nav after cycle failed (non-fatal): {e}")

        await self._poll_notifications(page)

    # Processes queued internal events (e.g. item hold released → notify waitlist buyers).
    async def _poll_notifications(self, page: Page) -> None:

        while not self.notification_queue.empty():
            event = await self.notification_queue.get()
            if event.get("type") == "item_released":
                item_uuid = event.get("item_uuid", "")
                waitlist  = event.get("waitlist", [])
                log.info(f"[notify] Hold released for {item_uuid[:12]} — notifying {len(waitlist)} buyer(s)")
                for wconv in waitlist:
                    player_name = wconv.get("player_name", "")
                    conv_id     = wconv.get("id", "")
                    player_id   = wconv.get("player_id", "")
                    msg_preview = wconv.get("raw_message", "")
                    item_name   = wconv.get("item_name") or "the item"
                    listed_price = wconv.get("listed_price")
                    price_str   = f"{listed_price:,}" if listed_price else "listed price"
                    notify_text = (
                        f"Good news {player_name}! "
                        f"The item you were interested in is available again. "
                        f"{item_name} — {price_str} gold. "
                        f"BattleTag: {self.settings.battletag} if you still want it!"
                    )
                    ok = await self._send_reply(
                        page=page,
                        conv_id=conv_id,
                        player_id=player_id,
                        player_name=player_name,
                        text=notify_text,
                        msg_preview=msg_preview,
                    )
                    if ok:

                        await self.repo.record_reply(
                            conv_id=conv_id,
                            player=player_name,
                            player_id=player_id,
                            reply=notify_text,
                            item_uuid=item_uuid,
                            item_name=item_name,
                            listed_price=listed_price,
                            status="replied",
                        )
                        log.success(f"[notify] Notified {player_name} that item is available")

    async def _daily_summary_task(self) -> None:

        log.info(f"Daily summary scheduler started (fires at {self.settings.summary_time})")
        while self._running:
            now    = datetime.now()
            target = now.replace(
                hour=int(self.settings.summary_time.split(":")[0]),
                minute=int(self.settings.summary_time.split(":")[1]),
                second=0,
                microsecond=0,
            )
            if now >= target:

                from datetime import timedelta
                target = target + timedelta(days=1)
            sleep_secs = (target - now).total_seconds()
            log.debug(f"Daily summary fires in {sleep_secs/3600:.1f}h")
            await asyncio.sleep(sleep_secs)
            if not self._running:
                break
            stats = await self.repo.get_daily_stats()
            acct_name = getattr(self.settings, "username", "") or self.settings.battletag.split("#")[0]
            await self.discord.send_daily_summary(stats, account_name=acct_name)

    async def run(self) -> None:

        if self.account_id:
            add_session_logger(self.settings.log_dir, self.account_id)
            self._log_ctx = log.contextualize(account_id=self.account_id)
            self._log_ctx.__enter__()

        await init_db(self.settings.db_path)
        log.success(f"{self._label}Database ready")

        self._running = True

        self._daily_task = asyncio.create_task(self._daily_summary_task())

        control_server: Optional[ControlServer] = None
        if self._shared_control_server is not None:
            control_server = self._shared_control_server
        elif self._start_services and self.settings.control_server_enabled:
            control_server = ControlServer(
                repo=self.repo,
                settings=self.settings,
                notification_queue=self.notification_queue,
            )
            try:
                await control_server.start()
            except Exception as e:
                log.warning(f"{self._label}Control server could not start (non-fatal): {e}")
                control_server = None

        if self._start_services and self.discord.enabled and self.discord.token:
            self._discord_task = asyncio.create_task(self.discord.start())
            log.info(f"{self._label}[discord] Discord bot task started (syncing in background)")

        try:
            while self._running:
                try:

                    proxy = None
                    if hasattr(self, 'dashboard') and getattr(self.dashboard, 'accounts', None):
                        sel = getattr(self.dashboard, '_selected_tab', 0)
                        if 0 <= sel < len(self.dashboard.accounts):
                            proxy = self.dashboard.accounts[sel].proxy
                            log.debug(f"Using proxy {proxy} for account selection {sel}")
                    _browser, page = await self._connect_browser(proxy=proxy)

                    self.api.set_page(page)

                    if hasattr(self, 'dashboard'):
                        self.dashboard.set_status("RUNNING")

                    self.listing_manager = ListingManager(
                        page=page,
                        api=self.api,
                    )
                    log.info("[listing] Listing manager initialized")

                    if self.discord.enabled and self.discord.token:
                        self.discord.listing_manager = self.listing_manager
                        self.discord.repo = self.repo
                        self.discord.bot_ref = self

                    if control_server:
                        control_server.listing_manager = self.listing_manager
                        control_server._bot_ref = self
                        control_server.register_bot(self)

                    await self._init_websocket(page)

                    await self._resolve_auto_fields(page)

                    if not self.my_id:
                        self.my_id = await self._fetch_my_id(page)
                    if not self.my_id and self.settings.my_user_id:
                        self.my_id = self.settings.my_user_id
                    log.info(f"{self._label}Authenticated as: {self.my_id[:20]}...")

                    self.inventory.REFRESH_INTERVAL = self.settings.inventory_refresh_interval
                    try:
                        count = await self.inventory.refresh(force=True)
                        log.success(f"{self._label}[inventory] Loaded {count} listing(s) from inventory")
                    except Exception as e:
                        log.warning(f"{self._label}[inventory] Initial load failed (will retry): {e}")

                    log.info(f"{self._label}AI enabled: {self.settings.use_ai}  |  model: {self.settings.gemini_model}")
                    log.info(f"{self._label}Interval: {self.settings.check_interval}s  |  batch window: {self._batch_window}s")
                    ws_status = "browser-WS" if self.ws._browser_listener_active else "GUI-only"
                    log.info(f"Messaging: {ws_status}")
                    log.info(f"{self._label}BNet auto-accept: {'yes' if self.settings.auto_accept_bnet_reveal else 'no'}")
                    log.info(f"{self._label}Hold expiry: {self.settings.hold_expiry_seconds}s  |  Inventory refresh: {self.settings.inventory_refresh_interval}s")

                    if self._start_services:
                        acct_names = getattr(self, '_all_account_names', None)
                        await self.discord.send_bot_online_notice(account_names=acct_names)

                    while self._running:
                        await self._poll_cycle(page)

                        interval = self.settings.check_interval
                        log.debug(f"Sleeping up to {interval}s (or until WS event)...")
                        try:
                            await asyncio.wait_for(
                                self.ws._wake_event.wait(), timeout=interval
                            )
                            log.debug("[ws] Woke early — real-time event received")
                        except asyncio.TimeoutError:
                            pass
                        finally:
                            self.ws._wake_event.clear()

                except KeyboardInterrupt:
                    log.info("Keyboard interrupt — shutting down.")
                    self._running = False
                    break
                except Exception as e:
                    log.error(f"Bot loop error: {e}")
                    if hasattr(self, 'dashboard'):
                        self.dashboard.set_status("ERROR")
                    await self.discord.send_error_alert(str(e))
                    if not self.settings.restart_on_error:
                        log.error("restart_on_error=false — exiting.")
                        break
                    log.warning("Restarting in 30 seconds...")
                    await asyncio.sleep(30)
        finally:

            # Cancel background tasks first so they don't linger and print
            # "Task destroyed but it is pending" warnings on shutdown.
            for _attr in ('_daily_task', '_discord_task'):
                _t = getattr(self, _attr, None)
                if _t and not _t.done():
                    _t.cancel()
                    try:
                        await _t
                    except (asyncio.CancelledError, Exception):
                        pass

            if hasattr(self, '_log_ctx'):
                try:
                    self._log_ctx.__exit__(None, None, None)
                except Exception:
                    pass

            if hasattr(self, 'dashboard'):
                self.dashboard.set_status("STOPPED")

            try:
                await self.discord.send_bot_offline_notice()
            except Exception:
                pass

            try:
                await self.ws.disconnect()
            except Exception:
                pass

            if control_server:
                try:
                    await control_server.stop()
                except Exception:
                    pass

            if self._start_services and self.discord.enabled and self.discord.token:
                try:
                    await self.discord.stop()
                except Exception:
                    pass

        log.info(f"{self._label}Bot stopped.")

    def stop(self) -> None:

        self._running = False
