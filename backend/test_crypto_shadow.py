"""Checks the crypto shadow track (NO_RSI_CAP) and the signals.py defect it exposed.

The defect is the reason this file exists. signals.py dispatched on variant name
with no NO_RSI_CAP branch, so the variant fell through to `else` -- which is
COMBINED, carrying BOTH an RSI < 70 ceiling and a 20/50 SMA gate. Asking for
"no RSI cap" therefore produced a rule STRICTER than the live BASELINE, and a
shadow track built on it would have spent months measuring the opposite of what
it claimed to measure, silently and with no error to notice.

The momentum fixtures below are BTC's real 2026-08-21 bar: at a 20-day high on
3.42x volume with RSI 86, above its 200 SMA. The live rule refused it on RSI
alone. That is the trade the shadow exists to price.

    python backend/test_crypto_shadow.py
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

import db      # noqa: E402,F401
import main    # noqa: E402
import config  # noqa: E402
from signals import generate_signal  # noqa: E402

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


REGIME = {"regime_ok": True, "vix_status": "NORMAL", "basis": "BTC"}
ETF = {"is_etf": True}


def analysis(price, rsi, high_20d, vol, above_200=True, stop=None, target=None):
    """A crypto analysis dict. Defaults mirror BTC's real 2026-08-21 geometry."""
    stop = price * 0.92 if stop is None else stop
    return {
        "price": price, "rsi": rsi, "high_20d": high_20d, "volume_ratio": vol,
        "above_200sma": above_200, "above_50sma": True, "sma20_above_sma50": False,
        "stop_loss": stop, "profit_target": target if target else price * 1.16,
        "stop_loss_swing": price * 0.85, "profit_target_swing": price * 1.30,
        "earnings_warning": False, "days_to_earnings": None,
    }


BTC_0821 = analysis(78335.19, 86.0, 73369.80, 3.42)     # real bar: breakout at RSI 86
INJ_0823 = analysis(5.36, 65.0, 5.32, 1.56)             # real bar: the one live took


def act(a, variant, position=None):
    return generate_signal(a, position, REGIME, ETF, None,
                           rs_rank=None, sector_ok=None, variant=variant)["action"]


print("\n[1] signals.py variant dispatch -- the defect")
check("NO_RSI_CAP takes BTC's RSI-86 breakout", act(BTC_0821, "NO_RSI_CAP") == "BUY")
check("BASELINE refuses it (the divergence the track measures)",
      act(BTC_0821, "BASELINE") != "BUY")
check("NEGATIVE CONTROL: COMBINED (the old fall-through) also refuses it, "
      "so the missing branch really did invert the variant",
      act(BTC_0821, "COMBINED") != "BUY")
check("both rules agree on INJ's real 08-23 entry",
      act(INJ_0823, "NO_RSI_CAP") == act(INJ_0823, "BASELINE") == "BUY")

print("\n[2] NO_RSI_CAP is BASELINE minus the ceiling -- nothing else")
check("mean-reversion still fires (RSI 30)",
      act(analysis(100, 30.0, 120, 0.8), "NO_RSI_CAP") == "BUY")
check("200 SMA still blocks",
      act(analysis(100, 30.0, 120, 3.0, above_200=False), "NO_RSI_CAP") != "BUY")
check("volume gate still blocks a breakout (1.2x < 1.5x)",
      act(analysis(100, 85.0, 99, 1.2), "NO_RSI_CAP") != "BUY")
check("a non-breakout at high RSI is still not an entry",
      act(analysis(100, 85.0, 130, 3.0), "NO_RSI_CAP") != "BUY")
def suggested(variant):
    return generate_signal(BTC_0821, None, REGIME, ETF, None, rs_rank=None,
                           sector_ok=None, variant=variant)["suggested_action"]


atr_stop = f"${BTC_0821['stop_loss']:.2f}"
swing_stop = f"${BTC_0821['stop_loss_swing']:.2f}"
check("keeps the ATR stop, not the swing-low one (backtest parity)",
      atr_stop in suggested("NO_RSI_CAP") and swing_stop not in suggested("NO_RSI_CAP"))
check("and D still uses the swing-low stop, so the two stay distinguishable",
      swing_stop in suggested("SWING_LOW_NOCAP"))


