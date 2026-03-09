import re
from typing import Optional

def _gold_amount_to_int(value: str, unit: str) -> int:
    try:
        amount = float(value)
        unit = unit.lower()
        if unit.startswith("b"):
            return int(amount * 1_000_000_000)
        return int(amount * 1_000_000)
    except:
        return 0

# parse_offered_price checks phrase patterns first ("I'll give 2b"), then
# falls back to structured offer format, then bare number+unit anywhere in text.
def parse_offered_price(message: str) -> Optional[int]:
    msg = message.lower()
    phrase_matches = re.findall(
        r'(?:i(?:ll| will)?\s+(?:give|offer)|i can\s+(?:give|offer|pay)|'
        r'how about|would you take|will you take|my offer(?: is)?|'
        r'offer you|offering|would you accept)[^\n|]{0,60}?(\d+(?:\.\d+)?)\s*(b(?:illion)?|m(?:illion)?)',
        msg,
    )
    if phrase_matches:
        value, unit = phrase_matches[-1]
        return _gold_amount_to_int(value, unit)

    structured_offer, _ = parse_trade_prices(message)
    if structured_offer:
        return structured_offer

    matches = re.findall(r'(\d+(?:\.\d+)?)\s*(b(?:illion)?|m(?:illion)?)', msg)
    if matches:
        value, unit = matches[-1]
        return _gold_amount_to_int(value, unit)
    return None

def parse_buyer_quantity(message: str) -> Optional[int]:
    msg = message.lower()
    m = re.search(r'(?:quantity|qty)\s*[:=]\s*(\d+)', msg)
    if m:
        return int(m.group(1))

    m = re.search(r'\b(?:buy|purchase|get|want|need|take|order)\b(?:\s+all)?\s+(\d+)\b', msg)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 999:
            return val

    m = re.search(r'\b(\d+)\s+(?:of them|units?|items?|pieces?|stacks?)\b', msg)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 999:
            return val

    return None

def parse_trade_prices(message: str) -> tuple[Optional[int], Optional[int]]:
    if not message:
        return None, None

    offer_match = re.search(r'(?:^|\n|\|)\s*[•*-]?\s*price\s*:\s*([\d,]+)', message, re.IGNORECASE)
    listed_match = re.search(r'currently listed\s*:\s*([\d,]+)', message, re.IGNORECASE)

    try:
        offered = int(offer_match.group(1).replace(",", "")) if offer_match else None
        listed = int(listed_match.group(1).replace(",", "")) if listed_match else None
        return offered, listed
    except:
        return None, None

def normalize_price(raw: str) -> str:
    raw = raw.strip().lower()
    match = re.match(r'^(\d+(?:\.\d+)?)\s*(b|m|k)?$', raw)
    if not match:
        return raw
    try:
        value = float(match.group(1))
        unit = match.group(2) or ""
        if unit == "b":
            return str(int(value * 1_000_000_000))
        if unit == "m":
            return str(int(value * 1_000_000))
        if unit == "k":
            return str(int(value * 1_000))
        return str(int(value))
    except:
        return raw
