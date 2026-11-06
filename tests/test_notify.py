"""v3 step 10: notifications - digest of missed notices and opt-in backup email."""
import pytest

import format as fmt
import limits
import notify
from conftest import MemStore
from flags import contains_banned

ENV = {"MAILBOX_IMAP_HOST": "imap.gmail.com", "MAILBOX_USER": "vendorcheck.agent@gmail.com",
       "MAILBOX_PASSWORD": "app-pass"}


@pytest.fixture
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


class Outbox:
    def __init__(self):
        self.sent = []

    def __call__(self, to, subject, body, env=None):
        self.sent.append((to, subject, body))


# ---------- digest ----------

def test_missed_notices_lead_the_next_reply_once(no_net):
    store = MemStore()
    notify.record(store, "me", "Reminder: case CABC23 for Sharma Traders is still waiting.")
    out = fmt.reply_for_text("hello", store, sender="me")
    assert out.startswith("**While you were away** (in case these didn't reach you):")
    assert "- Reminder: case CABC23 for Sharma Traders is still waiting." in out
    assert out.endswith(fmt.HELP_TEXT)
    assert fmt.reply_for_text("hello", store, sender="me") == fmt.HELP_TEXT  # shown once


def test_digest_is_per_user_and_capped(no_net):
    store = MemStore()
    for i in range(notify.MAX_NOTICES + 5):
        notify.record(store, "me", "notice %d" % i)
    assert fmt.reply_for_text("hello", store, sender="other") == fmt.HELP_TEXT
    out = fmt.reply_for_text("hello", store, sender="me")
    assert "notice 4" not in out and "notice 5" in out and "notice 24" in out


def test_rate_limited_reply_keeps_the_digest(no_net):
    store = MemStore()
    notify.record(store, "me", "something happened")
    for i in range(limits.MSG_LIMIT[0]):
        limits.allow_message(store, "me", now=1)
    assert fmt.reply_for_text("hello", store, sender="me", now=2) == limits.RATE_LIMITED_TEXT
    assert "something happened" in fmt.reply_for_text("hello", store, sender="me", now=1000)


def test_short_text():
    assert notify.short_text("📩 **New forwarded email**\n\nmore") == "New forwarded email"
    assert notify.short_text("Case `C123AB` for **X** expired.") == "Case C123AB for X expired."
    assert len(notify.short_text("x" * 500)) == 200


# ---------- backup email ----------

def test_email_opt_in_with_code(env):
    store, outbox = MemStore(), Outbox()
    assert notify.request_email(store, "me", "Owner@Example.com", now=0, sender=outbox) == "sent"
    to, subject, body = outbox.sent[0]
    code = subject.rsplit(" ", 1)[1]
    assert to == "owner@example.com" and len(code) == 6 and ("verify email %s" % code) in body
    assert notify.backup_email_for(store, "me") is None  # not active before verifying
    assert notify.verify(store, "me", "000000" if code != "000000" else "111111", now=1) == "wrong"
    assert notify.verify(store, "me", code, now=2) == "verified"
    assert notify.backup_email_for(store, "me") == "owner@example.com"


def test_code_expires_and_attempts_are_capped(env):
    store, outbox = MemStore(), Outbox()
    notify.request_email(store, "me", "a@b.co", now=0, sender=outbox)
    code = outbox.sent[0][1].rsplit(" ", 1)[1]
    assert notify.verify(store, "me", code, now=notify.CODE_TTL + 1) == "expired"
    notify.request_email(store, "me", "a@b.co", now=0, sender=outbox)
    code = outbox.sent[1][1].rsplit(" ", 1)[1]
    wrong = "%06d" % ((int(code) + 1) % 10 ** 6)
    for _ in range(notify.MAX_CODE_ATTEMPTS):
        notify.verify(store, "me", wrong, now=1)
    assert notify.verify(store, "me", code, now=2) == "expired"
    assert notify.backup_email_for(store, "me") is None


def test_bad_address_and_unavailable(monkeypatch):
    store = MemStore()
    assert notify.request_email(store, "me", "not-an-email", env=ENV, sender=Outbox()) == "bad_address"
    assert notify.request_email(store, "me", "a@b.co", env={}, sender=Outbox()) == "unavailable"


def test_send_failure_is_reported(env):
    def boom(*a, **k):
        raise OSError("smtp down")
    assert notify.request_email(MemStore(), "me", "a@b.co", sender=boom) == "send_failed"


def test_chat_commands(env, monkeypatch):
    outbox = Outbox()
    monkeypatch.setattr(notify, "send_email", outbox)
    store = MemStore()
    assert "No backup email is set" in fmt.reply_for_text("notify status", store, sender="me")
    out = fmt.reply_for_text("notify email owner@example.com", store, sender="me", now=0)
    assert "I've emailed a 6-digit code to **owner@example.com**" in out
    code = outbox.sent[0][1].rsplit(" ", 1)[1]
    assert "doesn't match" in fmt.reply_for_text("verify email 000001" if code != "000001" else "verify email 000002",
                                                 store, sender="me", now=1)
    assert "✅ Backup emails are on: **owner@example.com**" in fmt.reply_for_text(
        "verify email %s" % code, store, sender="me", now=2)
    assert "Backup emails go to **owner@example.com**" in fmt.reply_for_text("notify status", store, sender="me")
    assert "Backup emails are off" in fmt.reply_for_text("notify off", store, sender="me")
    assert notify.backup_email_for(store, "me") is None


def test_send_email_uses_gmail_smtp_ssl(env):
    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            seen["addr"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, user, pw):
            seen["login"] = (user, pw)

        def send_message(self, msg):
            seen["msg"] = msg

    notify.send_email("owner@example.com", "VendorCheck: test", "body", smtp_factory=FakeSMTP)
    assert seen["addr"] == ("smtp.gmail.com", 465)
    assert seen["login"] == ("vendorcheck.agent@gmail.com", "app-pass")
    assert seen["msg"]["To"] == "owner@example.com"


def test_backup_email_has_no_bank_details():
    res = {"status": "case_opened", "vendor": {"name": "Sharma Traders"},
           "case": {"id": "CABC23", "changes": [{"field": "ifsc", "old": "YESB0DNB002", "new": "SBIN0000001"}]}}
    short = fmt.email_alert_short(res)
    body = notify.backup_body(short)
    assert "CABC23" in body and "Sharma Traders" in body
    for detail in ("SBIN", "YESB", "IFSC", "account"):
        assert detail not in short
    assert not contains_banned(body)


def test_forget_all_clears_notifications(env, monkeypatch):
    outbox = Outbox()
    monkeypatch.setattr(notify, "send_email", outbox)
    store = MemStore()
    fmt.reply_for_text("notify email owner@example.com", store, sender="me", now=0)
    code = outbox.sent[0][1].rsplit(" ", 1)[1]
    fmt.reply_for_text("verify email %s" % code, store, sender="me", now=1)
    notify.record(store, "me", "pending notice")
    out = fmt.reply_for_text("forget all", store, sender="me")
    assert notify.backup_email_for(store, "me") is None
    assert "pending notice" not in fmt.reply_for_text("hello", store, sender="me")
    assert out  # the forget-all reply itself
