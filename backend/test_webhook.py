"""Checks the Telegram webhook that replaces the 24/7 long-poll.

Why this file exists: `telegram_command_listener` held a 20-second long-poll
open in an unconditional `while True`, so the process had to be alive around
the clock for a command to arrive at all. On Render's free tier that meant an
external pinger every 5 minutes, ~744 h/month against a 750 h workspace pot --
which is what suspended bus-bot, cashflow and trading together on 2026-08-30.
A webhook inverts it: Telegram's delivery is the inbound request that wakes the
app, and the app sleeps in between.

Two failure modes are guarded specifically because both have already happened
in this account:

  * bus-bot's "/ping answers 4 times" bug -- Telegram redelivers an update that
    isn't acked quickly, and a cold free instance is slow enough to miss the
    deadline. Hence ack-first and update_id dedupe.
  * Telegram answers getUpdates with 409 Conflict while a webhook is
    registered. If WEBHOOK_URL is ever unset, the fallback must clear the
    webhook first or every command silently disappears.

    python backend/test_webhook.py

Negative control: revert the `if webhook_url:` branch in lifespan, or drop the
dedupe from telegram_webhook, and this suite must fail.
"""
import asyncio
import inspect
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

import main  # noqa: E402
import telegram_bot  # noqa: E402
from fastapi import HTTPException  # noqa: E402

passed = 0
failed: list[str] = []


def check(name: str, cond: bool):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


class FakeRequest:
    """Only .json() is used by the route."""

    def __init__(self, payload, raise_on_json=False):
        self._payload = payload
        self._raise = raise_on_json

    async def json(self):
        if self._raise:
            raise ValueError("not json")
        return self._payload


def reset_state(webhook_url):
    main._tg_seen_ids.clear()
    main._tg_seen_order.clear()
    if webhook_url is None:
        main.os.environ.pop("WEBHOOK_URL", None)
    else:
        main.os.environ["WEBHOOK_URL"] = webhook_url


def run(coro):
    return asyncio.run(coro)


# -- [1] secret derivation ---------------------------------------------------
reset_state("https://trading-backend-wruf.onrender.com/tg/s3cr3t")
check("[1a] secret is the last path segment", main._webhook_path_secret() == "s3cr3t")

reset_state("https://trading-backend-wruf.onrender.com/tg/s3cr3t/")
check("[1b] trailing slash tolerated", main._webhook_path_secret() == "s3cr3t")

reset_state(None)
check("[1c] unset WEBHOOK_URL yields no secret", main._webhook_path_secret() == "")

reset_state("   ")
check("[1d] whitespace-only WEBHOOK_URL yields no secret", main._webhook_path_secret() == "")


# -- [2] the route stays shut without a configured secret --------------------
async def expect_404(secret, payload):
    try:
        await main.telegram_webhook(secret, FakeRequest(payload))
        return False
    except HTTPException as e:
        return e.status_code == 404


reset_state(None)
check("[2a] unconfigured webhook rejects any secret",
      run(expect_404("anything", {"update_id": 1, "message": {"text": "/health"}})))
check("[2b] unconfigured webhook rejects the empty secret",
      run(expect_404("", {"update_id": 2, "message": {"text": "/health"}})))

reset_state("https://x.onrender.com/tg/correct-secret")
check("[2c] wrong secret is rejected",
      run(expect_404("wrong-secret", {"update_id": 3, "message": {"text": "/health"}})))


# -- [3] dispatch, ack-first -------------------------------------------------
dispatched: list[str] = []


async def fake_handle(text: str):
    dispatched.append(text)


real_handle = main.handle_telegram_command
main.handle_telegram_command = fake_handle


async def deliver(secret, payload):
    r = await main.telegram_webhook(secret, FakeRequest(payload))
    # the command runs in a background task; let it start
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return r


reset_state("https://x.onrender.com/tg/sec")
dispatched.clear()
res = run(deliver("sec", {"update_id": 10, "message": {"text": "/health"}}))
check("[3a] correct secret is accepted", res == {"ok": True})
check("[3b] the command is dispatched", dispatched == ["/health"])

reset_state("https://x.onrender.com/tg/sec")
dispatched.clear()
run(deliver("sec", {"update_id": 11, "message": {"text": "hello there"}}))
check("[3c] plain text is not treated as a command", dispatched == [])

reset_state("https://x.onrender.com/tg/sec")
dispatched.clear()
run(deliver("sec", {"update_id": 12}))
check("[3d] an update with no message is survivable", dispatched == [])

reset_state("https://x.onrender.com/tg/sec")
dispatched.clear()
run(deliver("sec", {"update_id": 13, "message": {"text": None}}))
check("[3e] a null text is survivable", dispatched == [])

reset_state("https://x.onrender.com/tg/sec")
dispatched.clear()
res = run(main.telegram_webhook("sec", FakeRequest(None, raise_on_json=True)))
check("[3f] a non-JSON body is acked, not 500", res == {"ok": True} and dispatched == [])

reset_state("https://x.onrender.com/tg/sec")
dispatched.clear()
run(deliver("sec", {"update_id": 14, "message": {"text": "  /crypto  "}}))
check("[3g] surrounding whitespace is stripped", dispatched == ["/crypto"])


