import time
from typing import Optional
import aiosqlite

# All async DB operations for the bot. One Repository instance per account_id;
# account_id is injected into every INSERT so multi-account data stays separated.
class Repository:

    def __init__(self, db_path: str = "DATA/bot.db", account_id: str = ""):
        self.db_path = db_path
        self.account_id = account_id

    # Returns True only when the incoming message timestamp is newer than what
    # was last processed for this conversation. Prevents double-replies.
    async def needs_reply(
        self,
        conv_id:     str,
        last_msg_ts: int,
        msg_user_id: str,
        my_id:       str,
    ) -> bool:

        if msg_user_id == my_id:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT last_msg_ts FROM conversations WHERE id = ?",
                (conv_id,),
            ) as cursor:
                row = await cursor.fetchone()
        stored_ts = row[0] if row else 0
        return last_msg_ts > (stored_ts or 0)

    async def get_daily_stats(self) -> dict:

        since = int(time.time()) - 86400
        acct_filter = "AND account_id = ?" if self.account_id else ""
        acct_params: tuple = (self.account_id,) if self.account_id else ()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM conversations WHERE first_seen_at > ? {acct_filter}",
                (since, *acct_params),
            ) as c:
                total = (await c.fetchone())[0]
            async with db.execute(
                f"SELECT COUNT(*) FROM conversations WHERE replied_at > ? AND status='replied' {acct_filter}",
                (since, *acct_params),
            ) as c:
                replied = (await c.fetchone())[0]
            async with db.execute(
                f"SELECT COUNT(*) FROM conversations WHERE ai_used=1 AND first_seen_at > ? {acct_filter}",
                (since, *acct_params),
            ) as c:
                ai_used = (await c.fetchone())[0]

            try:
                async with db.execute(
                    f"SELECT COUNT(*) FROM item_holds WHERE status='holding' {acct_filter}",
                    acct_params,
                ) as c:
                    on_hold = (await c.fetchone())[0]
            except Exception:
                on_hold = 0

            try:
                async with db.execute(
                    f"SELECT ts, detail FROM action_logs WHERE action='AI_METRICS' AND ts > ? {acct_filter}",
                    (since, *acct_params),
                ) as c:
                    ai_metrics_rows = await c.fetchall()
                actual_ai_requests = len(ai_metrics_rows)

                total_tokens = 0
                ai_timeline = []
                for row in ai_metrics_rows:
                    ts, detail = row[0], row[1]
                    tks = 0
                    if detail and "tokens=" in detail:
                        try:
                            tks = int(detail.replace("tokens=", "").strip())
                        except Exception:
                            pass
                    total_tokens += tks
                    ai_timeline.append({"ts": ts, "tokens": tks})

                if actual_ai_requests > ai_used:
                    ai_used = actual_ai_requests
            except Exception:
                actual_ai_requests = ai_used
                total_tokens = 0
                ai_timeline = []

        return {
            "total_offers":  total,
            "replied":       replied,
            "pending":       max(0, total - replied),
            "ai_used":       max(ai_used, actual_ai_requests),
            "total_tokens":  total_tokens,
            "ai_timeline":   ai_timeline,
            "items_on_hold": on_hold,
        }

    async def get_all_conversations(self, limit: int = 100) -> list[dict]:

        # account-scoped query — also includes legacy rows with empty account_id
        if self.account_id:
            acct_clause = "WHERE (account_id = ? OR account_id = '')"
            acct_params: tuple = (self.account_id,)
        else:
            acct_clause = ""
            acct_params = ()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM conversations {acct_clause} ORDER BY first_seen_at DESC LIMIT ?",
                (*acct_params, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_all_holds(self) -> list[dict]:

        acct_clause = "WHERE account_id = ?" if self.account_id else ""
        acct_params: tuple = (self.account_id,) if self.account_id else ()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            try:
                async with db.execute(
                    f"SELECT * FROM item_holds {acct_clause} ORDER BY held_at DESC",
                    acct_params,
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
            except Exception:
                return []

    async def get_active_holds(self) -> list[dict]:

        if self.account_id:
            clause = "WHERE status = 'holding' AND account_id = ?"
            params: tuple = (self.account_id,)
        else:
            clause = "WHERE status = 'holding'"
            params = ()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            try:
                async with db.execute(
                    f"SELECT * FROM item_holds {clause} ORDER BY held_at DESC",
                    params,
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
            except Exception:
                return []

    async def get_sold_items(self, limit: int = 50) -> list[dict]:

        if self.account_id:
            clause = "WHERE status = 'sold' AND account_id = ?"
            params: tuple = (self.account_id,)
        else:
            clause = "WHERE status = 'sold'"
            params = ()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            try:
                async with db.execute(
                    f"SELECT * FROM item_holds {clause} ORDER BY held_at DESC LIMIT ?",
                    (*params, limit),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
            except Exception:
                return []

    async def get_item_hold(self, item_uuid: str) -> Optional[dict]:

        if not item_uuid:
            return None
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            try:
                async with db.execute(
                    "SELECT * FROM item_holds WHERE item_uuid = ? AND status = 'holding'",
                    (item_uuid,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
            except Exception:
                return None

    async def set_item_hold(
        self, item_uuid: str, conv_id: str, player_name: str, quantity: int = 1
    ) -> None:

        if not item_uuid:
            return
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO item_holds (item_uuid, account_id, conv_id, player_name, held_at, status, quantity)
                VALUES (?, ?, ?, ?, ?, 'holding', ?)
                ON CONFLICT(item_uuid) DO UPDATE SET
                    conv_id     = excluded.conv_id,
                    player_name = excluded.player_name,
                    held_at     = excluded.held_at,
                    status      = 'holding',
                    quantity    = excluded.quantity
                """,
                (item_uuid, self.account_id, conv_id, player_name, now, max(1, quantity)),
            )
            await db.commit()

    async def release_item_hold(self, item_uuid: str) -> Optional[dict]:

        hold = await self.get_item_hold(item_uuid)
        if not hold:
            return None
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE item_holds SET status='released' WHERE item_uuid=?",
                (item_uuid,),
            )
            await db.commit()
        return hold

    # Marks stale holds (older than max_age_seconds) as released.
    # Called once per poll cycle to prevent items staying locked indefinitely.
    async def expire_stale_holds(self, max_age_seconds: int = 7200) -> int:

        cutoff = int(time.time()) - max_age_seconds
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT item_uuid, player_name FROM item_holds WHERE status='holding' AND held_at < ?",
                (cutoff,),
            )
            stale = await cursor.fetchall()
            if stale:
                await db.execute(
                    "UPDATE item_holds SET status='released' WHERE status='holding' AND held_at < ?",
                    (cutoff,),
                )
                await db.commit()
            return len(stale)

    async def get_active_holds_count(self) -> int:

        acct_filter = "AND account_id = ?" if self.account_id else ""
        acct_params: tuple = (self.account_id,) if self.account_id else ()
        async with aiosqlite.connect(self.db_path) as db:
            try:
                async with db.execute(
                    f"SELECT COUNT(*) FROM item_holds WHERE status='holding' {acct_filter}",
                    acct_params,
                ) as c:
                    return (await c.fetchone())[0]
            except Exception:
                return 0

    async def get_conversation_record(self, conv_id: str) -> Optional[dict]:

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM conversations WHERE id = ?", (conv_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def mark_item_sold(self, item_uuid: str) -> None:

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE item_holds SET status='sold' WHERE item_uuid=?",
                (item_uuid,),
            )
            await db.commit()

    async def get_waitlist_for_item(self, item_uuid: str) -> list[dict]:

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM conversations
                WHERE item_uuid = ? AND status = 'on_hold'
                ORDER BY first_seen_at ASC
                """,
                (item_uuid,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def record_reply(
        self,
        conv_id:      str,
        player:       str,
        player_id:    str           = "",
        reply:        str           = "",
        item_uuid:    Optional[str] = None,
        item_name:    Optional[str] = None,
        listed_price: Optional[int] = None,
        raw_message:  Optional[str] = None,
        intent:       Optional[str] = None,
        ai_used:      bool          = False,
        last_msg_ts:  int           = 0,
        status:       str           = "replied",
    ) -> None:

        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO conversations
                    (id, account_id, player_name, player_id, item_uuid, item_name, listed_price,
                     first_seen_at, replied_at, reply_text, status, raw_message,
                     intent, ai_used, last_msg_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    player_name  = excluded.player_name,
                    player_id    = COALESCE(excluded.player_id,    conversations.player_id),
                    replied_at   = excluded.replied_at,
                    reply_text   = excluded.reply_text,
                    status       = excluded.status,
                    item_uuid    = COALESCE(excluded.item_uuid,    conversations.item_uuid),
                    intent       = excluded.intent,
                    ai_used      = excluded.ai_used,
                    last_msg_ts  = excluded.last_msg_ts,
                    item_name    = COALESCE(excluded.item_name,    conversations.item_name),
                    listed_price = COALESCE(excluded.listed_price, conversations.listed_price),
                    raw_message  = COALESCE(excluded.raw_message,  conversations.raw_message)
                """,
                (
                    conv_id, self.account_id, player, player_id, item_uuid, item_name, listed_price,
                    now, now, reply, status, raw_message,
                    intent, int(ai_used), last_msg_ts,
                ),
            )
            await db.commit()

    async def log_action(
        self,
        action:  str,
        conv_id: Optional[str] = None,
        detail:  Optional[str] = None,
    ) -> None:

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO action_logs (ts, account_id, conv_id, action, detail) VALUES (?, ?, ?, ?, ?)",
                (int(time.time()), self.account_id, conv_id, action, detail),
            )
            await db.commit()

    async def get_recent_actions(self, limit: int = 20) -> list[dict]:

        acct_clause = "WHERE account_id = ?" if self.account_id else ""
        acct_params: tuple = (self.account_id,) if self.account_id else ()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM action_logs {acct_clause} ORDER BY id DESC LIMIT ?",
                (*acct_params, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def delete_player_data(self, player_name: str) -> int:

        total = 0
        async with aiosqlite.connect(self.db_path) as db:

            cursor = await db.execute(
                "DELETE FROM conversations WHERE player_name = ?", (player_name,)
            )
            total += cursor.rowcount

            try:
                cursor = await db.execute(
                    "DELETE FROM item_holds WHERE player_name = ?", (player_name,)
                )
                total += cursor.rowcount
            except Exception:
                pass

            try:
                cursor = await db.execute(
                    "DELETE FROM action_logs WHERE detail LIKE ?",
                    (f"%{player_name}%",)
                )
                total += cursor.rowcount
            except Exception:
                pass

            await db.commit()
        return total
