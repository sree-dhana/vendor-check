"""v3 step 9: watching vendor emails (permission, forwarding setup, IMAP reading)."""
import json
from email.message import EmailMessage

import pytest

import cases
import format as fmt
import mailwatch
import vendors
from conftest import MemStore

BOX = "vendorcheck.agent@gmail.com"
ENV = {"MAILBOX_IMAP_HOST": "imap.gmail.com", "MAILBOX_USER": BOX, "MAILBOX_PASSWORD": "app-pass"}


@pytest.fixture
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def store(env):
    s = MemStore()
    vendors.save_vendor(s, "me", "Sharma Traders", {
        "gstin": "27AAPFU0939F1ZV", "ifsc": "YESB0DNB002", "acct_digits": "1234567890", "country": "IN",
        "phone": "+919876543210", "emails": ["accounts@sharmatraders.in"], "domains": ["sharmatraders.in"]}, now=0)
    vendors.save_vendor(s, "me", "Kumar Co", {"gstin": "07AAACM5678K1Z1", "emails": ["ap@kumarco.in"],
                                              "domains": ["kumarco.in"]}, now=0)
    return s


def _watch(store, name="Sharma Traders"):
    status, address = mailwatch.start_watching(store, "me", vendors.get_vendor(store, "me", name), now=10)
    assert status == "ok"
    return address


def _mail(frm, to, subject, body, msg_id, html=False, extra=()):
    m = EmailMessage()
    for k, v in extra:
        m[k] = v
    m["From"], m["To"], m["Subject"], m["Message-ID"] = frm, to, subject, msg_id
    if html:
        m.set_content(body, subtype="html")
    else:
        m.set_content(body)
    return m.as_bytes()


CHANGE = "We have changed our bank account. Please pay today.\nIFSC SBIN0000001\nAccount number 55500011112222"


# ---------- permission and setup ----------

def test_start_watching_gives_private_address_and_steps(store):
    out = fmt.reply_for_text("watch Sharma Traders", store, sender="me", now=10)
    state = mailwatch.watch_state(store, "me")
    address = "vendorcheck.agent+%s@gmail.com" % state["token"]
    assert "I'll watch for emails from **Sharma Traders** (accounts@sharmatraders.in)" in out
    assert "I never get access to your inbox" in out and address in out
    assert "**Forwarding and POP/IMAP**" in out and "**Add a forwarding address**" in out
    assert "**Show search options**" in out and "`accounts@sharmatraders.in`" in out
    assert mailwatch.owner_for_token(store, state["token"]) == "me"


def test_watch_needs_vendor_email_and_mailbox(monkeypatch, store):
    vendors.save_vendor(store, "me", "NoMail Co", {"gstin": "27AAPFU0939F1ZV"}, now=0)
    assert "Save an email address for **NoMail Co**" in fmt.reply_for_text("watch NoMail Co", store, sender="me")
    monkeypatch.delenv("MAILBOX_PASSWORD")
    assert "isn't switched on for this agent" in fmt.reply_for_text("watch Sharma Traders", store, sender="me")


def test_watch_status_and_stop(store):
    _watch(store)
    out = fmt.reply_for_text("watch status", store, sender="me")
    assert "**Vendors:** Sharma Traders" in out and "waiting for your first forwarded email" in out
    out = fmt.reply_for_text("stop watching Sharma Traders", store, sender="me")
    assert "Stopped watching emails for **Sharma Traders**" in out
    assert "not watching any vendor emails" in fmt.reply_for_text("watch status", store, sender="me")


# ---------- reading forwarded mail ----------

def test_gmail_auto_forward_opens_case_and_confirms_setup(store):
    address = _watch(store)
    raw = _mail("Sharma Traders <accounts@sharmatraders.in>", "owner@mybiz.in", "Updated bank details",
                CHANGE, "<a1@sharmatraders.in>", extra=[("X-Forwarded-To", address), ("Delivered-To", address)])
    out = mailwatch.handle_message(store, raw, now=100)
    assert out[0] == ("me", "text", "Watching vendor emails now: your forwarding works.")
    owner, kind, res = out[1]
    assert (owner, kind, res["status"], res["matched_by"]) == ("me", "email", "case_opened", "sender")
    alert = fmt.email_alert(res)
    assert alert.startswith("📩 **New forwarded email**") and "ON HOLD" in alert
    assert mailwatch.watch_state(store, "me")["confirmed_at"] == 100