# -- [4] dedupe -- the "/ping answers 4 times" bug ---------------------------
reset_state("https://x.onrender.com/tg/sec")
dispatched.clear()
run(deliver("sec", {"update_id": 20, "message": {"text": "/health"}}))
run(deliver("sec", {"update_id": 20, "message": {"text": "/health"}}))
run(deliver("sec", {"update_id": 20, "message": {"text": "/health"}}))
check("[4a] a redelivered update_id runs once", dispatched == ["/health"])

reset_state("https://x.onrender.com/tg/sec")
dispatched.clear()
run(deliver("sec", {"update_id": 30, "message": {"text": "/health"}}))
run(deliver("sec", {"update_id": 31, "message": {"text": "/crypto"}}))
check("[4b] distinct updates both run", dispatched == ["/health", "/crypto"])

reset_state("https://x.onrender.com/tg/sec")
dispatched.clear()
run(deliver("sec", {"message": {"text": "/health"}}))
run(deliver("sec", {"message": {"text": "/health"}}))
check("[4c] a missing update_id is never deduped away", dispatched == ["/health", "/health"])

reset_state("https://x.onrender.com/tg/sec")
maxlen = main._tg_seen_order.maxlen
for i in range(maxlen + 50):
    main._tg_is_duplicate(i)
check("[4d] the dedupe window is bounded", len(main._tg_seen_ids) == maxlen)
check("[4e] the oldest id is evicted, not retained", main._tg_is_duplicate(0) is False)
check("[4f] a recent id is still remembered", main._tg_is_duplicate(maxlen + 49) is True)

main.handle_telegram_command = real_handle


# -- [5] the Telegram API calls themselves -----------------------------------
calls = []


class FakeResp:
    def read(self):
        return b'{"ok":true}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    calls.append((req.full_url, json.loads(req.data.decode()) if req.data else {}))
    return FakeResp()


real_token = telegram_bot.TOKEN
real_urlopen = telegram_bot.urllib.request.urlopen
telegram_bot.TOKEN = "TESTTOKEN"
telegram_bot.urllib.request.urlopen = fake_urlopen

calls.clear()
ok = telegram_bot.set_webhook("https://x.onrender.com/tg/sec")
check("[5a] set_webhook reports success", ok is True)
check("[5b] set_webhook hits setWebhook", bool(calls) and calls[0][0].endswith("/setWebhook"))
check("[5c] set_webhook sends the url", calls[0][1].get("url") == "https://x.onrender.com/tg/sec")
check("[5d] set_webhook asks only for messages", calls[0][1].get("allowed_updates") == ["message"])

calls.clear()
ok = telegram_bot.delete_webhook()
check("[5e] delete_webhook reports success", ok is True)
check("[5f] delete_webhook hits deleteWebhook", bool(calls) and calls[0][0].endswith("/deleteWebhook"))


def boom(req, timeout=None):
    raise OSError("network down")


telegram_bot.urllib.request.urlopen = boom
check("[5g] a failed set_webhook returns False, not an exception",
      telegram_bot.set_webhook("https://x/y") is False)

telegram_bot.TOKEN = ""
telegram_bot.urllib.request.urlopen = fake_urlopen
calls.clear()
check("[5h] no token means no call",
      telegram_bot.set_webhook("https://x/y") is False and calls == [])

telegram_bot.TOKEN = real_token
telegram_bot.urllib.request.urlopen = real_urlopen


# -- [6] lifespan wiring -----------------------------------------------------
src = inspect.getsource(main.lifespan)
check("[6a] lifespan reads WEBHOOK_URL", 'os.getenv("WEBHOOK_URL"' in src)
check("[6b] a configured webhook is registered", "telegram_bot.set_webhook(webhook_url)" in src)
check("[6c] the fallback clears the webhook first -- else getUpdates 409s",
      "telegram_bot.delete_webhook()" in src)
def _ordered(src_text, first, second):
    """index() raises if either side is missing, which turns a failed check into
    a traceback and hides every check after it. Fail, don't crash."""
    return first in src_text and second in src_text and src_text.index(first) < src_text.index(second)


check("[6d] the long-poll listener is started only in the fallback branch",
      _ordered(src, "telegram_bot.delete_webhook()",
               "asyncio.create_task(telegram_command_listener())"))
check("[6e] the listener is no longer started unconditionally",
      src.count("asyncio.create_task(telegram_command_listener())") == 1)
check("[6f] shutdown tolerates a listener that was never started",
      "if listener is not None:" in src)

# The polling path must survive as a real fallback, not a stub.
check("[6g] telegram_command_listener still exists", callable(main.telegram_command_listener))
check("[6h] get_updates still exists for the fallback", callable(telegram_bot.get_updates))


# -- [7] the frontend keep-warm ----------------------------------------------
# An ungated 5-minute poll is shorter than Render's 15-minute idle timer, so an
# open dashboard tab is a second 24/7 pinger and undoes the whole exercise.
try:
    app_tsx = open("../frontend/src/App.tsx", encoding="utf-8").read()
except FileNotFoundError:
    app_tsx = open("frontend/src/App.tsx", encoding="utf-8").read()
check("[7a] the poll is gated on visibility", "visibilitychange" in app_tsx)
check("[7b] the interval is cleared when hidden", "clearInterval" in app_tsx)
check("[7c] returning to the tab refreshes",
      "document.visibilityState === 'visible'" in app_tsx)

print("\n" + "=" * 60 + f"\n  {passed} passed, {len(failed)} failed")
if failed:
    for f in failed:
        print(f"    FAILED: {f}")
    sys.exit(1)
print("  All checks passed.")
