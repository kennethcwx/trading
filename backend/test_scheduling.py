"""Checks that the daily scheduled pushes survive an instance that sleeps.

Every one of these jobs was written when this backend was awake around the
clock. That is what cost the free tier: 754.92 hours against a 750-hour pot,
and all three services suspended on 2026-08-30. The fix was to let the instance
sleep -- keep-warm pings moved to 30 minutes, and Render stops a service after
15 idle minutes -- which means the process now dies and restarts dozens of
times a day, and four jobs that each assumed otherwise broke in a different
direction:

  * morning_briefing_task  -- caught up on startup with nothing recording that
    it already had, so every restart between 08:30 and the 16:00 ET close sent
    another copy: ~15 identical briefings a weekday.
  * sgx_briefing_task      -- same shape, narrower window, 1-2 copies.
  * daily_summary_task     -- pure sleep-to-target with no persistence and no
    catch-up, so it simply stopped firing.
  * crypto_watcher         -- counted loop passes in memory, so the counter
    reset before it ever reached 16 and the 8-hour recap went silent.

All four now follow weekly_algocheck_task's proven shape. The tests drive the
real shipped tasks with a frozen clock and a fake sleep, so what is under test
is the task itself rather than a restatement of it.

    python backend/test_scheduling.py

Negative control: restore any of the four old loop bodies, or drop the
`if ok:` guard around _clear_events, and this suite must fail.
"""
import asyncio
import datetime as _dt
import inspect
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

import db  # noqa: E402
import main  # noqa: E402

SGT = ZoneInfo("Asia/Singapore")
ET = ZoneInfo("America/New_York")

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


def at(text, tz=SGT):
    """Parse a wall clock into an aware datetime."""
    return _dt.datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=tz)


def note(run, i=0):
    """The catch-up line of the i-th send, or None if it never happened, so a
    broken scheduler fails every check instead of crashing the run."""
    return run.sends[i][1] if len(run.sends) > i else None


class _Clock(_dt.datetime):
    """Stands in for main.datetime so now() is scripted; strptime still works."""
    _t = None

    @classmethod
    def now(cls, tz=None):
        return cls._t


class _Stop(Exception):
    """Breaks the task out of its while True once the script is exhausted."""


class Run:
    """One run of a real scheduled task over a scripted sequence of wall clocks.

    `times` is what datetime.now() returns on each pass through the loop.
    `send_attr` is the main-module function the task pushes through, replaced
    with a recorder. `results` is what each send returns, default delivered.
    `state` seeds the app_state rows, so a process that restarted with the day
    already sent is state-set and memo-empty, which is exactly the case that
    used to produce fifteen briefings.
    """

    def __init__(self, task, send_attr, times, results=None, state=None):
        self.task = task
        self.send_attr = send_attr
        self.times = list(times)
        self.results = list(results or [])
        self.store = {} if state is None else dict(state)
        self.sends = []      # (now, note) per send
        self.reads = 0       # db.get_state calls
        self.failures = []

    def __call__(self):
        idx = {"i": -1}

        async def fake_sleep(_seconds):
            idx["i"] += 1
            if idx["i"] >= len(self.times):
                raise _Stop()
            _Clock._t = self.times[idx["i"]]

        async def fake_send(note="", *_a, **_kw):
            self.sends.append((_Clock._t, note))
            return self.results.pop(0) if self.results else True

        def get_state(key):
            self.reads += 1
            return self.store.get(key)

        originals = (main.datetime, main.asyncio.sleep, db.get_state,
                     db.set_state, main._note_send_failure,
                     getattr(main, self.send_attr))
        main.datetime = _Clock
        main.asyncio.sleep = fake_sleep
        db.get_state = get_state
        db.set_state = lambda k, v: self.store.__setitem__(k, v)
        main._note_send_failure = lambda m, w: self.failures.append(w)
        setattr(main, self.send_attr, fake_send)
        try:
            asyncio.run(self.task())
        except _Stop:
            pass
        finally:
            (main.datetime, main.asyncio.sleep, db.get_state, db.set_state,
             main._note_send_failure) = originals[:5]
            setattr(main, self.send_attr, originals[5])
        return self


