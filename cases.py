"""Cases: what happens when someone asks to change a saved vendor's details.

A case holds the payment while the change is checked. Only code moves a case between
states, along the fixed TRANSITIONS table, and every move is written to the case's audit
trail. Only the user can approve or reject, and approval needs the case id plus the last 4
characters of the new bank detail. The audit trail never contains message text.

States: RECEIVED -> ON_HOLD -> CALL_SCHEDULED -> CALLING -> CALL_DONE | NO_ANSWER |
CALL_FAILED | HUMAN_CALLBACK -> AWAITING_USER -> APPROVED | REJECTED | EXPIRED"""
import os
import secrets
import time

import vendors

RECEIVED, ON_HOLD, CALL_SCHEDULED, CALLING = "RECEIVED", "ON_HOLD", "CALL_SCHEDULED", "CALLING"
CALL_DONE, NO_ANSWER, CALL_FAILED, HUMAN_CALLBACK = "CALL_DONE", "NO_ANSWER", "CALL_FAILED", "HUMAN_CALLBACK"
AWAITING_USER, APPROVED, REJECTED, EXPIRED = "AWAITING_USER", "APPROVED", "REJECTED", "EXPIRED"

TERMINAL = {APPROVED, REJECTED, EXPIRED}
USER_ONLY = {APPROVED, REJECTED}

# Allowed moves (besides REJECTED and EXPIRED, which any open case may move to).
TRANSITIONS = {
    RECEIVED: {ON_HOLD},
    ON_HOLD: {CALL_SCHEDULED, HUMAN_CALLBACK, AWAITING_USER},
    CALL_SCHEDULED: {CALLING, HUMAN_CALLBACK},
    CALLING: {CALL_DONE, NO_ANSWER, CALL_FAILED},
    NO_ANSWER: {CALL_SCHEDULED, HUMAN_CALLBACK, AWAITING_USER},
    CALL_FAILED: {CALL_SCHEDULED, HUMAN_CALLBACK, AWAITING_USER},
    CALL_DONE: {AWAITING_USER},
    HUMAN_CALLBACK: {AWAITING_USER},
    AWAITING_USER: {APPROVED},
}

MAX_CALL_ATTEMPTS = 3  # the first call plus up to 2 retries
MAX_FAILED_APPROVALS = 5
MAX_CASES = 100
ID_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L


class InvalidTransition(Exception):
    pass


def expiry_hours(env=None):
    env = os.environ if env is None else env
    try:
        return max(1, int(env.get("CASE_EXPIRY_HOURS", "72")))
    except ValueError:
        return 72


def _key(owner):
    return "cases:%s" % owner


