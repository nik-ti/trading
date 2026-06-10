# Agent Behavior — Algo Trading Workspace

This file defines how the coding agent should think and act inside this workspace. It supplements CLAUDE.md with step-by-step behavior guidelines. Any edits that are made to this file, should be cloned to CLAUDE.md

---

## Before Writing Any Code

1. **Search the web** for existing tools, libraries, or repos that solve the problem
2. **Check for a CLI or SDK** before building a custom integration
3. **Verify the library is still maintained** — check last commit date, open issues, stars
4. **Pick the simplest viable option**, not the most powerful one
5. Only then begin writing code

---

## When Writing Code

### Comments Are Mandatory
- Every function must have a docstring explaining what it does and what it returns
- Every logical section of a file must have a comment block explaining its purpose
- If a line of code isn't obvious, comment it
- Write for a beginner reader — assume nothing is self-evident

Example of the expected comment style:
```python
# --- Fetch OHLCV data from the exchange ---
# Pulls historical price candles for a given trading pair.
def fetch_candles(symbol, timeframe, limit):
    ...
```

### File Structure
Each file should start with a short comment block explaining:
- What this file does
- Where it fits in the overall system
- Any dependencies or env variables it needs

---

## Handling Errors

- All errors must be caught and sent to Telegram using the bot module
- Never let a script silently fail
- Log errors to a file as well as sending the Telegram notification
- Format error messages clearly: include what failed, why (if known), and which file/function

---

## Using the Proxy

- Check if the target exchange or API is likely to block a German VPS IP
- If unsure, default to routing through the proxy, but make sure not to use the proxy for the entire server, as different bots may need different IPs
- Proxy credentials live in `.env` — always load them from there

---

## Telegram Notifications

Use the Telegram bot for:
- Trade placed / trade failed
- Strategy triggered (entry/exit signals)
- Errors and exceptions
- Script started / script stopped (for long-running bots)

Always use `python-telegram-bot`. No exceptions.

---

## When the User Asks for Something Suboptimal

1. Flag the issue clearly: *"There's a simpler/better way to do this."*
2. Explain why the alternative is better (briefly)
3. Ask if they want to proceed the original way or switch
4. Default to the better approach unless they explicitly insist otherwise

Never silently implement a bad approach.

---

## Naming Checklist

Before creating any file or folder, ask:
- [ ] Can someone unfamiliar with the project understand what this is from the name alone?
- [ ] Does the name avoid jargon?
- [ ] Is it as short as it can be while still being clear?

---

## Backtesting Checklist

Before marking a crypto/forex strategy as ready:
- [ ] Backtest is written and runs without errors
- [ ] Results are saved to a readable output (CSV or printed summary)
- [ ] Edge case periods are tested (high volatility, low liquidity)
- [ ] Strategy parameters are documented

For Polymarket strategies:
- [ ] Thesis is written in plain language
- [ ] Expected edge is explained and estimated
- [ ] Resolution criteria and timing are accounted for

---

## General Reminders

- The user is learning — explanations are always welcome, never condescending
- Simple > clever
- If something feels overengineered, it probably is
- The goal is strategies that work, not code that impresses