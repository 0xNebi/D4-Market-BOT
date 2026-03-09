import asyncio
import re
from typing import Optional

from playwright.async_api import Page

from ..utils.logger import log
from ..utils.delays import short_delay, page_load_delay

LISTINGS_URL = "https://diablo.trade/user/listings"

# Playwright automation for /user/listings page: mark sold, remove, update prices.
# API calls don't work for these actions — the site requires full DOM interaction.
class ListingManager:

    def __init__(self, page: Page, api=None):
        self.page = page
        self._api = api

    async def _navigate_to_listings(self) -> bool:

        try:
            current = self.page.url
            if "/user/listings" not in current:
                log.info(f"[listing] Navigating to {LISTINGS_URL}")
                await self.page.goto(LISTINGS_URL, wait_until="load", timeout=20_000)
                await page_load_delay()
            else:

                await self.page.reload(wait_until="load", timeout=20_000)
                await page_load_delay()

            log.debug(f"[listing] On listings page: {self.page.url}")
            return True
        except Exception as e:
            log.error(f"[listing] Failed to navigate to listings: {e}")
            return False

    async def _find_item_row(self, item_name: str) -> Optional[object]:

        log.debug(f"[listing] Searching for item: {item_name}")

        try:
            await self.page.wait_for_selector("table", timeout=10_000)
        except Exception:

            try:
                await self.page.wait_for_selector("[class*='listing']", timeout=5_000)
            except Exception:
                log.warning("[listing] No table/listing content found on page")
                return None

        img_el = self.page.get_by_role("img", name=item_name).first
        try:
            if await img_el.is_visible(timeout=3_000):
                log.debug(f"[listing] Located item via image: {item_name}")
                row = img_el.locator("xpath=ancestor::tr[1]")

                text_in_row = row.get_by_text(
                    re.compile(re.escape(item_name), re.IGNORECASE)
                ).first
                try:
                    if await text_in_row.is_visible(timeout=2_000):
                        log.debug(f"[listing] Returning clickable text in row for: {item_name}")
                        return text_in_row
                except Exception:
                    pass

                log.debug(f"[listing] Returning parent row for: {item_name}")
                return row
        except Exception:
            pass

        item_el = self.page.get_by_text(
            re.compile(rf"^{re.escape(item_name)}(x?\d*)?$", re.IGNORECASE)
        ).first
        try:
            if await item_el.is_visible(timeout=3_000):
                log.debug(f"[listing] Found item by regex text: {item_name}")
                return item_el
        except Exception:
            pass

        item_el = self.page.get_by_text(item_name, exact=False).first
        try:
            if await item_el.is_visible(timeout=3_000):
                log.debug(f"[listing] Found item by partial text: {item_name}")
                return item_el
        except Exception:
            pass

        try:
            rows = self.page.locator("table tr, [role='row']")
            count = await rows.count()
            for i in range(count):
                row = rows.nth(i)
                text = await row.inner_text()
                if item_name.lower() in text.lower():
                    log.debug(f"[listing] Found item in row {i}: {text[:80]}")
                    name_in_row = row.get_by_text(item_name, exact=False).first
                    try:
                        if await name_in_row.is_visible(timeout=2_000):
                            return name_in_row
                    except Exception:
                        return row
        except Exception as e:
            log.debug(f"[listing] Row scan failed: {e}")

        log.warning(f"[listing] Item not found: {item_name}")
        return None

    async def _click_item(self, item_name: str) -> bool:

        item_el = await self._find_item_row(item_name)
        if not item_el:
            return False

        try:
            await item_el.click()
            await short_delay()
            log.debug(f"[listing] Clicked item: {item_name}")
            return True
        except Exception as e:
            log.error(f"[listing] Failed to click item {item_name}: {e}")
            return False

    async def _click_action_button(self, button_name: str) -> bool:

        btn = self.page.get_by_role("button", name=re.compile(button_name, re.IGNORECASE)).first
        try:
            await btn.wait_for(state="visible", timeout=5_000)
            await btn.click()
            await short_delay()
            log.debug(f"[listing] Clicked button: {button_name}")
            return True
        except Exception as e:
            log.warning(f"[listing] Button '{button_name}' not found or not clickable: {e}")
            return False

    async def _dismiss_modal(self) -> None:

        try:
            await self.page.keyboard.press("Escape")
            await short_delay()
        except Exception:
            pass

    async def mark_as_sold(self, item_name: str) -> dict:

        result = {"success": False, "item_name": item_name, "action": "mark_as_sold", "error": None}

        if not await self._navigate_to_listings():
            result["error"] = "Failed to navigate to listings page"
            return result

        if not await self._click_item(item_name):
            result["error"] = f"Item '{item_name}' not found in listings"
            return result

        if not await self._click_action_button("Mark as Sold"):
            result["error"] = "Could not find 'Mark as Sold' button"
            await self._dismiss_modal()
            return result

        await short_delay()
        try:
            confirm_btn = self.page.get_by_role(
                "button", name=re.compile(r"Mark as Sold|confirm|yes|ok", re.IGNORECASE)
            ).first
            if await confirm_btn.is_visible(timeout=3_000):
                await confirm_btn.click()
                await short_delay()
                log.debug("[listing] Confirmed sold dialog")
        except Exception:
            pass

        await self._dismiss_modal()

        log.success(f"[listing] Marked '{item_name}' as SOLD on diablo.trade")
        result["success"] = True
        return result

    async def remove_listing(self, item_name: str) -> dict:

        result = {"success": False, "item_name": item_name, "action": "remove", "error": None}

        if not await self._navigate_to_listings():
            result["error"] = "Failed to navigate to listings page"
            return result

        if not await self._click_item(item_name):
            result["error"] = f"Item '{item_name}' not found in listings"
            return result

        if not await self._click_action_button("Remove"):
            result["error"] = "Could not find 'Remove' button"
            await self._dismiss_modal()
            return result

        await short_delay()
        try:
            confirm_btn = self.page.get_by_role(
                "button", name=re.compile(r"Remove|confirm|yes|ok", re.IGNORECASE)
            ).first
            if await confirm_btn.is_visible(timeout=3_000):
                await confirm_btn.click()
                await short_delay()
                log.debug("[listing] Confirmed remove dialog")
        except Exception:
            pass

        await self._dismiss_modal()

        log.success(f"[listing] Removed '{item_name}' from diablo.trade")
        result["success"] = True
        return result

    async def update_price(self, item_name: str, new_price: str) -> dict:

        result = {
            "success": False, "item_name": item_name,
            "action": "update_price", "new_price": new_price, "error": None,
        }

        if not await self._navigate_to_listings():
            result["error"] = "Failed to navigate to listings page"
            return result

        if not await self._click_item(item_name):
            result["error"] = f"Item '{item_name}' not found in listings"
            return result

        btn_clicked = await self._click_action_button("Update Price")
        if not btn_clicked:
            btn_clicked = await self._click_action_button("Edit")
        if not btn_clicked:
            result["error"] = "Could not find 'Update Price' or 'Edit' button"
            await self._dismiss_modal()
            return result

        price_input = None

        selectors = [
            "input[type='number']",
            "input[name*='price']",
            "input[placeholder*='price']",
            "input[placeholder*='Price']",
            "input[type='text']",
        ]
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.is_visible(timeout=2_000):
                    price_input = el
                    break
            except Exception:
                continue

        if not price_input:
            result["error"] = "Could not find price input field"
            await self._dismiss_modal()
            return result

        try:
            await price_input.click()
            await price_input.fill("")
            await short_delay()
            await price_input.fill(new_price)
            await short_delay()
            log.debug(f"[listing] Entered new price: {new_price}")
        except Exception as e:
            result["error"] = f"Failed to enter price: {e}"
            await self._dismiss_modal()
            return result

        submitted = False
        for btn_name in ["Save", "Update", "Confirm", "Submit", "OK"]:
            try:
                save_btn = self.page.get_by_role(
                    "button", name=re.compile(btn_name, re.IGNORECASE)
                ).first
                if await save_btn.is_visible(timeout=1_500):
                    await save_btn.click()
                    await short_delay()
                    submitted = True
                    log.debug(f"[listing] Clicked submit button: {btn_name}")
                    break
            except Exception:
                continue

        if not submitted:

            try:
                await price_input.press("Enter")
                submitted = True
                log.debug("[listing] Pressed Enter to submit price")
            except Exception:
                pass

        if not submitted:
            result["error"] = "Could not find submit button for price update"
            await self._dismiss_modal()
            return result

        await self._dismiss_modal()

        log.success(f"[listing] Updated price of '{item_name}' to {new_price}")
        result["success"] = True
        return result

    async def navigate_back_to_home(self) -> None:

        try:
            await self.page.goto("https://diablo.trade", wait_until="load", timeout=15_000)
            await page_load_delay()
        except Exception as e:
            log.debug(f"[listing] Navigate home failed (non-fatal): {e}")

    async def mark_as_sold_by_id(
        self,
        item_id: str,
        sold_price: int = 0,
        quantity: int = 1,
        game_mode: str = "SEASONAL_SOFTCORE",
    ) -> dict:

        result = {
            "success": False, "item_id": item_id,
            "action": "mark_as_sold_api", "error": None,
        }
        if not self._api:
            result["error"] = "No DiabloAPI instance available"
            return result

        try:
            ok = await self._api.mark_item_sold(
                item_id=item_id,
                sold_price=sold_price,
                quantity=quantity,
                game_mode=game_mode,
            )
            if ok:
                log.success(f"[listing] Item {item_id[:12]}... marked SOLD via API (price={sold_price:,}, qty={quantity})")
                result["success"] = True
            else:
                result["error"] = "API mark_item_sold returned non-OK"
                log.warning(f"[listing] API mark_item_sold failed for {item_id[:12]}...")
        except Exception as e:
            result["error"] = str(e)
            log.warning(f"[listing] API mark_item_sold exception: {e}")

        return result

    async def get_my_listings_api(
        self,
        page_num: int = 1,
        take: int = 10,
        game_mode: str = "SEASONAL_SOFTCORE",
    ) -> Optional[str]:

        if not self._api:
            log.warning("[listing] No DiabloAPI instance for get_my_listings")
            return None
        try:
            return await self._api.get_my_listings(
                page_num=page_num,
                take=take,
                game_mode=game_mode,
            )
        except Exception as e:
            log.warning(f"[listing] get_my_listings_api failed: {e}")
            return None
