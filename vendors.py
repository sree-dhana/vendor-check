"""Opt-in vendor memory per sender, and bank-change detection. Pure, no network.

Saved only when the user asks: a name, GSTIN, PAN, IFSC and the last 4 digits of an
account number (never the full number). The IDs from a sender's most recent check are
kept for 30 minutes so `save vendor <name>` can use them. Message text is never stored."""
import re
import time
import unicodedata

MAX_VENDORS = 50
MAX_NAME = 40
LAST_CHECK_TTL = 1800

FIELDS = ("gstin", "pan", "ifsc", "acct_last4")
COMPARED = ("gstin", "ifsc", "acct_last4")
FIELD_LABELS = {"gstin": "GSTIN", "pan": "PAN", "ifsc": "IFSC", "acct_last4": "Account"}

ACCOUNT_RE = re.compile(
    r"\b(?:a/c|acct|account)\.?\s*(?:no\.?|number|num|#)?\s*[:#-]?\s*(\d[\d -]{7,22}\d)\b", re.I
)
NAME_END = re.compile(r"[:,;\n]|\b(?:gstin|gstn|gst|pan|ifsc|ifs|pincode|pin|a/c|acct|account)\b", re.I)


def account_last4(text):
    m = ACCOUNT_RE.search(text or "")
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return digits[-4:] if 9 <= len(digits) <= 18 else None


def clean_name(name):
    name = "".join(ch for ch in (name or "") if not unicodedata.category(ch).startswith("C"))
    return " ".join(name.split())[:MAX_NAME].strip()


def name_part(text):
    """The vendor name at the start of `text`: everything before the first ID keyword,
    punctuation or ID-like word."""
    words = []
    for w in NAME_END.split(text or "", 1)[0].split():
        if sum(c.isdigit() for c in w) >= 4:
            break
        words.append(w)
    return clean_name(" ".join(words))


def mask(field, value):
    if field == "acct_last4":
        return "••••" + value
    if len(value) <= 6:
        return value
    return value[:4] + "•" * (len(value) - 6) + value[-2:]


def _get(store, key, default):
    try:
        v = store.get(key, default)
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


def _vendors(store, sender):
    return _get(store, "vendors:%s" % sender, {})


def list_vendors(store, sender):
    return sorted(_vendors(store, sender).values(), key=lambda r: r["name"].casefold())


def get_vendor(store, sender, name):
    name = clean_name(name)
    return _vendors(store, sender).get(name.casefold()) if name else None


def find_by_gstin(store, sender, gstin):
    for rec in _vendors(store, sender).values():
        if gstin and rec.get("gstin") == gstin:
            return rec
    return None


def save_vendor(store, sender, name, ids):
    """Return 'saved', 'updated', 'no_name', 'no_ids' or 'full'."""
    name = clean_name(name)
    if not name:
        return "no_name"
    rec = {k: ids[k] for k in FIELDS if ids.get(k)}
    if not rec:
        return "no_ids"
    data = _vendors(store, sender)
    key = name.casefold()
    if key not in data and len(data) >= MAX_VENDORS:
        return "full"
    status = "updated" if key in data else "saved"
    rec["name"] = name
    data[key] = rec
    store.set("vendors:%s" % sender, data)
    return status


def forget(store, sender, name):
    data = _vendors(store, sender)
    if data.pop(clean_name(name).casefold(), None) is None:
        return False
    store.set("vendors:%s" % sender, data)
    return True


def forget_all(store, sender):
    n = len(_vendors(store, sender))
    store.set("vendors:%s" % sender, {})
    store.set("last:%s" % sender, {})
    store.set("batch:%s" % sender, {})
    return n


def remember_last(store, sender, ids, now=None):
    rec = {k: ids[k] for k in FIELDS if ids.get(k)}
    if rec:
        rec["t"] = time.time() if now is None else now
        store.set("last:%s" % sender, rec)


def last_ids(store, sender, now=None):
    rec = _get(store, "last:%s" % sender, {})
    now = time.time() if now is None else now
    if not rec or now - rec.get("t", 0) > LAST_CHECK_TTL:
        return {}
    return {k: rec[k] for k in FIELDS if rec.get(k)}


def compare(rec, ids):
    """Return (changed, same): changed is [(field, old, new)], same is [field], over the
    fields present both in the saved record and in this check."""
    changed, same = [], []
    for f in COMPARED:
        old, new = rec.get(f), ids.get(f)
        if old and new:
            if old == new:
                same.append(f)
            else:
                changed.append((f, old, new))
    return changed, same
