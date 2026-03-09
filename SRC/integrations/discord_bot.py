from SRC.utils.formatting import format_gold
from SRC.utils.price_parser import normalize_price

import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, TYPE_CHECKING

from ..utils.logger import log

if TYPE_CHECKING:
    from ..storage.repository import Repository
    from ..core.listing_manager import ListingManager

_CLR_OFFER   = 0xFF6B00
_CLR_SUMMARY = 0x00C2FF
_CLR_ERROR   = 0xFF2222
_CLR_SUCCESS = 0x00FF88
_CLR_INFO    = 0x42A5F5
_CLR_WARN    = 0xFFA726

_FOOTER      = "D4-Market Bot"

# Ephemeral Discord view attached to each offer notification.
# Buttons stay active for 24h; they disable themselves after any action.
class OfferActionView(discord.ui.View):

    def __init__(
        self,
        item_name: str,
        item_uuid: Optional[str],
        bot_ref: "D4DiscordBot",
        *,
        listed_price: int = 0,
        quantity: int = 1,
        caller_bot = None,
        timeout: float = 86400,
    ):
        super().__init__(timeout=timeout)
        self.item_name = item_name
        self.item_uuid = item_uuid
        self._bot_ref = bot_ref
        self._caller_bot = caller_bot
        self.listed_price = listed_price
        self.quantity = quantity

    async def _disable_buttons(self, interaction: discord.Interaction) -> None:

        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        self.stop()

    @discord.ui.button(label="Mark Sold", style=discord.ButtonStyle.success, emoji="✅")
    async def btn_mark_sold(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True)

        active_bot = self._caller_bot or self._bot_ref
        lm = active_bot.listing_manager if hasattr(active_bot, "listing_manager") else self._bot_ref.listing_manager
        repo = active_bot.repo if hasattr(active_bot, "repo") else self._bot_ref.repo
        inventory = getattr(active_bot, "inventory", None) or getattr(self._bot_ref, "inventory", None)

        if lm and self.item_uuid:
            result = await lm.mark_as_sold_by_id(
                self.item_uuid,
                sold_price=_price_to_full(self.listed_price),
                quantity=self.quantity,
            )
            if not result["success"]:
                await interaction.followup.send(embed=_error_embed(
                    f"Failed to mark **{self.item_name}** as sold.\n"
                    f"Error: {result.get('error', 'Unknown')}"
                ))
                await self._disable_buttons(interaction)
                return
        elif lm:

            await interaction.followup.send(embed=_error_embed(
                f"No item UUID available for **{self.item_name}** — cannot mark as sold."
            ))
            await self._disable_buttons(interaction)
            return

        if repo and self.item_uuid:
            await repo.mark_item_sold(self.item_uuid)

        if inventory and self.item_uuid:
            inventory.mark_sold_locally(self.item_uuid)

        embed = discord.Embed(
            title="✅ Marked as Sold",
            description=f"**{self.item_name}** has been marked as sold on diablo.trade.",
            color=_CLR_SUCCESS,
        )
        embed.set_footer(text=_FOOTER)
        await interaction.followup.send(embed=embed)
        await self._disable_buttons(interaction)
        log.info(f"[discord] '{self.item_name}' ({self.item_uuid[:12]}...) marked as SOLD via Discord button")

    @discord.ui.button(label="Release Hold", style=discord.ButtonStyle.secondary, emoji="🔓")
    async def btn_release_hold(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True)

        source = self._caller_bot or self._bot_ref
        repo = source.repo if hasattr(source, "repo") else self._bot_ref.repo
        if not repo or not self.item_uuid:
            await interaction.followup.send(embed=_error_embed("No item UUID — cannot release hold."))
            await self._disable_buttons(interaction)
            return

        released = await repo.release_item_hold(self.item_uuid)
        if released:
            embed = discord.Embed(
                title="🔓 Hold Released",
                description=(
                    f"**{self.item_name}** is back on sale.\n"
                    f"Previous holder: **{released.get('player_name', '?')}**"
                ),
                color=_CLR_INFO,
            )
        else:
            embed = discord.Embed(
                title="🔓 No Active Hold",
                description=f"**{self.item_name}** was not held by anyone.",
                color=_CLR_INFO,
            )
        embed.set_footer(text=_FOOTER)
        await interaction.followup.send(embed=embed)
        await self._disable_buttons(interaction)
        log.info(f"[discord] Button: released hold on '{self.item_name}'")

    @discord.ui.button(label="Remove Listing", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def btn_remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True)

        lm = self._bot_ref.listing_manager
        if not lm:
            await interaction.followup.send(embed=_error_embed(
                "Listing manager not connected — cannot remove listing."
            ))
            await self._disable_buttons(interaction)
            return

        result = await lm.remove_listing(self.item_name)
        await lm.navigate_back_to_home()

        if result["success"]:

            if self._bot_ref.repo and self.item_uuid:
                await self._bot_ref.repo.release_item_hold(self.item_uuid)
            embed = discord.Embed(
                title="🗑️ Listing Removed",
                description=f"**{self.item_name}** has been removed from diablo.trade.",
                color=_CLR_WARN,
            )
        else:
            embed = _error_embed(
                f"Failed to remove **{self.item_name}**.\n"
                f"Error: {result.get('error', 'Unknown')}"
            )
        embed.set_footer(text=_FOOTER)
        await interaction.followup.send(embed=embed)
        await self._disable_buttons(interaction)
        log.info(f"[discord] Button: removed listing '{self.item_name}'")

    async def on_timeout(self) -> None:

        for child in self.children:
            child.disabled = True

