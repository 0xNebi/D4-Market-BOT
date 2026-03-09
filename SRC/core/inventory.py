import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..utils.logger import log

# In-memory representation of a single listing fetched from diablo.trade.
@dataclass
class ListingItem:

    id: str = ""
    name: str = ""
    price: int = 0
    quantity: int = 1
    game_mode: str = "SEASONAL_SOFTCORE"
    is_sold: bool = False
    is_removed: bool = False
    listed_at: str = ""
    raw: dict = field(default_factory=dict)

    item_type: str = ""
    subname: str = ""
    listing_mode: str = "SELLING"
    price_type: str = "FIXED"
    rarity: str = ""
    created_at: str = ""
    sold_at: str = ""

    material_type: str = ""
    description: str = ""

    power: int = 0
    is_ancestral: bool = False
    greater_affix_count: int = 0
    affixes: list = field(default_factory=list)
    inherents: list = field(default_factory=list)

# Thread-safe local cache of the seller's active + sold listings.
# Refreshes from the API at most once per REFRESH_INTERVAL seconds.
class InventoryCache:

    REFRESH_INTERVAL = 300

    def __init__(self, api=None):
        self._api = api
        self._items: Dict[str, ListingItem] = {}
        self._last_refresh: float = 0
        self._refreshing: bool = False

    def set_api(self, api) -> None:
        self._api = api

    @property
    def items(self) -> Dict[str, ListingItem]:
        return dict(self._items)

    @property
    def active_items(self) -> List[ListingItem]:

        return [i for i in self._items.values() if not i.is_sold and not i.is_removed]

    @property
    def sold_items(self) -> List[ListingItem]:
        return [i for i in self._items.values() if i.is_sold]

    @property
    def count(self) -> int:
        return len(self._items)

    @property
    def active_count(self) -> int:
        return len(self.active_items)

    def get_item(self, item_id: str) -> Optional[ListingItem]:

        return self._items.get(item_id)

    def is_available(self, item_id: str) -> bool:

        item = self._items.get(item_id)
        if not item:
            return False
        return not item.is_sold and not item.is_removed

    def is_sold(self, item_id: str) -> bool:
        item = self._items.get(item_id)
        return item.is_sold if item else False

    def find_by_name(self, name_query: str) -> List[ListingItem]:

        q = name_query.lower()
        return [
            i for i in self._items.values()
            if q in i.name.lower() and not i.is_sold and not i.is_removed
        ]

    def needs_refresh(self) -> bool:
        return (time.time() - self._last_refresh) > self.REFRESH_INTERVAL

    async def refresh(self, force: bool = False) -> int:

        if not force and not self.needs_refresh():
            return len(self._items)
        if self._refreshing:
            return len(self._items)
        if not self._api:
            return 0

        self._refreshing = True
        try:
            items_loaded = 0
            all_items: Dict[str, ListingItem] = {}

            for page_num in range(1, 11):
                rsc_text = await self._api.get_my_listings(
                    page_num=page_num, take=50, sold=False, removed=False
                )
                if not rsc_text:
                    break

                if page_num == 1:
                    log.debug(f"[inventory] RSC payload sample (first 1000 chars): {rsc_text[:1000]}")

                parsed = self._parse_rsc_listings(rsc_text)
                if not parsed:
                    break

                for item in parsed:
                    item.is_sold = False
                    item.is_removed = False
                    all_items[item.id] = item
                    items_loaded += 1

                if len(parsed) < 50:
                    break

            try:
                sold_rsc_text = await self._api.get_my_listings(
                    page_num=1, take=50, sold=True, removed=False
                )
                if sold_rsc_text:
                    sold_parsed = self._parse_rsc_listings(sold_rsc_text)
                    for item in sold_parsed:
                        item.is_sold = True
                        all_items[item.id] = item
            except Exception:
                pass

            self._items = all_items
            self._last_refresh = time.time()
            log.info(f"[inventory] Refreshed: {items_loaded} active, {len(self.sold_items)} sold")
            return items_loaded

        except Exception as e:
            log.warning(f"[inventory] Refresh failed: {e}")
            # Still advance the timestamp so we don't hammer the API every cycle on repeated errors.
            # Back off for 30 seconds before retrying instead of the full interval.
            self._last_refresh = time.time() - self.REFRESH_INTERVAL + 30
            return len(self._items)
        finally:
            self._refreshing = False

    # diablo.trade returns listings as Next.js RSC (React Server Components) text,
    # not plain JSON. The actual JSON payload is buried on the "1:" prefixed line.
    def _parse_rsc_listings(self, rsc_text: str) -> List[ListingItem]:

        items: List[ListingItem] = []

        json_payload: Optional[str] = None
        for raw_line in rsc_text.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("1:"):
                json_payload = stripped[2:]
                break

        if not json_payload:

            for raw_line in rsc_text.splitlines():
                if '"listings"' in raw_line:
                    stripped = raw_line.strip()

                    json_payload = re.sub(r'^\d+:', '', stripped)
                    break

        if not json_payload:
            log.warning(
                f"[inventory] Could not locate JSON payload in RSC response "
                f"(len={len(rsc_text)}, sample={rsc_text[:200]!r})"
            )
            return items

        try:
            data = json.loads(json_payload)
        except json.JSONDecodeError as exc:
            log.warning(f"[inventory] JSON decode error in RSC response: {exc}")
            return items

        listings = data.get("listings") or []
        if not listings:
            log.debug(f"[inventory] RSC response contained no listings (total={data.get('total', 0)})")
            return items

        for listing in listings:
            item_id = listing.get("id", "")
            if not item_id:
                continue

            item = ListingItem()
            item.id = item_id
            item.name = listing.get("name") or ""

            item.price = listing.get("rawPrice") or 0

            item.is_sold = bool(listing.get("sold", False))
            item.is_removed = bool(listing.get("removed", False)) or bool(listing.get("expired", False))

            item.game_mode    = listing.get("gameMode")    or "SEASONAL_SOFTCORE"
            item.item_type    = listing.get("itemType")    or ""
            item.listing_mode = listing.get("listingMode") or "SELLING"
            item.price_type   = listing.get("priceType")   or "FIXED"
            item.rarity       = listing.get("rarity")      or ""
            item.listed_at    = listing.get("relistedAt")  or listing.get("createdAt") or ""
            item.created_at   = listing.get("createdAt")   or ""
            item.sold_at      = listing.get("soldAt")      or ""

            sub = listing.get("item") or {}
            item.subname = sub.get("subname") or ""

            if item.item_type == "MATERIAL":

                item.quantity      = sub.get("quantity") or 1
                item.material_type = sub.get("materialType") or ""
                item.description   = sub.get("description")  or ""
                # For materials the readable name is sometimes under sub["name"] / sub["subname"]
                # when the top-level "name" field holds a short type-code (e.g. "MOT").
                sub_name = sub.get("name") or sub.get("subname") or ""
                if sub_name and (not item.name or (len(item.name) <= 5 and item.name.isupper())):
                    item.name = sub_name
            else:

                item.quantity           = 1
                item.power              = sub.get("power") or 0
                item.is_ancestral       = bool(sub.get("isAncestral", False))
                item.greater_affix_count = sub.get("greaterAffixes") or 0
                item.affixes            = sub.get("affixes")   or []
                item.inherents          = sub.get("inherents") or []

            item.raw = listing
            items.append(item)

        log.debug(f"[inventory] Parsed {len(items)} listings from RSC JSON payload")
        return items

    def get_inventory_summary(self) -> str:

        active = self.active_items
        if not active:
            return "No active listings."

        def _fmt_price(p: int) -> str:
            if p >= 1_000_000_000:
                b = p / 1_000_000_000
                return f"{b:.1f}B" if b != int(b) else f"{int(b)}B"
            if p >= 1_000_000:
                m = p / 1_000_000
                return f"{m:.1f}M" if m != int(m) else f"{int(m)}M"
            if p > 0:
                return f"{p:,}"
            return "N/A"

        lines = []
        for item in active[:20]:
            price_str = _fmt_price(item.price) if item.price else "N/A"
            qty_str = f", qty: {item.quantity}" if item.quantity > 1 else ""

            extra = ""
            if item.item_type == "EQUIPMENT":
                parts = []
                if item.is_ancestral:
                    parts.append("Ancestral")
                if item.greater_affix_count:
                    parts.append(f"{item.greater_affix_count}GA")
                if item.power:
                    parts.append(f"power {item.power}")
                if parts:
                    extra = f" [{', '.join(parts)}]"
            elif item.item_type == "MATERIAL" and item.material_type:
                extra = f" [{item.material_type}]"

            lines.append(
                f"- {item.name} ({price_str} gold{qty_str}){extra} [ID: {item.id[:12]}...]"
            )

        summary = f"{len(active)} active listing(s):\n" + "\n".join(lines)
        if len(active) > 20:
            summary += f"\n... and {len(active) - 20} more"
        return summary

    def mark_sold_locally(self, item_id: str) -> None:

        if item_id in self._items:
            self._items[item_id].is_sold = True
            log.debug(f"[inventory] Marked {item_id[:12]}... as sold (local cache)")
