"""
Whale Analyzer — computes PnL and win rate for a Polymarket wallet.

Where it fits:
  Called from main.py when a new whale trade is detected, before the
  Telegram alert is sent. Results are embedded directly in the alert.

How it works:
  PnL    — pulled from the trader leaderboard endpoint (pre-calculated, lifetime).
  Win rate — derived from the wallet's closed positions. A position is a WIN
             when realized_pnl > 0 (made money), LOSS otherwise. This captures
             both early exits and resolution outcomes in one clean signal.

SDK usage:
  Both calls go through the same PublicClient that main.py already keeps open,
  so proxy config and connection pooling are shared for free.

Caching:
  Results are cached per wallet for CACHE_TTL_SECONDS. A whale that triggers
  multiple alerts in one session won't cause repeated API calls.
"""

import logging
import time
from decimal import Decimal

from polymarket import PublicClient

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Tuning knobs
# ──────────────────────────────────────────────────────────────────────────────

# How many closed positions to score for win rate.
# Fetched in one page — high enough for a reliable sample without being slow.
CLOSED_POSITIONS_SAMPLE = 100

# How long to keep a wallet's stats in memory before re-fetching.
CACHE_TTL_SECONDS = 3600

# ──────────────────────────────────────────────────────────────────────────────
# In-process cache: wallet_address → stats dict
# ──────────────────────────────────────────────────────────────────────────────
_cache: dict[str, dict] = {}


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def get_wallet_stats(wallet: str, client: PublicClient) -> dict:
    """
    Return PnL and win-rate stats for a wallet, using cache when fresh.

    Args:
        wallet: Proxy wallet address (as returned in trade.wallet).
        client: The open PublicClient from main.py's polling loop.

    Returns a dict with:
      pnl      — float, total lifetime profit in USD (None if unavailable)
      win_rate — float 0–1, fraction of sampled closed positions with pnl > 0
                 (None if the wallet has no closed positions)
      wins     — int, number of profitable closed positions in the sample
      losses   — int, number of unprofitable closed positions in the sample
    """
    now = time.time()
    cached = _cache.get(wallet)
    if cached and now - cached['_ts'] < CACHE_TTL_SECONDS:
        return cached

    stats = {
        'pnl':      _fetch_pnl(wallet, client),
        **_fetch_win_rate(wallet, client),
        '_ts':      now,
    }
    _cache[wallet] = stats
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# PnL fetch
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_pnl(wallet: str, client: PublicClient) -> float | None:
    """
    Fetch lifetime PnL via the trader leaderboard endpoint.

    Passes the wallet address as a user filter so the API returns just that
    wallet's entry. The TraderLeaderboardEntry.pnl field is pre-calculated
    by Polymarket and covers all-time realized profit.

    Returns a float in USD, or None on any failure.
    """
    try:
        page = client.list_trader_leaderboard(user=wallet, page_size=1).first_page()
        if page.items:
            pnl = page.items[0].pnl
            return float(pnl) if pnl is not None else None
    except Exception as e:
        log.warning(f"PnL fetch failed for {wallet[:10]}…: {e}")
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Win-rate computation
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_win_rate(wallet: str, client: PublicClient) -> dict:
    """
    Compute win rate from the wallet's most recent closed positions.

    A closed position is a WIN when realized_pnl > 0 (the trader made money),
    LOSS otherwise. This works for both early exits (sold before resolution)
    and fully resolved positions, making it a clean economic signal.

    Samples the most recent CLOSED_POSITIONS_SAMPLE positions to keep the
    call fast. Returns dict with keys: win_rate, wins, losses.
    """
    empty = {'win_rate': None, 'wins': 0, 'losses': 0}

    try:
        page = client.list_closed_positions(
            user=wallet,
            page_size=CLOSED_POSITIONS_SAMPLE,
        ).first_page()
    except Exception as e:
        log.warning(f"Closed positions fetch failed for {wallet[:10]}…: {e}")
        return empty

    wins = 0
    losses = 0

    for pos in page.items:
        # Skip positions where realized_pnl wasn't reported
        if pos.realized_pnl is None:
            continue
        if pos.realized_pnl > Decimal('0'):
            wins += 1
        else:
            losses += 1

    total = wins + losses
    if total == 0:
        return empty

    return {
        'win_rate': wins / total,
        'wins':     wins,
        'losses':   losses,
    }
