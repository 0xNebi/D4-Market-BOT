import re
from typing import Optional

# Matches the {d4:uuid} embed format the site uses to attach item references to messages.
D4_ITEM_PATTERN = re.compile(r'\{d4:([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\}')

def extract_item_uuid(content: str) -> Optional[str]:

    if not content:
        return None
    match = D4_ITEM_PATTERN.search(content)
    return match.group(1) if match else None

def extract_all_item_uuids(content: str) -> list[str]:

    if not content:
        return []
    return D4_ITEM_PATTERN.findall(content)
