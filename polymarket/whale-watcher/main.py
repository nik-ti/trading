"""
Polymarket Whale Watcher — main entry point.

What it does:
  Polls the Polymarket Data API every N seconds for trades above a USD size
  threshold ("whales"). Sends a formatted Telegram alert for each new trade.
  Runs until interrupted (Ctrl-C or SIGTERM).

Dependencies:
  - polymarket SDK  (/home/nikita/py-sdk — install with: pip install /home/nikita/py-sdk)
  - python-telegram-bot >= 20
  - python-dotenv

Environment:
  Loads from /home/nikita/trading/.env — see config.py for required keys.
"""

# ──────────────────────────────────────────────────────────────────────────────
# Proxy setup — MUST happen before any network-aware import (Polymarket SDK,
# httpx, etc.). The proxy only affects this process, not the whole server.
# ──────────────────────────────────────────────────────────────────────────────
import os
from dotenv import load_dotenv

load_dotenv('/home/nikita/trading/.env')

os.environ['HTTPS_PROXY'] = os.getenv('HTTPS_PROXY', '')
os.environ['HTTP_PROXY'] = os.getenv('HTTP_PROXY', '')

# ──────────────────────────────────────────────────────────────────────────────
# Standard imports (after proxy is in place)
# ──────────────────────────────────────────────────────────────────────────────
import asyncio
import logging
import re
import signal
import sys
import time
from decimal import Decimal

from config import (
    MIN_TRADE_SIZE_USD,
    MIN_WIN_RATE,
    POLL_INTERVAL_SECONDS,
    REQUIRE_POSITIVE_PNL,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TRADES_PAGE_SIZE,
)

from polymarket import PublicClient
from telegram import Bot  # used inside _send/_send_error async context managers
from whale_analyzer import get_wallet_stats

# ──────────────────────────────────────────────────────────────────────────────
# Logging: console + file
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('whale_watcher.log'),
    ],
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Maximum number of transaction hashes to remember for deduplication.
# Older hashes beyond this limit won't appear in the recent feed anyway.
# ──────────────────────────────────────────────────────────────────────────────
MAX_SEEN_HASHES = 2000

# ──────────────────────────────────────────────────────────────────────────────
# Graceful shutdown
# ──────────────────────────────────────────────────────────────────────────────
_shutdown = False


def _handle_signal(signum, frame):
    """Set the global shutdown flag so the main loop exits cleanly."""
    global _shutdown
    log.info("Shutdown signal received — stopping after this poll.")
    _shutdown = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _short_addr(address: str | None) -> str:
    """Shorten a hex address to first-6 … last-4 for display."""
    if not address:
        return "unknown"
    return f"{address[:6]}…{address[-4:]}"


def _market_url(trade) -> str:
    """Build a Polymarket URL from the trade's event and market slugs."""
    if trade.event_slug and trade.slug:
        return f"https://polymarket.com/event/{trade.event_slug}/{trade.slug}"
    if trade.slug:
        return f"https://polymarket.com/market/{trade.slug}"
    return "https://polymarket.com"


def _format_stats(stats: dict | None) -> str:
    """
    Build the stats section of the alert from a get_wallet_stats() result.

    Shows PnL and win rate. Returns a placeholder line if stats are unavailable.
    """
    if not stats:
        return "📊 <b>Whale Stats:</b> unavailable"

    # --- PnL line ---
    pnl = stats.get('pnl')
    if pnl is not None:
        sign = "+" if pnl >= 0 else ""
        pnl_str = f"{sign}${pnl:,.0f}"
    else:
        pnl_str = "N/A"

    # --- Win rate line ---
    win_rate = stats.get('win_rate')
    wins     = stats.get('wins', 0)
    losses   = stats.get('losses', 0)
    open_m   = stats.get('open_markets', 0)

    if win_rate is not None:
        wr_str = f"{win_rate * 100:.0f}% ({wins}W · {losses}L · {open_m} open)"
    else:
        wr_str = f"N/A ({open_m} open)"

    return (
        "📊 <b>Whale Stats</b>\n"
        f"💵 <b>PnL:</b> {pnl_str}\n"
        f"🎯 <b>Win rate:</b> {wr_str}"
    )


