"""v3 step 6: hold, human callback, approve/reject in chat, cancel window, reminders, expiry."""
import os

import pytest

import cases
import format as fmt
import vendors
import workflow
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


def _say(store, text, now=100, sender="me"):
    return fmt.reply_for_text(text, store, sender=sender, now=now)


def _open(store):
    _say(store, "check email\n" + _email("bank_change"))
    return cases.list_cases(store, "me")[0]


# ---------- routing ----------

def test_calls_off_by_default_goes_to_human_callback(store):
    case = _open(store)
    assert case["state"] == cases.HUMAN_CALLBACK
    assert "automated calls are switched off" in case["audit"][-1]["note"]


@pytest.mark.parametrize("env, vendor_patch, reason", [
    ({}, {}, "switched off"),
    ({"CALLS_ENABLED": "1"}, {"country": "GB"}, "not enabled for UK"),
    ({"CALLS_ENABLED": "1"}, {"phone": None}, "no saved phone"),
    ({"CALLS_ENABLED": "1"}, {}, "hasn't agreed"),
    ({"CALLS_ENABLED": "1"}, {"consent": {"status": "granted", "phone": "+910000000000"}}, "hasn't agreed"),
    ({"CALLS_ENABLED": "1"}, {"consent": {"status": "declined", "phone": "+919876543210"}}, "hasn't agreed"),
])
def test_call_permission_needs_switch_profile_phone_and_consent(store, env, vendor_patch, reason):
    vendor = dict(vendors.get_vendor(store, "me", "Sharma Traders"), **vendor_patch)
    allowed, why = workflow.call_permission(vendor, env)
    assert not allowed and reason in why


def test_call_allowed_only_with_everything(store):
    vendor = dict(vendors.get_vendor(store, "me", "Sharma Traders"),
                  consent={"status": "granted", "phone": "+919876543210"})
    assert workflow.call_permission(vendor, {"CALLS_ENABLED": "1"}) == (True, "")


def test_cancel_window_then_scheduled_or_cancelled(store):
    vendor = dict(vendors.get_vendor(store, "me", "Sharma Traders"),
                  consent={"status": "granted", "phone": "+919876543210"})
    env = {"CALLS_ENABLED": "1", "CANCEL_WINDOW_MIN": "10"}
    case, route, _ = workflow.open_and_route(store, "me", vendor, [{"field": "ifsc", "old": "a", "new": "b"}],
                                             {"type": "email", "ref": "x"}, [], now=0, env=env)
    assert route == "call" and case["state"] == cases.ON_HOLD and case["call_after"] == 600
    workflow.tick(store, "me", now=599)
    assert cases.get_case(store, "me", case["id"])["state"] == cases.ON_HOLD
    workflow.tick(store, "me", now=600)
    assert cases.get_case(store, "me", case["id"])["state"] == cases.CALL_SCHEDULED
    out = _say(store, "I'll call %s" % case["id"])
    assert "OK, no automated call." in out and "**Call Sharma Traders yourself**" in out
    assert cases.get_case(store, "me", case["id"])["state"] == cases.HUMAN_CALLBACK


# ---------- human callback and decision ----------

def test_full_callback_and_approval(store):
    case = _open(store)
    cid = case["id"]
    report = _say(store, "case %s" % cid)
    assert "waiting for you to call the vendor" in report
    assert "Use the number you saved: `+919876543210`" in report
    assert "It should be `2222`" in report
    assert "isn't ready to approve yet" in _say(store, "approve %s 2222" % cid)
    out = _say(store, "confirmed %s" % cid)
    assert "You called and the vendor said they confirm the change. This is not proof" in out
    assert "`approve %s 2222`" % cid in out
    assert "That code doesn't match" in _say(store, "approve %s 1111" % cid)
    out = _say(store, "approve %s 2222" % cid)
    assert out.startswith("✅ **Approved case `%s`.**" % cid)
    rec = vendors.get_vendor(store, "me", "Sharma Traders")
    assert rec["ifsc"] == "SBIN0000001" and rec["acct_last4"] == "2222"
    assert "already approved" in _say(store, "approve %s 2222" % cid)