class D4DiscordBot:

    def __init__(
        self,
        token: str,
        channel_id: int,
        repo: Optional["Repository"] = None,
        listing_manager: Optional["ListingManager"] = None,
        enabled: bool = True,
    ):
        self.token = token
        self.channel_id = channel_id
        self.repo = repo
        self.listing_manager = listing_manager
        self.enabled = enabled
        self.bot_ref = None
        self._bot: Optional[commands.Bot] = None
        self._ready = asyncio.Event()
        self._channel: Optional[discord.TextChannel] = None
        self._all_bots: list = []
        self._offline_notice_sent: bool = False

    def _create_bot(self) -> commands.Bot:

        intents = discord.Intents.default()

        bot = commands.Bot(
            command_prefix="!d4 ",
            intents=intents,
            description="D4-Market Trade Bot — manages diablo.trade listings",
        )

        @bot.event
        async def on_ready():
            log.success(f"[discord] Bot ready as {bot.user} ({bot.user.id})")
            try:
                synced = await bot.tree.sync()
                log.info(f"[discord] Synced {len(synced)} slash commands")
            except Exception as e:
                log.warning(f"[discord] Failed to sync commands: {e}")

            if self.channel_id:
                ch = bot.get_channel(self.channel_id)
                if ch is None:
                    try:
                        ch = await bot.fetch_channel(self.channel_id)
                    except Exception as e:
                        log.warning(f"[discord] Could not fetch channel {self.channel_id}: {e}")
                self._channel = ch
                if ch:
                    log.info(f"[discord] Notification channel: #{ch.name}")
                else:
                    log.warning(f"[discord] Channel {self.channel_id} not found")

            self._ready.set()

        def _resolve_bot(account: str = ""):

            if not account or account.lower() == "all":
                return self.bot_ref, ""
            for b in (self._all_bots or []):
                uname = getattr(b.settings, "username", "")
                if uname and uname.lower() == account.lower():
                    return b, uname

            return self.bot_ref, ""

        def _all_account_names() -> list[str]:
            return [
                getattr(b.settings, "username", "")
                for b in (self._all_bots or [])
                if getattr(b.settings, "username", "")
            ]

        async def _account_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            names = _all_account_names()
            choices = [app_commands.Choice(name="all", value="all")]
            for n in names:
                choices.append(app_commands.Choice(name=n, value=n))
            if current:
                choices = [c for c in choices if current.lower() in c.name.lower()]
            return choices[:25]

        async def _item_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:

            items: list = []
            for b in (self._all_bots or []):
                if hasattr(b, "inventory"):
                    items.extend(b.inventory.active_items or [])
            if not items and self.bot_ref and hasattr(self.bot_ref, "inventory"):
                items = self.bot_ref.inventory.active_items or []
            choices = []
            for it in items:
                label = it.name or "?"
                if current and current.lower() not in label.lower():
                    continue
                choices.append(app_commands.Choice(name=label[:100], value=label[:100]))

            seen = set()
            unique = []
            for c in choices:
                if c.name not in seen:
                    seen.add(c.name)
                    unique.append(c)
            return unique[:25]

        @bot.tree.command(name="sold", description="Mark an item as sold on diablo.trade")
        @app_commands.describe(
            item_name="Item name (autocomplete from inventory)",
            account="Account to operate on (leave blank for primary)",
        )
        @app_commands.autocomplete(item_name=_item_autocomplete, account=_account_autocomplete)
        async def cmd_sold(interaction: discord.Interaction, item_name: str, account: str = ""):
            await interaction.response.defer(thinking=True)

            target_bot, acct_label = _resolve_bot(account)
            lm = target_bot.listing_manager if (target_bot and hasattr(target_bot, "listing_manager")) else self.listing_manager
            if not lm:
                await interaction.followup.send(
                    embed=_error_embed("Listing manager not connected. Bot may still be starting up."),
                )
                return

            inv_cache = target_bot.inventory if (target_bot and hasattr(target_bot, "inventory")) else None

            result = {"success": False, "error": "Item not found in inventory cache"}
            if inv_cache:
                matches = inv_cache.find_by_name(item_name)
                if not matches:
                    await inv_cache.refresh(force=True)
                    matches = inv_cache.find_by_name(item_name)
                if matches:
                    item = matches[0]
                    qty = getattr(item, "quantity", 1) or 1
                    result = await lm.mark_as_sold_by_id(
                        item.id,
                        sold_price=_price_to_full(item.price),
                        quantity=qty,
                    )
                    if result["success"]:
                        repo = target_bot.repo if (target_bot and hasattr(target_bot, "repo")) else self.repo
                        if repo:
                            await repo.mark_item_sold(item.id)
                        inv_cache.mark_sold_locally(item.id)

            if result["success"]:
                desc = f"**{item_name}** has been marked as sold on diablo.trade."
                if acct_label:
                    desc += f"\nAccount: **{acct_label}**"
                embed = discord.Embed(title="✅ Item Marked as Sold", description=desc, color=_CLR_SUCCESS, timestamp=discord.utils.utcnow())
                embed.set_footer(text=_FOOTER)
            else:
                embed = _error_embed(f"Failed to mark '{item_name}' as sold.\n**Error:** {result.get('error', 'Unknown')}")
            await interaction.followup.send(embed=embed)

        @bot.tree.command(name="remove", description="Remove (unlist) an item from diablo.trade")
        @app_commands.describe(
            item_name="Item name to remove",
            account="Account to operate on",
        )
        @app_commands.autocomplete(item_name=_item_autocomplete, account=_account_autocomplete)
        async def cmd_remove(interaction: discord.Interaction, item_name: str, account: str = ""):
            await interaction.response.defer(thinking=True)
            target_bot, acct_label = _resolve_bot(account)
            lm = target_bot.listing_manager if (target_bot and hasattr(target_bot, "listing_manager")) else self.listing_manager
            if not lm:
                await interaction.followup.send(embed=_error_embed("Listing manager not connected."))
                return
            result = await lm.remove_listing(item_name)
            await lm.navigate_back_to_home()

            if result["success"]:
                desc = f"**{item_name}** has been removed from diablo.trade listings."
                if acct_label:
                    desc += f"\nAccount: **{acct_label}**"
                embed = discord.Embed(title="🗑️ Item Removed", description=desc, color=_CLR_WARN, timestamp=discord.utils.utcnow())
                embed.set_footer(text=_FOOTER)
            else:
                embed = _error_embed(f"Failed to remove '{item_name}'.\n**Error:** {result.get('error', 'Unknown')}")
            await interaction.followup.send(embed=embed)

        @bot.tree.command(name="price", description="Update an item's price on diablo.trade")
        @app_commands.describe(
            item_name="Item name to update",
            new_price="New price (e.g. '2.5b', '500m')",
            account="Account to operate on",
        )
        @app_commands.autocomplete(item_name=_item_autocomplete, account=_account_autocomplete)
        async def cmd_price(interaction: discord.Interaction, item_name: str, new_price: str, account: str = ""):
            await interaction.response.defer(thinking=True)
            target_bot, acct_label = _resolve_bot(account)
            lm = target_bot.listing_manager if (target_bot and hasattr(target_bot, "listing_manager")) else self.listing_manager
            if not lm:
                await interaction.followup.send(embed=_error_embed("Listing manager not connected."))
                return

            numeric_price = normalize_price(new_price)
            result = await lm.update_price(item_name, numeric_price)
            await lm.navigate_back_to_home()

            if result["success"]:
                desc = f"**{item_name}** price updated to **{numeric_price}** gold."
                if acct_label:
                    desc += f"\nAccount: **{acct_label}**"
                embed = discord.Embed(title="💰 Price Updated", description=desc, color=_CLR_SUCCESS, timestamp=discord.utils.utcnow())
                embed.set_footer(text=_FOOTER)
            else:
                embed = _error_embed(f"Failed to update price for '{item_name}'.\n**Error:** {result.get('error', 'Unknown')}")
            await interaction.followup.send(embed=embed)

        @bot.tree.command(name="listings", description="Show diablo.trade listings")
        @app_commands.describe(account="Account name or 'all' for all accounts")
        @app_commands.autocomplete(account=_account_autocomplete)
        async def cmd_listings(interaction: discord.Interaction, account: str = "all"):
            await interaction.response.defer(thinking=True)

            show_all = (not account or account.lower() == "all")
            bots_to_show = []
            if show_all:
                bots_to_show = list(self._all_bots or [])
                if not bots_to_show and self.bot_ref:
                    bots_to_show = [self.bot_ref]
            else:
                target, label = _resolve_bot(account)
                if target:
                    bots_to_show = [target]

            if not bots_to_show:
                await interaction.followup.send(embed=_error_embed("No bots available."))
                return

            embeds = []
            for b in bots_to_show:
                inv = getattr(b, "inventory", None)
                uname = getattr(b.settings, "username", "?") if hasattr(b, "settings") else "?"
                if not inv:
                    continue
                await inv.refresh(force=True)
                active = inv.active_items or []

                if not active:
                    embed = discord.Embed(
                        title=f"📦 {uname} — No Active Listings",
                        color=_CLR_INFO, timestamp=discord.utils.utcnow(),
                    )
                else:
                    lines = []
                    for i, item in enumerate(active[:25], 1):
                        price_str = f"{format_gold(item.price)} gold" if item.price else "N/A"
                        qty = getattr(item, "quantity", 1) or 1
                        qty_str = f" ×{qty}" if qty > 1 else ""
                        lines.append(f"**{i}.** {item.name}{qty_str} — {price_str}")
                    embed = discord.Embed(
                        title=f"📦 {uname} — {len(active)} Listing(s)",
                        description="\n".join(lines),
                        color=_CLR_INFO, timestamp=discord.utils.utcnow(),
                    )
                    if len(active) > 25:
                        embed.set_footer(text=f"{_FOOTER} • Showing 25 of {len(active)}")
                    else:
                        embed.set_footer(text=f"{_FOOTER} • {uname}")
                embeds.append(embed)

            if embeds:
                await interaction.followup.send(embeds=embeds[:10])
            else:
                await interaction.followup.send(embed=_error_embed("Inventory cache not available."))

        @bot.tree.command(name="holds", description="Show active item holds")
        @app_commands.describe(account="Account name or 'all'")
        @app_commands.autocomplete(account=_account_autocomplete)
        async def cmd_holds(interaction: discord.Interaction, account: str = "all"):
            await interaction.response.defer(thinking=True)

            show_all = (not account or account.lower() == "all")
            bots_to_query = []
            if show_all:
                bots_to_query = list(self._all_bots or [])
                if not bots_to_query and self.bot_ref:
                    bots_to_query = [self.bot_ref]
            else:
                target, _ = _resolve_bot(account)
                if target:
                    bots_to_query = [target]

            all_active = []
            for b in bots_to_query:
                repo = getattr(b, "repo", None) or self.repo
                if not repo:
                    continue
                holds = await repo.get_active_holds()
                uname = getattr(b.settings, "username", "?") if hasattr(b, "settings") else "?"
                for h in holds:
                    h["_acct"] = uname
                all_active.extend(holds)

            if not all_active:
                embed = discord.Embed(title="🔒 Active Holds", description="No items currently on hold.", color=_CLR_INFO, timestamp=discord.utils.utcnow())
            else:
                lines = []
                for h in all_active:
                    uuid_short = h.get("item_uuid", "?")[:12]
                    acct_tag = f" ({h['_acct']})" if show_all and len(bots_to_query) > 1 else ""
                    lines.append(f"• `{uuid_short}…` — held by **{h.get('player_name', '?')}**{acct_tag}")
                embed = discord.Embed(title=f"🔒 Active Holds ({len(all_active)})", description="\n".join(lines[:30]), color=_CLR_INFO, timestamp=discord.utils.utcnow())
            embed.set_footer(text=_FOOTER)
            await interaction.followup.send(embed=embed)

        @bot.tree.command(name="stats", description="Show 24h trading statistics")
        @app_commands.describe(account="Account name or 'all'")
        @app_commands.autocomplete(account=_account_autocomplete)
        async def cmd_stats(interaction: discord.Interaction, account: str = "all"):
            await interaction.response.defer(thinking=True)

            show_all = (not account or account.lower() == "all")
            bots_to_query = []
            if show_all:
                bots_to_query = list(self._all_bots or [])
                if not bots_to_query and self.bot_ref:
                    bots_to_query = [self.bot_ref]
            else:
                target, _ = _resolve_bot(account)
                if target:
                    bots_to_query = [target]

            embeds = []
            for b in bots_to_query:
                repo = getattr(b, "repo", None) or self.repo
                if not repo:
                    continue
                stats = await repo.get_daily_stats()
                uname = getattr(b.settings, "username", "?") if hasattr(b, "settings") else "?"
                embed = discord.Embed(
                    title=f"📊 24h Statistics — {uname}",
                    color=_CLR_SUMMARY, timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Total Offers", value=str(stats.get("total_offers", 0)), inline=True)
                embed.add_field(name="Replied", value=str(stats.get("replied", 0)), inline=True)
                embed.add_field(name="Pending", value=str(stats.get("pending", 0)), inline=True)
                embed.add_field(name="Items on Hold", value=str(stats.get("items_on_hold", 0)), inline=True)
                embed.set_footer(text=f"{_FOOTER} • {uname}")
                embeds.append(embed)

            if embeds:
                await interaction.followup.send(embeds=embeds[:10])
            else:
                await interaction.followup.send(embed=_error_embed("No stats available."))

        @bot.tree.command(name="status", description="Check bot connectivity")
        async def cmd_status(interaction: discord.Interaction):
            checks = [f"🟢 Discord Bot: Online as {bot.user}"]
            checks.append(f"{'🟢' if self.repo else '🔴'} Database: {'Connected' if self.repo else 'Disconnected'}")

            for b in (self._all_bots or []):
                uname = getattr(b.settings, "username", "?") if hasattr(b, "settings") else "?"
                lm = getattr(b, "listing_manager", None)
                lm_ok = lm is not None
                inv = getattr(b, "inventory", None)
                inv_count = len(inv.active_items) if inv and hasattr(inv, "active_items") else 0
                checks.append(f"{'🟢' if lm_ok else '🔴'} **{uname}**: LM={'Ready' if lm_ok else 'Off'} | {inv_count} items")

            if not self._all_bots:
                checks.append(f"{'🟢' if self.listing_manager else '🔴'} Listing Manager: {'Ready' if self.listing_manager else 'Not connected'}")

            embed = discord.Embed(
                title="🏥 Bot Status",
                description="\n".join(checks),
                color=_CLR_SUCCESS if self.listing_manager or self._all_bots else _CLR_WARN,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text=_FOOTER)
            await interaction.response.send_message(embed=embed)

        return bot

    async def start(self) -> None:

        if not self.enabled or not self.token:
            log.info("[discord] Discord bot disabled or no token set — skipping")
            return

        self._bot = self._create_bot()
        token_preview = self.token[:12] + "..." if len(self.token) > 12 else "???"
        log.info(f"[discord] Starting Discord bot (token: {token_preview}, ch: {self.channel_id})...")
        try:
            await self._bot.start(self.token)
        except discord.LoginFailure as e:
            log.error(f"[discord] Invalid bot token — Discord bot will not start: {e}")
        except Exception as e:
            log.error(f"[discord] Bot crashed: {e}")
            import traceback
            log.debug(f"[discord] Traceback: {traceback.format_exc()}")

    async def stop(self) -> None:

        if self._bot and not self._bot.is_closed():
            log.info("[discord] Shutting down Discord bot...")
            await self._bot.close()

    async def wait_ready(self, timeout: float = 30.0) -> bool:

        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            log.warning("[discord] Bot did not become ready within timeout")
            return False

    async def _send_embed(
        self,
        embed: discord.Embed,
        view: Optional[discord.ui.View] = None,
        channel_id: Optional[int] = None,
    ) -> None:

        if not self.enabled:
            return

        target_channel = self._channel
        if channel_id and self._bot:
            try:
                ch = self._bot.get_channel(channel_id)
                if ch is None:
                    ch = await self._bot.fetch_channel(channel_id)
                if ch:
                    target_channel = ch
            except Exception as e:
                log.warning(f"[discord] Could not resolve channel {channel_id}: {e}")

        if not target_channel:
            return
        try:
            kwargs = {"embed": embed}
            if view is not None:
                kwargs["view"] = view
            await target_channel.send(**kwargs)
        except Exception as e:
            log.warning(f"[discord] Failed to send message: {e}")

    async def send_offer_alert(
        self,
        player: str,
        item_name: str,
        message_preview: str,
        reply_sent: str,
        intent: str,
        ai_used: bool,
        listed_price: Optional[int] = None,
        offered_price: Optional[int] = None,
        item_uuid: Optional[str] = None,
        quantity: int = 1,
        buyer_quantity: Optional[int] = None,
        caller_bot = None,
    ) -> None:

        if not self.enabled:
            return
        if not self._ready.is_set():
            ready = await self.wait_ready(timeout=5.0)
            if not ready:
                return

        price_str = f"{format_gold(listed_price)} gold" if listed_price else "Unknown"
        offer_str = f"{format_gold(offered_price)} gold" if offered_price else "Unknown"
        mode_label = "AI" if ai_used else "Template"

        trade_intents = {"ready_to_buy", "still_available", "counter_offer"}
        is_trade = intent in trade_intents
        is_lowball = intent == "lowball"

        if is_trade:
            title = f"Trade Offer — {player}"
            color = _CLR_OFFER
            category = "Trade"
        elif is_lowball:
            title = f"Lowball Declined — {player}"
            color = _CLR_WARN
            category = "Lowball"
        else:
            title = f"Chat — {player}"
            color = 0x5865F2
            category = "Chat"

        acct_label = ""
        if caller_bot and hasattr(caller_bot, "settings"):
            acct_label = getattr(caller_bot.settings, "username", "") or ""

        embed = discord.Embed(
            title=title,
            description=f"> {message_preview}",
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Item", value=item_name or "Unknown", inline=True)

        if quantity and quantity > 1:
            embed.add_field(name="Listed Qty", value=str(quantity), inline=True)
        if buyer_quantity and buyer_quantity > 1:
            bq_val = str(buyer_quantity)
            if listed_price and buyer_quantity <= quantity:
                total = format_gold(_price_to_full(listed_price) * buyer_quantity)
                bq_val += f" (total: {total} gold)"
            embed.add_field(name="Buyer Wants", value=bq_val, inline=True)
        if is_trade or is_lowball:
            embed.add_field(name="Listed price", value=price_str, inline=True)
            embed.add_field(name="Buyer offer", value=offer_str, inline=True)
        embed.add_field(name="Intent", value=intent, inline=True)
        embed.add_field(name="Reply", value=f"[{mode_label}] {reply_sent[:400]}", inline=False)
        if acct_label:
            embed.set_footer(text=f"{_FOOTER} • {acct_label}")
        else:
            embed.set_footer(text=_FOOTER)

        view = None
        if is_trade and item_uuid:
            # Use buyer's requested quantity; fall back to total listed quantity so that
            # clicking "Mark Sold" on Discord marks what the buyer actually agreed to buy
            # (for materials with >1 quantity, default to selling everything listed).
            sell_qty = buyer_quantity if (buyer_quantity and buyer_quantity >= 1) else (quantity or 1)
            view = OfferActionView(
                item_name=item_name or "Unknown",
                item_uuid=item_uuid,
                bot_ref=self,
                listed_price=listed_price or 0,
                quantity=sell_qty,
                caller_bot=caller_bot,
            )
        await self._send_embed(embed, view=view)
        log.debug(f"[discord] {category} alert sent for {player}")

    async def send_trade_summary(
        self,
        player: str,
        item_name: str,
        listed_price: Optional[int] = None,
        item_uuid: Optional[str] = None,
        conversation_summary: str = "",
    ) -> None:

        if not self.enabled:
            return
        if not self._ready.is_set():
            ready = await self.wait_ready(timeout=5.0)
            if not ready:
                return

        price_str = f"{format_gold(listed_price)} gold" if listed_price else "Unknown"
        embed = discord.Embed(
            title=f"Trade Finalized — {player}",
            description=conversation_summary[:500] if conversation_summary else "Trade confirmed",
            color=0x2ECC71,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Item", value=item_name or "Unknown", inline=True)
        embed.add_field(name="Price", value=price_str, inline=True)
        embed.set_footer(text=_FOOTER)

        view = None
        if item_uuid:
            view = OfferActionView(
                item_name=item_name or "Unknown",
                item_uuid=item_uuid,
                bot_ref=self,
            )
        await self._send_embed(embed, view=view)
        log.debug(f"[discord] Trade summary sent for {player}")

    async def send_daily_summary(self, stats: dict, account_name: str = "") -> None:

        if not self.enabled:
            return
        title = f"📊 Daily Summary — {account_name}" if account_name else "📊 Daily Summary — D4-Market"
        embed = discord.Embed(
            title=title,
            color=_CLR_SUMMARY,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Total Offers", value=str(stats.get("total_offers", 0)), inline=True)
        embed.add_field(name="Replied", value=str(stats.get("replied", 0)), inline=True)
        embed.add_field(name="Pending", value=str(stats.get("pending", 0)), inline=True)
        embed.add_field(name="AI Replies", value=str(stats.get("ai_used", 0)), inline=True)
        if account_name:
            embed.set_footer(text=f"{_FOOTER} • {account_name}")
        else:
            embed.set_footer(text=_FOOTER)

        await self._send_embed(embed)
        log.info(f"[discord] Daily summary sent{' for ' + account_name if account_name else ''}")

    async def send_error_alert(self, error: str) -> None:

        if not self.enabled:
            return
        embed = discord.Embed(
            title="⚠️ Bot Error",
            description=f"```{error[:500]}```",
            color=_CLR_ERROR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=_FOOTER)
        await self._send_embed(embed)

    async def send_bot_online_notice(self, account_names: list[str] | None = None) -> None:

        if not self.enabled:
            return
        await self.wait_ready(timeout=15.0)

        if account_names:
            acct_list = ", ".join(f"**{n}**" for n in account_names)
            desc = f"Bot program is active. Auto-starting {len(account_names)} session(s): {acct_list}"
        else:
            desc = "Bot program is active and ready.\nUse the TUI dashboard to start individual account sessions."

        embed = discord.Embed(
            title="✅ D4-Market Bot Online",
            description=desc,
            color=_CLR_SUCCESS,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=_FOOTER)
        await self._send_embed(embed)

    async def send_bot_offline_notice(self) -> None:

        if not self.enabled:
            return
        if not self._ready.is_set():
            return
        if self._offline_notice_sent:
            return
        self._offline_notice_sent = True

        embed = discord.Embed(
            title="🔴 D4-Market Bot Offline",
            description="Bot program is shutting down. All sessions stopped.",
            color=_CLR_ERROR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=_FOOTER)
        await self._send_embed(embed)

    async def send_account_status(self, account_id: str, username: str, is_active: bool, channel_id: Optional[int] = None) -> None:

        if not self.enabled:
            return
        if not self._ready.is_set():
            ready = await self.wait_ready(timeout=5.0)
            if not ready:
                return

        if is_active:
            embed = discord.Embed(
                title="▶️ Account Session Started",
                description=f"**{username or account_id}** is now active and monitoring.",
                color=_CLR_SUCCESS,
                timestamp=discord.utils.utcnow(),
            )
        else:
            embed = discord.Embed(
                title="⏹️ Account Session Stopped",
                description=f"**{username or account_id}** has been stopped.",
                color=_CLR_WARN,
                timestamp=discord.utils.utcnow(),
            )
        embed.set_footer(text=_FOOTER)

        target_channel = self._channel
        if channel_id and self._bot:
            try:
                ch = self._bot.get_channel(channel_id)
                if ch is None:
                    ch = await self._bot.fetch_channel(channel_id)
                if ch:
                    target_channel = ch
            except Exception:
                pass

        if target_channel:
            try:
                await target_channel.send(embed=embed)
            except Exception as e:
                log.warning(f"[discord] Failed to send account status: {e}")

    async def send_startup_notice(self, battletag: str = "") -> None:

        await self.send_bot_online_notice()

    async def send_listing_action(self, action: str, item_name: str, detail: str = "") -> None:

        if not self.enabled:
            return
        icons = {"sold": "✅", "removed": "🗑️", "price_update": "💰"}
        icon = icons.get(action, "📋")
        embed = discord.Embed(
            title=f"{icon} Listing {action.replace('_', ' ').title()}",
            description=f"**{item_name}**\n{detail}" if detail else f"**{item_name}**",
            color=_CLR_SUCCESS if action == "sold" else _CLR_WARN,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=_FOOTER)
        await self._send_embed(embed)

def _error_embed(description: str) -> discord.Embed:

    return discord.Embed(
        title="❌ Error",
        description=description,
        color=_CLR_ERROR,
        timestamp=discord.utils.utcnow(),
    )

def _price_to_full(price: int) -> int:
    if not price: return 0
    if price >= 1_000_000: return price
    return price * 1_000_000
