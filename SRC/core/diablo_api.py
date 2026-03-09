import asyncio
import json
from typing import Optional, List, Dict, Any

from .api import auth as api_auth
from .api import bnet as api_bnet
from .api import chat as api_chat
from .api import listings as api_listings
from ..utils.logger import log

# Thin wrapper around the site's internal APIs.
# All actual HTTP calls are delegated to the core/api submodules;
# this class owns session-refresh logic and optional WS fallback.
class DiabloAPI:

    _MAX_AUTH_RETRIES = 2

    def __init__(self, ws=None, page=None):

        self._ws = ws
        self._page = page
        self._session_refreshing = False

    def set_page(self, page) -> None:

        self._page = page
        if self._ws:
            self._ws._page = page

    def set_ws(self, ws) -> None:

        self._ws = ws

    # Re-navigates to the homepage and re-fetches the realtime token when a 403 is hit.
    # Guards against concurrent refreshes with a flag so only one attempt runs at a time.
    async def _refresh_session(self) -> bool:

        if self._session_refreshing:

            await asyncio.sleep(2)
            return True
        self._session_refreshing = True
        try:
            log.warning("[api] Session appears expired (HTTP 403) — attempting refresh...")
            if not self._page:
                log.error("[api] No page reference — cannot refresh session")
                return False

            try:
                await self._page.goto(
                    "https://diablo.trade", wait_until="load", timeout=20_000
                )
                await asyncio.sleep(2)
            except Exception as e:
                log.warning(f"[api] Session refresh navigation failed: {e}")
                return False

            if self._ws:
                try:
                    token, user_id, username = await self._ws.fetch_credentials(self._page)
                    if token:
                        log.success(f"[api] Session refreshed successfully (user={username})")
                        return True
                    else:
                        log.error("[api] Session refresh got empty token — user may need to re-login")
                        return False
                except Exception as e:
                    log.error(f"[api] Credential refresh failed: {e}")
                    return False
            else:

                try:
                    check = await api_auth.session_is_available(self._page)
                    if check:
                        log.success("[api] Session refreshed (no WS client)")
                        return True
                except Exception:
                    pass
                return False
        finally:
            self._session_refreshing = False

    async def get_realtime_token(self) -> str:

        if not self._page:
            log.warning("[api] No page reference for get_realtime_token")
            return ""

        for attempt in range(1, self._MAX_AUTH_RETRIES + 1):
            try:
                data = await api_auth.fetch_realtime_token(self._page)
                if isinstance(data, dict) and "__error" in data:
                    status = data["__error"]
                    if status == 403 and attempt < self._MAX_AUTH_RETRIES:
                        log.warning(f"[api] Realtime token API returned HTTP 403 (attempt {attempt}) — refreshing session...")
                        if await self._refresh_session():
                            continue
                    log.error(f"[api] Realtime token API returned HTTP {status}")
                    return ""
                return data.get("token", "") if isinstance(data, dict) else ""
            except Exception as e:
                log.error(f"[api] get_realtime_token failed: {e}")
                return ""
        return ""

    async def get_user_id(self) -> dict:

        if not self._page:
            return {}
        try:
            session = await api_auth.fetch_user_identity(self._page)
            if isinstance(session, dict) and "__error" in session:
                log.warning(f"[api] get_user_id: session API returned {session['__error']}")
                return {}
            access_token = session.get("accessToken", "")
            user_id = ""
            if access_token and ":" in access_token:
                parts = access_token.split(":")
                if len(parts) >= 2:
                    user_id = parts[1]
            user_data = session.get("user", {})
            return {
                "user_id": user_id,
                "username": user_data.get("name", ""),
                "battletag_acknowledged": user_data.get("battleTagAcknowledged", False),
                "role": user_data.get("role", ""),
            }
        except Exception as e:
            log.warning(f"[api] get_user_id failed: {e}")
            return {}

    async def get_conversation_messages(
        self, conv_id: str, limit: int = 20
    ) -> List[dict]:

        if not self._page:
            return []
        try:
            result = await api_chat.fetch_conversation_messages(self._page, conv_id, limit)
            if isinstance(result, dict) and "__error" in result:
                log.debug(f"[api] Messages API returned HTTP {result['__error']} for {conv_id[:20]}")
                return []
            return result if isinstance(result, list) else []
        except Exception as e:
            log.debug(f"[api] get_conversation_messages failed: {e}")
            return []

    async def get_conversations(self) -> List[dict]:

        if not self._page:
            return []

        for attempt in range(1, self._MAX_AUTH_RETRIES + 1):
            try:
                result = await api_chat.fetch_conversations(self._page)
                if isinstance(result, dict) and "__error" in result:
                    status = result["__error"]
                    if status == 403 and attempt < self._MAX_AUTH_RETRIES:
                        log.warning(f"[api] Conversations API returned HTTP 403 (attempt {attempt}) — refreshing session...")
                        refreshed = await self._refresh_session()
                        if refreshed:
                            continue
                        else:
                            log.error("[api] Session refresh failed — returning empty conversations")
                            return []
                    log.warning(f"[api] Conversations API returned HTTP {status}")
                    return []
                return result if isinstance(result, list) else []
            except Exception as e:
                err_str = str(e)
                if "closed" in err_str.lower() and ("page" in err_str.lower() or "browser" in err_str.lower() or "context" in err_str.lower() or "target" in err_str.lower()):
                    raise
                log.error(f"[api] get_conversations failed: {e}")
                return []
        return []

    async def send_message(self, conv_id: str, text: str) -> bool:

        if self._ws and self._page:
            return await self._ws.send_message_via_browser(self._page, conv_id, text)
        log.warning("[api] send_message: no WS or page available")
        return False

    async def send_reply(self, conv_id: str, text: str,
                         reply_to_id: str = "",
                         reply_author_name: str = "",
                         reply_content: str = "") -> bool:

        if not self._ws or not self._page:
            log.warning("[api] send_reply: no WS or page available")
            return False

        if not reply_to_id:
            return await self.send_message(conv_id, text)

        token = self._ws._token
        if not token:
            log.warning("[api] send_reply: no token")
            return False

        try:
            result = await api_chat.send_reply_message(
                self._page,
                token,
                conv_id,
                text,
                reply_to_id,
                reply_author_name,
                reply_content,
            )
            ok = bool(result)
            if ok:
                log.debug(f"[api] Reply sent to {conv_id[:30]}... (replyTo={reply_to_id})")
            return ok
        except Exception as e:
            log.warning(f"[api] send_reply failed: {e}")
            return False

    async def send_typing(self, conv_id: str, is_typing: bool = True) -> None:

        if self._ws and self._page:
            await self._ws.send_typing_via_browser(self._page, conv_id, is_typing)

    async def mark_as_read(self, conv_id: str, message_id: str) -> bool:

        if self._ws:
            return await self._ws.mark_as_read(conv_id, message_id)
        if not self._page:
            return False
        try:
            result = await api_chat.mark_as_read(self._page, conv_id, message_id)
            return result == 200
        except Exception as e:
            log.debug(f"[api] mark_as_read failed: {e}")
            return False

    async def send_system_message(self, conv_id: str, content: str,
                                   kind: str = "info",
                                   destined_user_id: str = "") -> bool:

        if not self._ws or not self._page:
            return False
        token = self._ws._token
        if not token:
            return False

        try:
            result = await api_chat.send_system_message(
                self._page,
                token,
                conv_id,
                content,
                kind,
                destined_user_id,
            )
            ok = bool(result)
            if ok:
                log.debug(f"[api] System message ({kind}) sent to {conv_id[:30]}...")
            return ok
        except Exception as e:
            log.warning(f"[api] send_system_message failed: {e}")
            return False

    async def start_bnet_reveal(self, conv_id: str) -> bool:

        if not self._page:
            return False
        try:
            result = await api_bnet.start_reveal(self._page, conv_id)
            ok = bool(result)
            if ok:
                log.info(f"[api] BNet reveal request sent for conv {conv_id[:30]}...")
            else:
                log.warning(f"[api] BNet reveal start returned non-OK for {conv_id[:30]}...")
            return ok
        except Exception as e:
            log.warning(f"[api] start_bnet_reveal failed: {e}")
            return False

    async def accept_bnet_reveal(self, conv_id: str) -> bool:

        if not self._page:
            return False
        try:
            result = await api_bnet.accept_reveal(self._page, conv_id)
            ok = bool(result)
            if ok:
                log.info(f"[api] BNet reveal accepted for conv {conv_id[:30]}...")
            else:
                log.warning(f"[api] BNet reveal accept returned non-OK for {conv_id[:30]}...")
            return ok
        except Exception as e:
            log.warning(f"[api] accept_bnet_reveal failed: {e}")
            return False

    async def get_bnet_reveal_status(self, conv_id: str) -> Optional[dict]:

        if not self._page:
            return None
        try:
            result = await api_bnet.fetch_reveal_status(self._page, conv_id)
            if isinstance(result, dict) and "__error" in result:
                log.debug(f"[api] BNet reveal status returned HTTP {result['__error']}")
                return None
            return result if isinstance(result, dict) else None
        except Exception as e:
            log.warning(f"[api] get_bnet_reveal_status failed: {e}")
            return None

    async def get_listing(self, uuid: str) -> Optional[dict]:

        if not self._page:
            return None
        try:
            result = await api_listings.fetch_listing(self._page, uuid)
            if isinstance(result, dict) and "__error" in result:
                log.debug(f"[api] Listing API returned HTTP {result['__error']} for {uuid[:12]}")
                return None
            return result if isinstance(result, dict) else None
        except Exception as e:
            log.debug(f"[api] get_listing failed for {uuid}: {e}")
            return None

    async def get_my_listings(
        self,
        page_num: int = 1,
        take: int = 10,
        game_mode: str = "SEASONAL_SOFTCORE",
        sold: bool = False,
        removed: bool = False,
    ) -> Optional[str]:

        if not self._page:
            return None
        try:
            result = await api_listings.fetch_my_listings(
                self._page,
                page_num,
                take,
                game_mode,
                sold,
                removed,
            )
            if result:
                log.debug(f"[api] get_my_listings returned {len(result)} chars of RSC data")
            return result
        except Exception as e:
            log.warning(f"[api] get_my_listings failed: {e}")
            return None

    async def mark_item_sold(
        self,
        item_id: str,
        sold_price: int = 0,
        quantity: int = 1,
        game_mode: str = "SEASONAL_SOFTCORE",
    ) -> bool:

        if not self._page:
            return False
        try:
            result = await api_listings.mark_item_sold(
                self._page,
                item_id,
                sold_price,
                quantity,
                game_mode,
            )
            ok = bool(result)
            if ok:
                log.info(f"[api] Item {item_id[:12]}... marked as SOLD (soldPrice={sold_price:,}, qty={quantity})")
            else:
                log.warning(f"[api] mark_item_sold returned non-OK for {item_id[:12]}...")
            return ok
        except Exception as e:
            log.warning(f"[api] mark_item_sold failed: {e}")
            return False

    async def fetch_credentials(self) -> tuple:

        if self._ws and self._page:
            return await self._ws.fetch_credentials(self._page)
        return "", "", ""

    async def auto_detect_identity(self) -> dict:

        if self._ws and self._page:
            return await self._ws.auto_detect_identity(self._page)
        return await self.get_user_id()
