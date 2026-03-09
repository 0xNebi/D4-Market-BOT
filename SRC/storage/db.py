import aiosqlite
from pathlib import Path

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,       -- conversationId from API (userId1|userId2)
    account_id      TEXT DEFAULT '',        -- which account owns this conversation
    player_name     TEXT NOT NULL,          -- buyer's display name
    player_id       TEXT,                   -- buyer's user UUID
    item_uuid       TEXT,                   -- parsed from {d4:uuid} in message
    item_name       TEXT,                   -- resolved from listing API
    listed_price    INTEGER,                -- seller's listed price in gold
    first_seen_at   INTEGER NOT NULL,       -- unix timestamp (seconds)
    replied_at      INTEGER,                -- when the bot sent the reply
    reply_text      TEXT,                   -- the exact text sent
    status          TEXT DEFAULT 'pending', -- pending | replied | ignored | error | on_hold
    raw_message     TEXT,                   -- original buyer message content
    intent          TEXT,                   -- classified intent (MessageIntent value)
    ai_used         INTEGER DEFAULT 0,      -- 1 if Gemini was used for this reply
    last_msg_ts     INTEGER DEFAULT 0       -- api lastMessage.ts of the message we last acted on (ms)
);

CREATE TABLE IF NOT EXISTS action_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  TEXT DEFAULT '',            -- which account performed this action
    ts          INTEGER NOT NULL,           -- unix timestamp (seconds)
    conv_id     TEXT,                       -- linked conversation ID (nullable)
    action      TEXT NOT NULL,             -- POLL | REPLY | SCREENSHOT | ERROR | SKIP
    detail      TEXT                        -- human-readable free-form description
);

-- Tracks which item is on hold for which buyer.
-- At most one active hold per item_uuid at a time.
CREATE TABLE IF NOT EXISTS item_holds (
    item_uuid   TEXT PRIMARY KEY,
    account_id  TEXT DEFAULT '',        -- which account owns this hold
    conv_id     TEXT NOT NULL,          -- conversation that has the hold
    player_name TEXT NOT NULL,          -- buyer's display name
    held_at     INTEGER NOT NULL,       -- unix timestamp (seconds)
    status      TEXT DEFAULT 'holding', -- holding | sold | released
    quantity    INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_conv_status  ON conversations(status);
CREATE INDEX IF NOT EXISTS idx_conv_time    ON conversations(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_conv_acct    ON conversations(account_id);
CREATE INDEX IF NOT EXISTS idx_log_ts       ON action_logs(ts);
CREATE INDEX IF NOT EXISTS idx_log_conv     ON action_logs(conv_id);
CREATE INDEX IF NOT EXISTS idx_log_acct     ON action_logs(account_id);
CREATE INDEX IF NOT EXISTS idx_holds_status ON item_holds(status);
CREATE INDEX IF NOT EXISTS idx_holds_acct   ON item_holds(account_id);
"""

_MIGRATIONS = [
    "ALTER TABLE conversations ADD COLUMN last_msg_ts INTEGER DEFAULT 0",
    "ALTER TABLE conversations ADD COLUMN account_id TEXT DEFAULT ''",
    "ALTER TABLE action_logs ADD COLUMN account_id TEXT DEFAULT ''",
    "ALTER TABLE item_holds ADD COLUMN account_id TEXT DEFAULT ''",
    "ALTER TABLE item_holds ADD COLUMN quantity INTEGER DEFAULT 0",
]

# Schema applied with CREATE IF NOT EXISTS so it is safe to run on every startup.
# Migrations list is attempted first (ALTER TABLE) and silently ignored if the
# column already exists — this handles upgrades from older schema versions.
async def init_db(db_path: str = "DATA/bot.db", primary_account_id: str = "") -> None:

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:

        for stmt in _MIGRATIONS:
            try:
                await db.execute(stmt)
                await db.commit()
            except Exception:
                pass

        await db.executescript(_SCHEMA)

        if primary_account_id:
            for table in ("conversations", "action_logs", "item_holds"):
                await db.execute(
                    f"UPDATE {table} SET account_id = ? WHERE account_id = '' OR account_id IS NULL",
                    (primary_account_id,),
                )
            await db.commit()
