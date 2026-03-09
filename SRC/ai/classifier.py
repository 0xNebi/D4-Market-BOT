from enum import Enum

class MessageIntent(str, Enum):
    READY_TO_BUY     = "ready_to_buy"
    PRICE_INQUIRY    = "price_inquiry"
    COUNTER_OFFER    = "counter_offer"
    STILL_AVAIL      = "still_available"
    LOWBALL          = "lowball"
    INVENTORY_QUERY  = "inventory_query"
    UNKNOWN          = "unknown"

# Rule-based intent classifier with fuzzy matching for common typos.
# Levenshtein distance is used for short-window substring matching, not full-string.


_RULES: list[tuple[MessageIntent, list[str]]] = [
    (MessageIntent.INVENTORY_QUERY, [
        "what do you sell", "what do you have", "what are you selling",
        "what items do you have", "what items you have", "list your items",
        "list the items", "show me your items", "show me what you have",
        "what you got", "what u got", "what u sell", "what you sell",
        "what items", "items for sale", "show items", "list items",
        "your items", "what are u selling", "any items", "what do u have",
        "what do u sell", "whats for sale", "what's for sale",
    ]),
    (MessageIntent.COUNTER_OFFER, [
        "i'll offer", "i will offer", "will offer", "i can offer", "i can pay",
        "how about", "would you take", "will you take", "i offer", "my offer",
        "offer you", "offering", "i'll give", "ill offer", "ill give",
        "id offer", "id give", "would you accept",
    ]),
    (MessageIntent.READY_TO_BUY, [
        "interested in buying", "i want to buy", "i'd like to buy", "i want it",
        "i'll take it", "i want to purchase", "i'd like to purchase",
        "i'm interested", "want to buy", "looking to buy",
        "ill take it", "im interested", "id like to buy", "id like to purchase",
        "want it", "buying", "i buy", "ill buy",
    ]),
    (MessageIntent.PRICE_INQUIRY, [
        "how much", "what's the price", "whats the price", "is it negotiable",
        "is price negotiable", "can you go lower", "any discount", "best price",
        "cheaper", "lower price", "price check", "wats the price", "wat price",
        "the price", "ur price",
    ]),
    (MessageIntent.STILL_AVAIL, [
        "still available", "still for sale", "still selling",
        "is it sold", "is this sold", "sold?", "available?", "still up",
        "stil available", "still avaialble", "still avail", "is it available",
        "this available", "u still got", "you still got", "still got it",
        "still have it", "u still have", "you still have",
    ]),
]

def _normalize(text: str) -> str:

    text = text.lower()

    text = text.replace("'", "").replace("\u2019", "")

    text = " ".join(text.split())
    return text

def _levenshtein_distance(s1: str, s2: str) -> int:

    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]

# Sliding-window Levenshtein: checks all substrings of msg that are
# close in length to keyword, catching typos like "intrested" or "stil".
def _fuzzy_match(msg: str, keyword: str, max_distance: int = 1) -> bool:

    if keyword in msg:
        return True

    if len(keyword) < 5:
        return False

    kw_len = len(keyword)
    for window_size in (kw_len, kw_len - 1, kw_len + 1):
        if window_size > len(msg) or window_size < 1:
            continue
        for i in range(len(msg) - window_size + 1):
            candidate = msg[i:i + window_size]
            if _levenshtein_distance(candidate, keyword) <= max_distance:
                return True

    return False

def classify_intent(message: str) -> MessageIntent:

    msg = _normalize(message)
    for intent, keywords in _RULES:
        for kw in keywords:
            norm_kw = _normalize(kw)
            if _fuzzy_match(msg, norm_kw):
                return intent
    return MessageIntent.UNKNOWN
