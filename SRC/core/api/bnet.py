from __future__ import annotations

from typing import Any

async def start_reveal(page, conv_id: str) -> Any:
    return await page.evaluate(
        """async (convId) => {
            const r = await fetch('/api/chat/bnet-reveal/start', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify({ conversationId: convId })
            });
            return r.ok;
        }""",
        conv_id,
    )

async def accept_reveal(page, conv_id: str) -> Any:
    return await page.evaluate(
        """async (convId) => {
            const r = await fetch('/api/chat/bnet-reveal/accept', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify({ conversationId: convId })
            });
            return r.ok;
        }""",
        conv_id,
    )

async def fetch_reveal_status(page, conv_id: str) -> Any:
    return await page.evaluate(
        """async (convId) => {
            const r = await fetch('/api/chat/bnet-reveal/status?conversationId=' + convId);
            if (!r.ok) return {__error: r.status};
            return await r.json();
        }""",
        conv_id,
    )
