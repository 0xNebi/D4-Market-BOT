from __future__ import annotations

from typing import Any

async def fetch_conversation_messages(page, conv_id: str, limit: int) -> Any:
    return await page.evaluate(
        """async ([convId, limit]) => {
            const r = await fetch(
                '/api/chat/messages?conversationId=' + convId + '&limit=' + limit
            );
            if (!r.ok) return {__error: r.status};
            return await r.json();
        }""",
        [conv_id, limit],
    )

async def fetch_conversations(page) -> Any:
    return await page.evaluate(
        "async () => {"
        "  const r = await fetch('/api/chat/conversations');"
        "  if (!r.ok) return {__error: r.status};"
        "  return await r.json();"
        "}"
    )

async def send_reply_message(
    page,
    token: str,
    conv_id: str,
    text: str,
    reply_to_id: str,
    reply_author_name: str,
    reply_content: str,
) -> Any:
    return await page.evaluate(
        """async ([token, convId, msgText, replyToId, replyAuthorName, replyContent]) => {
            return new Promise((resolve) => {
                const ws = new WebSocket("wss://realtime.diablo.trade/connection/websocket");
                const channel = "chat:project-d4:conversation:" + convId + ":submit";

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
                                "channel": channel,
                                "data": {
                                    "content": msgText,
                                    "type": "text",
                                    "meta": {
                                        "richDoc": {
                                            "type": "doc",
                                            "content": [{
                                                "type": "paragraph",
                                                "content": [{"type": "text", "text": msgText}]
                                            }]
                                        },
                                        "replyToId": replyToId,
                                        "replyTo": {
                                            "id": replyToId,
                                            "authorName": replyAuthorName,
                                            "authorAvatarSrc": "",
                                            "authorAvatarFallback": replyAuthorName.substring(0, 2).toUpperCase(),
                                            "content": replyContent
                                        }
                                    }
                                }
                            },
                            "id": 2
                        }));
                    }
                    if (resp.id === 2) {
                        ws.close();
                        resolve(!resp.error);
                    }
                };

                ws.onerror = function() { resolve(false); };
                setTimeout(function() { ws.close(); resolve(false); }, 10000);
            });
        }""",
        [token, conv_id, text, reply_to_id, reply_author_name, reply_content],
    )

async def mark_as_read(page, conv_id: str, message_id: str) -> Any:
    return await page.evaluate(
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

async def send_system_message(
    page,
    token: str,
    conv_id: str,
    content: str,
    kind: str,
    destined_user_id: str,
) -> Any:
    return await page.evaluate(
        """async ([token, convId, content, kind, destinedUserId]) => {
            return new Promise((resolve) => {
                const ws = new WebSocket("wss://realtime.diablo.trade/connection/websocket");
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
                        const data = {
                            "appId": "project-d4",
                            "conversationId": convId,
                            "content": content,
                            "type": "system",
                            "meta": {
                                "system": {"code": "", "kind": kind}
                            }
                        };
                        if (destinedUserId) {
                            data.meta.destinedUserId = destinedUserId;
                        }
                        ws.send(JSON.stringify({
                            "publish": {
                                "channel": "chat:project-d4:conversation:" + convId + ":submit",
                                "data": data
                            },
                            "id": 2
                        }));
                    }
                    if (resp.id === 2) {
                        ws.close();
                        resolve(!resp.error);
                    }
                };
                ws.onerror = function() { resolve(false); };
                setTimeout(function() { ws.close(); resolve(false); }, 10000);
            });
        }""",
        [token, conv_id, content, kind, destined_user_id],
    )