def _passes_quality_filter(stats: dict | None) -> tuple[bool, str]:
    """
    Check whether a whale's stats meet the quality thresholds.

    Returns (True, "") if the trade should be alerted, or
    (False, reason_string) if it should be skipped.

    When stats are None or a metric is unavailable (None), that filter is
    not applied — we err on the side of alerting rather than silently dropping.
    """
    if stats is None:
        return True, ""

    # --- PnL filter ---
    pnl = stats.get('pnl')
    if REQUIRE_POSITIVE_PNL and pnl is not None and pnl < 0:
        return False, f"negative PnL (${pnl:,.0f})"

    # --- Win-rate filter ---
    win_rate = stats.get('win_rate')
    if win_rate is not None and win_rate < MIN_WIN_RATE:
        return False, f"win rate too low ({win_rate * 100:.0f}% < {MIN_WIN_RATE * 100:.0f}%)"

    return True, ""


def _format_alert(trade, stats: dict | None = None) -> str:
    """
    Build an HTML-formatted Telegram message for a whale trade.

    Shows: market title, outcome, side, USD size, price (in cents),
    wallet address, timestamp, TX hash, whale stats, and a link to the market.
    """
    # USD notional = shares × price-per-share
    usd_value = (trade.size or Decimal(0)) * (trade.price or Decimal(0))
    price_cents = float(trade.price or 0) * 100

    side_emoji = "🟢" if trade.side == "BUY" else "🔴"
    timestamp_str = (
        trade.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
        if trade.timestamp else "—"
    )

    lines = [
        "🐋 <b>WHALE ALERT</b>",
        "",
        f"📋 <b>Market:</b> {trade.title or 'Unknown Market'}",
        f"🎯 <b>Outcome:</b> {trade.outcome or 'Unknown'}",
        f"{side_emoji} <b>Side:</b> {trade.side or '—'}",
        f"💰 <b>Size:</b> ${usd_value:,.0f}",
        f"📈 <b>Price:</b> {price_cents:.1f}¢",
        f"👛 <b>Wallet:</b> <a href=\"https://polymarket.com/{trade.wallet}\">{_short_addr(str(trade.wallet) if trade.wallet else None)}</a>",
        f"⏰ <b>Time:</b> {timestamp_str}",
        f"🔗 <b>TX:</b> <code>{_short_addr(str(trade.transaction_hash) if trade.transaction_hash else None)}</code>",
        "",
        _format_stats(stats),
        "",
        f'<a href="{_market_url(trade)}">View on Polymarket</a>',
    ]
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Telegram helpers (python-telegram-bot v20+ is fully async)
# ──────────────────────────────────────────────────────────────────────────────

async def _send(text: str) -> None:
    """
    Send an HTML message to the configured Telegram chat.

    Creates a fresh Bot inside an async-with block each time so the httpx
    client is always tied to the current event loop. This is required for
    python-telegram-bot v20+ when asyncio.run() is called repeatedly.
    """
    async with Bot(token=TELEGRAM_BOT_TOKEN) as bot:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode='HTML',
            disable_web_page_preview=True,
        )


async def _send_error(error_msg: str) -> None:
    """Send an error notification; silently swallows its own failures."""
    try:
        await _send(f"⚠️ <b>Whale Watcher Error</b>\n\n<code>{error_msg[:500]}</code>")
    except Exception:
        pass  # Never let error-reporting crash the bot


def tg(text: str) -> None:
    """Synchronous wrapper: run a Telegram send in a fresh event loop."""
    asyncio.run(_send(text))


def tg_error(msg: str) -> None:
    """Synchronous error sender; safe to call in exception handlers."""
    asyncio.run(_send_error(msg))


# ──────────────────────────────────────────────────────────────────────────────
# Trade fetching
# ──────────────────────────────────────────────────────────────────────────────

_VS_PATTERN = re.compile(r'\bvs\.?\b', re.IGNORECASE)


def _is_sports_market(title: str | None) -> bool:
    """Return True if the market title looks like a sports matchup (Team A vs Team B)."""
    return bool(title and _VS_PATTERN.search(title))


