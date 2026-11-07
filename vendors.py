"""Opt-in vendor records per sender, and bank-change detection. Pure, no network.

Saved only when the user asks. A vendor record holds: an id, display and legal name, country,
tax and bank IDs (GSTIN, PAN, IFSC, IBAN, VAT), the last 4 digits of an account number plus a
salted hash of the full number (used only to notice a change; the full number is never stored),
a saved phone number in E.164 form, a contact person, expected sender emails and domains, a
consent record, and timestamps. The IDs from a sender's most recent check are kept for 30
minutes so `save vendor <name>` can use them. Message text is never stored."""
import hashlib
import re
import secrets
import time
import unicodedata

import countries
from checks import check_gstin, check_pan
from iban import IBAN_LENGTHS, check_iban, mod97, normalize as normalize_iban

MAX_VENDORS = 50
MAX_NAME = 40
MAX_TEXT = 80
MAX_EMAILS = 5
LAST_CHECK_TTL = 1800

FIELDS = ("gstin", "pan", "ifsc", "iban", "vat", "acct_last4")  # IDs a check can carry
DETAILS = ("legal_name", "country", "phone", "contact")
COMPARED = ("gstin", "ifsc", "iban", "acct_last4")
FIELD_LABELS = {"gstin": "GSTIN", "pan": "PAN", "ifsc": "IFSC", "iban": "IBAN", "vat": "VAT",
                "acct_last4": "Account", "phone": "Phone"}

ACCOUNT_RE = re.compile(
    r"\b(?:a/c|acct|account)\.?\s*(?:no\.?|number|num|#)?\s*[:#-]?\s*(\d[\d -]{7,22}\d)\b", re.I
)
NAME_END = re.compile(
    r"[:,;\n]|\b(?:gstin|gstn|gst|pan|ifsc|ifs|iban|vat|pincode|pin|a/c|acct|account|phone|mobile|"
    r"tel|email|e-mail|contact|legal name|country|domain|whatsapp)\b", re.I)

# Labelled details. Longer alternatives first; ID labels are listed only so a value stops there.
SEG_RE = re.compile(
    r"(?<![\w@.])(legal name|registered name|contact person|contact number|contact|phone|mobile|mob|"
    r"telephone|tel|whatsapp|e-?mail|country|vat number|vat no|vat|iban|domain|"
    r"gstin|gstn|gst|pan|ifsc|account|acct|a/c|pincode|pin)\b\.?\s*(?:no\.?\s*)?[:#=-]?\s*", re.I)
SEG_KIND = {
    "legal name": "legal_name", "registered name": "legal_name", "contact person": "contact",
    "contact": "contact", "contact number": "phone", "phone": "phone", "mobile": "phone", "mob": "phone",
    "telephone": "phone", "tel": "phone", "whatsapp": "phone", "email": "email", "e-mail": "email",
    "country": "country", "vat number": "vat", "vat no": "vat", "vat": "vat", "iban": "iban",
    "domain": "domain",
}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
DOMAIN_RE = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}")
IBAN_IN_TEXT = re.compile(r"\b[A-Za-z]{2}\d{2}(?:[ ]?[A-Za-z0-9]{4}){2,7}(?:[ ]?[A-Za-z0-9]{1,4})?\b")


# ---------- small helpers ----------

def account_digits(text):
    m = ACCOUNT_RE.search(text or "")
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return digits if 9 <= len(digits) <= 18 else None


def account_last4(text):
    digits = account_digits(text)
    return digits[-4:] if digits else None


def _acct_hash(salt, digits):
    return hashlib.sha256(("%s:%s" % (salt, digits)).encode()).hexdigest()[:32]


def clean_name(name, limit=MAX_NAME):
    name = "".join(ch for ch in (name or "") if not unicodedata.category(ch).startswith("C"))
    return " ".join(name.split())[:limit].strip()


def name_part(text):
    """The vendor name at the start of `text`: everything before the first keyword,
    punctuation or ID-like word."""
    words = []
    for w in NAME_END.split(text or "", 1)[0].split():
        if sum(c.isdigit() for c in w) >= 4 or "@" in w:
            break
        words.append(w)
    return clean_name(" ".join(words))


def normalize_phone(raw, country=None):
    """E.164 (+ and 8 to 15 digits), or None. Numbers without a country code are accepted
    only for India (10-digit mobiles, or 0 + 10 digits)."""
    s = (raw or "").strip()
    digits = re.sub(r"\D", "", s)
    if s.startswith("00"):
        digits, s = digits[2:], "+" + s[2:]
    if s.startswith("+"):
        return "+" + digits if 8 <= len(digits) <= 15 else None
    if country in (None, "IN"):
        if len(digits) == 10 and digits[0] in "6789":
            return "+91" + digits
        if len(digits) == 11 and digits[0] == "0":
            return "+91" + digits[1:]
        if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
            return "+" + digits
    return None


def mask(field, value):
    if field == "acct_last4":
        return "••••" + value
    if field == "phone":
        return value[:3] + "•" * max(len(value) - 7, 0) + value[-4:]
    if len(value) <= 6:
        return value
    return value[:4] + "•" * (len(value) - 6) + value[-2:]


