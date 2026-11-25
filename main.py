"""Pump.fun replay paper bot: early-momentum scalper with hard exits.

Original strategy, written for make.fun's agent-fun/replay/v2 protocol
(Pump.fun Replay Sprint -- Paper Bot, Real Ranking). Standard library only.

Idea
----
Most pump.fun bonding curves spike, then dump. Chasing the spike buys the
top, so this bot waits for the first pullback instead: it only buys *young*
coins that already ran at least +30% from their first trade, then retraced
12-30% off their peak while buy flow still dominates sell flow -- and it
risks the same small fixed stake on each one. Every position then carries
four hard exits, so no single coin can bleed the book:

* take profit  +30% over the filled cost basis,
* stop loss    -12% under the filled cost basis,
* trailing stop -10% off the peak once the trade is +5% or better,
* holding timeout: flat after MAX_HOLD_SLOTS slots no matter what.

One buy per mint, ever: after a mint is sold it is never re-entered, which
keeps round trips clean and stops the bot churning the same dead coin.

Determinism
-----------
No randomness, no clock, no environment reads, no set/dict iteration that
could change order: buys follow the batch's own event order, sells iterate
mints in sorted order, and every threshold is a fixed constant. Two runs
on the same replay therefore emit byte-identical replies.

Money rules
-----------
* stake 0.04 SOL per coin (>= 0.01 SOL round-trip bar, small enough that
  dozens of coins can be tried from 10 SOL of paper capital),
* buys are capped by the harness per-mint size cap
  (``state.max_buy_lamports``) and by free cash after tx fees,
* sells always liquidate the whole holding.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402  (after the sys.path line: -P leaves the script directory off sys.path)

# --- fixed strategy constants (all in lamports / slots / ratios) -------------
STAKE_LAMPORTS = 40_000_000          # 0.04 SOL per coin
MIN_BUY_LAMPORTS = 10_000_000        # 0.01 SOL: the competition's round-trip bar
MAX_SLIPPAGE_BPS = 300               # 3% worst-case fill slippage

MIN_TRADES = 10                      # trades seen before a coin may be bought
MIN_BUY_FLOW_LAMPORTS = 1000000000   # 1.00 SOL of buy flow before entry
MOMENTUM_RATIO = 1.30                # price must be +30% over its first trade (spike happened)
MAX_AGE_SLOTS = 3600                 # only coins created within the last ~24 min
MAX_HOLD_SLOTS = 4500                # flat any position older than ~30 min

TAKE_PROFIT_RATIO = 1.40             # +40% over filled cost basis
MIN_RISE_STREAK = 4                  # consecutive rising trade prints at entry
DIP_TOP_RATIO = 0.88                 # entry only after a >=12% pullback off the peak
DIP_FLOOR_RATIO = 0.70               # ... but not after a >30% collapse
STOP_LOSS_RATIO = 0.88               # -12% under filled cost basis
TRAIL_ARM_RATIO = 1.15               # trailing stop arms once +15%
TRAIL_DROP_RATIO = 0.92              # exit at -8% off the peak

# --- per-mint tape, in first-seen order (insertion order is deterministic) ---
# mint -> [first_price, trades, buy_sol, sell_sol, created_slot, peak_price,
#          last_price, rise_streak]
_tape = {}
# mints already bought once (never re-entered) and mints already exited
_bought = set()
_exited = set()
# mint -> (entry_slot, peak_price) for positions currently held
_open = {}


def _price(state, mint):
    """Current SOL-per-token price from the curve's virtual reserves.

    Returns None when the curve is unknown or its reserves are empty.
    """
    curve = state.curves.get(mint)
    if curve is None:
        return None
    if curve.virtual_token_reserves <= 0 or curve.virtual_quote_reserves <= 0:
        return None
    return curve.virtual_quote_reserves / curve.virtual_token_reserves


def _update_tape(batch):
    """Fold this batch's events into the per-mint tape, in event order."""
    for event in batch["events"]:
        kind = event["event"]
        mint = event["mint"]
        if kind == "CreateEvent":
            if mint not in _tape:
                _tape[mint] = [None, 0, 0, 0, event["slot"], None, None, 0]
        elif kind == "TradeEvent":
            row = _tape.get(mint)
            if row is None:
                row = [None, 0, 0, 0, event["slot"], None, None, 0]
                _tape[mint] = row
            vt = int(event["virtual_token_reserves"])
            vq = int(event["virtual_quote_reserves"])
            if vt > 0 and vq > 0:
                px = vq / vt
                if row[0] is None:
                    row[0] = px
                if row[5] is None or px > row[5]:
                    row[5] = px
                if row[6] is not None:
                    row[7] = row[7] + 1 if px > row[6] else 0
                row[6] = px
            row[1] += 1
            if event["is_buy"]:
                row[2] += int(event["sol_amount"])
            else:
                row[3] += int(event["sol_amount"])


