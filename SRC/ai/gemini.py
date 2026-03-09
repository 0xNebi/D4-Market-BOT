from SRC.utils.formatting import format_gold

import asyncio
import re
from typing import Optional, List, Tuple, Callable, Any

from ..utils.logger import log, ai_log

try:
    from google import genai
    from google.genai import types as _genai_types
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False

_SYSTEM_PROMPT = """\
You are a real person selling items on a Diablo 4 trading website (diablo.trade).
You are NOT a bot, assistant, or AI — you are a normal gamer selling your loot.

PERSONALITY:
- Casual, friendly, very brief — like texting a gaming buddy
- Lowercase, short sentences, occasional gaming slang (gl, ty, np, gg, rn, ngl)
- Never sound robotic, formal, or scripted
- Never greet by name ("Hi {name}!") — jump straight into the reply
- Never say "I received your offer", "Thanks for reaching out", "I understand", "Certainly", etc.
- Every reply should feel natural and varied — avoid repeating the same phrase structure twice
- 1–2 sentences unless the buyer asked for a full item list (then list everything clearly)

TONE GUIDE (not templates — write naturally, not verbatim):
- Confirming availability: short, direct, no fluff
- Sharing battletag: only when confirming trade, always use the exact tag from [SELLER IDENTITY]
- Declining low offer: casual, not rude, one line
- Item is sold: ultra brief
- Item on hold for someone else: brief, offer to notify
- Listing all items: name + price per line, plain text, no markdown

TRADE RULES:
- Only share your BattleTag when confirming a trade (intent is READY_TO_BUY or buyer explicitly says they want to buy)
- Use EXACTLY the BattleTag from [SELLER IDENTITY] — never invent or modify it
- Real item data comes from [CURRENT ITEM] and [INTERNAL: YOUR INVENTORY] — never fabricate prices or stats
- If [CURRENT ITEM] status is AVAILABLE, it IS available now regardless of what old chat messages say — status changes over time
- If item status is on_hold for THIS buyer (pending_trade), proceed with the trade
- If item status is on_hold for ANOTHER buyer, tell them it is reserved but you will follow up if it falls through
- If the buyer lowballs, decline casually — price is firm unless you decide otherwise
- Never discuss anything outside the trade

INVENTORY QUERIES — CRITICAL RULE:
When the buyer asks ANY variation of "what do you sell", "what do you have", "list your items", "show me your items", "what items", "what are you selling" — you MUST call get_full_inventory FIRST, then reply with every item name and price listed naturally. Do NOT give a vague "I have a few things" or "let me know what you want" response. List the actual items.

CONTEXT RULES:
- Sections in the prompt: [SELLER IDENTITY], [CURRENT ITEM], [CONVERSATION HISTORY], [INTERNAL: YOUR INVENTORY], [BUYER'S NEW MESSAGE]
- [INTERNAL: YOUR INVENTORY] is your reference only — never expose item IDs or raw internal fields to the buyer
- [CONVERSATION HISTORY] shows real messages only — system notifications are excluded
- Reply only to [BUYER'S NEW MESSAGE]; do not parrot back data from internal sections

TOOLS:
- accept_tag_reveal — accept a pending BattleTag reveal the buyer initiated
- request_tag_reveal — initiate BattleTag sharing when confirming a trade
- check_item_status — look up a specific item by name when not already in context
- get_full_inventory — get all listings with prices and quantities; call this whenever the buyer asks what you sell
You can call a tool AND send a reply in the same response.
"""

