"""Vendor consent for automated verification calls. Plain code, no LLM.

The agent never contacts the vendor. It drafts a request for the user to send, then reads
the vendor's reply that the user pastes back. Calls and recording are separate yes/no
answers. Consent applies only to the saved phone number it was given for; a reply from an
address that isn't saved for the vendor is not trusted until the user confirms it by
calling the saved number. Consent never makes a call result trustworthy and never removes
the user's approval step.

Statuses: not_requested, pending, granted, declined, revoked."""
import re
import time

import vendors

WORDING_VERSION = "consent-v1-2026-11"
CALLS_RE = re.compile(r"\bcalls?\b\s*[:\-=]?\s*(yes|no)\b", re.I)
RECORDING_RE = re.compile(r"\brecord(?:ing|ed)?\b\s*[:\-=]?\s*(yes|no)\b", re.I)
NUMBERED_RE = re.compile(r"^\s*([12])\s*[.):\-]\s*(yes|no)\b", re.I | re.M)
FROM_RE = re.compile(r"^\s*from\s*:\s*(.+)$", re.I | re.M)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def draft_request(vendor, business_name=None):
    """Plain text, no links, for the user to send from their own email or read out on a call."""
    who = vendor.get("contact") or vendor["name"]
    phone = vendor.get("phone") or "<the phone number we have saved for you>"
    sign = business_name or "<your business name>"
    return "\n".join([
        "Subject: Checking requests to change your bank details",
        "",
        "Hello %s," % who,
        "",
        "To protect payments between us, we check every request to change your bank details "
        "before we pay.",
        "",
        "If we receive such a request, an automated assistant may call %s on our behalf to ask "
        "whether you sent it. The call will say that it is automated and on our behalf. It will "
        "only ask you to confirm the request and read back the last few digits of the new "
        "account; it will never ask for passwords, codes or full account numbers." % phone,
        "",
        "Please reply with:",
        "1. CALLS: YES or NO. May we place this automated verification call to %s?" % phone,
        "2. RECORDING: YES or NO. May that call be recorded and transcribed?",
        "",
        "You can say NO and ask for a call from a person instead, and you can withdraw your "
        "answer at any time by telling us.",
        "",
        "Thank you,",
        sign,
    ])


def parse_reply(text):
    """Return (calls, recording, sender): calls/recording are True, False or None (not
    answered); sender is the From address if the paste includes one."""
    calls = recording = None
    m = CALLS_RE.search(text or "")
    if m:
        calls = m.group(1).lower() == "yes"
    m = RECORDING_RE.search(text or "")
    if m:
        recording = m.group(1).lower() == "yes"
    for num, ans in NUMBERED_RE.findall(text or ""):
        if num == "1" and calls is None:
            calls = ans.lower() == "yes"
        if num == "2" and recording is None:
            recording = ans.lower() == "yes"
    sender = None
    m = FROM_RE.search(text or "")
    if m:
        em = EMAIL_RE.search(m.group(1))
        sender = em.group(0).lower() if em else None
    return calls, recording, sender


def sender_is_saved(vendor, sender):
    if not sender:
        return None  # unknown: pasted without headers
    dom = sender.rsplit("@", 1)[1]
    return sender in vendor.get("emails", []) or any(
        dom == d or dom.endswith("." + d) for d in vendor.get("domains", []))


def _update(store, owner, vendor_id, consent):
    data = vendors._vendors(store, owner)
    for rec in data.values():
        if rec["id"] == vendor_id:
            rec["consent"] = consent
            store.set("vendors:%s" % owner, data)
            return rec
    return None


def mark_requested(store, owner, vendor, now=None):
    now = time.time() if now is None else now
    return _update(store, owner, vendor["id"], {
        "status": "pending", "requested_at": now, "version": WORDING_VERSION,
        "phone": vendor.get("phone")})


def record_reply(store, owner, vendor, text, now=None):
    """Store the vendor's answers. Returns (status, consent, warnings)."""
    now = time.time() if now is None else now
    calls, recording, sender = parse_reply(text)
    warnings = []
    if calls is None:
        return "unanswered", vendor.get("consent") or {}, [
            "I couldn't find a clear YES or NO for calls in that reply. Ask the vendor to answer "
            "`CALLS: YES` or `CALLS: NO`."]
    saved = sender_is_saved(vendor, sender)
    consent = {
        "status": "granted" if calls else "declined",
        "call": calls,
        "recording": bool(recording) if calls else False,
        "phone": vendor.get("phone"),
        "date": now,
        "version": (vendor.get("consent") or {}).get("version", WORDING_VERSION),
        "replied_by": sender or "pasted by you (sender not shown)",
        "sender_saved": saved,
    }
    if recording is None and calls:
        warnings.append("The reply didn't answer the recording question, so calls won't be recorded.")
    if not vendor.get("phone"):
        consent["status"] = "pending"
        warnings.append("This vendor has no saved phone number, so the consent can't apply to one yet. "
                        "Save their number first.")
    if saved is False and calls:
        consent["status"] = "pending"
        consent["proposed"] = {"call": calls, "recording": consent["recording"]}
        warnings.append("This reply came from %s, which isn't an address you saved for %s. I haven't "
                        "accepted it. Call %s on the saved number to check, then say `confirm consent %s`."
                        % (sender, vendor["name"], vendor["name"], vendor["name"]))
    _update(store, owner, vendor["id"], consent)
    return consent["status"], consent, warnings


def confirm(store, owner, vendor, now=None):
    """The user checked a pending reply by calling the saved number."""
    consent = dict(vendor.get("consent") or {})
    proposed = consent.pop("proposed", None)
    if consent.get("status") != "pending" or not proposed:
        return "nothing_to_confirm", consent
    if not vendor.get("phone"):
        return "no_phone", consent
    consent.update(proposed)
    consent.update({"status": "granted" if proposed["call"] else "declined", "phone": vendor["phone"],
                    "confirmed_by_user_at": time.time() if now is None else now, "sender_saved": True})
    _update(store, owner, vendor["id"], consent)
    return consent["status"], consent


def revoke(store, owner, vendor, now=None):
    consent = dict(vendor.get("consent") or {})
    consent.update({"status": "revoked", "call": False, "recording": False,
                    "revoked_at": time.time() if now is None else now})
    consent.pop("proposed", None)
    _update(store, owner, vendor["id"], consent)
    return consent


def describe(consent):
    status = (consent or {}).get("status", "not_requested")
    if status == "granted":
        return "granted for automated calls to %s; recording %s" % (
            consent.get("phone"), "allowed" if consent.get("recording") else "not allowed")
    if status == "pending" and consent.get("proposed"):
        return "pending: a reply came from an address you didn't save; confirm it by calling the saved number"
    if status == "pending" and consent.get("note"):
        return "pending: %s" % consent["note"]
    return status.replace("_", " ")