def fetch_whale_trades(client: PublicClient) -> list:
    """
    Fetch the most recent page of trades with cash value >= MIN_TRADE_SIZE_USD.

    The API-side filter (filterType=CASH, filterAmount=X) does the heavy
    lifting; we also verify locally with size × price to be safe.

    Returns a list of Trade objects ordered newest-first.
    """
    paginator = client.list_trades(
        filter_type="CASH",
        filter_amount=MIN_TRADE_SIZE_USD,
        page_size=TRADES_PAGE_SIZE,
    )
    page = paginator.first_page()
    trades = list(page.items)

    # Local guard: re-check the threshold in case the API filter is approximate
    min_usd = Decimal(str(MIN_TRADE_SIZE_USD))
    return [
        t for t in trades
        if (t.size or Decimal(0)) * (t.price or Decimal(0)) >= min_usd
        and not _is_sports_market(t.title)
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────────────

def main():
    """
    Entry point. Runs the polling loop until SIGINT / SIGTERM.

    First poll: silently marks all existing whale trades as seen so we don't
    spam alerts for old trades on startup. Every subsequent poll: alerts on
    any transaction hash not seen before.
    """
    log.info("Starting Polymarket Whale Watcher")
    log.info(f"  Threshold  : ${MIN_TRADE_SIZE_USD:,.0f}")
    log.info(f"  Interval   : {POLL_INTERVAL_SECONDS}s")
    log.info(f"  Page size  : {TRADES_PAGE_SIZE}")
    log.info(f"  +PnL only  : {REQUIRE_POSITIVE_PNL}")
    log.info(f"  Min win %  : {MIN_WIN_RATE * 100:.0f}%")

    tg(
        f"🐋 <b>Whale Watcher started</b>\n"
        f"Alerting on trades ≥ <b>${MIN_TRADE_SIZE_USD:,.0f}</b>\n"
        f"Filters: positive PnL={REQUIRE_POSITIVE_PNL}, win rate ≥ {MIN_WIN_RATE * 100:.0f}%\n"
        f"Polling every <b>{POLL_INTERVAL_SECONDS}s</b>",
    )

    # Set of transaction hashes we have already alerted on
    seen_hashes: set[str] = set()
    is_first_poll = True

    with PublicClient() as client:
        while not _shutdown:
            try:
                trades = fetch_whale_trades(client)

                if is_first_poll:
                    # Seed deduplication set without sending any alerts
                    for trade in trades:
                        if trade.transaction_hash:
                            seen_hashes.add(str(trade.transaction_hash))
                    log.info(f"Startup: seeded {len(seen_hashes)} existing trades as seen")
                    is_first_poll = False

                else:
                    new_trades = [
                        t for t in trades
                        if t.transaction_hash
                        and str(t.transaction_hash) not in seen_hashes
                    ]

                    if new_trades:
                        log.info(f"Found {len(new_trades)} new whale trade(s)")

                    for trade in new_trades:
                        tx_hash = str(trade.transaction_hash)
                        seen_hashes.add(tx_hash)

                        usd_value = (trade.size or Decimal(0)) * (trade.price or Decimal(0))
                        log.info(
                            f"Alert: ${usd_value:,.0f} | {trade.side} '{trade.outcome}' @ "
                            f"{trade.price} | {trade.title} | wallet={trade.wallet}"
                        )

                        # Fetch wallet stats before sending — used both for filtering
                        # and for display in the alert.
                        wallet_str = str(trade.wallet) if trade.wallet else None
                        stats = None
                        if wallet_str:
                            try:
                                stats = get_wallet_stats(wallet_str, client)
                                log.info(
                                    f"Stats for {wallet_str[:10]}…: "
                                    f"PnL={stats.get('pnl')} "
                                    f"WR={stats.get('win_rate')}"
                                )
                            except Exception as e:
                                log.warning(f"Stats fetch failed for {wallet_str[:10]}…: {e}")

                        # Quality filter: skip negative-PnL or low-win-rate wallets
                        ok, reason = _passes_quality_filter(stats)
                        if not ok:
                            log.info(f"Skipping trade — {reason} | wallet={wallet_str}")
                            continue

                        try:
                            tg(_format_alert(trade, stats=stats))
                        except Exception as e:
                            log.error(f"Failed to send Telegram alert: {e}")
                            tg_error(f"Alert send failed: {e}")

                # Prevent unbounded memory growth for long-running deployments
                if len(seen_hashes) > MAX_SEEN_HASHES:
                    seen_hashes.clear()
                    is_first_poll = True  # Re-seed on next poll to avoid false alerts
                    log.info(f"Seen-hash set cleared (exceeded {MAX_SEEN_HASHES}); re-seeding")

            except Exception as e:
                log.error(f"Poll error: {e}", exc_info=True)
                tg_error(f"Poll error in whale_watcher/main.py:\n{e}")

            # Interruptible sleep: check for shutdown every second
            for _ in range(POLL_INTERVAL_SECONDS):
                if _shutdown:
                    break
                time.sleep(1)

    log.info("Whale Watcher stopped.")
    try:
        tg("🛑 <b>Whale Watcher stopped</b>")
    except Exception:
        pass


if __name__ == '__main__':
    main()