# Tool declarations wired to Gemini function_calling. Each tool maps to a
# Python callable that the bot executes when Gemini requests it in a response.
def _build_tool_declarations():

    if not _GENAI_AVAILABLE:
        return None
    try:
        return [_genai_types.Tool(
            function_declarations=[
                _genai_types.FunctionDeclaration(
                    name="accept_tag_reveal",
                    description=(
                        "Accept a pending Battle.net tag reveal from the buyer. "
                        "Call this when the buyer has requested a tag reveal and you "
                        "agree to share BattleTags for trading."
                    ),
                    parameters=_genai_types.Schema(
                        type=_genai_types.Type.OBJECT,
                        properties={},
                    ),
                ),
                _genai_types.FunctionDeclaration(
                    name="request_tag_reveal",
                    description=(
                        "Initiate a Battle.net tag reveal with the buyer. Call this "
                        "when you want to start the process of sharing BattleTags, "
                        "typically when confirming a trade and you want to connect in-game."
                    ),
                    parameters=_genai_types.Schema(
                        type=_genai_types.Type.OBJECT,
                        properties={},
                    ),
                ),
                _genai_types.FunctionDeclaration(
                    name="check_item_status",
                    description=(
                        "Check the current availability and status of a specific item "
                        "in your inventory. Use when the buyer asks about an item not "
                        "identified in the current context."
                    ),
                    parameters=_genai_types.Schema(
                        type=_genai_types.Type.OBJECT,
                        properties={
                            "item_name": _genai_types.Schema(
                                type=_genai_types.Type.STRING,
                                description="Name or partial name of the item to look up",
                            ),
                        },
                        required=["item_name"],
                    ),
                ),
                _genai_types.FunctionDeclaration(
                    name="get_full_inventory",
                    description=(
                        "Get a complete summary of all items currently listed in your "
                        "inventory, including names, prices, and quantities. Use when "
                        "the buyer asks what items you have for sale."
                    ),
                    parameters=_genai_types.Schema(
                        type=_genai_types.Type.OBJECT,
                        properties={},
                    ),
                ),
            ],
        )]
    except Exception as e:
        log.warning(f"Could not build tool declarations: {e}")
        return None

