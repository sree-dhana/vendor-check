"""Message length cap and per-sender rate limits. Stores only message timestamps per
sender address, never message text."""
import time

MAX_CHARS = 1000
LLM_MAX_CHARS = 500  # the LLM path only ever sees this much of a message

MSG_LIMIT = (20, 600)    # 20 messages per 10 minutes
LLM_LIMIT = (5, 3600)    # 5 LLM calls per hour

TOO_LONG_TEXT = (
    "That message is too long for me (%d characters). Please send up to 1,000 characters, "
    "for example just the vendor's GSTIN, PAN and IFSC."
)
RATE_LIMITED_TEXT = (
    "You've sent a lot of messages in a short time. Please wait a few minutes and try again."
)


def too_long(text):
    return len(text or "") > MAX_CHARS


def allow(store, bucket, sender, limit, now=None):
    """Record one use of `bucket` by `sender` if it is within `limit` (count, window_secs)."""
    count, window = limit
    now = time.time() if now is None else now
    key = "rate:%s:%s" % (bucket, sender)
    try:
        stamps = store.get(key, [])
        if not isinstance(stamps, list):
            stamps = []
    except Exception:
        stamps = []
    stamps = [s for s in stamps if isinstance(s, (int, float)) and now - s < window]
    if len(stamps) >= count:
        return False
    stamps.append(now)
    try:
        store.set(key, stamps)
    except Exception:
        pass
    return True


def allow_message(store, sender, now=None):
    return allow(store, "msg", sender, MSG_LIMIT, now)


def allow_llm(store, sender, now=None):
    return allow(store, "llm", sender, LLM_LIMIT, now)
