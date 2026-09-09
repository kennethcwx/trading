"""Checks that watcher heartbeats survive the sleep that used to erase them.

The gap this closes: /health's heartbeats lived only in `main._heartbeats`, a
plain dict. Once the instance started sleeping between windows, every wake
showed it empty -- so "did last night's entry scan actually run?" could not be
answered after the fact, which is precisely when it gets asked. A track with no
entries then looks identical to a watcher that never ran at all.

The beats now write through to app_state and are restored at startup. /health
still reads memory and still does zero work; the row only has to bridge the gap.

    python backend/test_scan_heartbeats.py

Negative controls:
  - drop the throttle in _beat() and [3] must fail (every beat would write)
  - make _restore_heartbeats() assign instead of setdefault and [7] must fail
    (a restored beat would overwrite a fresher one from this process)
  - make _persist_heartbeats() re-raise and [9] must fail
"""
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
    """Stands in for main.datetime so the throttle window is scripted."""
    _t = None

    @classmethod
    def now(cls, tz=None):
        return cls._t


# ── Fake app_state, so nothing touches a real database ───────────────────────
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

try:
    # ── A beat records in memory and writes through ──────────────────────────
    main._heartbeats.clear()
    main._heartbeats_written = None
    writes.clear()
    store.clear()

    _Clock._t = _dt.datetime(2026, 9, 9, 3, 35, 0, tzinfo=SGT)
    main._beat("us_entry_scan")

    check("[1] the beat is in memory",
          main._heartbeats.get("us_entry_scan", "").startswith("2026-09-09T03:35"))
    check("[2] the first beat writes through to app_state",
          json.loads(store[main._HEARTBEAT_STATE_KEY])["us_entry_scan"].startswith("2026-09-09T03:35"))

    # ── The throttle: a burst must not become a burst of writes ──────────────
    n_before = len(writes)
    _Clock._t = _dt.datetime(2026, 9, 9, 3, 36, 0, tzinfo=SGT)   # +1 min
    main._beat("us_scan")
    main._beat("news_scan")
    check(f"[3] beats inside the throttle window do not write ({len(writes) - n_before} writes)",
          len(writes) == n_before)
    check("[4] but they are still visible in memory immediately",
          "us_scan" in main._heartbeats and "news_scan" in main._heartbeats)

    _Clock._t = _dt.datetime(2026, 9, 9, 3, 41, 0, tzinfo=SGT)   # +6 min, past 5
    main._beat("us_scan")
    check(f"[5] a beat past the throttle window writes again ({len(writes)} total)",
          len(writes) == n_before + 1)
    check("[6] the write carries every watcher, not just the one that beat",
          set(json.loads(store[main._HEARTBEAT_STATE_KEY])) == {"us_entry_scan", "us_scan", "news_scan"})

    # ── The restart: memory is wiped, the row is not ─────────────────────────
    persisted = store[main._HEARTBEAT_STATE_KEY]
    main._heartbeats.clear()
    main._heartbeats_written = None
    # A watcher that beats before restore finishes must win over the stored copy.
    _Clock._t = _dt.datetime(2026, 9, 9, 10, 0, 0, tzinfo=SGT)
    main._heartbeats["us_scan"] = "2026-09-09T10:00:00+08:00"
    n = main._restore_heartbeats()

    check(f"[7] a fresher in-process beat is not overwritten by the stored one ({main._heartbeats['us_scan']})",
          main._heartbeats["us_scan"] == "2026-09-09T10:00:00+08:00")
    check(f"[8] the other beats came back across the restart ({n} restored)",
          main._heartbeats.get("us_entry_scan", "").startswith("2026-09-09T03:35")
          and main._heartbeats.get("news_scan", "").startswith("2026-09-09T03:36"))

    # ── /health must still be zero-work, and must show the restored beats ────
    reads: list[str] = []
    db.get_state = lambda k: (reads.append(k), store.get(k))[1]
    payload = {"ok": True, "scans": main._heartbeats}   # shape /health returns
    check("[10] /health serves the restored beats without a database read",
          payload["scans"]["us_entry_scan"].startswith("2026-09-09T03:35") and not reads)

    # ── Failure must never reach a watcher ───────────────────────────────────
    def boom(*a, **k):
        raise RuntimeError("neon is down")

    db.set_state = boom
    _Clock._t = _dt.datetime(2026, 9, 9, 11, 0, 0, tzinfo=SGT)
    try:
        main._beat("crypto_scan")
        survived = True
    except Exception:
        survived = False
    check("[9] a database failure does not kill the beat", survived)
    check("[11] and the beat is still recorded in memory",
          main._heartbeats.get("crypto_scan", "").startswith("2026-09-09T11:00"))

    db.set_state = fake_set_state
    store[main._HEARTBEAT_STATE_KEY] = "{ not json"
    main._heartbeats.clear()
    check("[12] corrupt stored JSON restores nothing rather than crashing",
          main._restore_heartbeats() == 0)

    store[main._HEARTBEAT_STATE_KEY] = json.dumps(["not", "a", "dict"])
    check("[13] a stored value of the wrong shape restores nothing",
          main._restore_heartbeats() == 0)

    del store[main._HEARTBEAT_STATE_KEY]
    check("[14] a first-ever boot with no row restores nothing",
          main._restore_heartbeats() == 0)

finally:
    db.set_state, db.get_state, main.datetime = _orig
    main._heartbeats.clear()
    main._heartbeats_written = None

print("\n" + "=" * 60)
print(f"{passed} passed, {len(failed)} failed")
if failed:
    print("\n".join(f"  - {f}" for f in failed))
sys.exit(1 if failed else 0)
