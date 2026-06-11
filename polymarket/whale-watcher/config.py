"""
Configuration for the Polymarket Whale Watcher.

Reads all settings from /home/nikita/trading/.env (the shared trading environment).

Required keys in .env:
  MAIN_BOT_TOKEN     — Telegram bot token
  PERSONAL_CHAT_ID   — Telegram chat ID to send alerts to
  HTTPS_PROXY        — Proxy URL (required; Polymarket blocks direct VPS traffic)
  HTTP_PROXY         — Same proxy URL for HTTP

Optional keys (with defaults):
  WHALE_MIN_SIZE_USD   — Minimum trade USD value to alert on (default: 30000)
  WHALE_POLL_INTERVAL  — Seconds between polls (default: 20)
  WHALE_PAGE_SIZE      — Trades fetched per poll (default: 50)
"""

import os
from dotenv import load_dotenv

# Load from the shared trading env file
load_dotenv('/home/nikita/trading/.env')

# --- Proxy (required for Polymarket; see polymarket skill docs) ---
HTTPS_PROXY: str = os.getenv('HTTPS_PROXY', '')
HTTP_PROXY: str = os.getenv('HTTP_PROXY', '')

# --- Telegram ---
TELEGRAM_BOT_TOKEN: str = os.getenv('MAIN_BOT_TOKEN', '')
TELEGRAM_CHAT_ID: str = os.getenv('PERSONAL_CHAT_ID', '')

# --- Bot behavior ---
# Minimum USDC value (size × price) required to trigger a whale alert
MIN_TRADE_SIZE_USD: float = float(os.getenv('WHALE_MIN_SIZE_USD', '30000'))

# Seconds to wait between each poll of the trades endpoint
POLL_INTERVAL_SECONDS: int = int(os.getenv('WHALE_POLL_INTERVAL', '20'))

# --- Quality filters ---
# Skip alert if the whale's lifetime PnL is negative (they're a net loser overall)
REQUIRE_POSITIVE_PNL: bool = os.getenv('WHALE_REQUIRE_POSITIVE_PNL', 'true').lower() == 'true'

# Skip alert if the whale's win rate (on sampled closed positions) is below this threshold.
# Value is a fraction: 0.60 = 60%.
MIN_WIN_RATE: float = float(os.getenv('WHALE_MIN_WIN_RATE', '0.60'))

# Number of recent trades to fetch per poll.
# Keep this high enough that no whale trade slips between two polls.
TRADES_PAGE_SIZE: int = int(os.getenv('WHALE_PAGE_SIZE', '50'))
