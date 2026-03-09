from SRC.utils.formatting import format_gold

from enum import Enum
from typing import Optional
from .settings import Settings

class MessageIntent(str, Enum):
    READY_TO_BUY    = "ready_to_buy"
    PRICE_INQUIRY   = "price_inquiry"
    COUNTER_OFFER   = "counter_offer"
    STILL_AVAIL     = "still_available"
    LOWBALL         = "lowball"
    INVENTORY_QUERY = "inventory_query"
    UNKNOWN         = "unknown"

# Maps MessageIntent values to the template strings from settings.
# Falls back to template_unknown if formatting fails (e.g. missing {battletag}).
class TemplateEngine:
    def __init__(self, settings: Settings):
        self._settings = settings

    def render(
        self,
        intent: MessageIntent,
        player: str,
        item_name: str = "the item",
        price: int = 0,
    ) -> str:

        fmt_price = format_gold(price)
        variables = {
            "player":    player,
            "item_name": item_name,
            "price":     fmt_price,
            "battletag": self._settings.battletag,
        }
        template_map = {
            MessageIntent.READY_TO_BUY:    self._settings.template_ready_to_buy,
            MessageIntent.PRICE_INQUIRY:   self._settings.template_price_inquiry,
            MessageIntent.STILL_AVAIL:     self._settings.template_still_available,
            MessageIntent.LOWBALL:         self._settings.template_lowball_decline,
            MessageIntent.INVENTORY_QUERY: self._settings.template_unknown,
            MessageIntent.UNKNOWN:         self._settings.template_unknown,
            MessageIntent.COUNTER_OFFER:   self._settings.template_price_inquiry,
        }
        template = template_map.get(intent, self._settings.template_unknown)
        try:
            return template.format(**variables)
        except (KeyError, ValueError):
            return self._settings.template_unknown.format(**variables)
