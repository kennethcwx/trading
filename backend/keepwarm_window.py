"""Decides whether the backend needs to be awake right now.

Keeping the instance warm around the clock cost the entire free-tier budget —
754.92 hours against 750, suspending all three services on 2026-08-30 — so it
now sleeps by design and Render stops it after 15 idle minutes. But nothing
inside a sleeping process can wake it: Render's idle timer watches *inbound*
HTTP only. Something external has to knock, and the scheduled work only lands
if the knock arrives while that work is due.

So rather than pinging on a fixed cadence, this answers a narrower question —
"is anything due in the next few minutes?" — and the workflow pings only then.
That buys the schedules their wake for a few hours of uptime a day instead of
the ~720 that blew the budget.

Stdlib only, on purpose: the keep-warm workflow must not depend on installing
backend/requirements.txt to decide whether to send one HTTP request.

Times here MUST agree with the schedules in main.py. If one moves, move both.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SGT = ZoneInfo("Asia/Singapore")

MON_FRI = (0, 1, 2, 3, 4)
TUE_SAT = (1, 2, 3, 4, 5)
EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)
SATURDAY = (5,)


class Window:
    """A scheduled moment, plus how long either side of it we must be awake.

    `pre` covers GitHub Actions cron drift, which routinely runs a job minutes
    late and occasionally skips one outright. `post` covers the span the job
    itself occupies — the US entry scan is a 30-minute window, not an instant —
    and leaves room for the backend's own poll interval to come round again.
    """

    def __init__(self, label, tz, hour, minute, weekdays, pre, post):
        self.label = label
        self.tz = tz
        self.hour = hour
        self.minute = minute
        self.weekdays = weekdays
        self.pre = timedelta(minutes=pre)
        self.post = timedelta(minutes=post)

    def contains(self, now: datetime) -> bool:
        local = now.astimezone(self.tz)
        # The window can straddle local midnight (a crypto recap at 00:00 SGT
        # starts the evening before), so yesterday's and tomorrow's occurrence
        # both have to be considered, not just today's.
        for offset in (-1, 0, 1):
            day = (local + timedelta(days=offset)).date()
            due = datetime(day.year, day.month, day.day,
                           self.hour, self.minute, tzinfo=self.tz)
            if due.weekday() not in self.weekdays:
                continue
            if due - self.pre <= local <= due + self.post:
                return True
        return False


WINDOWS = [
    # Entries confirm ONLY in the last 30 minutes of the US session, so a wake
    # that misses this misses the trade outright — there is no catching up on a
    # trade the way there is on a recap. Widest pad of the lot for that reason.
    Window("US entry window", ET, 15, 30, MON_FRI, pre=20, post=40),
    Window("US briefing", ET, 8, 30, MON_FRI, pre=20, post=20),
    Window("US summary", SGT, 7, 30, TUE_SAT, pre=20, post=20),
    Window("Crypto recap", SGT, 0, 0, EVERY_DAY, pre=20, post=20),
    Window("Crypto recap", SGT, 8, 0, EVERY_DAY, pre=20, post=20),
    Window("Crypto recap", SGT, 16, 0, EVERY_DAY, pre=20, post=20),
    Window("Weekly verdict", SGT, 9, 0, SATURDAY, pre=20, post=40),
]


def due_now(now: datetime | None = None) -> str | None:
    """The label of a window we are inside, or None to stay asleep."""
    now = now or datetime.now(ZoneInfo("UTC"))
    for window in WINDOWS:
        if window.contains(now):
            return window.label
    return None


if __name__ == "__main__":
    label = due_now()
    # Consumed by the workflow: first line is the decision, second the reason.
    print("true" if label else "false")
    print(label or "nothing due")
    sys.exit(0)
