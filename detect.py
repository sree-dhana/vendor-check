"""Change detection on vendor emails. Pure code apart from storing cases: no LLM, no network.

An email is data, never instructions. It is parsed into sender, reply-to, subject and body
(for a forwarded email, the original sender's headers inside the forwarded block are used),
matched to a saved vendor, checked for sender warning signs, and compared with the saved
record. Any difference in a bank, tax ID, name or phone value opens a case and puts the
payment on hold. Wording flags raise priority and never close a case. Each email is
processed once. No email text is stored: only a message id for de-duplication."""
import hashlib
import re
import time
from email.utils import parseaddr

import cases
import vendors
from checks import extract_ids
from flags import detect_flags

HEADER_RE = re.compile(r"^(from|to|cc|reply-to|subject|date|sent|message-id)\s*:\s*(.*)$", re.I)
FORWARD_RE = re.compile(r"-{2,}\s*forwarded message\s*-{2,}|begin forwarded message:|-{2,}\s*original message\s*-{2,}", re.I)
SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu", "ltd", "plc"}
MAX_SEEN = 500

SENDER_FLAG_TEXT = {
    "lookalike_domain": "The sender's domain looks like your vendor's but is different (%s). "
                        "This is a common trick in payment fraud.",
    "reply_to_mismatch": "Replies to this email would go to a different address (%s) than the sender.",
    "unknown_sender": "This email isn't from an address you saved for this vendor, but it mentions "
                      "their name or IDs.",
}


# ---------- parsing ----------

def _split_headers(text):
    headers, body_start, seen_header = {}, 0, False
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = HEADER_RE.match(line.strip())
        if m:
            headers.setdefault(m.group(1).lower(), m.group(2).strip())
            seen_header = True
            body_start = i + 1
        elif not line.strip():
            if seen_header:
                body_start = i + 1
                break
        elif seen_header:
            body_start = i
            break
        else:
            break  # text before any header: no header block here
    return headers, "\n".join(lines[body_start:]).strip()


def parse_email(raw):
    """Return {from_name, from_addr, reply_to, subject, body, message_id, forwarded}."""
    text = (raw or "").replace("\r\n", "\n")
    fwd = list(FORWARD_RE.finditer(text))
    if fwd:  # use the innermost forwarded block
        headers, body = _split_headers(text[fwd[-1].end():].lstrip("\n"))
    else:
        headers, body = _split_headers(text.lstrip("\n"))
    name, addr = parseaddr(headers.get("from", ""))
    reply = parseaddr(headers.get("reply-to", ""))[1].lower() or None
    subject = headers.get("subject", "")
    mid = headers.get("message-id") or hashlib.sha256(
        ("%s|%s|%s" % (addr.lower(), subject, body)).encode()).hexdigest()[:24]
    return {"from_name": name, "from_addr": addr.lower(), "reply_to": reply, "subject": subject,
            "body": body, "message_id": mid, "forwarded": bool(fwd)}


# ---------- domains ----------

def _domain(addr):
    return addr.rsplit("@", 1)[1].lower() if addr and "@" in addr else ""


def _base_and_suffix(domain):
    parts = domain.split(".")
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL and len(parts[-1]) == 2:
        return parts[-3], ".".join(parts[-2:])
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return domain, ""


