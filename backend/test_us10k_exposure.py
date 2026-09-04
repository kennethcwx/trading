"""Checks that the us10k track cannot deploy capital it does not have.

The bug: sizing capped a single position at MAX_POSITION_PCT (10%) of equity, but
nothing subtracted an open position from the money available for the next one.
Equity came from base + realized, which does not move when a position opens, so
every candidate in a pass was sized against the full account. The track's ceiling
was therefore 10% x len(watchlist):

  * at 8 symbols  -> 80%, under 100%, so the flaw could never be reached
  * at 25 symbols -> 250% of notional, reached on the first busy day

The watchlist was widened to 25 on 2026-09-04, which is what made it reachable.

The fix mirrors backtest.py's portfolio sim -- mark-to-market equity, a 10% cash
floor, and funding in RS-rank order so the floor does not let arrival order decide
which signals get taken. Without matching the sim, no CAGR or drawdown the track
reports can be compared with the research it exists to validate.

    python backend/test_us10k_exposure.py

Negative control: size against `_us10k_equity_sgd` instead of the running cash (the
old behaviour) and [2] must fail; drop the `sorted(...)` in _fund_us10k_entries and
[3] must fail.
"""
import sys
import types

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

import config  # noqa: E402

# Pin the account so the arithmetic below is readable: S$10,000 at 0.75 is
# US$7,500 of equity, a 10% cash floor is US$750, and a 10% position cap is US$750.
config.US10K_PORTFOLIO_SGD = 10000
config.US10K_MIN_CASH_PCT = 0.10
SGD_TO_USD = 0.75
BASE_USD = 10000 * SGD_TO_USD          # 7500
POS_CAP_USD = BASE_USD * 0.10          # 750

import db  # noqa: E402
import main  # noqa: E402

main.US10K_PORTFOLIO_SGD = 10000
main.US10K_MIN_CASH_PCT = 0.10

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed.append(label)
        print(f"  FAIL  {label}")


# --- an in-memory paper_trades ------------------------------------------------------

OPENED = []
STATE = {"open": []}


def _open_paper_trade(track, symbol, variant, shares, date, price, stop, target, reason):
    OPENED.append({"symbol": symbol, "shares": shares, "price": price,
                   "value": shares * price})
    STATE["open"].append({"symbol": symbol, "shares": shares, "entry_price": price})
    return len(OPENED)


def reset():
    OPENED.clear()
    STATE["open"] = []


db.open_paper_trade = _open_paper_trade
db.get_open_paper_trades = lambda track: list(STATE["open"])
# No realized P&L: equity is the plain base, so every number below is checkable by
# hand. `fetch` is what _us10k_equity_sgd reads.
db.fetch = lambda sql, params=(): []


def candidate(symbol, rank, price=100.0, stop=90.0):
    """One BUY the pass decided on, in the shape _fund_us10k_entries consumes."""
    d = {"symbol": symbol,
         "analysis": {"price": price, "stop_loss": stop, "profit_target": price * 1.2}}
    return (rank, symbol, d, {"reasons": ["test"]})


def stock_data_for(cands, extra=()):
    rows = [c[2] for c in cands]
    rows.extend(extra)
    return rows


def fund(cands, extra=()):
    reset_opened = list(OPENED)
    main._fund_us10k_entries(list(cands), stock_data_for(cands, extra),
                             SGD_TO_USD, 1.0, "2026-09-04")
    return [o for o in OPENED if o not in reset_opened]


print("\n[1] a single entry is unchanged by any of this")
reset()
opened = fund([candidate("AAPL", 90.0)])
check(f"it opens ({len(opened)})", len(opened) == 1)
# 1% of US$7,500 risked over a $10 stop distance = 7.5 shares = $750, which is
# exactly the 10% cap. Sizing is untouched; only what follows it is new.
check(f"sized to the 10% cap (${opened[0]['value']:.0f} of ${POS_CAP_USD:.0f})",
      abs(opened[0]["value"] - POS_CAP_USD) < 1)


print("\n[2] the account cannot be deployed past its cash floor")
# The bug, stated as a test. Twenty-five candidates at a 10% cap each would have
# opened 25 positions worth 250% of the account.
reset()
cands = [candidate(f"S{i:02d}", 100.0 - i) for i in range(25)]
opened = fund(cands)
deployed = sum(o["value"] for o in opened)
check(f"it does not open all 25 ({len(opened)})", len(opened) < 25)
check(f"deployed stays within equity (${deployed:.0f} of ${BASE_USD:.0f})",
      deployed <= BASE_USD)