def test_manual_forward_uses_original_sender(store):
    address = _watch(store)
    body = ("Please check.\n\n---------- Forwarded message ---------\n"
            "From: Sharma Traders <accounts@sharmatraders.in>\nSubject: Change of bank\n\n" + CHANGE)
    raw = _mail("Owner <owner@mybiz.in>", address, "Fwd: Change of bank", body, "<fwd1@mybiz.in>")
    res = [p for _, k, p in mailwatch.handle_message(store, raw, now=100) if k == "email"][0]
    assert res["matched_by"] == "sender" and res["status"] == "case_opened"


def test_html_only_email(store):
    address = _watch(store)
    html = "<p>We have changed our bank account.</p><p>IFSC SBIN0000001<br>Account number 55500011112222</p>"
    raw = _mail("accounts@sharmatraders.in", address, "Bank", html, "<h1@sharmatraders.in>", html=True)
    kinds = [k for _, k, _ in mailwatch.handle_message(store, raw, now=100)]
    assert "email" in kinds


def test_unknown_token_is_ignored(store):
    _watch(store)
    raw = _mail("accounts@sharmatraders.in", "vendorcheck.agent+ffffffffff@gmail.com", "x", CHANGE, "<u1@x>")
    assert mailwatch.handle_message(store, raw, now=100) == []
    assert mailwatch.handle_message(store, _mail("a@b.c", BOX, "x", CHANGE, "<u2@x>"), now=100) == []


def test_only_watched_vendors_are_acted_on(store):
    address = _watch(store)  # Sharma only
    raw = _mail("ap@kumarco.in", address, "New account", "Please use IFSC SBIN0000001, account 55500011112222",
                "<k1@kumarco.in>")
    out = mailwatch.handle_message(store, raw, now=100)
    assert [k for _, k, _ in out] == ["text"]  # only the "watching now" confirmation
    assert cases.list_cases(store, "me") == []


def test_stop_watching_stops_cases(store):
    address = _watch(store)
    mailwatch.stop_watching(store, "me", vendors.get_vendor(store, "me", "Sharma Traders"))
    raw = _mail("accounts@sharmatraders.in", address, "Bank", CHANGE, "<s1@sharmatraders.in>")
    mailwatch.handle_message(store, raw, now=100)
    assert cases.list_cases(store, "me") == []


def test_duplicate_delivery_processed_once(store):
    address = _watch(store)
    raw = _mail("accounts@sharmatraders.in", address, "Bank", CHANGE, "<dup@sharmatraders.in>")
    mailwatch.handle_message(store, raw, now=100)
    second = mailwatch.handle_message(store, raw, now=101)
    assert [k for _, k, _ in second] == []
    assert len(cases.list_cases(store, "me")) == 1


# ---------- Gmail forwarding confirmation ----------

GOOD_AUTH = ("mx.google.com; dkim=pass header.i=@google.com header.s=20230601 header.b=abc; "
             "spf=pass smtp.mailfrom=forwarding-noreply@google.com")
LINK = "https://mail-settings.google.com/mail/vf-%5BANGjdJ-abc%5D-xyz"


def test_genuine_gmail_confirmation_is_relayed(store):
    address = _watch(store)
    raw = _mail("Gmail Team <forwarding-noreply@google.com>", address,
                "(#123456789) Gmail Forwarding Confirmation - Receive Mail from owner@gmail.com",
                "To allow forwarding, click the link below:\n%s\n" % LINK, "<g1@google.com>",
                extra=[("Authentication-Results", GOOD_AUTH)])
    out = mailwatch.handle_message(store, raw, now=50)
    assert len(out) == 1 and out[0][1] == "text"
    assert LINK in out[0][2] and "`123456789`" in out[0][2]
    assert not mailwatch.watch_state(store, "me").get("confirmed_at")  # not the vendor test email