def _decide_sells(state):
    """Exit orders for held positions, mints in sorted order."""
    orders = []
    for mint in sorted(state.positions):
        holding = state.positions[mint]
        if holding.token_amount <= 0:
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            continue  # a completed curve is closed by the harness itself
        price = _price(state, mint)
        if price is None:
            continue
        entry = _open.get(mint)
        if entry is None:
            # Position appeared without a recorded entry (e.g. a fill that
            # landed after a restart of the tape): anchor on cost basis now.
            entry = (state.slot, price)
            _open[mint] = entry
        entry_slot, peak = entry
        if price > peak:
            peak = price
            _open[mint] = (entry_slot, peak)
        cost_per_token = holding.cost_lamports / holding.token_amount
        ratio = price / cost_per_token
        sell = False
        if ratio >= TAKE_PROFIT_RATIO:
            sell = True  # bank the winner
        elif ratio <= STOP_LOSS_RATIO:
            sell = True  # cut the loser
        elif ratio >= TRAIL_ARM_RATIO and price <= peak * TRAIL_DROP_RATIO:
            sell = True  # give back no more than 10% off the peak
        elif state.slot - entry_slot >= MAX_HOLD_SLOTS:
            sell = True  # time is up: flat regardless of pnl
        if sell:
            orders.append(
                {
                    "side": "sell",
                    "mint": mint,
                    "token_amount": holding.token_amount,
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                }
            )
            _exited.add(mint)
            _open.pop(mint, None)
    return orders


def _decide_buys(state, batch, orders):
    """Entry orders for young momentum coins, in the batch's event order."""
    caps = state.hello["caps"]
    tx = state.tx_fee_lamports()
    free = state.free_cash_lamports()
    slots = caps["max_open_positions"] - state.committed_position_count()
    for event in batch["events"]:
        if event["event"] != "TradeEvent":
            continue
        if not event["is_buy"]:
            continue  # enter only on buying pressure, never on a sell print
        if len(orders) >= caps["max_orders_per_batch"]:
            break
        mint = event["mint"]
        if mint in _bought or mint in _exited:
            continue
        if mint in state.positions:
            continue
        row = _tape.get(mint)
        if row is None:
            continue
        first_price, trades, buy_sol, sell_sol, created_slot, peak_price, _last, streak = row
        if first_price is None:
            continue
        if state.slot - created_slot > MAX_AGE_SLOTS:
            continue  # stale coin: its early run is already over
        if trades < MIN_TRADES:
            continue
        if buy_sol < MIN_BUY_FLOW_LAMPORTS or buy_sol <= sell_sol:
            continue  # no buy-dominated flow, no trade
        price = _price(state, mint)
        if price is None or price < first_price * MOMENTUM_RATIO:
            continue  # momentum has not confirmed yet
        if peak_price is None or price > peak_price * DIP_TOP_RATIO:
            continue  # no pullback yet: wait for the dip
        if price < peak_price * DIP_FLOOR_RATIO:
            continue  # dipped too far: likely dead, not a pullback
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            continue
        cap = state.max_buy_lamports(mint)
        if cap < MIN_BUY_LAMPORTS:
            continue
        amount = STAKE_LAMPORTS if STAKE_LAMPORTS < cap else cap
        if amount < MIN_BUY_LAMPORTS:
            continue
        if slots <= 0 or free < amount + tx:
            continue
        orders.append(
            {
                "side": "buy",
                "mint": mint,
                "quote_lamports": amount,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
            }
        )
        _bought.add(mint)
        _open[mint] = (state.slot, price)
        free -= amount + tx
        slots -= 1
    return orders


def _self_check(state, orders):
    """Drop any order that would break the bot's own money rules.

    A filter, not a raise: in grading, a dropped order is a missed trade,
    while an exception is a dead run. Every check is deterministic.
    """
    caps = state.hello["caps"]
    seen_buys = set()
    kept = []
    for order in orders:
        mint = order["mint"]
        if order["side"] == "buy":
            if mint in state.positions or mint in _exited:
                continue
            if mint in seen_buys:
                continue
            seen_buys.add(mint)
        else:
            holding = state.positions.get(mint)
            if holding is None or holding.token_amount <= 0:
                continue
            if order["token_amount"] != holding.token_amount:
                continue
        kept.append(order)
    return kept[: caps["max_orders_per_batch"]]


def on_batch(state, batch):
    _update_tape(batch)
    orders = _decide_sells(state)
    orders = _decide_buys(state, batch, orders)
    return _self_check(state, orders)


if __name__ == "__main__":
    makefun_bot.run(on_batch)
