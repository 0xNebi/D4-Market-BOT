# Randomized async delays to avoid bot-detection fingerprinting.
# human_delay is for user-visible actions; short/page_load for navigation waits.
import asyncio
import random

async def human_delay(min_ms: int = 800, max_ms: int = 2500) -> None:

    ms = random.randint(min_ms, max_ms)
    await asyncio.sleep(ms / 1000)

async def short_delay() -> None:

    await asyncio.sleep(random.uniform(0.3, 0.7))

async def page_load_delay() -> None:

    await asyncio.sleep(random.uniform(1.5, 3.0))