class FakeDB:
    """Records paper_trades writes. Any write to `trades` raises."""

    def __init__(self):
        self.paper = []
        self.next_id = 1

    def get_open_paper_trades(self, track):
        return [r for r in self.paper if r["track"] == track and r["exit_price"] is None]

    def get_all_paper_trades(self, track):
        return [r for r in self.paper if r["track"] == track]

    def open_paper_trade(self, track, symbol, variant, shares, entry_date, entry_price,
                         stop_loss, profit_target, reason=None):
        self.paper.append({
            "id": self.next_id, "track": track, "symbol": symbol, "variant": variant,
            "shares": shares, "entry_shares": shares, "entry_date": entry_date,
            "entry_price": entry_price, "stop_loss": stop_loss,
            "profit_target": profit_target, "peak_price": entry_price,
            "exit_price": None, "half_sold": 0, "realized_pnl_usd": 0.0,
        })
        self.next_id += 1
        return self.next_id - 1

    def _row(self, tid):
        return next(r for r in self.paper if r["id"] == tid)

    def close_paper_trade(self, tid, price, date, reason):
        r = self._row(tid)
        r["exit_price"] = price
        r["realized_pnl_usd"] += r["shares"] * (price - r["entry_price"])

    def sell_half_paper_trade(self, tid, price):
        r = self._row(tid)
        r["realized_pnl_usd"] += r["shares"] * 0.5 * (price - r["entry_price"])
        r["shares"] *= 0.5
        r["half_sold"] = 1
        r["stop_loss"] = r["entry_price"]

    def update_paper_peak(self, tid, peak):
        self._row(tid)["peak_price"] = peak

    def fetch(self, sql, params=()):
        if "paper_trades" in sql:
            track = params[0]
            return [{"realized_pnl_usd": r["realized_pnl_usd"]}
                    for r in self.paper if r["track"] == track]
        # the live `trades` read behind _crypto_shadow_summary
        return [{"symbol": "INJ-USD", "entry_date": "2026-08-27"},
                {"symbol": "BTC-USD", "entry_date": "2020-01-01"},   # before the start
                {"symbol": "AAPL", "entry_date": "2026-08-28"}]      # not crypto

    def mutate(self, *a, **k):
        raise AssertionError("shadow track must never write to `trades`")


def row_of(price, rsi, high, vol, sym="BTC-USD"):
    return {"symbol": sym, "analysis": analysis(price, rsi, high, vol),
            "fundamentals": ETF, "action": None, "signal": None,
            "position": None, "pos_size": None}


def with_fake(fn):
    fake = FakeDB()
    real = main.db
    main.db = fake
    try:
        return fake, fn(fake)
    finally:
        main.db = real


print("\n[3] the track logs entries -- and only into paper_trades")
fake, summary = with_fake(lambda f: main._run_crypto_track(
    [row_of(78335.19, 86.0, 73369.80, 3.42)], REGIME, 0.7866))
check("opened the RSI-86 breakout the live rule refused", len(fake.paper) == 1)
check("wrote to track 'crypto_shadow'", fake.paper[0]["track"] == "crypto_shadow")
check("tagged with the variant under test", fake.paper[0]["variant"] == "NO_RSI_CAP")
check("fractional size (crypto, not whole shares)", 0 < fake.paper[0]["shares"] < 1)
# Quantity is rounded to 6dp, exactly as the live crypto path does it
# (main.py, _build_crypto_snapshot), and that rounding can go UP -- here by 4
# US cents on a US$275 position. The cap is asserted to within one unit of that
# granularity rather than exactly, because matching live sizing is the whole
# point of a shadow track; tightening the rounding here would make the two
# rules differ by something other than the entry condition under test.
cap_usd = config.CRYPTO_POSITION_SGD * 0.7866
check("size respects the S$350 crypto cap (to 6dp rounding)",
      fake.paper[0]["shares"] * 78335.19 <= cap_usd + 78335.19 * 5e-7)
check("and the cap, not the 1% risk rule, is what bound this position",
      fake.paper[0]["shares"] * 78335.19 > cap_usd * 0.99)