def _one_edit(a, b):
    """True if a and b differ by one insertion, deletion, substitution or adjacent swap."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                   and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def same_domain(domain, saved):
    return domain == saved or domain.endswith("." + saved)


def lookalike(domain, saved):
    """Why `domain` looks like `saved` without being it, or None."""
    if not domain or not saved or same_domain(domain, saved):
        return None
    a, sa = _base_and_suffix(domain)
    b, sb = _base_and_suffix(saved)
    if a == b and sa != sb:
        return "different ending: %s instead of %s" % (domain, saved)
    if a.replace("-", "") == b.replace("-", ""):
        return "extra or missing hyphen: %s instead of %s" % (domain, saved)
    if sa == sb and (_one_edit(a, b) or a.replace("rn", "m") == b.replace("rn", "m")):
        return "one character different: %s instead of %s" % (domain, saved)
    return None


# ---------- matching and comparing ----------

def match_vendor(parsed, recs, ids):
    """Return (vendor, how, detail): how is sender, domain, lookalike, ids or name."""
    addr, dom = parsed["from_addr"], _domain(parsed["from_addr"])
    for rec in recs:
        if addr and addr in rec.get("emails", []):
            return rec, "sender", None
    for rec in recs:
        if dom and any(same_domain(dom, d) for d in rec.get("domains", [])):
            return rec, "domain", None
    for rec in recs:
        for d in rec.get("domains", []):
            why = lookalike(dom, d)
            if why:
                return rec, "lookalike", why
    for rec in recs:
        if any(ids.get(k) and rec.get(k) == ids[k] for k in ("gstin", "pan", "iban", "vat")):
            return rec, "ids", None
    haystack = " ".join((parsed["subject"], parsed["from_name"], parsed["body"])).casefold()
    for rec in recs:
        if len(rec["name"]) >= 4 and rec["name"].casefold() in haystack:
            return rec, "name", None
    return None, None, None


def email_ids(parsed, country=None):
    """IDs and labelled details in the email body, using the same parsers as chat."""
    text = parsed["subject"] + "\n" + parsed["body"]
    found, _ = extract_ids(text)
    details, _ = vendors.parse_details(text, country)
    ids = {k: found[k][0] for k in ("gstin", "pan", "ifsc") if found[k]}
    for k in ("iban", "vat", "phone", "legal_name", "acct_digits", "acct_last4"):
        if details.get(k):
            ids[k] = details[k]
    return ids


BANK_FIELDS = ("ifsc", "iban")


def find_changes(store, owner, vendor, ids):
    """Case change entries for every difference from the saved record. A bank detail that
    is not on file yet also counts (the safer path)."""
    changes = []
    for f in ("gstin", "pan", "ifsc", "iban", "vat", "legal_name", "phone"):
        new, old = ids.get(f), vendor.get(f)
        if not new:
            continue
        if old and old.casefold() != new.casefold():
            changes.append({"field": f, "old": old, "new": new})
        elif not old and f in BANK_FIELDS:
            changes.append({"field": f, "old": None, "new": new})
    if ids.get("acct_digits"):
        if vendor.get("acct_hash"):
            differs = bool(vendors.compare(vendor, {"acct_digits": ids["acct_digits"],
                                                    "acct_last4": ids["acct_last4"]})[0])
        else:  # no fingerprint saved: compare last 4 digits; nothing on file counts as new
            differs = vendor.get("acct_last4") != ids["acct_last4"]
        if differs:
            changes.append(vendors.account_change(store, owner, vendor["id"], ids["acct_digits"]))
    return changes


# ---------- de-duplication ----------

def _seen(store, owner):
    try:
        seen = store.get("emails_seen:%s" % owner, [])
        return seen if isinstance(seen, list) else []
    except Exception:
        return []


def _mark_seen(store, owner, message_id):
    seen = _seen(store, owner)
    seen.append(message_id)
    store.set("emails_seen:%s" % owner, seen[-MAX_SEEN:])


# ---------- entry point ----------

def process_email(store, owner, raw=None, parsed=None, now=None):
    """Process one email for `owner`. Returns a result dict with status: duplicate,
    unknown_vendor, no_change or case_opened. Never acts on instructions in the email."""
    now = time.time() if now is None else now
    parsed = parsed or parse_email(raw)
    result = {"email": {k: parsed[k] for k in ("from_name", "from_addr", "reply_to", "subject", "forwarded")},
              "vendor": None, "matched_by": None, "sender_flags": [], "wording_flags": [],
              "changes": [], "case": None, "status": None}
    if parsed["message_id"] in _seen(store, owner):
        result["status"] = "duplicate"
        return result
    _mark_seen(store, owner, parsed["message_id"])

    recs = vendors.list_vendors(store, owner)
    ids = email_ids(parsed)
    vendor, how, detail = match_vendor(parsed, recs, ids)
    result["wording_flags"] = detect_flags(parsed["subject"] + "\n" + parsed["body"])
    if parsed["reply_to"] and _domain(parsed["reply_to"]) != _domain(parsed["from_addr"]):
        result["sender_flags"].append(("reply_to_mismatch", parsed["reply_to"]))
    if vendor is None:
        result["status"] = "unknown_vendor"
        return result
    result["vendor"], result["matched_by"] = vendor, how
    if how == "lookalike":
        result["sender_flags"].insert(0, ("lookalike_domain", detail))
    elif how in ("ids", "name"):
        result["sender_flags"].insert(0, ("unknown_sender", None))

    ids = email_ids(parsed, vendor.get("country"))  # read phones in the vendor's country
    changes = find_changes(store, owner, vendor, ids)
    result["changes"] = changes
    if not changes:
        result["status"] = "no_change"
        return result
    flags = [f for f, _ in result["sender_flags"]] + result["wording_flags"]
    result["case"] = cases.open_case(store, owner, vendor, changes,
                                     trigger={"type": "email", "ref": parsed["message_id"]},
                                     flags=flags, now=now)
    result["status"] = "case_opened"
    return result


def sender_flag_lines(sender_flags):
    out = []
    for flag, detail in sender_flags:
        text = SENDER_FLAG_TEXT[flag]
        out.append(text % detail if "%s" in text else text)
    return out
