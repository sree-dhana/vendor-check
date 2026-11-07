"""Notifications the agent sends without being asked (case alerts, call results, reminders,
expiry, email-watching notes).

It is not yet verified that ASI:One shows unprompted agent messages, so every notice is
also (1) kept for a short "while you were away" digest shown with the user's next reply,
and (2) optionally emailed to an address the user has verified with a code. Backup emails
are short on purpose: case id, vendor name and what to do, never bank details.

Gmail SMTP: smtp.gmail.com, port 465 with implicit TLS
(https://developers.google.com/workspace/gmail/imap/imap-smtp, checked 2026-11-07)."""
import os
import re
import secrets
import smtplib
import time
from email.message import EmailMessage

MAX_NOTICES = 20
CODE_TTL = 3600
MAX_CODE_ATTEMPTS = 5
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")


def _get(store, key, default):
    try:
        v = store.get(key, default)
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


# ---------- the digest ----------

def record(store, owner, short, now=None):
    """Keep a one-line version of a notice for the next reply's digest."""
    notices = _get(store, "notices:%s" % owner, [])
    notices.append({"at": time.time() if now is None else now, "text": short})
    store.set("notices:%s" % owner, notices[-MAX_NOTICES:])


def take_digest(store, owner):
    """Lines for notices since the user's last message, then clear them."""
    notices = _get(store, "notices:%s" % owner, [])
    if not notices:
        return []
    store.set("notices:%s" % owner, [])
    lines = ["**While you were away** (in case these didn't reach you):", ""]
    lines.extend("- %s" % n["text"] for n in notices)
    return lines + ["", "---", ""]


def short_text(text):
    """A one-line version of a notice for the digest and backup email: first line, without
    Markdown, at most 200 characters."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    first = re.sub(r"[*_`📩]", "", first).strip()
    return first[:200]


# ---------- backup email ----------

def settings(store, owner):
    return _get(store, "notify:%s" % owner, {})


def _save(store, owner, s):
    store.set("notify:%s" % owner, s)


def smtp_config(env=None):
    env = os.environ if env is None else env
    try:
        port = int(env.get("MAILBOX_SMTP_PORT", 465))
    except ValueError:
        port = 465
    user = env.get("MAILBOX_USER", "").strip()
    return {"host": env.get("MAILBOX_SMTP_HOST", "smtp.gmail.com").strip(), "port": port,
            "user": user, "password": env.get("MAILBOX_PASSWORD", "").strip(),
            "from": (env.get("MAILBOX_ADDRESS", "") or user).strip()}


def email_available(env=None):
    c = smtp_config(env)
    return bool(c["host"] and c["user"] and c["password"])


def send_email(to, subject, body, env=None, smtp_factory=None, reply_to=None, msg_id=None,
               from_name="VendorCheck"):
    c = smtp_config(env)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "%s <%s>" % (from_name, c["from"]), to, subject
    if reply_to:
        msg["Reply-To"] = reply_to
    if msg_id:
        msg["Message-ID"] = msg_id
    msg.set_content(body)
    factory = smtp_factory or smtplib.SMTP_SSL
    with factory(c["host"], c["port"], timeout=15) as s:
        s.login(c["user"], c["password"])
        s.send_message(msg)


def request_email(store, owner, address, now=None, env=None, sender=None):
    """Start opt-in: email a 6-digit code to the address. Returns a status."""
    sender = sender or send_email  # looked up at call time, so it can be replaced in tests
    address = (address or "").strip().lower()
    if not EMAIL_RE.match(address):
        return "bad_address"
    if not email_available(env):
        return "unavailable"
    now = time.time() if now is None else now
    code = "%06d" % secrets.randbelow(10 ** 6)
    try:
        sender(address, "Your VendorCheck code: %s" % code,
               "Someone asked VendorCheck to send backup notifications to this address.\n\n"
               "If that was you, reply to VendorCheck in ASI:One with:  verify email %s\n\n"
               "If it wasn't you, ignore this email; nothing will be sent." % code, env)
    except Exception:
        return "send_failed"
    s = settings(store, owner)
    s.update({"pending": address, "code": code, "code_expires": now + CODE_TTL, "attempts": 0})
    _save(store, owner, s)
    return "sent"


def verify(store, owner, code, now=None):
    now = time.time() if now is None else now
    s = settings(store, owner)
    if not s.get("pending"):
        return "nothing_pending"
    if now > s.get("code_expires", 0) or s.get("attempts", 0) >= MAX_CODE_ATTEMPTS:
        for k in ("pending", "code", "code_expires", "attempts"):
            s.pop(k, None)
        _save(store, owner, s)
        return "expired"
    if (code or "").strip() != s["code"]:
        s["attempts"] = s.get("attempts", 0) + 1
        _save(store, owner, s)
        return "wrong"
    s["email"] = s.pop("pending")
    for k in ("code", "code_expires", "attempts"):
        s.pop(k, None)
    s["verified_at"] = now
    _save(store, owner, s)
    return "verified"


def turn_off(store, owner):
    _save(store, owner, {})


def backup_email_for(store, owner):
    return settings(store, owner).get("email")


def backup_body(short):
    return ("%s\n\nOpen VendorCheck in ASI:One for the details (say `my cases`). For your "
            "protection this email never contains bank details.\n\nTo stop these emails, say "
            "`notify off` to VendorCheck." % short)