fake2, _ = with_fake(lambda f: main._run_crypto_track(
    [row_of(100.0, 85.0, 130.0, 3.0)], REGIME, 0.7866))
check("no entry when the rule says no", len(fake2.paper) == 0)

fake3, _ = with_fake(lambda f: main._run_crypto_track(
    [row_of(100.0, 30.0, 120.0, 0.8)], REGIME, 0.7866))
check("mean-reversion entry is logged too", len(fake3.paper) == 1)

print("\n[4] exits")


def seeded(price_now, rsi_now, half_sold=0, peak=None, entry=100.0):
    f = FakeDB()
    f.open_paper_trade("crypto_shadow", "BTC-USD", "NO_RSI_CAP", 0.5,
                       "2026-08-27", entry, entry * 0.92, entry * 1.16)
    f.paper[0]["half_sold"] = half_sold
    if half_sold:
        f.paper[0]["shares"] = 0.25
        f.paper[0]["stop_loss"] = entry
    if peak:
        f.paper[0]["peak_price"] = peak
    real = main.db
    main.db = f
    try:
        main._run_crypto_track([row_of(price_now, rsi_now, price_now * 2, 1.0)],
                               REGIME, 0.7866)
    finally:
        main.db = real
    return f.paper[0]


check("stop-out closes the position", seeded(80.0, 45.0)["exit_price"] == 80.0)
r = seeded(120.0, 45.0)
check("target sells half", r["half_sold"] == 1 and r["shares"] == 0.25)
check("does not sell half twice",
      seeded(120.0, 45.0, half_sold=1, peak=120.0)["exit_price"] is None)

# Persistent trail: peak 150 (+50% off a 100 entry), retrace to 134. The 10% band
# is 135, so this closes. The arm comes off the PEAK and stays armed -- crypto's
# rule, not the US de-arming one.
check("persistent trail closes on a retrace below peak-10%",
      seeded(134.0, 45.0, half_sold=1, peak=150.0)["exit_price"] == 134.0)
check("trail holds while inside the 10% band",
      seeded(148.0, 45.0, half_sold=1, peak=150.0)["exit_price"] is None)
check("trail does not arm below the 15% trigger",
      seeded(105.0, 45.0, half_sold=1, peak=110.0)["exit_price"] is None)

print("\n[5] kill switch and summary")
real_enabled = main.CRYPTO_SHADOW_ENABLED
main.CRYPTO_SHADOW_ENABLED = False
fake4, out = with_fake(lambda f: main._run_crypto_track(
    [row_of(78335.19, 86.0, 73369.80, 3.42)], REGIME, 0.7866))
check("disabled flag writes nothing and returns None",
      len(fake4.paper) == 0 and out is None)
main.CRYPTO_SHADOW_ENABLED = real_enabled

fake5, summ = with_fake(lambda f: main._run_crypto_track(
    [row_of(78335.19, 86.0, 73369.80, 3.42)], REGIME, 0.7866))
check("summary counts the shadow entry", summ["entries"] == 1 and summ["open"] == 1)
check("summary counts only crypto live trades on/after the start date "
      "(1 of 3 rows qualifies)", summ["live_entries"] == 1)
check("summary names the variant and start date",
      summ["variant"] == "NO_RSI_CAP" and summ["since"] == config.CRYPTO_SHADOW_START)

print("\n[6] the digest line")
import telegram_bot  # noqa: E402

rows = [{"symbol": "BTC-USD", "action": "SKIP", "analysis": BTC_0821,
         "signal": {"reasons": ["Overbought"]}, "position": None, "pos_size": None}]
plain = telegram_bot.format_crypto_digest(rows, 0.7866)
withs = telegram_bot.format_crypto_digest(rows, 0.7866, summ)
check("no shadow line when there is no shadow data", "Shadow" not in plain)
check("shadow line appears when there is", "Shadow (NO_RSI_CAP)" in withs)
check("shadow line shows both counts", "1 entries vs live 1" in withs)
check("it is one line, not a second message", withs.count("Shadow") == 1)

print(f"\n{'=' * 60}\n  {passed} passed, {len(failed)} failed")
if failed:
    for f in failed:
        print(f"    FAILED: {f}")
    sys.exit(1)
print("  All checks passed.")
