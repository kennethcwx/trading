"""Checks that the backend can say whether a scheduled push actually went out.

The gap this closes: /health's watcher heartbeats live in memory. Once the
instance started sleeping between 30-minute pings, a fresh wake showed them
empty, so the one question worth asking after the 2026-09-02 scheduler fix --
"did this morning's briefing go, and exactly once?" -- could only be answered by
scrolling Telegram. The marks the scheduler already writes to app_state do
survive a restart, and they are the same rows it consults before sending: a tag
for today means today's push is done and cannot repeat.

So this surfaces them, in /api/schedules and on the Telegram /health card.

    python backend/test_health_schedules.py

Negative control: drop an entry from _scheduled_pushes() and [1] must fail;
make the "today" comparison unconditional and [4] must fail.
"""
import asyncio
import datetime as _dt
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
    """Stands in for main.datetime so "today" is scripted, not the real date."""
    _t = None

    @classmethod
    def now(cls, tz=None):
        return cls._t


TODAY = "2026-09-04"
NOW = _dt.datetime(2026, 9, 4, 10, 0, tzinfo=SGT)

# A plausible mid-morning state: both briefings done today, the SGX summary not
# yet due, the weekly verdict from Saturday, and one push that has never run.
STATE = {
    "morning_briefing_last_sent": {"value": TODAY, "updated_at": "2026-09-04 00:31:02"},
    "sgx_briefing_last_sent": {"value": TODAY, "updated_at": "2026-09-04 00:32:10"},
    "daily_summary_last_sent_US": {"value": TODAY, "updated_at": "2026-09-03 23:31:00"},
    "daily_summary_last_sent_SGX": {"value": "2026-09-03", "updated_at": "2026-09-03 09:45:00"},
    "algocheck_last_sent_week": {"value": "2026-W35", "updated_at": "2026-08-29 01:00:00"},
}


print("\n[1] the diagnostic covers every push that marks a day done")
# The drift this guards against: someone adds a seventh scheduled push, and the
# card that is supposed to answer "did everything go out" quietly omits it. The
# expected set is derived from main's own constants, not retyped here.
registry_keys = {key for _label, key, _due in main._scheduled_pushes()}
declared = {
    v for k, v in vars(main).items()
    if k.endswith("_STATE_KEY") and isinstance(v, str)
    # Not daily pushes: one records the commit a startup notice was sent for,
    # the other is the summary's event log.
    and k not in ("STARTUP_STATE_KEY", "_EVENT_LOG_STATE_KEY")
}
declared |= {"daily_summary_last_sent_US", "daily_summary_last_sent_SGX"}
# SGX was shelved on 2026-09-04. Its pushes are not scheduled, so listing them would
# show a mark that never moves — indistinguishable from a scheduler that has died.
if not main.SGX_ENABLED:
    declared -= {"sgx_briefing_last_sent", "daily_summary_last_sent_SGX"}
missing = declared - registry_keys
check(f"every scheduled-push key is listed ({len(registry_keys)} of {len(declared)})", not missing)
check(f"nothing invented that no scheduler writes ({sorted(registry_keys - declared)})",
      not (registry_keys - declared))
check("each entry says when it is due", all(due for _n, _k, due in main._scheduled_pushes()))
# Both directions, so the flag is proven to drive the card rather than assumed to.
_flag = main.SGX_ENABLED
try:
    main.SGX_ENABLED = True
    on = {k for _l, k, _d in main._scheduled_pushes()}
    main.SGX_ENABLED = False
    off = {k for _l, k, _d in main._scheduled_pushes()}
finally:
    main.SGX_ENABLED = _flag
sgx_keys = {"sgx_briefing_last_sent", "daily_summary_last_sent_SGX"}
check("enabling SGX puts its pushes on the card", sgx_keys <= on)
check("shelving SGX takes them off", not (sgx_keys & off))
check("and leaves the other four alone", on - sgx_keys == off)


print("\n[2] /api/schedules reports what completed and when it was marked")
_orig_rows, _orig_dt = db.get_state_rows, main.datetime
db.get_state_rows = lambda: dict(STATE)
main.datetime = _Clock
_Clock._t = NOW
try:
    out = asyncio.run(main.schedules())
finally:
    main.datetime = _orig_dt

check(f"it answers ok ({out['ok']})", out["ok"] is True)
rows = {r["name"]: r for r in out["schedules"]}
check(f"one row per push ({len(out['schedules'])})", len(out["schedules"]) == len(registry_keys))
check("the US briefing shows today's tag", rows["US briefing"]["last_completed"] == TODAY)
check("and the time the mark was written",
      rows["US briefing"]["marked_at"] == "2026-09-04 00:31:02")
# The whole point of marked_at: the tag says which day it was *for*, and only
# this says when it really went. A push caught up hours late has both.
check("the US summary's tag and mark can disagree on the day",
      rows["US summary"]["last_completed"] == TODAY
      and rows["US summary"]["marked_at"].startswith("2026-09-03"))
check("a push that never ran reads as null",
      rows["Crypto recap"]["last_completed"] is None
      and rows["Crypto recap"]["marked_at"] is None)


print("\n[3] a database that will not answer does not take the endpoint down")
# This is a diagnostic. Failing closed with a 500 would mean the one call you
# make when something looks wrong is also the one that breaks.
def _boom():
    raise RuntimeError("connection refused")


db.get_state_rows = _boom
main.datetime = _Clock
try:
    broken = asyncio.run(main.schedules())
finally:
    main.datetime = _orig_dt
check(f"it reports the failure instead of raising ({broken['ok']})", broken["ok"] is False)
check("and names it", "connection refused" in broken["error"])
check("with no rows invented", broken["schedules"] == [])


print("\n[4] the Telegram /health card carries the same marks")
db.get_state_rows = lambda: dict(STATE)
sent = []
_o_send, _o_regime = main.telegram_bot.send, main.get_market_regime
main.telegram_bot.send = lambda m: sent.append(m)
main.get_market_regime = lambda: (_ for _ in ()).throw(RuntimeError("no network in tests"))
main.datetime = _Clock
_Clock._t = NOW
try:
    asyncio.run(main._send_health())
finally:
    main.telegram_bot.send, main.get_market_regime = _o_send, _o_regime
    main.datetime = _orig_dt
    db.get_state_rows = _orig_rows

card = sent[0] if sent else ""
check(f"a card was sent ({len(sent)})", len(sent) == 1)
check("it has a scheduled-pushes section", "Scheduled pushes" in card)
check("it names each push", all(label in card for label, _k, _d in main._scheduled_pushes()))
check("today's briefing is flagged as done today", "2026-09-04 OK" in card)
# The negative half: a mark that is not today's must NOT be dressed up as one.
# Uses the weekly verdict because SGX, whose summary used to carry this check,
# is shelved and no longer on the card.
stale = next((ln for ln in card.split("\n") if ln.startswith("Weekly verdict")), "")
check(f"a mark that is not from today is not flagged ({stale.strip()!r})",
      "2026-W35" in stale and "OK" not in stale)
check("a push that never ran shows a dash", "Crypto recap   —" in card)
check("the regime failure did not lose the card", "Backend Health" in card)


print("\n" + "=" * 60)
print(f"{passed} passed, {len(failed)} failed")
if failed:
    print("\n".join(f"  - {f}" for f in failed))
sys.exit(1 if failed else 0)