def _load(store, owner):
    try:
        data = store.get(_key(owner), {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(store, owner, data):
    if len(data) > MAX_CASES:  # drop the oldest closed cases first
        closed = sorted((c for c in data.values() if c["state"] in TERMINAL), key=lambda c: c["created_at"])
        for c in closed[:len(data) - MAX_CASES]:
            data.pop(c["id"], None)
    store.set(_key(owner), data)


def _new_id(existing):
    while True:
        cid = "C" + "".join(secrets.choice(ID_CHARS) for _ in range(5))
        if cid not in existing:
            return cid


def _log(case, frm, to, by, note, now):
    case["audit"].append({"at": now, "from": frm, "to": to, "by": by, "note": note})
    case["updated_at"] = now


def confirm_code(case):
    """What the user must quote to approve: the last 4 characters of the new bank detail
    (account, else IBAN, else IFSC, else the first changed value)."""
    by_field = {c["field"]: c["new"] for c in case["changes"]}
    for f in ("acct_last4", "iban", "ifsc"):
        if by_field.get(f):
            return by_field[f][-4:].upper()
    return (case["changes"][0]["new"][-4:] if case["changes"] else "").upper()


def confirm_label(case):
    fields = {c["field"] for c in case["changes"]}
    if "acct_last4" in fields:
        return "the last 4 digits of the new account number"
    if "iban" in fields:
        return "the last 4 characters of the new IBAN"
    if "ifsc" in fields:
        return "the last 4 characters of the new IFSC"
    return "the last 4 characters of the new %s" % vendors.FIELD_LABELS.get(
        case["changes"][0]["field"], "value") if case["changes"] else "the case code"


def open_case(store, owner, vendor, changes, trigger=None, flags=(), now=None):
    """Open a case for proposed changes to a saved vendor and put it ON_HOLD.
    changes: [{"field", "old", "new"}] plus "new_hash" for an account change.
    trigger: {"type": "email"|"chat", "ref": message id}. Never message text."""
    now = time.time() if now is None else now
    data = _load(store, owner)
    case = {
        "id": _new_id(data),
        "owner": owner,
        "vendor_id": vendor["id"],
        "vendor_name": vendor["name"],
        "demo": bool(vendor.get("demo")),  # sample data from the guided demo
        "state": RECEIVED,
        "trigger": dict(trigger or {"type": "chat"}),
        "changes": [dict(c) for c in changes],
        "flags": list(flags),
        "priority": "high" if flags else "normal",
        "call_attempts": [],
        "outcome": None,
        "summary": None,
        "decision": None,
        "failed_approvals": 0,
        "audit": [],
        "created_at": now,
        "updated_at": now,
        "expires_at": now + expiry_hours() * 3600,
    }
    _log(case, None, RECEIVED, "system", "case opened", now)
    data[case["id"]] = case
    transition_case(case, ON_HOLD, "system", "payment on hold until you decide", now)
    _save(store, owner, data)
    return case


def transition_case(case, to, by="system", note="", now=None, _approval=False):
    """Move a case (in memory) along an allowed edge, or raise InvalidTransition.
    APPROVED is reachable only through approve(), which checks the confirmation code."""
    now = time.time() if now is None else now
    frm = case["state"]
    if frm in TERMINAL:
        raise InvalidTransition("case %s is already %s" % (case["id"], frm))
    if to in USER_ONLY and by != "user":
        raise InvalidTransition("only the user can move a case to %s" % to)
    if to == APPROVED and not _approval:
        raise InvalidTransition("approval must go through approve() with the confirmation code")
    if to not in TRANSITIONS.get(frm, set()) and to not in (REJECTED, EXPIRED):
        raise InvalidTransition("%s -> %s is not allowed" % (frm, to))
    if to == CALL_SCHEDULED and len(case["call_attempts"]) >= MAX_CALL_ATTEMPTS:
        raise InvalidTransition("no more call attempts for case %s" % case["id"])
    case["state"] = to
    _log(case, frm, to, by, note, now)
    return case


def transition(store, owner, case_id, to, by="system", note="", now=None):
    data = _load(store, owner)
    case = data.get((case_id or "").upper())
    if not case:
        raise KeyError(case_id)
    transition_case(case, to, by, note, now)
    _save(store, owner, data)
    return case


def get_case(store, owner, case_id):
    """Cases are stored per owner, so another sender can never see or act on them."""
    return _load(store, owner).get((case_id or "").upper())


def list_cases(store, owner, open_only=True):
    cases = sorted(_load(store, owner).values(), key=lambda c: c["created_at"], reverse=True)
    return [c for c in cases if not open_only or c["state"] not in TERMINAL]


def record_call_attempt(store, owner, case_id, status, now=None):
    now = time.time() if now is None else now
    data = _load(store, owner)
    case = data[case_id.upper()]
    case["call_attempts"].append({"at": now, "status": status})
    _log(case, case["state"], case["state"], "system", "call attempt: %s" % status, now)
    _save(store, owner, data)
    return case


def approve(store, owner, case_id, code, now=None):
    """User approval. Returns (status, case): 'approved', 'already_approved', 'wrong_code',
    'locked' (too many wrong codes), 'not_ready', 'closed' or 'not_found'. Only an approved
    case changes the vendor record."""
    now = time.time() if now is None else now
    data = _load(store, owner)
    case = data.get((case_id or "").upper())
    if not case:
        return "not_found", None
    if case["state"] == APPROVED:
        return "already_approved", case
    if case["state"] in TERMINAL:
        return "closed", case
    if case["state"] != AWAITING_USER:
        return "not_ready", case
    if case["failed_approvals"] >= MAX_FAILED_APPROVALS:
        return "locked", case
    if (code or "").strip().upper() != confirm_code(case):
        case["failed_approvals"] += 1
        _log(case, case["state"], case["state"], "user", "approval failed: wrong code", now)
        _save(store, owner, data)
        return "wrong_code", case
    vendors.apply_changes(store, owner, case["vendor_id"], case["changes"], now)
    case["decision"] = {"decision": "approved", "at": now}
    transition_case(case, APPROVED, "user", "user approved; vendor record updated", now, _approval=True)
    _save(store, owner, data)
    return "approved", case


def reject(store, owner, case_id, now=None):
    """User rejection: the saved details stay as they are."""
    now = time.time() if now is None else now
    data = _load(store, owner)
    case = data.get((case_id or "").upper())
    if not case:
        return "not_found", None
    if case["state"] == REJECTED:
        return "already_rejected", case
    if case["state"] in TERMINAL:
        return "closed", case
    case["decision"] = {"decision": "rejected", "at": now}
    transition_case(case, REJECTED, "user", "user rejected; saved details unchanged", now)
    _save(store, owner, data)
    return "rejected", case


def expire_due(store, owner, now=None):
    """Expire open cases past their deadline. Changes nothing on the vendor record."""
    now = time.time() if now is None else now
    data = _load(store, owner)
    expired = []
    for case in data.values():
        if case["state"] not in TERMINAL and now >= case["expires_at"]:
            transition_case(case, EXPIRED, "system", "expired without a decision; nothing changed", now)
            expired.append(case)
    if expired:
        _save(store, owner, data)
    return expired


def forget_all(store, owner):
    store.set(_key(owner), {})