class GeminiClient:

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.api_key  = api_key
        self.model_id = model
        self._client  = None
        self._ready   = False
        self._tools   = None

        if not _GENAI_AVAILABLE:
            log.warning("google-genai not installed — AI replies disabled")
            return
        if not api_key or api_key == "your_gemini_api_key_here":
            log.warning("GOOGLE_API_KEY not set — AI replies disabled")
            return

        try:
            self._client = genai.Client(api_key=api_key)
            self._ready  = True
            self._tools  = _build_tool_declarations()
            tools_status = "with tools" if self._tools else "text-only"
            log.info(f"Gemini client ready — model: {model} ({tools_status})")
        except Exception as e:
            log.error(f"Gemini init failed: {e}")

    @property
    def is_ready(self) -> bool:
        return self._ready

    async def generate_reply(
        self,
        player:    str,
        item_name: str,
        price:     int,
        message:   str,
        battletag: str,
        conversation_history: Optional[List[dict]] = None,
        inventory_summary: str = "",
        item_status: str = "available",
        tool_executor: Optional[Callable] = None,
        item_quantity: int = 1,
        buyer_quantity: Optional[int] = None,
    ) -> Tuple[Optional[str], int, List[dict]]:

        if not self._ready or not self._client:
            return None, 0, []

        prompt = self._build_prompt(
            player=player,
            item_name=item_name,
            price=price,
            message=message,
            battletag=battletag,
            conversation_history=conversation_history,
            inventory_summary=inventory_summary,
            item_status=item_status,
            item_quantity=item_quantity,
            buyer_quantity=buyer_quantity,
        )

        ai_log.info(
            f"{'='*60}\n"
            f"AI REQUEST — Player: {player} | Item: {item_name} | Status: {item_status}\n"
            f"System prompt: (see _SYSTEM_PROMPT in gemini.py)\n"
            f"{prompt}\n"
            f"{'='*60}"
        )

        use_tools = (tool_executor is not None and self._tools is not None)

        def _first_call():
            cfg = _genai_types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                tools=self._tools if use_tools else None,
            )
            resp = self._client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=cfg,
            )
            return resp

        try:
            resp = await asyncio.to_thread(_first_call)
        except Exception as e:
            ai_log.warning(f"AI ERROR — Player: {player} | Error: {e}")
            log.warning(f"Gemini generation failed: {e}")
            return None, 0, []

        total_tokens = 0
        if hasattr(resp, "usage_metadata") and resp.usage_metadata:
            total_tokens = resp.usage_metadata.total_token_count or 0

        actions_taken: List[dict] = []
        if use_tools and resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts:
            function_calls = [
                p for p in resp.candidates[0].content.parts
                if hasattr(p, 'function_call') and p.function_call
            ]

            if function_calls:

                function_response_parts = []
                for fc_part in function_calls:
                    fc = fc_part.function_call
                    fc_name = fc.name
                    fc_args = dict(fc.args) if fc.args else {}

                    log.info(f"[ai-tool] AI called tool: {fc_name}({fc_args})")
                    ai_log.info(f"AI TOOL CALL — {fc_name}({fc_args})")

                    try:
                        result = await tool_executor(fc_name, fc_args)
                    except Exception as e:
                        log.warning(f"[ai-tool] Tool execution failed: {fc_name} — {e}")
                        result = {"error": str(e)}

                    actions_taken.append({
                        "action": fc_name,
                        "args": fc_args,
                        "result": result,
                    })
                    ai_log.info(f"AI TOOL RESULT — {fc_name} → {result}")

                    function_response_parts.append(
                        _genai_types.Part(
                            function_response=_genai_types.FunctionResponse(
                                name=fc_name,
                                response=result,
                            )
                        )
                    )

                def _second_call():

                    contents = [
                        _genai_types.Content(
                            role="user",
                            parts=[_genai_types.Part(text=prompt)],
                        ),
                        resp.candidates[0].content,
                        _genai_types.Content(
                            role="user",
                            parts=function_response_parts,
                        ),
                    ]
                    cfg = _genai_types.GenerateContentConfig(
                        system_instruction=_SYSTEM_PROMPT,
                    )
                    resp2 = self._client.models.generate_content(
                        model=self.model_id,
                        contents=contents,
                        config=cfg,
                    )
                    return resp2

                try:
                    resp = await asyncio.to_thread(_second_call)
                    if hasattr(resp, "usage_metadata") and resp.usage_metadata:
                        total_tokens += resp.usage_metadata.total_token_count or 0
                except Exception as e:
                    ai_log.warning(f"AI ERROR (2nd call) — {e}")
                    log.warning(f"Gemini second call failed: {e}")

                    return None, total_tokens, actions_taken

        try:
            reply = resp.text.strip() if resp.text else None
        except Exception:

            reply = None

        if reply:

            if reply.startswith('"') and reply.endswith('"'):
                reply = reply[1:-1]

            reply = re.sub(r'```[\w]*\n.*?\n```', '', reply, flags=re.DOTALL).strip()

            _TOXIC_PATTERNS = ('default_api.', 'print(', 'tool_code', '```', 'function_call')
            if any(pat in reply for pat in _TOXIC_PATTERNS):
                log.warning(f"[ai] Response contained code artifacts — discarding: {reply[:100]}")
                reply = None

            if reply is not None and not reply.strip():
                reply = None

        actions_summary = ", ".join(a["action"] for a in actions_taken) if actions_taken else "none"
        ai_log.info(
            f"AI RESPONSE — Player: {player} | Tokens: {total_tokens} | Tools: {actions_summary}\n"
            f"Reply: {reply}\n"
            f"{'-'*60}"
        )
        return reply, total_tokens, actions_taken

    def _build_prompt(
        self,
        player: str,
        item_name: str,
        price: int,
        message: str,
        battletag: str,
        conversation_history: Optional[List[dict]] = None,
        inventory_summary: str = "",
        item_status: str = "available",
        item_quantity: int = 1,
        buyer_quantity: Optional[int] = None,
    ) -> str:

        identity_section = (
            f"[SELLER IDENTITY]\n"
            f"BattleTag: {battletag}\n"
            f"Buyer: {player}"
        )

        if item_name and item_name != "the item":
            price_str = format_gold(price)

            status_map = {
                "available": "AVAILABLE — ready to trade",
                "sold": "SOLD — item is no longer for sale",
                "on_hold": "ON HOLD — reserved for a different buyer",
                "pending_trade": "RESERVED FOR THIS BUYER — proceed with the trade",
                "unknown": "status unknown",
            }
            status_text = status_map.get(item_status, item_status)

            item_section = (
                f"[CURRENT ITEM]\n"
                f"Name: {item_name}\n"
                f"Price: {price_str}\n"
                f"Listed Quantity: {item_quantity}\n"
                f"Status: {status_text}"
            )

            if buyer_quantity and buyer_quantity > 1:
                item_section += (
                    f"\n⚠ BUYER WANTS TO PURCHASE {buyer_quantity} UNITS. "
                    f"You have {item_quantity} listed. "
                )
                if buyer_quantity <= item_quantity:
                    total = buyer_quantity * price if price else 0
                    item_section += (
                        f"This is within your listed quantity. "
                        f"Total trade value: {format_gold(total)}. "
                        f"Proceed with the trade for all {buyer_quantity} units."
                    )
                else:
                    item_section += (
                        f"Buyer wants MORE than you have listed! "
                        f"Let them know you only have {item_quantity} available."
                    )

            if item_status == "available":
                item_section += (
                    "\n⚠ STATUS IS AVAILABLE. Even if older messages mention "
                    "'reserved' or 'on hold', the item is NOW available."
                )
        else:
            item_section = "[CURRENT ITEM]\nNot identified from message."

        _SYSTEM_MSG_FILTERS = (
            "battle.net tags are now visible",
            "both users have consented",
            "reveal request was cancelled",
            "requested a battle.net reveal",
            "battle.net reveal requested",
            "waiting for user to consent",
            "has requested to reveal",
            "wants to reveal battle.net",
        )

        history_section = "[CONVERSATION HISTORY]"
        if conversation_history:
            filtered_lines = []
            for msg in conversation_history[-8:]:
                content = msg.get("content", "")[:200]
                if any(phrase in content.lower() for phrase in _SYSTEM_MSG_FILTERS):
                    continue
                who = "You" if msg.get("is_mine") else player
                filtered_lines.append(f"  {who}: {content}")
            if filtered_lines:
                history_section += "\n" + "\n".join(filtered_lines)
            else:
                history_section += "\nNo previous messages."
        else:
            history_section += "\nNo previous messages."

        inventory_section = "[INTERNAL: YOUR INVENTORY]\n(This data is for your reference only — the buyer cannot see it.)"
        if inventory_summary:
            inventory_section += f"\n{inventory_summary}"
        else:
            inventory_section += "\nNo inventory data available."

        buyer_section = f"[BUYER'S NEW MESSAGE]\n\"{message}\""

        task_section = (
            "[TASK]\n"
            "Write a short, casual reply to the buyer's latest message above. "
            "You are a real person, not a bot. "
            "If the buyer is asking what you sell, asking for a list of items or prices, "
            "or asking what you have available — you MUST list your items and prices "
            "from [INTERNAL: YOUR INVENTORY] in plain casual text (no IDs, no internal formatting). "
            "Do NOT dodge or deflect listing requests — buyers expect a real answer. "
            "If the buyer is NOT asking for a list, keep your reply to 1-2 sentences. "
            "If the buyer wants to trade and the item is available, you may use "
            "the request_tag_reveal or accept_tag_reveal tool to initiate BattleTag "
            "sharing (only if appropriate for this stage of the trade). "
            "If the buyer mentions wanting to buy multiple units, check the "
            "Listed Quantity above. If you have enough, confirm the full order "
            "and state the total price. If they want more than you have, let them "
            "know how many you currently have available."
        )

        sections = [
            identity_section,
            item_section,
            history_section,
            inventory_section,
            buyer_section,
            task_section,
        ]
        return "\n\n".join(sections)
