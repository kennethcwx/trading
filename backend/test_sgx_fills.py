"""Checks that /api/sgx-fills never again reports the window bug as the user's
missed replies.

That is not a hypothetical regression. On 2026-08-06 this endpoint reported
"the signal path works; the gap is in reporting fills" over 25 alerts that had
every one of them fired past the SGX close and could not have been filled — a
verdict that stood for five days and was retracted only when the window bug was
found. The fixture below is his real alert history, so the case that produced
the wrong answer is the case under test.

    python backend/test_sgx_fills.py
"""
import asyncio
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

import db  # noqa: E402
import main  # noqa: E402

SGT = ZoneInfo("Asia/Singapore")

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


def alert(id_, symbol, ts, fill_ts=None, fill_price=None, signal_price=10.0):
    return {
        "id": id_, "symbol": symbol, "side": "BUY",
        "signal_price": signal_price, "signal_qty": 100.0,
        "stop_loss": None, "profit_target": None,
        "signal_ts": ts, "fill_ts": fill_ts, "fill_price": fill_price,
        "fill_qty": None,
        "slippage_pct": None if fill_price is None
        else round((fill_price - signal_price) / signal_price * 100, 3),
        "created_at": ts,
    }


def run(rows):
    """Call the endpoint with `rows` standing in for the table."""
    original = db.get_all_sgx_signals
    db.get_all_sgx_signals = lambda: rows
    try:
        return asyncio.run(main.get_sgx_fills())
    finally:
        db.get_all_sgx_signals = original


now = datetime.now(SGT)
recent = (now - timedelta(hours=3)).isoformat(timespec="seconds")
yesterday = (now - timedelta(days=2)).isoformat(timespec="seconds")

# His real history, abridged to its shape: 25 past-close alerts before the fix,
# then the four that actually landed inside the 16:30–17:00 window.
PRE_FIX = [alert(i, "V03", f"2026-07-{16 + i % 10:02d}T17:0{i % 10}:10+08:00")
           for i in range(1, 26)]
POST_FIX = [
    alert(26, "C09", "2026-08-13T16:40:46+08:00"),
    alert(27, "F34", "2026-08-13T16:40:54+08:00"),
    alert(28, "S63", "2026-08-14T16:38:19+08:00"),
    alert(29, "BUOU", "2026-08-14T16:53:42+08:00"),
]

print("\n[1] the 2026-08-06 case — 25 past-close alerts, nothing else")
r = run(PRE_FIX)
check("total still counts every alert issued", r["total"] == 25)
check("none are counted as fillable", r["n_fillable"] == 0)
check("all 25 are held out as unfillable", r["n_unfillable"] == 25)
check("nothing is reported as a missed reply", r["n_missed"] == 0)
check("n_pending is 0, not 25", r["n_pending"] == 0)
check("the diagnosis does NOT blame fill reporting",
      "gap is in reporting fills" not in r["diagnosis"])
check("the diagnosis says nothing was ever fillable",
      "No alert has ever been fillable" in r["diagnosis"])
check("the excluded alerts are still visible", len(r["unfillable"]) == 25)

print("\n[2] his real state today — 25 unfillable + 4 fillable, none filled")
r = run(PRE_FIX + POST_FIX)
check("total is 29", r["total"] == 29)
check("only 4 count as fillable", r["n_fillable"] == 4)
check("25 held out", r["n_unfillable"] == 25)
check("the 4 are the ones awaiting /fill", r["n_missed"] == 4)
check("diagnosis counts 4, not 29", r["diagnosis"].startswith("4 fillable alert"))
check("diagnosis names the exclusion", "window bug" in r["diagnosis"])
check("pending holds only the fillable ones",
      {p["symbol"] for p in r["pending"]} == {"C09", "F34", "S63", "BUOU"})

print("\n[3] a fresh alert is not yet a missed reply")
r = run(PRE_FIX + [alert(30, "D05", recent)])
check("fresh, not missed", r["n_fresh"] == 1 and r["n_missed"] == 0)
check("diagnosis says nothing is wrong yet", "Nothing is wrong yet" in r["diagnosis"])
check("still mentions the exclusion", "window bug" in r["diagnosis"])

print("\n[4] a pre-fix alert he DID fill still counts — the data is real")
filled_pre_fix = alert(1, "V03", "2026-07-16T17:02:10+08:00",
                       fill_ts="2026-07-16T17:30:00+08:00", fill_price=10.05)
r = run([filled_pre_fix] + PRE_FIX[1:])
check("the filled one is reported, not excluded", r["n_reported"] == 1)
check("it is not in unfillable", r["n_unfillable"] == 24)
check("its slippage reaches the stats", r["stats"] is not None and r["stats"]["n"] == 1)

print("\n[5] the honest success case — everything fillable was filled")
r = run([
    alert(26, "C09", "2026-08-13T16:40:46+08:00",
          fill_ts="2026-08-13T16:44:00+08:00", fill_price=10.01),
    alert(27, "F34", "2026-08-13T16:40:54+08:00",
          fill_ts="2026-08-13T16:45:00+08:00", fill_price=10.02),
])
check("reports 2 of 2 fillable", r["diagnosis"].startswith("2 of 2 fillable"))
check("no exclusion clause when there is nothing to exclude",
      "window bug" not in r["diagnosis"])
check("slippage stats are computed", r["stats"]["n"] == 2)

print("\n[5b] an outstanding fill outranks the summary")
r = run(POST_FIX[:1] + [alert(27, "F34", "2026-08-13T16:40:54+08:00",
                              fill_ts="2026-08-13T16:45:00+08:00", fill_price=10.02)])
check("says what is still awaiting /fill, not the tally",
      r["diagnosis"].startswith("1 fillable alert(s) older than a day"))

print("\n[6] no alerts at all — the original 'look at the signal path' case")
r = run([])
check("total 0", r["total"] == 0)
check("diagnosis points away from the user",
      "nothing ever asked for a /fill" in r["diagnosis"])
check("n_fillable is 0 and not a crash", r["n_fillable"] == 0)

print(f"\n{passed} passed, {len(failed)} failed")
if failed:
    print("\n".join(f"  - {f}" for f in failed))
sys.exit(1 if failed else 0)
