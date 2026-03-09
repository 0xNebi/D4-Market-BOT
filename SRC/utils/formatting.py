# Formats a raw gold integer into B/M suffix shorthand for display.
# Trailing .0 is stripped so "2.0B" becomes "2B".
def format_gold(price: int) -> str:
    if not price or price <= 0:
        return "0"

    if price >= 1_000_000_000:
        billions = price / 1_000_000_000
        if billions == int(billions):
            return f"{int(billions)}B"
        return f"{billions:.1f}B"

    if price >= 1_000_000:
        millions = price / 1_000_000
        if millions == int(millions):
            return f"{int(millions)}M"
        return f"{millions:.1f}M"

    return f"{price}"
