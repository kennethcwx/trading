"""Checks the keep-warm workflow only wakes the backend when work is due.

Two failures this guards against, both silent:

  * waking too little — the 2026-09-05 evidence that started this. Nothing had
    pinged the instance between roughly 07:30 and 16:06 SGT, so Saturday's
    weekly verdict sat undelivered for seven hours (its catch-up window is 72h,
    which saved it) while that morning's US summary was lost outright (its
    window was six hours and closed unseen). A window that is off by an hour,
    or that quietly stops matching main.py, reproduces exactly that.
  * waking too much — the opposite failure, and the more expensive one: warm
    around the clock cost 754.92 hours against the free tier's 750 and
    suspended every service on 2026-08-30.

The DST cases are the ones worth having. GitHub Actions cron is fixed UTC while
every schedule in main.py is defined in ET or SGT, so a naive fixed-offset
window is correct for half the year and an hour wrong for the other half.

    python backend/test_keepwarm_window.py

Negative controls, all verified: setting the US entry window's `post` to 0
fails [1], [2], [5] and [7] (the window collapses to an instant); dropping the
offset=+1 case from Window.contains fails [9], the one that straddles midnight;
replacing ET with a fixed UTC-4 offset fails [7], the EST half of the year.
"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

from keepwarm_window import due_now  # noqa: E402

UTC = ZoneInfo("UTC")

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
        print(f"  [{passed + len(failed)}] ok   {label}")
    else:
        failed.append(label)
        print(f"  [{passed + len(failed)}] FAIL {label}")


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


print("Keep-warm windows\n")

# 2026-09-04 is a Friday, 2026-09-05 a Saturday, 2026-09-07 a Monday.
# July is EDT (UTC-4); January is EST (UTC-5).

# --- the entry window, the one that costs a trade rather than a message ------
check("inside the US entry window (15:40 ET, Fri, EDT)",
      due_now(at(2026, 9, 4, 19, 40)) == "US entry window")
check("the pad still covers 16:05 ET, after the 16:00 close",
      due_now(at(2026, 9, 4, 20, 5)) == "US entry window")
check("the pad reaches back before 15:30 ET, for cron drift",
      due_now(at(2026, 9, 4, 19, 15)) == "US entry window")
check("no entry window on a Saturday",
      due_now(at(2026, 9, 5, 19, 40)) is None)

# --- DST: the same UTC clock time is a different ET hour by season ----------
check("19:40 UTC is the entry window in July (EDT) ...",
      due_now(at(2026, 7, 10, 19, 40)) == "US entry window")
check("... but not in January, when the same instant is 14:40 EST",
      due_now(at(2026, 1, 9, 19, 40)) is None)
check("20:40 UTC IS the entry window in January (15:40 EST) ...",
      due_now(at(2026, 1, 9, 20, 40)) == "US entry window")
check("... and is not in July, when it is 16:40 EDT, past the close",
      due_now(at(2026, 7, 10, 20, 40)) is None)

# --- a window that straddles local midnight --------------------------------
check("the 00:00 SGT crypto recap is awake from 23:45 the evening before",
      due_now(at(2026, 9, 4, 15, 45)) == "Crypto recap")
check("and is still awake at 00:10 SGT, just after it fires",
      due_now(at(2026, 9, 4, 16, 10)) == "Crypto recap")

# --- weekday gating is evaluated in the schedule's own timezone -------------
check("the US summary runs Tue-Sat: Mon 23:30 UTC is Tue 07:30 SGT",
      due_now(at(2026, 9, 7, 23, 30)) == "US summary")
check("but Sun 23:30 UTC is Mon 07:30 SGT, which is skipped",
      due_now(at(2026, 9, 6, 23, 30)) is None)
check("the weekly verdict fires Sat 09:00 SGT",
      due_now(at(2026, 9, 5, 1, 0)) == "Weekly verdict")
check("and not the same time on a Sunday",
      due_now(at(2026, 9, 6, 1, 0)) is None)

# --- and the expensive half: staying asleep the rest of the time ------------
for hh, mm in ((3, 0), (5, 30), (10, 0), (14, 30), (17, 30), (22, 0)):
    check(f"asleep at {hh:02d}:{mm:02d} UTC on a weekday",
          due_now(at(2026, 9, 4, hh, mm)) is None)

# A crude budget guard: sample every 10 minutes across an ordinary week and
# count how much of it we would be pinging for. Real Render uptime is higher —
# each window keeps the instance alive for a further 15 idle minutes after its
# last ping — so read this as a floor, not a bill. The whole point is that it
# stays a small fraction — if a future edit widens a pad into an always-on
# ping, the hours quietly come back and this is what notices.
awake = total = 0
for day in range(7):
    for slot in range(24 * 6):
        total += 1
        moment = at(2026, 9, 7 + day, slot // 6, (slot % 6) * 10)
        if due_now(moment):
            awake += 1
share = awake / total
hours_per_month = share * 24 * 30
print(f"\n  awake {share:.1%} of the week, about {hours_per_month:.0f} h/month "
      f"of the 750 shared across all three Render services")
check(f"the ping schedule stays well inside the free tier ({hours_per_month:.0f} h/month)",
      hours_per_month < 250)
check("and still wakes for a meaningful share of the week", share > 0.05)

print("\n" + "=" * 60)
print(f"{passed} passed, {len(failed)} failed")
if failed:
    print("\n".join(f"  - {f}" for f in failed))
sys.exit(1 if failed else 0)
