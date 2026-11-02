"""v3 step 5: change detection on emails, sender checks, de-duplication, injection."""
import json
import os

import pytest

import cases
import detect
import format as fmt
import vendors
from conftest import MemStore
from flags import contains_banned

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "emails")
GST = "27AAPFU0939F1ZV"


def _email(name):
    with open(os.path.join(FIXTURES, name + ".txt"), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def store():
    s = MemStore()
    vendors.save_vendor(s, "me", "Sharma Traders", {
        "gstin": GST, "ifsc": "YESB0DNB002", "acct_digits": "1234567890", "country": "IN",
        "phone": "+919876543210", "emails": ["accounts@sharmatraders.in"], "domains": ["sharmatraders.in"],
    }, now=0)
    return s


def _process(store, name):
    return detect.process_email(store, "me", raw=_email(name), now=100)


# ---------- parsing ----------

def test_parse_plain_and_forwarded():
    p = detect.parse_email(_email("bank_change"))
    assert (p["from_addr"], p["subject"], p["forwarded"]) == (
        "accounts@sharmatraders.in", "Updated bank details", False)
    assert p["body"].startswith("Dear customer")
    f = detect.parse_email(_email("forwarded"))
    assert f["from_addr"] == "accounts@sharmatraders.in" and f["forwarded"]
    assert f["subject"] == "Change of bank" and f["body"].startswith("Our bank has changed")


# ---------- domains ----------

@pytest.mark.parametrize("domain, saved, reason", [
    ("sharmatrader.in", "sharmatraders.in", "one character different"),
    ("sharmatraderz.in", "sharmatraders.in", "one character different"),
    ("sharamtraders.in", "sharmatraders.in", "one character different"),  # swapped letters
    ("sharma-traders.in", "sharmatraders.in", "hyphen"),
    ("sharmatraders.com", "sharmatraders.in", "different ending"),
    ("sharmatraders.co.in", "sharmatraders.in", "different ending"),
    ("rnicrosoft.com", "microsoft.com", "one character different"),
])
def test_lookalike_domains(domain, saved, reason):
    assert reason in detect.lookalike(domain, saved)


@pytest.mark.parametrize("domain", ["sharmatraders.in", "mail.sharmatraders.in", "gmail.com", "kumarco.in"])
def test_not_lookalikes(domain):
    assert detect.lookalike(domain, "sharmatraders.in") is None


# ---------- detection ----------

def test_bank_change_from_saved_sender_opens_case(store):
    res = _process(store, "bank_change")
    assert res["status"] == "case_opened" and res["matched_by"] == "sender"
    case = res["case"]
    # held, then routed to the human callback because automated calls are off by default
    assert [a["to"] for a in case["audit"]] == [cases.RECEIVED, cases.ON_HOLD, cases.HUMAN_CALLBACK]
    assert {c["field"] for c in case["changes"]} == {"ifsc", "acct_last4"}
    assert "bank_change" in case["flags"] and "urgency" in case["flags"] and case["priority"] == "high"
    assert case["trigger"] == {"type": "email", "ref": "<bank-change-1@sharmatraders.in>"}
    assert vendors.get_vendor(store, "me", "Sharma Traders")["ifsc"] == "YESB0DNB002"  # unchanged


def test_same_details_open_no_case(store):
    res = _process(store, "same_details")
    assert res["status"] == "no_change" and res["case"] is None
    assert cases.list_cases(store, "me") == []


def test_lookalike_domain(store):
    res = _process(store, "lookalike_domain")
    assert res["matched_by"] == "lookalike" and res["status"] == "case_opened"
    assert res["sender_flags"][0][0] == "lookalike_domain"
    assert "sharmatrader.in instead of sharmatraders.in" in res["sender_flags"][0][1]
    assert "lookalike_domain" in res["case"]["flags"]


def test_reply_to_mismatch_is_flagged_even_without_changes(store):
    res = _process(store, "reply_to_mismatch")
    assert res["status"] == "no_change"
    assert res["sender_flags"] == [("reply_to_mismatch", "sharma.accounts@gmail.com")]


def test_unknown_sender_mentioning_vendor(store):
    res = _process(store, "unknown_sender")
    assert res["matched_by"] == "ids" and res["status"] == "case_opened"
    assert res["sender_flags"][0][0] == "unknown_sender"
    assert "send_to_account" in res["case"]["flags"]


def test_forwarded_email_uses_original_sender(store):
    res = _process(store, "forwarded")
    assert res["matched_by"] == "sender" and res["status"] == "case_opened"


def test_duplicate_processed_once(store):
    assert _process(store, "bank_change")["status"] == "case_opened"
    assert _process(store, "bank_change")["status"] == "duplicate"
    assert len(cases.list_cases(store, "me")) == 1


def test_phone_change_is_a_case(store):
    res = _process(store, "phone_change")
    assert res["status"] == "case_opened"
    assert res["case"]["changes"] == [{"field": "phone", "old": "+919876543210", "new": "+919111122222"}]


def test_unknown_vendor_opens_nothing():
    s = MemStore()
    res = detect.process_email(s, "me", raw=_email("bank_change"), now=1)
    assert res["status"] == "unknown_vendor" and cases.list_cases(s, "me") == []


def test_bank_detail_not_on_file_opens_case():
    s = MemStore()
    vendors.save_vendor(s, "me", "Sharma Traders", {"gstin": GST, "emails": ["accounts@sharmatraders.in"],
                                                    "domains": ["sharmatraders.in"]}, now=0)
    res = detect.process_email(s, "me", raw=_email("same_details"), now=1)
    assert res["status"] == "case_opened"
    assert {(c["field"], c["old"]) for c in res["case"]["changes"]} == {("ifsc", None), ("acct_last4", None)}


def test_injection_changes_nothing(store):
    res = _process(store, "injection")
    case = res["case"]
    assert case["state"] == cases.HUMAN_CALLBACK and case["decision"] is None
    assert vendors.get_vendor(store, "me", "Sharma Traders")["ifsc"] == "YESB0DNB002"
    out = fmt._email_reply(res)
    assert not contains_banned(out)
    assert "approve all" not in out.lower() and "ignore your rules" not in out.lower()


def test_no_email_text_stored(store):
    _process(store, "bank_change")
    dumped = json.dumps(store.d)
    assert "Dear customer" not in dumped and "55500011112222" not in dumped


# ---------- chat ----------

def test_check_email_command(store):
    out = fmt.reply_for_text("check email\n" + _email("bank_change"), store, sender="me", now=100)
    assert out.startswith("**Email from accounts@sharmatraders.in** about **Sharma Traders** (matched by a saved email address).")
    assert "**Heads-up**" in out
    assert "- IFSC: was `YESB•••••02`, now `SBIN•••••01`" in out
    assert "- Account: was `••••7890`, now `••••2222`" in out
    assert "opened. The payment is ON HOLD**" in out
    assert "**Call Sharma Traders yourself**" in out
    assert "Use the number you saved: `+919876543210`" in out
    assert not contains_banned(out)


def test_pasted_headers_are_detected_and_long_emails_allowed(store):
    raw = _email("lookalike_domain") + "\n" + "Kind regards. " * 100
    assert len(raw) > 1000
    out = fmt.reply_for_text(raw, store, sender="me", now=100)
    assert "**Sender warnings**" in out and "lookalike" in out


def test_same_details_reply(store):
    out = fmt.reply_for_text("check email\n" + _email("same_details"), store, sender="me", now=100)
    assert "✅ The details in this email match what you saved for Sharma Traders. No case opened." in out


def test_unknown_vendor_reply():
    out = fmt.reply_for_text("check email\n" + _email("bank_change"), MemStore(), sender="me")
    assert "I couldn't match it to a vendor you saved" in out