def briefing(times, **kw):
    return Run(main.morning_briefing_task, "send_morning_briefing", times, **kw)()


def sgx(times, **kw):
    return Run(main.sgx_briefing_task, "send_sgx_morning_briefing", times, **kw)()


def summary(market, hour, minute, skip, times, **kw):
    def task():
        return main.daily_summary_task(market, hour, minute, skip)
    return Run(task, "_send_daily_summary", times, **kw)()


BKEY = main.BRIEFING_STATE_KEY
SKEY = main.SGX_BRIEFING_STATE_KEY


# -- [1] _last_daily: which occurrence is the live one ------------------------
ld = main._last_daily
print("\n[1] _last_daily")
check("[1a] the scheduled minute itself is due now",
      ld(at("2026-09-02 08:30"), 8, 30) == at("2026-09-02 08:30"))
check("[1b] a minute before belongs to yesterday",
      ld(at("2026-09-02 08:29"), 8, 30) == at("2026-09-01 08:30"))
check("[1c] late in the day still points at this morning",
      ld(at("2026-09-02 23:59"), 8, 30) == at("2026-09-02 08:30"))
check("[1d] Saturday is skipped, not reported as due",
      ld(at("2026-09-05 09:00"), 8, 30, (5, 6)) == at("2026-09-04 08:30"))
check("[1e] Monday looks back past the whole weekend",
      ld(at("2026-09-07 08:00"), 8, 30, (5, 6)) == at("2026-09-04 08:30"))
check("[1f] a Tue-Sat job on Monday looks back to Saturday",
      ld(at("2026-09-07 08:00"), 7, 30, (6, 0)) == at("2026-09-05 07:30"))
check("[1g] it crosses a month boundary",
      ld(at("2026-09-01 07:00"), 8, 30) == at("2026-08-31 08:30"))


# -- [2] the morning briefing: the ~15-copies bug -----------------------------
# This is the one actively firing on the deployed build. A restart at any point
# between 08:30 and the 16:00 ET close re-sent the whole briefing.
print("\n[2] morning briefing")
r = briefing([at("2026-09-02 08:30", ET)])
check("[2a] it sends at 08:30", len(r.sends) == 1)
check("[2b] a timely send carries no catch-up line", note(r) == "")
check("[2c] the day is written down", r.store.get(BKEY) == "2026-09-02")

r = briefing([at("2026-09-02 12:00", ET)], state={BKEY: "2026-09-02"})
check("[2d] a restart mid-session sends nothing", r.sends == [])
check("[2e] it consulted the database to find that out", r.reads >= 1)

r = briefing([at("2026-09-02 08:30", ET), at("2026-09-02 09:00", ET),
              at("2026-09-02 11:00", ET), at("2026-09-02 15:00", ET)])
check("[2f] many polls across one session still send once", len(r.sends) == 1)
check("[2g] later polls never reach the database again", r.reads == 1)

r = briefing([at("2026-09-02 09:30", ET)])
check("[2h] a late first poll still sends", len(r.sends) == 1)
check("[2i] and admits it is late", "Catch-up" in (note(r) or ""))
check("[2j] the catch-up names when it was due", "Wed 02 Sep 08:30" in (note(r) or ""))

check("[2k] nothing fires on a Saturday",
      briefing([at("2026-09-05 09:00", ET)]).sends == [])
check("[2l] past the 16:00 close the briefing is stale and skipped",
      briefing([at("2026-09-02 16:30", ET)]).sends == [])
check("[2m] 16:00 exactly is the last minute it may arrive",
      len(briefing([at("2026-09-02 16:00", ET)]).sends) == 1)

r = briefing([at("2026-09-02 08:30", ET), at("2026-09-02 08:35", ET)],
             results=[False, True])
