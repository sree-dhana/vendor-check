"""Watching vendor emails, with the user's permission.

The agent has one mailbox. Each user gets a private forwarding address on it using plus
addressing (agent+<token>@gmail.com); the token tells the agent whose email it is. The user
sets up forwarding in their own email provider (the agent never asks for access to the
user's inbox). Emails are read over IMAP, a capped number per run, each processed once, and
only emails about vendors the user chose to watch are acted on. Email text is processed in
memory and never stored by the agent.

Checked 2026-11-07:
- Gmail IMAP: imap.gmail.com, port 993, implicit TLS
  (https://developers.google.com/workspace/gmail/imap/imap-smtp). Google prefers OAuth;
  app passwords need 2-Step Verification (https://support.google.com/accounts/answer/185833).
- Gmail forwarding is confirmed by a link sent to the forwarding address, and only-some-mail
  forwarding uses a filter (https://support.google.com/mail/answer/10957)."""
import email
import email.policy
import imaplib
import os
import re
import secrets
import time
from email.utils import getaddresses, parseaddr
from urllib.parse import urlparse

import detect

GOOGLE_FWD_SENDER = "forwarding-noreply@google.com"
GOOGLE_LINK_HOSTS = {"mail-settings.google.com", "mail.google.com"}
SILENCE_DAYS = 30
URL_RE = re.compile(r"https://[^\s<>\"')\]]+")


# ---------- config ----------

def config(env=None):
    env = os.environ if env is None else env
    def num(name, default):
        try:
            return int(env.get(name, default))
        except ValueError:
            return default
    return {
        "host": env.get("MAILBOX_IMAP_HOST", "").strip(),
        "port": num("MAILBOX_IMAP_PORT", 993),
        "user": env.get("MAILBOX_USER", "").strip(),
        "password": env.get("MAILBOX_PASSWORD", "").strip(),
        "address": (env.get("MAILBOX_ADDRESS", "") or env.get("MAILBOX_USER", "")).strip().lower(),
        "max_per_run": max(1, num("MAILBOX_MAX_PER_RUN", 20)),
        "poll_sec": max(30, num("MAILBOX_POLL_SEC", 120)),
        "delete_after": str(env.get("MAILBOX_DELETE_AFTER", "0")).strip() == "1",
    }


def configured(env=None):
    c = config(env)
    return bool(c["host"] and c["user"] and c["password"] and "@" in c["address"])


# ---------- per-user watch state ----------

def _get(store, key, default):
    try:
        v = store.get(key, default)
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


def watch_state(store, owner):
    return _get(store, "watch:%s" % owner, {})


def _save_state(store, owner, state):
    store.set("watch:%s" % owner, state)


def forwarding_address(base, token):
    user, domain = base.split("@", 1)
    return "%s+%s@%s" % (user.split("+", 1)[0], token, domain)


def owner_for_token(store, token):
    return _get(store, "watch_tokens", {}).get(token)


def start_watching(store, owner, vendor, env=None, now=None):
    """The user said yes to watching this vendor's emails. Returns (status, address)."""
    if not (vendor.get("emails") or vendor.get("domains")):
        return "no_email", None
    if not configured(env):
        return "no_mailbox", None
    now = time.time() if now is None else now
    state = watch_state(store, owner)
    if not state.get("token"):
        token = secrets.token_hex(5)
        state["token"] = token
        tokens = _get(store, "watch_tokens", {})
        tokens[token] = owner
        store.set("watch_tokens", tokens)
    ids = state.setdefault("vendors", [])
    if vendor["id"] not in ids:
        ids.append(vendor["id"])
    state.setdefault("consented_at", now)
    state.setdefault("permissions", {})[vendor["id"]] = now
    _save_state(store, owner, state)
    return "ok", forwarding_address(config(env)["address"], state["token"])


def stop_watching(store, owner, vendor):
    state = watch_state(store, owner)
    ids = state.get("vendors", [])
    if vendor["id"] not in ids:
        return False
    ids.remove(vendor["id"])
    state.get("permissions", {}).pop(vendor["id"], None)
    _save_state(store, owner, state)
    return True


def forget_all(store, owner):
    state = watch_state(store, owner)
    tokens = _get(store, "watch_tokens", {})
    tokens.pop(state.get("token"), None)
    store.set("watch_tokens", tokens)
    _save_state(store, owner, {})


def gmail_steps(address, vendor):
    """Exact steps, from Gmail's help pages (checked 2026-11-07)."""
    senders = vendor.get("emails") or ["@" + d for d in vendor.get("domains", [])]
    return [
        "1. In Gmail, click **Settings** (top right), then **See all settings**, then the **Forwarding "
        "and POP/IMAP** tab.",
        "2. Click **Add a forwarding address**, enter `%s`, then **Next**, **Proceed** and **OK**." % address,
        "3. Gmail sends a confirmation link to that address. **I'll show it to you here** when it "
        "arrives; open it to confirm. (Leave \"Forward a copy of incoming mail\" off, so only this "
        "vendor's mail is forwarded.)",
        "4. In the Gmail search box, click **Show search options**, put `%s` in **From**, then click "
        "**Create filter**, tick **Forward it to**, choose `%s`, and click **Create filter**."
        % (" OR ".join(senders), address),
        "5. Forward me one email from %s as a test. When it arrives I'll say \"Watching vendor emails now\"."
        % vendor["name"],
    ]