check(f"and leaves the 10% floor in cash (${BASE_USD - deployed:.0f} >= "
      f"${BASE_USD * 0.10:.0f})",
      BASE_USD - deployed >= BASE_USD * 0.10 - 1)
# 9 positions of $750 = $6,750, leaving $750 = exactly the floor. The tenth cannot
# be funded, so this is the arithmetic the fix has to produce.
check(f"which is nine positions here ({len(opened)})", len(opened) == 9)


print("\n[3] when the floor binds, the best-ranked signals are the ones funded")
# Arrival order must not decide this. The batch is built worst-first on purpose.
reset()
cands = [candidate(f"R{i:02d}", float(i)) for i in range(25)]   # R24 is best
opened = fund(cands)
funded = {o["symbol"] for o in opened}
best = {f"R{i:02d}" for i in range(24, 24 - len(opened), -1)}
check(f"the top {len(opened)} by RS rank got the capital ({sorted(funded)})",
      funded == best)
check("and the worst-ranked did not", "R00" not in funded)


print("\n[4] open positions are counted, so a later pass cannot re-deploy the same cash")
# The heart of the bug: equity is base + realized and does not move when a position
# opens, so a second pass used to see a full account again.
reset()
STATE["open"] = [{"symbol": "HELD", "shares": 60.0, "entry_price": 100.0}]  # $6,000
held_row = {"symbol": "HELD", "analysis": {"price": 100.0, "stop_loss": 90.0}}
opened = fund([candidate("NEW", 50.0)], extra=(held_row,))
deployed_new = sum(o["value"] for o in opened)
# Equity is $6,000 held + $1,500 cash = $7,500. The floor is $750, so at most $750
# is spendable — and the position cap is also $750.
check(f"the new entry is limited by the cash left (${deployed_new:.0f} <= $750)",
      deployed_new <= 751)
check(f"6000 held + new stays inside equity (${6000 + deployed_new:.0f} <= "
      f"${BASE_USD:.0f})", 6000 + deployed_new <= BASE_USD)

reset()
STATE["open"] = [{"symbol": "HELD", "shares": 70.0, "entry_price": 100.0}]  # $7,000
opened = fund([candidate("NEW", 50.0)],
              extra=({"symbol": "HELD", "analysis": {"price": 100.0, "stop_loss": 90.0}},))
check(f"with $7,000 held there is nothing left to spend ({len(opened)})", not opened)


print("\n[5] a position already held is never opened twice")
reset()
STATE["open"] = [{"symbol": "AAPL", "shares": 1.0, "entry_price": 100.0}]
opened = fund([candidate("AAPL", 90.0)],
              extra=({"symbol": "AAPL", "analysis": {"price": 100.0, "stop_loss": 90.0}},))
check(f"the held symbol is skipped ({len(opened)})", not opened)


print("\n[6] mark-to-market, not cost, decides how much room there is")
# A position that has doubled raises equity and so raises the money available. The
# sim marks to market every day; costing at entry would understate the account.
reset()
STATE["open"] = [{"symbol": "WIN", "shares": 30.0, "entry_price": 100.0}]  # cost $3,000
win_row = {"symbol": "WIN", "analysis": {"price": 200.0, "stop_loss": 180.0}}  # now $6,000
opened = fund([candidate("NEW", 50.0)], extra=(win_row,))
# Cash = 7500 - 3000 = 4500; equity = 4500 + 6000 = 10500; cap = 10% = 1050.
check(f"the winner's gain raises the cap (${opened[0]['value']:.0f} > ${POS_CAP_USD:.0f})",
      opened and opened[0]["value"] > POS_CAP_USD)


print("\n[7] nothing is opened when the numbers cannot be trusted")
reset()
db.get_open_paper_trades = lambda track: (_ for _ in ()).throw(RuntimeError("db down"))
opened = fund([candidate("AAPL", 90.0)])
check(f"a failed read funds nothing rather than guessing ({len(opened)})", not opened)
db.get_open_paper_trades = lambda track: list(STATE["open"])

reset()
opened = fund([])
check("an empty batch is a no-op", not opened)


print("\n" + "=" * 60)
print(f"{passed} passed, {len(failed)} failed")
if failed:
    print("\n".join(f"  - {f}" for f in failed))
sys.exit(1 if failed else 0)
