from __future__ import annotations

from typing import Any

async def fetch_listing(page, uuid: str) -> Any:
    return await page.evaluate(
        """async (uuid) => {
            const r = await fetch('/api/listing/get?ids=' + uuid);
            if (!r.ok) return {__error: r.status};
            const data = await r.json();
            if (Array.isArray(data) && data.length > 0) return data[0];
            if (typeof data === 'object') return data;
            return null;
        }""",
        uuid,
    )

async def fetch_my_listings(
    page,
    page_num: int,
    take: int,
    game_mode: str,
    sold: bool,
    removed: bool,
) -> Any:
    return await page.evaluate(
        """async ([pageNum, take, gameMode, sold, removed]) => {
            const r = await fetch('/user/listings?realm=' + gameMode, {
                method: 'POST',
                headers: {
                    'Content-Type': 'text/plain;charset=UTF-8',
                    'Next-Action': '4084e026e2f5c935294782367718e4bdf54954e544',
                    'Accept': 'text/x-component'
                },
                body: JSON.stringify([{
                    pagination: { page: pageNum, take: take },
                    filters: {
                        sort: undefined,
                        gameMode: gameMode,
                        listingType: 'ITEM',
                        listingMode: null,
                        statFilters: [],
                        sold: sold,
                        removed: removed,
                        name: undefined,
                        itemCategory: undefined,
                        classType: undefined,
                        itemRarity: undefined
                    }
                }])
            });
            return await r.text();
        }""",
        [page_num, take, game_mode, sold, removed],
    )

async def mark_item_sold(
    page,
    item_id: str,
    sold_price: int,
    quantity: int,
    game_mode: str,
) -> Any:
    return await page.evaluate(
        """async ([itemId, soldPrice, quantity, gameMode]) => {
            const r = await fetch('/user/listings?realm=' + gameMode, {
                method: 'POST',
                headers: {
                    'Content-Type': 'text/plain;charset=UTF-8',
                    'Next-Action': '408d8bfe2ff6f60705614dbcd00fe9420e55556142',
                    'Accept': 'text/x-component'
                },
                body: JSON.stringify([
                    {
                        id: itemId,
                        isSold: true,
                        soldPrice: soldPrice,
                        quantity: quantity
                    },
                    {
                        client: "$T",
                        meta: undefined,
                        mutationKey: undefined
                    }
                ])
            });
            return r.ok;
        }""",
        [item_id, sold_price, quantity, game_mode],
    )

async def create_material_listing(
    page,
    material_id: str,
    quantity: int,
    price: int,
    game_mode: str,
) -> Any:
    return await page.evaluate(
        """async ([materialId, quantity, price, gameMode]) => {
            const r = await fetch('/listings/create/material', {
                method: 'POST',
                headers: {
                    'Content-Type': 'text/plain;charset=UTF-8',
                    'Next-Action': '40c131bb6d009d22f2c108038ce83aa19b35c0bb38',
                    'Accept': 'text/x-component'
                },
                body: JSON.stringify([
                    {
                        materialId: materialId,
                        itemQuantity: quantity,
                        price: price,
                        gameMode: gameMode,
                        listingMode: "SELLING"
                    },
                    {
                        client: "$T",
                        meta: undefined,
                        mutationKey: undefined
                    }
                ])
            });
            return r.ok;
        }""",
        [material_id, quantity, price, game_mode],
    )

async def delete_listing(
    page,
    listing_id: str,
    status_filter: str = "SOLD"
) -> Any:
    return await page.evaluate(
        """async ([listingId, statusFilter]) => {
            const r = await fetch('/user/listings?status=' + statusFilter, {
                method: 'POST',
                headers: {
                    'Content-Type': 'text/plain;charset=UTF-8',
                    'Next-Action': '40b75797d24c705030e40b7b1aae0f417f8507be14',
                    'Accept': 'text/x-component'
                },
                body: JSON.stringify([
                    listingId,
                    {
                        client: "$T",
                        meta: undefined,
                        mutationKey: undefined
                    }
                ])
            });
            return r.ok;
        }""",
        [listing_id, status_filter],
    )