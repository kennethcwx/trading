"""Checks that the weekly algo verdict cannot go missing without saying so.

The old scheduler slept once, straight to the next Saturday 09:00 SGT. Two
ordinary events cost a whole week in silence: a restart across the fire time
(Render redeploys on every push) recomputed the target to the *following*
Saturday, and a failed send fell into the handler and did the same — and since
telegram_bot.send() returns False rather than raising, a dropped message never
even reached the handler. The job whose entire point is answering "is this
working" without being asked was the one job that could go quiet.

Every test below drives the real loop body with a frozen clock and a fake
sleep, so what is under test is the shipped task, not a restatement of it.

    python backend/test_algocheck_schedule.py
"""
import asyncio
import datetime as _dt
import sys
from datetime import timedelta
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


def note(run, i=0):
    """The catch-up line of the i-th send, or None if it never happened — so a
    broken scheduler fails every check instead of crashing the run on the
    first missing send and hiding the rest."""
    return run.sends[i][1] if len(run.sends) > i else None


def sgt(text):
    """'2026-08-15 09:05' -> aware SGT datetime."""
    return _dt.datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=SGT)


class _Clock(_dt.datetime):
    """Stands in for main.datetime so now() is scripted; strptime still works."""
    _t = None

    @classmethod
    def now(cls, tz=None):
        return cls._t


class _Stop(Exception):
    """Breaks the task out of its while True once the script is exhausted."""


class Run:
    """One run of weekly_algocheck_task over a scripted sequence of wall clocks.

    `times` is what datetime.now(SGT) returns on each pass through the loop.
    `results` is what _send_algocheck returns on each call it makes, defaulting
    to delivered. `state` seeds the app_state row; `memo` seeds the in-process
    mirror, so a fresh process after a restart is memo=None with state intact.
    """

    def __init__(self, times, results=None, state=None, memo=None,
                 enabled=True, raise_on=()):
        self.times = list(times)
        self.results = list(results or [])
        self.store = {} if state is None else dict(state)
        self.memo = memo
        self.enabled = enabled
        self.raise_on = set(raise_on)   # loop indexes that blow up mid-body
        self.sends = []                 # (now, note) per _send_algocheck call
        self.reads = 0                  # db.get_state calls
        self.failures = []

    def __call__(self):
        idx = {"i": -1}

        async def fake_sleep(_seconds):
            idx["i"] += 1
            if idx["i"] >= len(self.times):
                raise _Stop()
            _Clock._t = self.times[idx["i"]]

        async def fake_send(note=""):
            if idx["i"] in self.raise_on:
                raise RuntimeError("yfinance exploded")
            self.sends.append((_Clock._t, note))
            return self.results.pop(0) if self.results else True

        def get_state(key):
            self.reads += 1
            return self.store.get(key)

        originals = (main.datetime, main.asyncio.sleep, main._send_algocheck,
                     db.get_state, db.set_state, main.US10K_ENABLED,
                     main._algocheck_sent_tag, main._note_send_failure)
        main.datetime = _Clock
        main.asyncio.sleep = fake_sleep
        main._send_algocheck = fake_send
        db.get_state = get_state
        db.set_state = lambda k, v: self.store.__setitem__(k, v)
        main.US10K_ENABLED = self.enabled
        main._algocheck_sent_tag = self.memo
        main._note_send_failure = lambda m, w: self.failures.append(w)
        try:
            asyncio.run(main.weekly_algocheck_task())
        except _Stop:
            pass
        finally:
            (main.datetime, main.asyncio.sleep, main._send_algocheck,
             db.get_state, db.set_state, main.US10K_ENABLED,
             main._algocheck_sent_tag, main._note_send_failure) = originals
        return self


KEY = main.ALGOCHECK_STATE_KEY
SAT = "2026-08-15"          # a Saturday
DUE = sgt("2026-08-15 09:00")


print("\n[1] which Saturday is the verdict for — pure clock arithmetic")
ls = main._last_scheduled
check("Saturday 08:00 still belongs to LAST week's verdict",
      ls(sgt("2026-08-15 08:00"), 5, 9, 0) == sgt("2026-08-08 09:00"))
check("09:00 exactly is due now", ls(DUE, 5, 9, 0) == DUE)
check("09:05 is the same week, not the next",
      ls(sgt("2026-08-15 09:05"), 5, 9, 0) == DUE)