check("[2n] a dropped briefing is retried", len(r.sends) == 2)
check("[2o] the failed day was never marked sent", r.store.get(BKEY) == "2026-09-02")
check("[2p] the drop is surfaced in /health", len(r.failures) == 1)
r = briefing([at("2026-09-02 08:30", ET)], results=[False])
check("[2q] a day that never got through stays unrecorded", BKEY not in r.store)


# -- [3] the SGX pre-open briefing --------------------------------------------
print("\n[3] SGX briefing")
r = sgx([at("2026-09-02 08:30")])
check("[3a] it sends at 08:30 SGT", len(r.sends) == 1)
check("[3b] the day is recorded", r.store.get(SKEY) == "2026-09-02")
check("[3c] a restart before the open does not repeat it",
      sgx([at("2026-09-02 08:50")], state={SKEY: "2026-09-02"}).sends == [])
r = sgx([at("2026-09-02 08:50")])
check("[3d] a missed 08:30 is still caught before the open", len(r.sends) == 1)
check("[3e] and says so", "Catch-up" in (note(r) or ""))
check("[3f] after the 09:00 open it is not sent at all",
      sgx([at("2026-09-02 09:05")]).sends == [])
check("[3g] 09:00 exactly is the boundary",
      len(sgx([at("2026-09-02 09:00")]).sends) == 1)
check("[3h] weekends are skipped",
      sgx([at("2026-09-06 08:30")]).sends == [])


# -- [4] the daily summaries: the job that stopped firing entirely ------------
print("\n[4] daily summaries")
UKEY = "daily_summary_last_sent_US"
r = summary("US", 7, 30, (6, 0), [at("2026-09-02 07:30")])
check("[4a] the US summary fires at 07:30 SGT", len(r.sends) == 1)
check("[4b] its day is recorded under its own key", r.store.get(UKEY) == "2026-09-02")
r = summary("US", 7, 30, (6, 0), [at("2026-09-02 09:00"), at("2026-09-02 12:00")])
check("[4c] a restart after the target still catches up", len(r.sends) == 1)
check("[4d] and only once", r.reads == 1)
# Was "7 hours late is past the window" until 2026-09-05, when a recap exactly
# that late was dropped for real: nothing woke the instance inside the old
# six-hour window, while the weekly verdict due the same morning was delivered
# on the same late wake because it was willing to wait 72. The window now runs
# to the next US open instead.
check("[4e] a recap 7 hours late still arrives",
      len(summary("US", 7, 30, (6, 0), [at("2026-09-02 14:31")]).sends) == 1)
check("[4e2] but one past the next US open is stale and is dropped",
      summary("US", 7, 30, (6, 0), [at("2026-09-02 21:31")]).sends == [])
check("[4f] Monday is skipped for the US track",
      summary("US", 7, 30, (6, 0), [at("2026-09-07 07:30")],
              state={UKEY: "2026-09-05"}).sends == [])
r = summary("SGX", 17, 45, (5, 6), [at("2026-09-02 17:45")])
check("[4g] the SGX summary fires at 17:45", len(r.sends) == 1)
check("[4h] the two markets never share a state key",
      r.store.get("daily_summary_last_sent_SGX") == "2026-09-02" and UKEY not in r.store)


# -- [5] the crypto recap: a wall clock, not a counter ------------------------
print("\n[5] crypto recap")
loh = main._last_of_hours
H = main.CRYPTO_DIGEST_HOURS
check("[5a] the recap keeps its 8-hour cadence", len(H) == 3 and sorted(H) == [0, 8, 16])
check("[5b] 08:00 exactly is the live slot",
      loh(at("2026-09-02 08:00"), H) == at("2026-09-02 08:00"))
check("[5c] 07:59 still belongs to midnight",
      loh(at("2026-09-02 07:59"), H) == at("2026-09-02 00:00"))
check("[5d] the first slot of the day is reachable",
      loh(at("2026-09-02 00:00"), H) == at("2026-09-02 00:00"))
