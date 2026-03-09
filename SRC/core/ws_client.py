import asyncio
import json
import time
from typing import Optional, Callable, Any

from ..utils.logger import log

WS_URL = "wss://realtime.diablo.trade/connection/websocket"
APP_ID = "project-d4"
TOKEN_REFRESH_INTERVAL = 20 * 60

# Manages the browser-side WebSocket connection to diablo.trade's realtime service.
# Rather than keeping a Python WS socket, we inject JS into the browser page
# so the connection shares the real login session and cookies.
class DiabloWebSocket:

    def __init__(self):
        self._token: str = ""
        self._user_id: str = ""
        self._username: str = ""
        self._on_message: Optional[Callable] = None
        self._page = None
        self._last_token_time: float = 0.0
        self._cookies: dict = {}
        self._browser_listener_active: bool = False
        self._wake_event: asyncio.Event = asyncio.Event()

    @property
    def connected(self) -> bool:

        return self._browser_listener_active

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def username(self) -> str:
        return self._username

    # Fetches the session JWT and realtime token from the browser's /api/auth/session
    # and /api/realtime/token endpoints. Must be called before any WS operations.
    async def fetch_credentials(self, page) -> tuple[str, str, str]:

        self._page = page

        try:
            session_data = await page.evaluate(
                "async () => {"
                "  const r = await fetch('/api/auth/session');"
                "  if (!r.ok) return {__error: r.status};"
                "  const ct = r.headers.get('content-type') || '';"
                "  if (!ct.includes('application/json')) return {__error: 'not-json'};"
                "  return await r.json();"
                "}"
            )
            if isinstance(session_data, dict) and "__error" in session_data:
                log.warning(f"[ws] Session API returned {session_data['__error']} -- not logged in?")
                return "", "", ""
        except Exception as e:
            log.error(f"[ws] Failed to fetch session: {e}")
            return "", "", ""

        access_token = session_data.get("accessToken", "")
        user_id = ""
        username = ""

        if access_token and ":" in access_token:

            parts = access_token.split(":")
            if len(parts) >= 2:
                user_id = parts[1]

        user_data = session_data.get("user", {})
        username = user_data.get("name", "")

        try:
            token_data = await page.evaluate(
                "async () => { "
                "  const r = await fetch('/api/realtime/token'); "
                "  if (!r.ok) return {__error: r.status}; "
                "  return await r.json(); "
                "}"
            )
            if isinstance(token_data, dict) and "__error" in token_data:
                log.error(f"[ws] Realtime token API returned HTTP {token_data['__error']} — not logged in?")
                return "", user_id, username
            token = token_data.get("token", "") if isinstance(token_data, dict) else ""
        except Exception as e:
            log.error(f"[ws] Failed to fetch realtime token: {e}")
            return "", user_id, username

        self._token = token
        self._user_id = user_id
        self._username = username
        self._last_token_time = time.time()

        if token:
            log.success(f"[ws] Realtime token obtained: {token[:40]}...")
        else:
            log.warning("[ws] Realtime token is EMPTY — WebSocket auth will fail")

        try:
            raw_cookies = await page.context.cookies()
            self._cookies = {c["name"]: c["value"] for c in raw_cookies}
            log.debug(f"[ws] Forwarding {len(self._cookies)} cookies to WS session")
        except Exception as e:
            log.warning(f"[ws] Could not extract browser cookies: {e}")
            self._cookies = {}

        log.info(f"[ws] Credentials: user_id={user_id[:20]}... username={username}")
        return token, user_id, username

    async def auto_detect_identity(self, page) -> dict:

        try:
            session_data = await page.evaluate(
                "async () => {"
                "  const r = await fetch('/api/auth/session');"
                "  if (!r.ok) return {__error: r.status};"
                "  const ct = r.headers.get('content-type') || '';"
                "  if (!ct.includes('application/json')) return {__error: 'not-json'};"
                "  return await r.json();"
                "}"
            )
            if isinstance(session_data, dict) and "__error" in session_data:
                log.warning(f"[ws] auto_detect_identity: session API returned {session_data['__error']}")
                return {}
        except Exception as e:
            log.warning(f"[ws] auto_detect_identity failed: {e}")
            return {}

        access_token = session_data.get("accessToken", "")
        user_id = ""
        if access_token and ":" in access_token:
            parts = access_token.split(":")
            if len(parts) >= 2:
                user_id = parts[1]

        user_data = session_data.get("user", {})
        return {
            "user_id": user_id,
            "username": user_data.get("name", ""),
            "battletag_acknowledged": user_data.get("battleTagAcknowledged", False),
            "role": user_data.get("role", ""),
        }

    async def disconnect(self) -> None:

        self._browser_listener_active = False
        if self._page:
            try:
                await self._page.evaluate(
                    """() => {
                        if (window.__d4wsRef) {
                            window.__d4wsRef.close();
                            window.__d4wsListening = false;
                        }
                    }"""
                )
            except Exception:
                pass
        log.info("[ws] Browser listener disconnected")

    async def reconnect(self) -> bool:

        log.info("[ws] Reconnecting browser listener...")
        await self.disconnect()
        if not self._page:
            return False
        try:
            token, _, _ = await self.fetch_credentials(self._page)
            if not token:
                return False
            return await self.start_browser_listener(self._page)
        except Exception as e:
            log.warning(f"[ws] Reconnect failed: {e}")
            return False

    async def subscribe_conversation(self, conv_id: str) -> bool:

        if self._page and conv_id:
            await self.subscribe_conv_via_browser(self._page, conv_id)
            return True
        return False

    async def send_message_via_browser(self, page, conv_id: str, text: str) -> bool:

        if not self._token:
            log.warning("[ws] No token for browser-side send")
            return False
        if not page:
            log.warning("[ws] No page reference for browser-side send")
            return False

        try:
            result = await page.evaluate(
                """async ([token, convId, msgText]) => {
                    return new Promise((resolve) => {
                        const ws = new WebSocket(
                            "wss://realtime.diablo.trade/connection/websocket"
                        );
                        const channel =
                            "chat:project-d4:conversation:" + convId + ":submit";

                        ws.onopen = function() {
                            ws.send(JSON.stringify({
                                "connect": {
                                    "data": {"appId": "project-d4"},
                                    "name": "js",
                                    "headers": {
                                        "Authorization": "Bearer " + token
                                    }
                                },
                                "id": 1
                            }));
                        };

                        ws.onmessage = function(event) {
                            const resp = JSON.parse(event.data);
                            // Handshake accepted — send the message
                            if (resp.id === 1 && resp.connect) {
                                ws.send(JSON.stringify({
                                    "publish": {
                                        "channel": channel,
                                        "data": {
                                            "content": msgText,
                                            "type": "text",
                                            "meta": {
                                                "richDoc": {
                                                    "type": "doc",
                                                    "content": [{
                                                        "type": "paragraph",
                                                        "content": [{
                                                            "type": "text",
                                                            "text": msgText
                                                        }]
                                                    }]
                                                }
                                            }
                                        }
                                    },
                                    "id": 2
                                }));
                            }
                            // Publish ack received — success
                            if (resp.id === 2) {
                                ws.close();
                                resolve(!resp.error);
                            }
                        };

                        ws.onerror = function() { resolve(false); };
                        // Safety timeout: 10 s
                        setTimeout(function() { ws.close(); resolve(false); }, 10000);
                    });
                }""",
                [self._token, conv_id, text],
            )
            ok = bool(result)
            if ok:
                log.debug(
                    f"[ws-browser] Message sent to {conv_id[:30]}... ({len(text)} chars)"
                )
            else:
                log.warning("[ws-browser] Browser-side WS send returned false")
            return ok
        except Exception as e:
            log.warning(f"[ws-browser] send_message_via_browser failed: {e}")
            return False

    async def send_typing(self, conv_id: str, is_typing: bool = True) -> None:

        if self._page:
            await self.send_typing_via_browser(self._page, conv_id, is_typing)

    async def mark_as_read(self, conv_id: str, message_id: str) -> bool:

        if not self._page:
            return False
        try:
            result = await self._page.evaluate(
                """async (params) => {
                    const r = await fetch('/api/chat/mark-as-read', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            conversationId: params.convId,
                            messageId: params.msgId
                        })
                    });
                    return r.status;
                }""",
                {"convId": conv_id, "msgId": message_id},
            )
            ok = result == 200
            if ok:
                log.debug(f"[ws] Marked as read: {conv_id[:30]}... msg={message_id}")
            return ok
        except Exception as e:
            log.debug(f"[ws] mark-as-read failed: {e}")
            return False

    async def send_typing_via_browser(
        self, page, conv_id: str, is_typing: bool = True
    ) -> None:

        if not self._token or not self._user_id or not page:
            return
        try:
            await page.evaluate(
                """async ([token, convId, userId, isTyping]) => {
                    return new Promise((resolve) => {
                        const ws = new WebSocket(
                            "wss://realtime.diablo.trade/connection/websocket"
                        );
                        ws.onopen = function() {
                            ws.send(JSON.stringify({
                                "connect": {
                                    "data": {"appId": "project-d4"},
                                    "name": "js",
                                    "headers": {"Authorization": "Bearer " + token}
                                },
                                "id": 1
                            }));
                        };
                        ws.onmessage = function(event) {
                            const resp = JSON.parse(event.data);
                            if (resp.id === 1 && resp.connect) {
                                ws.send(JSON.stringify({
                                    "publish": {
                                        "channel":
                                            "ephemeral:project-d4:chat:" + convId + ":typing",
                                        "data": {
                                            "appId": "project-d4",
                                            "conversationId": convId,
                                            "userId": userId,
                                            "isTyping": isTyping,
                                            "ts": Date.now(),
                                            "type": "typing"
                                        }
                                    },
                                    "id": 2
                                }));
                                setTimeout(function() { ws.close(); resolve(); }, 800);
                            }
                        };
                        ws.onerror = function() { resolve(); };
                        setTimeout(function() { ws.close(); resolve(); }, 5000);
                    });
                }""",
                [self._token, conv_id, self._user_id, is_typing],
            )
        except Exception as e:
            log.debug(f"[ws-br] Typing indicator error (non-fatal): {e}")

    async def start_browser_listener(self, page) -> bool:

        if not self._token or not self._user_id:
            log.warning("[ws-br] Cannot start browser listener: no token or user_id")
            return False
        if not page:
            return False
        if self._browser_listener_active:
            log.debug("[ws-br] Browser listener already active")
            return True

        async def _on_incoming(data: dict) -> None:
            try:
                await self._handle_push(data)
            except Exception as exc:
                log.debug(f"[ws-br] handle_push error: {exc}")

        try:
            await page.expose_function("__d4wsIncoming", _on_incoming)
        except Exception:
            pass

        try:
            await page.evaluate(
                """([token, userId]) => {
                    // Guard: prevent double-start.  Once this runs, the inner
                    // connect() loop handles reconnects autonomously.
                    if (window.__d4wsConnecting) return;
                    window.__d4wsConnecting = true;

                    const subscribedChannels = new Set();
                    const seenMessages = new Set();
                    let sequence = 200;
                    let pingInterval;

                    function nextId() { return ++sequence; }

                    function subscribeToChannel(channel) {
                        var ws = window.__d4wsRef;
                        if (!ws || ws.readyState !== WebSocket.OPEN) return;
                        if (subscribedChannels.has(channel)) return;
                        subscribedChannels.add(channel);
                        ws.send(JSON.stringify({
                            "subscribe": {"channel": channel},
                            "id": nextId()
                        }));
                    }

                    function subscribeToConv(convId) {
                        subscribeToChannel(
                            "chat:project-d4:conversation:" + convId + ":user:" + userId
                        );
                    }

                    // Expose so Python can call window.__d4wsSubscribeConv(convId)
                    window.__d4wsSubscribeConv = subscribeToConv;

                    function connect() {
                        var ws = new WebSocket(
                            "wss://realtime.diablo.trade/connection/websocket"
                        );
                        window.__d4wsRef = ws;
                        window.__d4wsListening = false;

                        ws.onopen = function() {
                            window.__d4wsListening = true;
                            ws.send(JSON.stringify({
                                "connect": {
                                    "data": {"appId": "project-d4"},
                                    "name": "js",
                                    "headers": {"Authorization": "Bearer " + token}
                                },
                                "id": 1
                            }));
                            // Keep-alive ping every 20 seconds
                            clearInterval(pingInterval);
                            pingInterval = setInterval(function() {
                                if (ws.readyState === WebSocket.OPEN) {
                                    ws.send(JSON.stringify({}));
                                }
                            }, 20000);
                        };

                        ws.onmessage = function(event) {
                            var msg = JSON.parse(event.data);

                            // Handshake accepted — subscribe to inbox
                            if (msg.id === 1 && msg.connect) {
                                subscribeToChannel(
                                    "chat:project-d4:user:" + userId + ":inbox"
                                );
                            }

                            // Forward push events to Python
                            if (msg.push) {
                                var pubData = msg.push.pub && msg.push.pub.data;
                                if (pubData) {
                                    var convId = pubData.conversationId;

                                    // Auto-subscribe to the conversation channel
                                    if (convId) subscribeToConv(convId);

                                    // Deduplication — avoid firing the Python
                                    // handler twice for the same message
                                    var content = pubData.snippet || pubData.content ||
                                        (pubData.lastMessage && pubData.lastMessage.content) || "";
                                    var ts = pubData.ts ||
                                        (pubData.lastMessage && pubData.lastMessage.ts) ||
                                        Date.now();
                                    var msgKey = pubData.messageId || pubData.id ||
                                        (ts + "-" + content);

                                    if (!seenMessages.has(msgKey)) {
                                        seenMessages.add(msgKey);
                                        // Keep cache bounded
                                        if (seenMessages.size > 200) {
                                            seenMessages.delete(
                                                seenMessages.values().next().value
                                            );
                                        }
                                        window.__d4wsIncoming(msg).catch(function() {});
                                    }
                                }
                            }
                        };

                        ws.onclose = function() {
                            window.__d4wsListening = false;
                            clearInterval(pingInterval);
                            subscribedChannels.clear();
                            // Auto-reconnect after 1 s
                            setTimeout(connect, 1000);
                        };

                        ws.onerror = function() {
                            ws.close();
                        };
                    }

                    connect();
                }""",
                [self._token, self._user_id],
            )
            self._browser_listener_active = True
            log.success(
                "[ws-br] Persistent browser-side listener started "
                "(inbox subscribed; auto-subscribes to conversation channels)"
            )
            return True
        except Exception as e:
            log.warning(f"[ws-br] Failed to start browser listener: {e}")
            return False

    async def subscribe_conv_via_browser(self, page, conv_id: str) -> None:

        if not self._browser_listener_active or not page:
            return
        try:
            await page.evaluate(
                """(convId) => {
                    if (window.__d4wsSubscribeConv) {
                        window.__d4wsSubscribeConv(convId);
                    }
                }""",
                conv_id,
            )
            log.debug(f"[ws-br] Subscribed browser listener to conv {conv_id[:30]}...")
        except Exception as e:
            log.debug(f"[ws-br] subscribe_conv_via_browser error (non-fatal): {e}")

    def set_message_handler(self, handler: Callable) -> None:

        self._on_message = handler

    async def _handle_push(self, data: dict) -> None:

        push = data.get("push")
        if not push:
            return

        pub = push.get("pub")
        if not pub:
            return

        self._wake_event.set()

        pub_data = pub.get("data") or {}
        msg_type = pub_data.get("type", "")

        if msg_type == "typing":
            return

        if not self._on_message:
            return

        sender = pub_data.get("userId", "")
        if sender and sender == self._user_id:
            return

        try:
            await self._on_message(pub_data)
        except Exception as e:
            log.debug(f"[ws] Push handler error for type '{msg_type}': {e}")