check("Monday looks back to Saturday",
      ls(sgt("2026-08-17 12:00"), 5, 9, 0) == DUE)
check("Friday 23:59 is still six days after its verdict",
      ls(sgt("2026-08-21 23:59"), 5, 9, 0) == DUE)
check("it crosses a year boundary",
      ls(sgt("2027-01-01 10:00"), 5, 9, 0) == sgt("2026-12-26 09:00"))
check("a Saturday minute before 09:00 never lands on itself",
      ls(sgt("2026-08-15 08:59"), 5, 9, 0) < sgt("2026-08-15 00:00"))


print("\n[2] the ordinary week — one verdict, on time, recorded")
r = Run([sgt("2026-08-15 09:03")])()
check("it sent once", len(r.sends) == 1)
check("no catch-up line on a timely send", note(r) == "")
check("the week is written down", r.store.get(KEY) == SAT)


print("\n[3] THE BUG: the process starts 40 minutes after the fire time")
# The old scheduler computed 'next Saturday' here and said nothing for 7 days.
r = Run([sgt("2026-08-15 09:40")])()
check("the verdict still goes out", len(r.sends) == 1)
check("and admits it is late", "Catch-up" in (note(r) or ""))
check("the catch-up names when it was due", "Sat 15 Aug 09:00" in (note(r) or ""))
check("the week is recorded, not left open", r.store.get(KEY) == SAT)


print("\n[4] polling the rest of the week does not re-send")
r = Run([sgt("2026-08-15 09:03"), sgt("2026-08-15 09:13"),
         sgt("2026-08-15 18:00"), sgt("2026-08-17 08:00")])()
check("exactly one verdict for the week", len(r.sends) == 1)
check("later polls never reach the database", r.reads == 1)


print("\n[5] a restart mid-window must not re-send what the last process sent")
# Fresh process: the in-memory mirror is gone, only the app_state row remains.
r = Run([sgt("2026-08-15 11:00"), sgt("2026-08-15 11:10")],
        state={KEY: SAT}, memo=None)()
check("nothing is sent twice across a restart", r.sends == [])
check("it did consult the database to find that out", r.reads >= 1)


print("\n[6] a dropped Telegram message is retried, not counted as delivered")
r = Run([sgt("2026-08-15 09:03"), sgt("2026-08-15 09:13")],
        results=[False, True])()
check("it tried twice", len(r.sends) == 2)
check("the failed week was never marked sent early", r.store.get(KEY) == SAT)
check("the drop is surfaced in /health", len(r.failures) == 1)
check("the failure names the week", any(SAT in f for f in r.failures))

r = Run([sgt("2026-08-15 09:03")], results=[False])()
check("a week that never got through stays unrecorded", KEY not in r.store)


print("\n[7] a verdict too old to be useful is dropped, not delivered stale")
# Also the deploy case: shipping this on a Wednesday must not fire last
# Saturday's verdict at him out of nowhere.
r = Run([sgt("2026-08-19 08:00")])()
check("nothing sent 4 days late", r.sends == [])
check("no database round-trip outside the window", r.reads == 0)
check("the boundary is 72h: 71h late still sends",
      len(Run([DUE + timedelta(hours=71)])().sends) == 1)
check("73h late does not", Run([DUE + timedelta(hours=73)])().sends == [])


print("\n[8] a crash mid-verdict costs one poll, not one week")
r = Run([sgt("2026-08-15 09:03"), sgt("2026-08-15 09:13")], raise_on=(0,))()
check("the loop survived and sent on the next poll", len(r.sends) == 1)
check("the retry was still inside the same week", r.store.get(KEY) == SAT)


print("\n[9] the track being off means silence, not a stale verdict")
r = Run([sgt("2026-08-15 09:03")], enabled=False)()
check("nothing sent while US10K_ENABLED is False", r.sends == [])
check("and no database traffic either", r.reads == 0)


print("\n[10] a new week reopens the question")
r = Run([sgt("2026-08-15 09:03"), sgt("2026-08-22 09:03")])()
check("two Saturdays, two verdicts", len(r.sends) == 2)
check("the recorded week advances", r.store.get(KEY) == "2026-08-22")
check("the second is not marked as a catch-up", note(r, 1) == "")


print(f"\n{passed} passed, {len(failed)} failed")
if failed:
    print("\n".join(f"  - {f}" for f in failed))
sys.exit(1 if failed else 0)