@pytest.mark.parametrize("auth, link", [
    (None, LINK),                                                       # no DKIM result
    ("mx.google.com; dkim=fail header.i=@google.com", LINK),            # DKIM failed
    ("evil.example; dkim=pass header.i=@google.com", LINK),             # not Gmail's own server
    (GOOD_AUTH, "https://mail-settings.google.com.evil.example/vf"),   # lookalike host
])
def test_fake_confirmations_are_not_relayed(store, auth, link):
    address = _watch(store)
    extra = [("Authentication-Results", auth)] if auth else []
    raw = _mail("forwarding-noreply@google.com", address, "(#1) Gmail Forwarding Confirmation",
                "Click: %s" % link, "<f-%s@x>" % hash(link + str(auth)), extra=extra)
    out = mailwatch.handle_message(store, raw, now=50)
    for _, kind, payload in out:
        if kind == "text":
            assert "evil" not in payload
            if auth != GOOD_AUTH:
                assert "Open this Google link" not in payload


def test_attacker_header_below_gmails_own_does_not_count():
    m = EmailMessage()
    m["Authentication-Results"] = "mx.google.com; dkim=fail header.i=@google.com"  # Gmail's, first
    m["Authentication-Results"] = GOOD_AUTH  # forged lower down
    assert not mailwatch.google_dkim_pass(m)


# ---------- fetching ----------

class FakeIMAP:
    def __init__(self, host, port, messages):
        self.host, self.port, self.messages = host, port, messages
        self.flags, self.expunged, self.logged_in = {}, False, None

    def login(self, user, pw):
        self.logged_in = (user, pw)

    def select(self, box):
        return "OK", [b"1"]

    def search(self, charset, crit):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]

    def fetch(self, i, what):
        return "OK", [(b"1 (RFC822 {n})", self.messages[int(i) - 1])]

    def store(self, i, op, flag):
        self.flags.setdefault(int(i), []).append(flag)

    def expunge(self):
        self.expunged = True

    def logout(self):
        pass


def test_fetch_new_caps_marks_seen_and_can_delete():
    made = []
    msgs = [b"m%d" % i for i in range(5)]

    def factory(host, port):
        made.append(FakeIMAP(host, port, msgs))
        return made[-1]

    env = dict(ENV, MAILBOX_MAX_PER_RUN="3")
    assert mailwatch.fetch_new(env, factory) == [b"m0", b"m1", b"m2"]
    imap = made[0]
    assert (imap.host, imap.port, imap.logged_in) == ("imap.gmail.com", 993, (BOX, "app-pass"))
    assert imap.flags == {1: ["\\Seen"], 2: ["\\Seen"], 3: ["\\Seen"]} and not imap.expunged
    mailwatch.fetch_new(dict(env, MAILBOX_DELETE_AFTER="1"), factory)
    assert made[1].expunged and "\\Deleted" in made[1].flags[1]


# ---------- silence, privacy, forget ----------

def test_silence_notice_after_30_days_once(store):
    _watch(store)
    assert mailwatch.silence_notices(store, "me", now=10 + 29 * 86400) == []
    assert "nothing has arrived in 30 days" in mailwatch.silence_notices(store, "me", now=10 + 30 * 86400)[0]
    assert mailwatch.silence_notices(store, "me", now=10 + 31 * 86400) == []


def test_no_email_text_stored(store):
    address = _watch(store)
    mailwatch.handle_message(store, _mail("accounts@sharmatraders.in", address, "Bank", CHANGE, "<p1@s>"), now=100)
    dumped = json.dumps(store.d)
    assert "We have changed our bank account" not in dumped and "55500011112222" not in dumped


def test_forget_all_removes_watch(store):
    address = _watch(store)
    token = mailwatch.watch_state(store, "me")["token"]
    fmt.reply_for_text("forget all", store, sender="me")
    assert mailwatch.owner_for_token(store, token) is None
    assert mailwatch.handle_message(store, _mail("accounts@sharmatraders.in", address, "B", CHANGE, "<z@s>"), now=1) == []


def test_save_asks_to_watch_when_monitoring_chosen(env):
    s = MemStore()
    for a in ("start", "yes", "India", "pay", "same", "yes", "skip"):
        fmt.reply_for_text(a, s, sender="me", now=1)
    out = fmt.reply_for_text("save vendor Kumar Co email ap@kumarco.in", s, sender="me", now=2)
    assert "Want me to watch for emails from ap@kumarco.in" in out and "`watch Kumar Co`" in out