# ---------- reading mail ----------

def fetch_new(env=None, imap_factory=None):
    """Network only (safe to run off the event loop): fetch up to max_per_run unread emails
    as raw bytes and mark them read (and deleted, if MAILBOX_DELETE_AFTER=1)."""
    c = config(env)
    factory = imap_factory or imaplib.IMAP4_SSL
    conn = factory(c["host"], c["port"])
    out = []
    try:
        conn.login(c["user"], c["password"])
        conn.select("INBOX")
        typ, data = conn.search(None, "UNSEEN")
        ids = (data[0] or b"").split()[:c["max_per_run"]] if typ == "OK" else []
        for i in ids:
            typ, msg_data = conn.fetch(i, "(RFC822)")
            if typ == "OK" and msg_data and isinstance(msg_data[0], tuple):
                out.append(msg_data[0][1])
            conn.store(i, "+FLAGS", "\\Seen")
            if c["delete_after"]:
                conn.store(i, "+FLAGS", "\\Deleted")
        if c["delete_after"] and ids:
            conn.expunge()
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return out


def _token_from(msg):
    for header in ("Delivered-To", "X-Forwarded-To", "To", "Cc"):
        for _, addr in getaddresses(msg.get_all(header, [])):
            m = re.search(r"\+([0-9a-f]{10})@", addr.lower())
            if m:
                return m.group(1)
    return None


def _plain_body(msg):
    part = msg.get_body(preferencelist=("plain", "html")) if hasattr(msg, "get_body") else None
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
    return text


def as_text(msg):
    """Headers the detector needs, then the plain body. A manually forwarded email keeps
    its forwarded block in the body, which detect.parse_email reads instead."""
    lines = []
    for h in ("From", "Reply-To", "Subject", "Message-ID"):
        if msg.get(h):
            lines.append("%s: %s" % (h, msg.get(h)))
    return "\n".join(lines) + "\n\n" + _plain_body(msg)


def google_dkim_pass(msg):
    """True only if Gmail's own receiving server (the first Authentication-Results header,
    added on arrival) recorded a DKIM pass for google.com. Headers inside the message
    cannot fake this, because the receiving server's header comes first."""
    results = msg.get_all("Authentication-Results", [])
    if not results:
        return False
    first = " ".join(str(results[0]).split()).lower()
    return first.startswith("mx.google.com") and re.search(
        r"dkim=pass[^;]*header\.(?:i=@|d=)google\.com\b", first) is not None


def forwarding_confirmation(msg):
    """Gmail's forwarding confirmation, if this genuinely is one: {code, link}. Else None."""
    sender = parseaddr(msg.get("From", ""))[1].lower()
    if sender != GOOGLE_FWD_SENDER or not google_dkim_pass(msg):
        return None
    subject = msg.get("Subject", "")
    code = re.search(r"#(\d{6,12})", subject)
    link = None
    for url in URL_RE.findall(_plain_body(msg)):
        if urlparse(url).hostname in GOOGLE_LINK_HOSTS:
            link = url
            break
    return {"code": code.group(1) if code else None, "link": link}


def handle_message(store, raw, now=None):
    """Process one fetched email. Returns [(owner, kind, payload)] where kind is
    'text' (a ready message) or 'email' (a detect result for format.email_alert)."""
    now = time.time() if now is None else now
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    token = _token_from(msg)
    owner = owner_for_token(store, token) if token else None
    if not owner:
        return []  # not addressed to a known user: ignored, nothing stored
    state = watch_state(store, owner)
    out = []
    conf = forwarding_confirmation(msg)
    if conf is not None:
        text = "Gmail sent a confirmation for your forwarding address."
        if conf["link"]:
            text += " Open this Google link to confirm it: %s" % conf["link"]
        if conf["code"]:
            text += " Confirmation code: `%s`." % conf["code"]
        return [(owner, "text", text)]
    if not state.get("confirmed_at"):
        state["confirmed_at"] = now
        out.append((owner, "text", "Watching vendor emails now: your forwarding works."))
    state["last_email_at"] = now
    _save_state(store, owner, state)
    parsed = detect.parse_email(as_text(msg))
    res = detect.process_email(store, owner, parsed=parsed, now=now, vendor_ids=state.get("vendors", []))
    if res["status"] == "case_opened" or (res["status"] == "no_change" and res["sender_flags"]):
        out.append((owner, "email", res))
    return out


def silence_notices(store, owner, now=None):
    """If nothing has arrived for SILENCE_DAYS, say so (once per period)."""
    now = time.time() if now is None else now
    state = watch_state(store, owner)
    if not state.get("vendors") or not state.get("consented_at"):
        return []
    since = max(state.get("last_email_at", 0), state.get("silence_noticed_at", 0), state["consented_at"])
    if now - since < SILENCE_DAYS * 86400:
        return []
    state["silence_noticed_at"] = now
    _save_state(store, owner, state)
    return ["I'm still watching your vendors' emails, but nothing has arrived in %d days. If you "
            "changed email settings, check that the forwarding filter is still on." % SILENCE_DAYS]


def watching_owners(store):
    return list(_get(store, "watch_tokens", {}).values())
