"""Prints the external scheduler's jobs, derived from the windows themselves.

GitHub Actions is a best-effort scheduler and has been missing most of its
runs: over 2026-09-05 to 09-06 it delivered 6 of roughly 60 scheduled
keep-warm runs, and on bus-bot it was landing 2-3 of 13 a day, none of them
in the window they existed to cover. A missed wake costs a message for a
recap and a trade for the US entry scan, so the waking moves to a scheduler
that keeps its schedule.

That scheduler expresses a job as a cross-product of minutes x hours x
weekdays in one timezone, which is not how WINDOWS is shaped. Rather than
transcribe the windows by hand into a third place -- keepwarm_window.py
already carries the warning that its times must be kept in step with
main.py, and two copies is one too many -- this expands the windows into
slots and regroups them into the smallest set of jobs that covers exactly
those slots and no others.

    python backend/cronjob_spec.py           # table, for entering by hand
    python backend/cronjob_spec.py --json    # same jobs, for the API

Stdlib only, for the same reason keepwarm_window.py is: deciding when to
knock must not depend on installing requirements.txt.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "backend")

from keepwarm_window import WINDOWS  # noqa: E402

HEALTH_URL = "https://trading-backend-wruf.onrender.com/health"
STEP_MIN = 10  # knock cadence inside a window
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def slots():
    """Every (tz, weekday, hour, minute) the backend must be awake for.

    Walked over a real week rather than reasoned about, so a window that
    straddles local midnight or shifts under DST is expanded by the same
    code that will judge it at run time.
    """
    start = datetime(2026, 9, 7, tzinfo=ZoneInfo("UTC"))  # a Monday
    found = set()
    for i in range(7 * 24 * 60 // STEP_MIN):
        now = start + timedelta(minutes=STEP_MIN * i)
        for window in WINDOWS:
            if window.contains(now):
                local = now.astimezone(window.tz)
                found.add((window.tz.key, local.weekday(),
                           local.hour, local.minute))
    return found


def jobs():
    """The smallest cross-product jobs covering exactly those slots.

    Minutes that recur on the same weekdays at the same hour are one job;
    minutes whose weekdays differ must be separate, because a job cannot say
    "07:10 on Tue-Sat but 07:40 every day" in a single cross-product.
    """
    weekdays_of = defaultdict(set)
    for tz, weekday, hour, minute in slots():
        weekdays_of[(tz, hour, minute)].add(weekday)

    grouped = defaultdict(set)
    for (tz, hour, minute), weekdays in weekdays_of.items():
        grouped[(tz, hour, tuple(sorted(weekdays)))].add(minute)

    out = []
    for (tz, hour, weekdays), minutes in sorted(grouped.items()):
        out.append({
            "url": HEALTH_URL,
            "timezone": tz,
            "hour": hour,
            "minutes": sorted(minutes),
            "weekdays": list(weekdays),
        })
    return out


def check() -> int:
    """Round-trips the jobs back into slots: they must cover every due slot
    and no others. Under-covering is a missed push; over-covering is uptime
    billed against the free tier that suspended everything on 2026-08-30.
    """
    covered = {(j["timezone"], d, j["hour"], m)
               for j in jobs() for d in j["weekdays"] for m in j["minutes"]}
    due = slots()
    missing, extra = due - covered, covered - due
    for label, bad in (("missing", missing), ("extra", extra)):
        for slot in sorted(bad):
            print(f"FAIL {label}: {slot}")
    if missing or extra:
        return 1
    print(f"ok - {len(due)} due slots covered exactly, no spare uptime")
    return 0


def main():
    if "--check" in sys.argv:
        sys.exit(check())
    spec = jobs()
    if "--json" in sys.argv:
        print(json.dumps(spec, indent=2))
        return
    print(f"{len(spec)} jobs, all GET {HEALTH_URL}\n")
    print(f"{'#':<3}{'timezone':<18}{'days':<28}{'hour':<6}minutes")
    for n, job in enumerate(spec, 1):
        days = ("daily" if len(job["weekdays"]) == 7
                else ",".join(DAY_NAMES[d] for d in job["weekdays"]))
        mins = ",".join(f"{m:02d}" for m in job["minutes"])
        print(f"{n:<3}{job['timezone']:<18}{days:<28}{job['hour']:02d}    {mins}")
    awake = len(slots()) * STEP_MIN / 60
    print(f"\n{len(slots())} slots per week -> ~{awake:.0f} h/wk knocking, "
          f"~{awake * 52 / 12:.0f} h/mo of the shared 750.")


if __name__ == "__main__":
    main()
