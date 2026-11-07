"""Automated consent requests: emailed only after the user confirms, reply recorded automatically."""
import json
from email.message import EmailMessage

import pytest

import consent
import format as fmt
import mailwatch
import notify
import vendors
import workflow
from conftest import MemStore
from flags import contains_banned

ENV = {"MAILBOX_IMAP_HOST": "imap.gmail.com", "MAILBOX_USER": "vendorcheck.agent@gmail.com",
       "MAILBOX_PASSWORD": "app-pass"}
PHONE = "+919876543210"
VENDOR_EMAIL = "accounts@sharmatraders.in"


class Outbox:
    def __init__(self):
        self.sent = []

    def __call__(self, to, subject, body, env=None, reply_to=None, msg_id=None, from_name="VendorCheck"):
        self.sent.append({"to": to, "subject": subject, "body": body, "reply_to": reply_to,
                          "msg_id": msg_id, "from_name": from_name})


@pytest.fixture
def outbox(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    box = Outbox()
    monkeypatch.setattr(notify, "send_email", box)
    return box


@pytest.fixture
def store(outbox):
    s = MemStore()
    vendors.save_vendor(s, "me", "Sharma Traders", {
        "gstin": "27AAPFU0939F1ZV", "country": "IN", "phone": PHONE, "contact": "Ravi Sharma",
        "emails": [VENDOR_EMAIL], "domains": ["sharmatraders.in"]}, now=0)
    return s


def _say(store, text, now=100):
    return fmt.reply_for_text(text, store, sender="me", now=now)


def _vendor(store):
    return vendors.get_vendor(store, "me", "Sharma Traders")


def _reply(to, frm, in_reply_to, body):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, to, "Re: Checking requests to change your bank details"
    m["In-Reply-To"], m["Message-ID"] = in_reply_to, "<reply-%s@mail.example>" % abs(hash(body))
    m.set_content(body)
    return m.as_bytes()


QUOTED = ("\n\nOn Mon, 9 Nov 2026 at 10:00, VendorCheck wrote:\n"
          "> Please reply with:\n> 1. CALLS: YES or NO. May we place this call?\n"
          "> 2. RECORDING: YES or NO. May that call be recorded?\n")


def test_nothing_is_sent_without_confirmation(store, outbox):
    out = _say(store, "consent request Sharma Traders")
    assert outbox.sent == []
    assert "say `send consent Sharma Traders`" in out and VENDOR_EMAIL in out
    assert "I only contact a vendor when you tell me to" in out


def test_send_without_seeing_the_draft_shows_it_first(store, outbox):
    out = _say(store, "send consent Sharma Traders")
    assert outbox.sent == [] and "say `send consent Sharma Traders` again" in out


def test_send_after_confirmation(store, outbox):
    _say(store, "business name Demo Traders Pvt Ltd")
    _say(store, "consent request Sharma Traders")
    out = _say(store, "send consent Sharma Traders")
    assert out.startswith("✅ Sent the consent request to **Sharma Traders** (%s)" % VENDOR_EMAIL)
    mail = outbox.sent[0]
    token = mailwatch.watch_state(store, "me")["token"]
    assert mail["to"] == VENDOR_EMAIL
    assert mail["reply_to"] == "vendorcheck.agent+%s@gmail.com" % token
    assert mail["from_name"] == "VendorCheck for Demo Traders Pvt Ltd"
    assert mail["subject"] == "Demo Traders Pvt Ltd: Checking requests to change your bank details"
    assert "Sent by VendorCheck on behalf of Demo Traders Pvt Ltd, at their request." in mail["body"]
    assert "1. CALLS: YES or NO" in mail["body"] and "Subject:" not in mail["body"]
    assert "http" not in mail["body"].lower() and not contains_banned(mail["body"])
    c = _vendor(store)["consent"]
    assert c["sent_to"] == VENDOR_EMAIL and c["request_msgid"] == mail["msg_id"] and c["status"] == "pending"
    assert mailwatch.watch_state(store, "me").get("vendors", []) == []  # not silently watching


def test_only_one_email_per_day(store, outbox):
    _say(store, "consent request Sharma Traders")
    _say(store, "send consent Sharma Traders", now=100)
    assert "I already emailed" in _say(store, "send consent Sharma Traders", now=200)
    _say(store, "send consent Sharma Traders", now=100 + consent.RESEND_AFTER + 1)
    assert len(outbox.sent) == 2


def test_requires_email_phone_and_mailbox(store, outbox, monkeypatch):
    vendors.save_vendor(store, "me", "NoMail", {"gstin": "27AAPFU0939F1ZV", "phone": PHONE}, now=0)
    assert "Save an email address for **NoMail**" in _say(store, "send consent NoMail")
    vendors.save_vendor(store, "me", "NoPhone", {"gstin": "27AAPFU0939F1ZV", "emails": ["a@b.co"]}, now=0)
    assert "Save a phone number for **NoPhone**" in _say(store, "send consent NoPhone")
    monkeypatch.delenv("MAILBOX_PASSWORD")
    assert "isn't switched on" in _say(store, "send consent Sharma Traders")
    assert outbox.sent == []


def _sent(store, outbox):
    _say(store, "consent request Sharma Traders")
    _say(store, "send consent Sharma Traders")
    return outbox.sent[-1]


def test_vendor_reply_is_recorded_automatically(store, outbox):
    mail = _sent(store, outbox)
    raw = _reply(mail["reply_to"], "Ravi <%s>" % VENDOR_EMAIL, mail["msg_id"],
                 "Hi, CALLS: YES\nRECORDING: NO\nRavi" + QUOTED)
    out = mailwatch.handle_message(store, raw, now=500)
    assert len(out) == 1 and out[0][1] == "consent"
    text = fmt.consent_result_text(**out[0][2])
    assert "✅ Recorded: **Sharma Traders** agreed to automated verification calls" in text
    c = _vendor(store)["consent"]
    assert (c["status"], c["call"], c["recording"]) == ("granted", True, False)
    assert workflow.call_permission(_vendor(store), {"CALLS_ENABLED": "1", "DEMO_MODE": "1"})[0]


def test_quoted_request_never_counts_as_an_answer(store, outbox):
    mail = _sent(store, outbox)
    raw = _reply(mail["reply_to"], VENDOR_EMAIL, mail["msg_id"], "Let me check with my manager." + QUOTED)
    out = mailwatch.handle_message(store, raw, now=500)
    assert out[0][2]["status"] == "unanswered"
    assert _vendor(store)["consent"]["status"] == "pending"


def test_decline_by_email(store, outbox):
    mail = _sent(store, outbox)
    raw = _reply(mail["reply_to"], VENDOR_EMAIL, mail["msg_id"], "1. No\n2. No" + QUOTED)
    mailwatch.handle_message(store, raw, now=500)
    assert _vendor(store)["consent"]["status"] == "declined"


def test_reply_from_another_address_stays_pending(store, outbox):
    mail = _sent(store, outbox)
    raw = _reply(mail["reply_to"], "someone@gmail.com", mail["msg_id"], "CALLS: YES RECORDING: YES")
    out = mailwatch.handle_message(store, raw, now=500)
    assert out[0][2]["status"] == "pending"
    assert "isn't an address you saved" in fmt.consent_result_text(**out[0][2])


def test_unrelated_email_to_the_token_is_not_treated_as_consent(store, outbox):
    mail = _sent(store, outbox)
    raw = _reply(mail["reply_to"], VENDOR_EMAIL, "<something-else@x>", "CALLS: YES")
    out = mailwatch.handle_message(store, raw, now=500)
    assert all(kind != "consent" for _, kind, _ in out)
    assert _vendor(store)["consent"]["status"] == "pending"


def test_strip_quoted():
    assert consent.strip_quoted("CALLS: YES" + QUOTED).strip() == "CALLS: YES"
    assert consent.strip_quoted("> CALLS: YES\nok").strip() == "ok"
    assert consent.strip_quoted("1. CALLS: YES or NO").strip() == ""


def test_reply_text_is_not_stored(store, outbox):
    mail = _sent(store, outbox)
    mailwatch.handle_message(store, _reply(mail["reply_to"], VENDOR_EMAIL, mail["msg_id"],
                                           "CALLS: YES. My personal note: hello"), now=500)
    assert "My personal note" not in json.dumps(store.d)
