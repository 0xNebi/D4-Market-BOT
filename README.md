![D4-Market Banner](assets/BANNER.SVG)

# D4-Market

Automated trading bot for [diablo.trade](https://diablo.trade) with multi-account support, Gemini AI replies, Discord integration, and a real-time web dashboard.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Running the Bot](#running-the-bot)
7. [Web Dashboard](#web-dashboard)
8. [Discord Integration](#discord-integration)
9. [AI Configuration](#ai-configuration)
10. [Screenshots](#screenshots)

---

## Overview

D4-Market monitors your diablo.trade listings, auto-replies to incoming trade messages using rule-based templates or Google Gemini AI, tracks holds, and pushes offer alerts to a Discord channel. Each monitored account runs as an independent session (a live Chrome browser attached via Chrome DevTools Protocol).

**Key features:**

- Multi-account — run N accounts from one process, each with its own browser session
- Rule-based template replies (no AI required)
- Google Gemini AI fallback for complex messages
- Discord offer alerts with interactive "Mark Sold / Release Hold / Remove Listing" buttons
- Hold system — reserves an item for a buyer while they log in-game
- SQLite conversation history and action log
- Real-time web dashboard (React) on `http://localhost:8080`

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         main.py                             │
│  AccountManager → reads CONFIG/accounts.yaml                │
│  SessionManager → launches headless Chrome per account      │
│  Spawns one D4MarketBot task per enabled account            │
└────────────────┬────────────────────────────────────────────┘
                 │ asyncio.gather
     ┌───────────┴───────────┐
     ▼                       ▼
D4MarketBot (acct A)   D4MarketBot (acct B)  ...
     │                       │
     ├─ Playwright CDP ───► Chrome (port 9222+)
     │    attaches to running headless session per account
     │
     ├─ DiabloWebSocket (browser-injected JS)
     │    realtime inbox subscription + message send
     │
     ├─ InventoryCache (RSC parser)
     │    in-memory listing store, refreshed on demand
     │
     ├─ AI (gemini.py)
     │    Gemini 2.0 Flash Lite, tool-calling enabled
     │
     ├─ IntentClassifier (classifier.py)
     │    rule-based Levenshtein intent → template or AI
     │
     └─ Repository (storage/repository.py)
          aiosqlite  →  DATA/bot.db
          tables: conversations, action_logs, item_holds

Shared singletons (created once in main.py):
  D4DiscordBot  ── discord.py → Discord channel alerts + 7 slash cmds
  ControlServer ── aiohttp REST API + static files → port 8080

SRC/managers/
  AccountManager ── loads & saves accounts.yaml, hot-reload on change
  SessionManager ── manages Chrome subprocesses per account

SRC/web_ui/frontend/   React + TypeScript + Vite + Tailwind + Recharts
  npm run build  →  SRC/web_ui/frontend/dist/  (served by ControlServer)
```
---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | `python --version` |
| Node.js | 18+ | Only needed to build the frontend |
| Google Chrome / Chromium | any recent | Launched with CDP enabled |
| Discord Bot Token | — | For alerts (optional but recommended) |
| Google Gemini API Key | — | For AI replies (optional) |

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/0xNebi/D4-Market-BOT.git
cd D4-Market-BOT
```

### 2. Install Python dependencies (choose one)

```bash
# Option A: requirements.txt
pip install -r requirements.txt

# Option B: pyproject.toml (PEP 517/518-aware installers)
pip install .

playwright install chromium
```

If you prefer to manage a dedicated virtual environment, create and activate it using your tooling of choice before installing dependencies.

### 3. Build the frontend

```bash
cd SRC/web_ui/frontend
npm install
npm run build
cd ../../..
```

---

## Configuration

All configuration lives in the `CONFIG/` directory. **Never commit real credentials** — `CONFIG/.env` and `CONFIG/accounts.yaml` are gitignored.

### CONFIG/.env

Copy the example file and fill in your values:

```bash
cp CONFIG/.env.example CONFIG/.env
```

```env
# Bot Identity — BattleTag and internal user ID (auto-detected at runtime if left blank)
BATTLETAG=YourTag#1234
MY_USER_ID=

# Discord
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_CHANNEL_ID=0

# Google Gemini AI
GOOGLE_API_KEY=your_google_api_key_here
USE_AI=false
GEMINI_MODEL=gemini-2.0-flash-lite

# Browser (CDP)
CDP_URL=http://localhost:9222
TARGET_URL=https://diablo.trade
```

> **Note:** `BATTLETAG` and `MY_USER_ID` are global fallbacks. Per-account values in `accounts.yaml` take precedence. `MY_USER_ID` is auto-resolved from the active session at startup if not set.

### CONFIG/config.yaml

Global settings. All values here override the built-in defaults. Per-account `accounts.yaml` overrides these for each session.

```yaml
bot:
  check_interval: 10              # seconds between inbox polls
  hold_expiry_seconds: 7200       # auto-release holds after 2 hours
  auto_accept_bnet_reveal: true   # automatically accept Battle.net tag reveal requests
  message_batch_window: 5.0       # seconds to wait for more messages before replying

reply:
  use_ai: false
  ai_threshold: "complex"         # always | complex | simple
  gemini_model: "gemini-2.0-flash-lite"
  min_delay_ms: 800
  max_delay_ms: 2500
  template_ready_to_buy: "yeah item is ready, add me {battletag}"
  template_price_inquiry: "looking for {price} gold for that one. {battletag} if interested"
  template_still_available: "yep still available, {battletag}"
  template_lowball_decline: "nah sorry cant go that low"
  template_unknown: "hey, lmk what item ur looking at and ill check. {battletag}"
  template_item_reserved: "that one is reserved for someone rn, ill msg u if it falls through"

discord_bot:
  token: ""           # override via DISCORD_BOT_TOKEN in .env
  channel_id: 0       # right-click Discord channel → Copy Channel ID

price_rules:
  auto_decline_below_pct: 50.0    # auto-decline offers below this % of listed price
  auto_accept_above_pct: 90.0     # auto-accept offers above this % of listed price
```

### CONFIG/accounts.yaml

Copy the example and fill in your details:

```bash
cp CONFIG/accounts.yaml.example CONFIG/accounts.yaml
```

Each entry in the `accounts` list represents one monitored account:

```yaml
accounts:
  - id: "main-account"            # unique key (any string or UUID)
    username: YourUsername
    player_tag: "YourTag#1234"    # BattleTag shown on diablo.trade
    cdp_port: 9222                # Chrome DevTools Protocol port for this session
    cdp_url: http://localhost:9222
    use_ai: true                  # enable Gemini AI replies for this account
    use_templates: false          # true = fallback to templates when AI is off
    ai_model: gemini-2.0-flash-lite
    discord_channel_id: 0         # per-account Discord channel override
    bot_enabled: true
    proxy: null                   # optional: "http://user:pass@host:port"
```

See `CONFIG/accounts.yaml.example` for all available fields with comments.

### Setting up Chrome sessions

Each account needs its own Chrome instance running with remote debugging enabled **and** already logged in to diablo.trade. Use the TUI dashboard's "Setup Session" action — it will launch Chrome on the correct CDP port so you can log in, then close it when done. The bot re-launches Chrome headlessly on every start using the saved session.

Manual setup (if not using the dashboard):

```bash
# Replace PORT and PROFILE_DIR for each account
chrome.exe --remote-debugging-port=9222 --user-data-dir="DATA/sessions/MyAccount"
```

Log in to diablo.trade inside that browser window, then close Chrome. The bot will use the saved cookies on the next run.

---

## Running the Bot

```bash
python BOT/main.py
```

The bot will:

1. Load accounts from `CONFIG/accounts.yaml`
2. Launch headless Chrome for each account that has a saved session
3. Start the ControlServer web dashboard on `http://localhost:8080`
4. Begin polling inboxes and auto-replying

To stop all processes gracefully:

```bash
BOT\kill.bat
```

Run without the Rich TUI (useful for plain logging):

```bash
python BOT/main.py --no-dashboard
```

---

## Web Dashboard

Open **http://localhost:8080** in your browser after starting the bot.

### Features

- Per-account tabs — conversations, inventory, active holds
- Active holds list with release button
- Action log (sold, released, errors)
- REST API: `GET /api/status`, `GET /api/inventory`, `GET /api/activity`

---

## Discord Integration

### Offer Alerts

When a new offer arrives, the bot posts an embed to your configured `discord_channel_id` with:

- Item name, listed price, buyer's offered price
- **Mark Sold ✅** — records the sale, marks item sold on diablo.trade, releases the hold
- **Release Hold 🔓** — frees the reservation without marking sold
- **Remove Listing 🗑️** — removes the listing from diablo.trade entirely

### Slash Commands

| Command | Description |
|---|---|
| `/sold <item>` | Mark an item as sold on diablo.trade |
| `/remove <item>` | Remove a listing from diablo.trade |
| `/price <item> <new_price>` | Update the price of a listing |
| `/listings [account]` | Show all active listings |
| `/holds [account]` | Show all active holds |
| `/stats [account]` | Show 24-hour reply and AI usage stats |
| `/status` | Show bot uptime and active session count |

All commands support an optional `account` parameter for multi-account setups.

---

## AI Configuration

The bot uses **Google Gemini 2.0 Flash Lite** by default. AI is invoked when:

- `ai_threshold` is `"always"` — every reply goes through AI
- `ai_threshold` is `"complex"` (default) — AI handles `COUNTER_OFFER` and `INVENTORY_QUERY` intents
- `ai_threshold` is `"simple"` — only template reply intents use AI (i.e., AI is rarely used)

### Enabling / disabling AI per account

```yaml
# CONFIG/accounts.yaml
accounts:
  - id: "main-account"
    use_ai: true        # set false to disable AI for this account
    use_templates: true # true = fall back to template replies when AI is off
```

### Tools available to the AI

The AI can call these functions during a reply:

| Tool | What it does |
|---|---|
| `get_full_inventory` | Returns all active listings so AI can answer "what do you have for sale?" |
| `check_item_status` | Checks if a specific item is still available |
| `request_tag_reveal` | Asks the buyer to reveal their Battle.net tag for in-game trading |
| `accept_tag_reveal` | Accepts an incoming Battle.net tag reveal request |

---

## Screenshots

### Terminal TUI Dashboard
The main control interface featuring account management, session configuration, and real-time monitoring:

![Terminal Account Setup](assets/terminal_account_setup.png)

Navigate accounts, configure settings, setup Chrome sessions, and manage the bot lifecycle from the terminal dashboard.

---

### Terminal View - Active Sessions

![Terminal View](assets/terminal_view.png)

Real-time activity log showing bot status, connections, message exchanges, and system events. Useful for monitoring trade conversations and debugging.

---

### Web Dashboard - Global Telemetry

![WebUI Dashboard](assets/webui_dashboard.png)

Bird's-eye view of all sessions with global metrics:
- Total value traded
- Active sessions and holds
- AI token usage and statistics
- Recent conversations across all accounts

---

### Web Dashboard - Account Workspace

![WebUI Session](assets/webui_session.png)

Per-account detailed workspace showing:
- Active item holds with release controls
- Tracked inventory
- Sold items history
- Recent conversations with full details
- System activity log

---

### Discord Integration - Offer Alerts

![Discord Commands](assets/discord_available_commands.png)

Interactive Discord bot with slash commands:
- `/sold` — Mark items as sold
- `/remove` — Delete listings
- `/price` — Update prices
- `/listings`, `/holds`, `/stats` — View data
- `/status` — Check bot health

Automatic offer alerts with one-click actions directly in Discord.

---

## THIS IS STILL IN BETA AND SOME FUNCTIONS MAY BE BUGGED
