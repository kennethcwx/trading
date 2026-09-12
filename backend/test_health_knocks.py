"""Checks that /health remembers who knocked, across the sleep.

The gap this closes: the instance only wakes for an inbound request, and the
waking is done by an external scheduler this box cannot see into. When a
window is missed -- 2026-09-10 to 09-12, two US entry windows and a briefing
in a row -- nothing here could say whether the scheduler had stopped knocking
or the box had ignored the knock. The heartbeats answer "did the watcher run";
they cannot answer "did the knock arrive".

/health now records each hit (time + user-agent) in a ring, writes it from a
thread at most once a minute, restores it at startup, and serves the tail.

    python backend/test_health_knocks.py

Negative controls:
  - drop the per-minute throttle in _knock() and [3] must fail
  - make _restore_knocks() replace the list instead of prepending and [6] must fail
    (only if a hit lands before startup restore; the order in [6] is the real one)
  - make _persist_knocks() re-raise and [8] must fail
  - drop the Render/ filter in _knock() and [16] must fail
"""
import asyncio
import datetime as _dt
import json
import sys
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


class _Clock(_dt.datetime):
    _t = None

    @classmethod
    def now(cls, tz=None):
        return cls._t


store: dict[str, str] = {}
writes: list[str] = []


def fake_set_state(key, value):
    store[key] = value
    writes.append(key)


def fake_get_state(key):
    return store.get(key)


_orig = (db.set_state, db.get_state, main.datetime)
db.set_state, db.get_state = fake_set_state, fake_get_state
main.datetime = _Clock

CRON_UA = "Mozilla/5.0 (compatible; cron-job.org; https://cron-job.org)"


async def knock_async(agent):
    """Under a running loop, the write goes to a thread -- wait for it."""
    main._knock(agent)
    await asyncio.sleep(0.05)