check("[5e] a slot tag is unique per day and hour",
      loh(at("2026-09-02 16:30"), H).strftime("%Y-%m-%d %H") == "2026-09-02 16")
check("[5f] the catch-up can never reach the next slot",
      main.CRYPTO_DIGEST_CATCHUP_H < 8)
src = inspect.getsource(main.crypto_watcher)
check("[5g] the in-memory pass counter is gone", "DIGEST_EVERY" not in src)
check("[5h] the recap is memoed in the database",
      "CRYPTO_DIGEST_STATE_KEY" in src and "db.set_state" in src)
check("[5i] a dropped recap is surfaced, not swallowed",
      "_note_send_failure" in src)


# -- [6] the event log has to outlive the process -----------------------------
# _send_daily_summary reports "what fired today". Its source was a list in
# memory, which a sleeping instance clears every half hour, so the summaries
# this file just fixed would have arrived on time and said "No signals fired."
print("\n[6] event log")
store = {}
_g, _s = db.get_state, db.set_state
db.get_state = lambda k: store.get(k)
db.set_state = lambda k, v: store.__setitem__(k, v)
try:
    main._log_event("US", "BUY AAPL", True)
    main._log_event("SGX", "BUY D05", False)
    check("[6a] events are written outside the process", bool(store))
    check("[6b] a fresh process still sees them", len(main._read_events("US")) == 1)
    check("[6c] each market reads only its own", len(main._read_events("SGX")) == 1)
    sgx_ev = (main._read_events("SGX") or [None])[0]
    us_ev = (main._read_events("US") or [None])[0]
    check("[6d] the queued flag survives the round trip",
          sgx_ev is not None and sgx_ev["sent"] is False)
    check("[6e] the timestamp survives as a readable time",
          us_ev is not None and main._event_time(us_ev) != "--:--")
    main._clear_events("US")
    check("[6f] clearing one market leaves the other alone",
          main._read_events("US") == [] and len(main._read_events("SGX")) == 1)
    for i in range(210):
        main._log_event("US", f"e{i}", True)
    check("[6g] the log is still capped", len(main._read_events("US")) <= 200)
finally:
    db.get_state, db.set_state = _g, _s

ssrc = inspect.getsource(main._send_daily_summary)
check("[6h] the summary reads the log rather than draining it up front",
      "_read_events(" in ssrc and "_drain_events" not in ssrc)
check("[6i] events are only cleared once the push has landed",
      "if ok:" in ssrc and ssrc.index("ok = telegram_bot.send") < ssrc.index("_clear_events"))
check("[6j] a day with nothing to report is still marked done",
      "return True" in ssrc)


# -- [7] the startup notice ---------------------------------------------------
# Not one of the four, but the same assumption: ~48 "Trading Dashboard Online"
# pushes a day once the process restarts every half hour.
print("\n[7] startup notice")
lsrc = inspect.getsource(main.lifespan)
check("[7a] the startup push is gated",
      "format_startup" in lsrc and "STARTUP_STATE_KEY" in lsrc)
check("[7b] it is keyed on the deploy, not the process", "RENDER_GIT_COMMIT" in lsrc)
check("[7c] a failure there cannot take down startup", "startup notice error" in lsrc)


# -- [8] none of the four kept its sleep-to-target loop ------------------------
print("\n[8] no task holds a schedule across a sleep")
for name in ("morning_briefing_task", "sgx_briefing_task", "daily_summary_task"):
    body = inspect.getsource(getattr(main, name))
    check(f"[8] {name} delegates to the polled scheduler",
          "_scheduled_daily(" in body and "while True" not in body)
check("[8d] the poll is short enough to run inside one wake",
      main.SCHEDULE_POLL_MIN * 60 < 15 * 60)

print("\n" + "=" * 60 + f"\n  {passed} passed, {len(failed)} failed")
if failed:
    for f in failed:
        print(f"    FAILED: {f}")
    sys.exit(1)
print("  All checks passed.")