def find_iban(text):
    """An IBAN with valid check digits anywhere in the text, else None."""
    for m in IBAN_IN_TEXT.finditer(text or ""):
        cand = normalize_iban(m.group(0))
        if cand[:2] in IBAN_LENGTHS and len(cand) == IBAN_LENGTHS[cand[:2]] and mod97(cand) == 1:
            return cand
    return None


def parse_details(text, default_country=None):
    """Read labelled vendor details from a message. Returns (details, problems).
    details may include: legal_name, contact, phone, country, vat, iban, emails, domains,
    acct_digits (never stored) and acct_last4."""
    details, problems = {}, []
    matches = list(SEG_RE.finditer(text or ""))
    for i, m in enumerate(matches):
        kind = SEG_KIND.get(m.group(1).lower())
        if not kind:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        value = text[m.end():end].split("\n")[0].strip(" ,;")
        if not value:
            continue
        if kind in ("legal_name", "contact"):
            details[kind] = clean_name(value, MAX_TEXT)
        elif kind == "country":
            code = countries.resolve(value)
            if code:
                details["country"] = code
            else:
                problems.append("I didn't recognise the country \"%s\"." % clean_name(value))
        elif kind == "vat":
            details["vat"] = re.sub(r"[\s\-.]", "", value).upper()[:20]
        elif kind == "iban":
            details["iban"] = normalize_iban(value)[:34]
        elif kind == "domain":
            dm = DOMAIN_RE.search(value.lower())
            if dm:
                details.setdefault("domains", []).append(dm.group(0))
        elif kind == "phone":
            details["phone_raw"] = value

    emails = []
    for e in EMAIL_RE.findall(text or ""):
        e = e.lower().rstrip(".")
        if e not in emails:
            emails.append(e)
    if emails:
        details["emails"] = emails[:MAX_EMAILS]
        domains = details.setdefault("domains", [])
        for e in emails:
            d = e.split("@", 1)[1]
            if d not in domains:
                domains.append(d)
    if "domains" in details:
        details["domains"] = details["domains"][:MAX_EMAILS]

    if "iban" not in details:
        iban = find_iban(text)
        if iban:
            details["iban"] = iban

    digits = account_digits(text)
    if digits:
        details["acct_digits"] = digits
        details["acct_last4"] = digits[-4:]

    country = details.get("country") or (details["iban"][:2] if details.get("iban") and
                                         countries.resolve(details["iban"][:2]) else None) or default_country
    if country:
        details["country"] = country
    if "phone_raw" in details:
        raw = details.pop("phone_raw")
        phone = normalize_phone(raw, country)
        if phone:
            details["phone"] = phone
        else:
            problems.append("I couldn't read the phone number. Write it with the country code, "
                            "for example `phone +44 20 7946 0000` or `phone +91 98765 43210`.")
    return details, problems


def id_warnings(ids):
    """Offline sanity checks to show when saving. Never blocks a save."""
    out = []
    for field, fn in (("gstin", check_gstin), ("pan", check_pan), ("iban", check_iban)):
        if ids.get(field):
            r = fn(ids[field])
            if r["level"] == "fail":
                out.append("%s `%s`: %s" % (FIELD_LABELS[field], ids[field], r["text"]))
    return out


# ---------- storage ----------

def _get(store, key, default):
    try:
        v = store.get(key, default)
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


def _vendors(store, sender):
    data = _get(store, "vendors:%s" % sender, {})
    for key, rec in data.items():
        if "id" not in rec:  # records saved before v3: a stable id derived from the key
            rec["id"] = "v" + hashlib.sha256(("%s|%s" % (sender, key)).encode()).hexdigest()[:8]
    return data


def list_vendors(store, sender):
    return sorted(_vendors(store, sender).values(), key=lambda r: r["name"].casefold())


def get_vendor(store, sender, name):
    name = clean_name(name)
    return _vendors(store, sender).get(name.casefold()) if name else None


def get_vendor_by_id(store, sender, vendor_id):
    for rec in _vendors(store, sender).values():
        if rec["id"] == vendor_id:
            return rec
    return None


def find_by_gstin(store, sender, gstin):
    for rec in _vendors(store, sender).values():
        if gstin and rec.get("gstin") == gstin:
            return rec
    return None


def find_by_iban(store, sender, iban):
    for rec in _vendors(store, sender).values():
        if iban and rec.get("iban") == iban:
            return rec
    return None


