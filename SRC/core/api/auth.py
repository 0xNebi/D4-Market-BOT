from __future__ import annotations

from typing import Any

async def fetch_realtime_token(page) -> Any:
    return await page.evaluate(
        "async () => {"
        "  const r = await fetch('/api/realtime/token');"
        "  if (!r.ok) return {__error: r.status};"
        "  return await r.json();"
        "}"
    )

async def fetch_user_identity(page) -> Any:
    return await page.evaluate(
        "async () => {"
        "  const r = await fetch('/api/auth/session');"
        "  if (!r.ok) return {__error: r.status};"
        "  const ct = r.headers.get('content-type') || '';"
        "  if (!ct.includes('application/json')) return {__error: 'not-json'};"
        "  return await r.json();"
        "}"
    )

async def session_is_available(page) -> bool:
    return await page.evaluate(
        "async () => { const r = await fetch('/api/auth/session'); return r.ok; }"
    )
