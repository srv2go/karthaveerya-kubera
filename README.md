# AlphaTrader HITL

An AI trading **signal** assistant with a human always in the loop. It watches a small stock/
crypto watchlist, has an LLM propose buy/sell/hold setups, risk-sizes and filters them with
ordinary deterministic code, and posts the survivors to Telegram as signal cards. You place the
trade yourself on eToro (or wherever you actually trade) and confirm back to the bot. Nothing in
this codebase places, modifies, or cancels a real order.

## How it works

1. **07:30 UTC daily scan** (or on-demand `/scan`): fetch a quote + 60 daily candles per watchlist
   symbol, compute RSI(14), EMA(20/50), ATR(14), MACD, and swing levels.
2. The LLM analyst proposes buy/sell/hold per symbol. The `RiskEngine` (plain Python, no LLM
   involved) validates the proposal, enforces the spread floor, and sizes the position in GBP.
3. The bot posts up to the top 3 signal cards (ranked by risk/reward, then confidence), each with
   inline buttons: **[I placed it] [Skip]**.
4. You open eToro, manually enter the trade using the card's numbers, and tap **I placed it**
   (or reply with the actual fill price via `/filled <id> <price>`).
5. When the position closes (stop/target hit or you close it manually), report it with
   `/closed <id> <price>` (or the `/closed <id> sl` / `/closed <id> tp` shortcuts). The ledger
   books realized P&L in GBP.
6. A 15-minute job marks open positions to market and a daily/weekly circuit breaker halts new
   signals if loss limits or the cash-preservation floor are hit — until the next reset period or
   until you `/resume` after acknowledging.

## Setup

1. Python 3.11+, then:
   ```
   python -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```
2. Copy `.env.example` to `.env` and fill in your own credentials:
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — from `@BotFather` and your own chat.
   - `LLM_PROVIDER` (`anthropic` or `openai_compat`) and `LLM_API_KEY`.
   - `ALPACA_KEY_ID` / `ALPACA_SECRET` — a **data-only** Alpaca account (paper or live market
     data plan); this codebase never calls Alpaca's trading/order endpoints.
   - Crypto quotes/candles come from public ccxt endpoints and need no key.
3. Review `config/risk.yaml` (bankroll, max risk per trade, breaker thresholds) and
   `config/symbols.yaml` (your watchlist).
4. Validate everything without starting the bot or scheduler:
   ```
   python -m alphatrader --check
   ```
5. Run it:
   ```
   python -m alphatrader
   ```

## Daily workflow / Telegram commands

- `/ping` — liveness check.
- `/portfolio` — open positions and current balance.
- `/pnl` — realized/unrealized P&L summary.
- `/scan` — run a scan on demand (also runs automatically at 07:30 UTC).
- `/signals` — list pending signal cards.
- `/filled <id> [price]` — confirm you placed a trade (same as tapping **I placed it**).
- `/skipped <id>` — mark a signal as intentionally skipped.
- `/closed <id> <price|sl|tp>` — book the close and realized P&L.
- `/halt` / `/resume` — manually stop or resume signal issuance.
- `/report` — the same summary sent automatically at 21:00 UTC daily and Sunday 21:05 UTC
  (weekly skipped-vs-taken review).

Only the chat ID configured in `TELEGRAM_CHAT_ID` is served; every other chat is ignored.

## Backtesting

`alphatrader.backtest` replays a rule-based proxy strategy (EMA20/50 cross + RSI filter) through
the same `RiskEngine` used live, over historical daily candles, to sanity-check the risk plumbing
and calibrate expectations — see `tests/test_backtest.py` for example fixtures and usage.

## Optional dashboard

A read-only Streamlit dashboard (`src/alphatrader/ui/dashboard.py`) shows pending signal cards,
open positions with live unrealized P&L, the realized-equity curve, recent signal history, and the
weekly skipped-vs-taken review — all read directly from the same SQLite ledger. It has no write
path: it never fills, skips, closes, halts, or resumes anything; every confirmation still happens
in Telegram. Install and run it separately from the bot:
```
pip install -e ".[dashboard]"
streamlit run src/alphatrader/ui/dashboard.py
```

## Running unattended (Raspberry Pi / VPS)

A minimal systemd unit is provided at `deploy/alphatrader.service`. Adjust the paths and user,
then:
```
sudo cp deploy/alphatrader.service /etc/systemd/system/alphatrader.service
sudo systemctl daemon-reload
sudo systemctl enable --now alphatrader
```

## Warnings

- This software generates **signals only** and never places trades. All trading decisions and
  actions are made by you, manually, in your own account. Nothing here is financial advice.
- LLMs have **no predictive edge** over markets. They summarize technical conditions; the risk
  math is done by ordinary code. Expect losing signals and losing weeks.
- £150/week on £1,000 (~15% weekly) is **not a realistic sustained return**. It is tracked as an
  experimental metric only. The risk rules exist so losing streaks are survivable.
- Strongly recommended: run the full workflow against **eToro's Virtual Portfolio (demo)** for at
  least one month before considering real funds, and never trade money you cannot afford to lose.
- The ledger and circuit breakers are only accurate if you confirm fills and closes promptly.

## Tests

```
ruff check .
pytest -q
```