def save_vendor(store, sender, name, ids, now=None):
    """Create a vendor or merge new details into an existing one.
    Return 'saved', 'updated', 'no_name', 'no_ids' or 'full'."""
    name = clean_name(name)
    if not name:
        return "no_name"
    useful = [k for k in FIELDS + DETAILS + ("acct_digits", "emails", "domains") if ids.get(k)]
    if not useful:
        return "no_ids"
    data = _vendors(store, sender)
    key = name.casefold()
    if key not in data and len(data) >= MAX_VENDORS:
        return "full"
    now = time.time() if now is None else now
    status = "updated" if key in data else "saved"
    rec = data.get(key) or {
        "id": "v" + secrets.token_hex(4),
        "created_at": now,
        "consent": {"status": "not_requested"},
    }
    rec["name"] = name

    old_phone = rec.get("phone")
    for k in FIELDS + DETAILS:
        if k == "acct_last4" or not ids.get(k):
            continue
        rec[k] = ids[k]
    consent = rec.get("consent") or {}
    if old_phone and rec.get("phone") != old_phone and consent.get("status") in ("granted", "pending"):
        # consent covers one number: a new number needs a new answer from the vendor
        rec["consent"] = {"status": "pending", "version": consent.get("version"), "phone": rec["phone"],
                          "note": "the saved phone changed, so ask the vendor again"}
    digits = ids.get("acct_digits")
    if digits:
        rec.setdefault("acct_salt", secrets.token_hex(8))
        rec["acct_hash"] = _acct_hash(rec["acct_salt"], digits)
        rec["acct_last4"] = digits[-4:]
    elif ids.get("acct_last4") and ids["acct_last4"] != rec.get("acct_last4"):
        rec["acct_last4"] = ids["acct_last4"]
        rec.pop("acct_hash", None)  # the old hash belonged to a different number
    for k in ("emails", "domains"):
        if ids.get(k):
            merged = list(rec.get(k, []))
            merged += [v for v in ids[k] if v not in merged]
            rec[k] = merged[-MAX_EMAILS:]
    rec["updated_at"] = now
    data[key] = rec
    store.set("vendors:%s" % sender, data)
    return status


def set_fields(store, sender, vendor_id, **fields):
    """Set extra fields on a vendor record (used by the guided demo to mark its sample vendor)."""
    data = _vendors(store, sender)
    for rec in data.values():
        if rec["id"] == vendor_id:
            rec.update(fields)
            store.set("vendors:%s" % sender, data)
            return rec
    return None


def forget_demo(store, sender):
    """Delete the guided demo's sample vendor and cases. Real data is never touched."""
    data = _vendors(store, sender)
    demo_ids = [k for k, rec in data.items() if rec.get("demo")]
    for k in demo_ids:
        data.pop(k)
    store.set("vendors:%s" % sender, data)
    cases_data = _get(store, "cases:%s" % sender, {})
    for cid in [cid for cid, c in cases_data.items() if c.get("demo")]:
        cases_data.pop(cid)
    store.set("cases:%s" % sender, cases_data)
    return len(demo_ids)


def apply_changes(store, sender, vendor_id, changes, now=None):
    """Write an approved case's changes into the vendor record. Called only by
    cases.approve, after the user has approved with the right code."""
    data = _vendors(store, sender)
    for key, rec in data.items():
        if rec["id"] != vendor_id:
            continue
        for c in changes:
            if c["field"] == "acct_last4":
                rec["acct_last4"] = c["new"]
                if c.get("new_hash"):
                    rec["acct_hash"] = c["new_hash"]
                else:
                    rec.pop("acct_hash", None)
            else:
                rec[c["field"]] = c["new"]
        rec["updated_at"] = time.time() if now is None else now
        store.set("vendors:%s" % sender, data)
        return True
    return False


def account_change(store, sender, vendor_id, digits):
    """A case change entry for a new account number, carrying only the last 4 digits and a
    fingerprint made with this vendor's salt (so approval never needs the full number).
    Creates and saves the vendor's salt if it has none yet."""
    data = _vendors(store, sender)
    rec = next((r for r in data.values() if r["id"] == vendor_id), None)
    if rec is None:
        return None
    if not rec.get("acct_salt"):
        rec["acct_salt"] = secrets.token_hex(8)
        store.set("vendors:%s" % sender, data)
    return {"field": "acct_last4", "old": rec.get("acct_last4"), "new": digits[-4:],
            "new_hash": _acct_hash(rec["acct_salt"], digits)}


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
    store.set("profile:%s" % sender, {})
    store.set("cases:%s" % sender, {})
    return n


def remember_last(store, sender, ids, now=None):
    rec = {k: ids[k] for k in FIELDS if ids.get(k)}  # never acct_digits
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
    fields present both in the saved record and in this check. When both sides have a full
    account number, the salted hashes are compared, so a different number with the same
    last 4 digits still counts as a change."""
    changed, same = [], []
    for f in COMPARED:
        old, new = rec.get(f), ids.get(f)
        if f == "acct_last4" and ids.get("acct_digits") and rec.get("acct_hash") and rec.get("acct_salt"):
            if _acct_hash(rec["acct_salt"], ids["acct_digits"]) == rec["acct_hash"]:
                same.append(f)
            else:
                new_last4 = ids["acct_digits"][-4:]
                changed.append((f, old, new_last4 if new_last4 != old else
                                new_last4 + " (a different number with the same last 4 digits)"))
            continue
        if old and new:
            if old == new:
                same.append(f)
            else:
                changed.append((f, old, new))
    return changed, same