def test_callback_without_id_when_one_case_waits(store):
    case = _open(store)
    assert "Noted." in _say(store, "not confirmed")
    out = _say(store, "case %s" % case["id"])
    assert "the vendor said they did not send this" in out
    assert "I suggest `reject %s`" % case["id"] in out


def test_reject_after_denial_suggests_reporting(store):
    case = _open(store)
    _say(store, "not confirmed %s" % case["id"])
    out = _say(store, "reject %s" % case["id"])
    assert "Your saved details for **Sharma Traders** stay as they were" in out
    assert "reporting the email to your bank" in out
    assert vendors.get_vendor(store, "me", "Sharma Traders")["ifsc"] == "YESB0DNB002"


def test_no_answer_warns_not_to_approve(store):
    case = _open(store)
    out = _say(store, "no answer %s" % case["id"])
    assert "Don't approve until you've reached them" in out


def test_wrong_codes_lock_approval(store):
    case = _open(store)
    _say(store, "confirmed %s" % case["id"])
    for _ in range(cases.MAX_FAILED_APPROVALS):
        _say(store, "approve %s 0000" % case["id"])
    assert "approval is locked" in _say(store, "approve %s 2222" % case["id"])
    assert vendors.get_vendor(store, "me", "Sharma Traders")["ifsc"] == "YESB0DNB002"


def test_other_user_cannot_touch_the_case(store):
    case = _open(store)
    _say(store, "confirmed %s" % case["id"])
    assert "I don't have a case" in _say(store, "approve %s 2222" % case["id"], sender="mallory")
    assert "I don't have a case" in _say(store, "reject %s" % case["id"], sender="mallory")
    assert cases.get_case(store, "me", case["id"])["state"] == cases.AWAITING_USER


def test_approve_without_code_asks_for_it(store):
    case = _open(store)
    _say(store, "confirmed %s" % case["id"])
    assert "quote the case id and the last 4 digits of the new account number" in _say(store, "approve %s" % case["id"])


# ---------- listing, reminders, expiry ----------

def test_my_cases(store):
    assert _say(store, "my cases") == "You have no open cases."
    case = _open(store)
    out = _say(store, "my cases")
    assert "`%s` Sharma Traders: waiting for you to call the vendor" % case["id"] in out


def test_reminders_and_expiry(store):
    case = _open(store)
    assert workflow.tick(store, "me", now=100 + 3600) == []
    msgs = workflow.tick(store, "me", now=100 + workflow.REMINDER_HOURS * 3600)
    assert len(msgs) == 1 and "Reminder: case `%s`" % case["id"] in msgs[0]
    assert workflow.tick(store, "me", now=100 + workflow.REMINDER_HOURS * 3600 + 60) == []  # not again yet
    msgs = workflow.tick(store, "me", now=case["expires_at"])
    assert "expired without a decision. Nothing was changed" in msgs[0]
    assert vendors.get_vendor(store, "me", "Sharma Traders")["ifsc"] == "YESB0DNB002"
    assert "case_owners" in store.d and "me" in workflow.owners(store)


def test_expired_case_cannot_be_approved(store):
    case = _open(store)
    _say(store, "confirmed %s" % case["id"])
    out = _say(store, "approve %s 2222" % case["id"], now=case["expires_at"] + 1)
    assert "expired" in out and "Approved" not in out


# ---------- safety ----------

def test_injection_email_still_needs_the_user(store):
    _say(store, "check email\n" + _email("injection"))
    case = cases.list_cases(store, "me")[0]
    assert case["state"] == cases.HUMAN_CALLBACK and case["decision"] is None


def test_case_texts_have_no_banned_phrases(store):
    case = _open(store)
    outs = [_say(store, "case %s" % case["id"]), _say(store, "confirmed %s" % case["id"]),
            _say(store, "approve %s 2222" % case["id"]), _say(store, "my cases")]
    for o in outs:
        assert not contains_banned(o), o
    for text in workflow.OUTCOME_TEXT.values():
        assert not contains_banned(text)