try:
    main._knocks.clear()
    main._knocks_written = None
    writes.clear()
    store.clear()

    # ── A knock is recorded and written, off the request path ────────────────
    _Clock._t = _dt.datetime(2026, 9, 12, 15, 40, 0, tzinfo=SGT)
    asyncio.run(knock_async(CRON_UA))
    check("[1] the knock is in memory with its time and agent",
          len(main._knocks) == 1
          and main._knocks[0]["t"].startswith("2026-09-12T15:40")
          and "cron-job.org" in main._knocks[0]["ua"])
    check("[2] the first knock writes through to app_state",
          writes == [main._KNOCK_STATE_KEY]
          and json.loads(store[main._KNOCK_STATE_KEY])[0]["t"].startswith("2026-09-12T15:40"))

    # ── A burst inside the same minute is one write ──────────────────────────
    n_before = len(writes)
    _Clock._t = _dt.datetime(2026, 9, 12, 15, 40, 20, tzinfo=SGT)
    asyncio.run(knock_async("curl/8.0"))
    _Clock._t = _dt.datetime(2026, 9, 12, 15, 40, 40, tzinfo=SGT)
    asyncio.run(knock_async("curl/8.0"))
    check(f"[3] knocks inside the minute do not write again ({len(writes) - n_before} writes)",
          len(writes) == n_before)
    check("[4] but all three are in memory", len(main._knocks) == 3)

    _Clock._t = _dt.datetime(2026, 9, 12, 15, 50, 0, tzinfo=SGT)
    asyncio.run(knock_async(CRON_UA))
    check(f"[5] a knock past the minute writes, carrying the whole ring ({len(writes)} total)",
          len(writes) == n_before + 1
          and len(json.loads(store[main._KNOCK_STATE_KEY])) == 4)

    # ── Restart: stored knocks come back, older than anything seen since ─────
    main._knocks.clear()
    main._knocks_written = None
    n = main._restore_knocks()                    # lifespan startup, before any hit
    _Clock._t = _dt.datetime(2026, 9, 12, 16, 15, 0, tzinfo=SGT)
    asyncio.run(knock_async("curl/8.0"))          # this process's first hit
    check(f"[6] restored knocks sit before the fresh one, in order ({n} restored)",
          n == 4 and len(main._knocks) == 5
          and main._knocks[0]["t"].startswith("2026-09-12T15:40:00")
          and main._knocks[-1]["t"].startswith("2026-09-12T16:15"))

    # ── /health serves the tail without a database read ──────────────────────
    reads: list[str] = []
    db.get_state = lambda k: (reads.append(k), store.get(k))[1]
    tail = main._knocks[-10:]
    check("[7] the /health tail is the newest knocks and needs no read",
          tail[-1]["t"].startswith("2026-09-12T16:15") and not reads)

    # ── The ring is capped ───────────────────────────────────────────────────
    for i in range(main._KNOCK_KEEP + 20):
        _Clock._t = _dt.datetime(2026, 9, 13, 0, 0, 0, tzinfo=SGT) + _dt.timedelta(minutes=i)
        main._knock("x")
    check(f"[9] the ring keeps the last {main._KNOCK_KEEP} only ({len(main._knocks)})",
          len(main._knocks) == main._KNOCK_KEEP
          and main._knocks[-1]["t"].startswith("2026-09-13T01:19"))

    # ── A database failure never reaches the request ─────────────────────────
    def boom(*a, **k):
        raise RuntimeError("neon is down")

    db.set_state = boom
    main._knocks_written = None
    _Clock._t = _dt.datetime(2026, 9, 13, 2, 0, 0, tzinfo=SGT)
    try:
        main._knock(CRON_UA)    # no loop: writes inline, so the failure is here
        survived = True
    except Exception:
        survived = False
    check("[8] a database failure does not fail the knock", survived)
    check("[10] and the knock is still in memory", main._knocks[-1]["t"].startswith("2026-09-13T02:00"))

    # ── Bad stored data restores nothing rather than crashing ────────────────
    db.set_state, db.get_state = fake_set_state, fake_get_state
    main._knocks.clear()
    store[main._KNOCK_STATE_KEY] = "{not json"
    check("[11] corrupt stored JSON restores nothing", main._restore_knocks() == 0 and not main._knocks)
    store[main._KNOCK_STATE_KEY] = json.dumps({"t": "a dict, not a list"})
    check("[12] a stored value of the wrong shape restores nothing", main._restore_knocks() == 0)
    store[main._KNOCK_STATE_KEY] = json.dumps([{"t": "2026-09-12T15:40:00+08:00", "ua": "ok"}, "junk", {"ua": "no time"}])
    check("[13] entries without a time are dropped on restore",
          main._restore_knocks() == 1 and main._knocks[0]["ua"] == "ok")
    del store[main._KNOCK_STATE_KEY]
    main._knocks.clear()
    check("[14] a first-ever boot with no row restores nothing", main._restore_knocks() == 0)

    # ── Render's own 5-second health check is not a knock ────────────────────
    main._knocks.clear()
    main._knocks_written = None
    writes.clear()
    _Clock._t = _dt.datetime(2026, 9, 13, 3, 0, 0, tzinfo=SGT)
    for i in range(12):
        main._knock("Render/1.0")
    check("[16] Render/1.0 is neither recorded nor written", not main._knocks and not writes)
    main._knock(CRON_UA)
    check("[17] a real knock after the checks is the only entry", len(main._knocks) == 1)

    # ── The guard in test_health_schedules must know this key ────────────────
    src = open("backend/test_health_schedules.py", encoding="utf-8").read()
    check("[15] the state-key guard lists _KNOCK_STATE_KEY", '"_KNOCK_STATE_KEY"' in src)

finally:
    db.set_state, db.get_state, main.datetime = _orig

print(f"\n{passed} passed, {len(failed)} failed")
if failed:
    for f in failed:
        print(f"  - {f}")
    sys.exit(1)
