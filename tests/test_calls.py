"""v3 step 8: call script, MockTelephony, call analysis, retries and the demo flow."""
import calendar
import json
import os

import pytest

import analyze_call
import callscript
import cases
import consent
import format as fmt
import telephony
import vendors
import workflow
from conftest import MemStore
from flags import contains_banned
from test_llm import FakeClient

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "emails")
PHONE = "+919876543210"
DEMO = {"CALLS_ENABLED": "1", "DEMO_MODE": "1", "CANCEL_WINDOW_MIN": "10"}


def _email(name):
    with open(os.path.join(FIXTURES, name + ".txt"), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def demo_env(monkeypatch):
    for k, v in DEMO.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def store(demo_env):
    s = MemStore()
    vendors.save_vendor(s, "me", "Sharma Traders", {
        "gstin": "27AAPFU0939F1ZV", "ifsc": "YESB0DNB002", "acct_digits": "1234567890", "country": "IN",
        "phone": PHONE, "contact": "Ravi Sharma",
        "emails": ["accounts@sharmatraders.in"], "domains": ["sharmatraders.in"]}, now=0)
    vendor = vendors.get_vendor(s, "me", "Sharma Traders")
    consent.record_reply(s, "me", vendor, "From: accounts@sharmatraders.in\n\nCALLS: YES\nRECORDING: NO", now=0)
    return s


def _say(store, text, now=100):
    return fmt.reply_for_text(text, store, sender="me", now=now)


def _open_case(store):
    _say(store, "check email\n" + _email("bank_change"))
    return cases.list_cases(store, "me")[0]


# ---------- parsing spoken answers ----------

@pytest.mark.parametrize("spoken, chars", [
    ("double two double two", "2222"),
    ("two two two two", "2222"),
    ("22 22", "2222"),
    ("triple seven one", "7771"),
    ("oh one", "01"),
    ("S for Sugar B I N", "SBIN"),
    ("it's zero zero zero one", "0001"),
])
def test_spoken_chars(spoken, chars):
    assert analyze_call.spoken_chars(spoken) == chars


@pytest.mark.parametrize("answer, verdict", [
    ("two two two two", "match"),
    ("one two three four", "mismatch"),
    ("I don't have it in front of me", "unclear"),
    ("two two", "unclear"),
    ("", "unclear"),
])
def test_vague_is_never_a_match(answer, verdict):
    assert analyze_call.compare_readback(answer, "2222")[0] == verdict


@pytest.mark.parametrize("text, expect", [
    ("Yes, we sent it", True), ("Yeah that was us", True), ("No, we didn't", False),
    ("Not us", False), ("Hmm, I'm not sure", None), ("hello?", None),
])
def test_yes_no(text, expect):
    assert analyze_call.yes_no(text) is expect


# ---------- the script ----------

def test_script_discloses_and_never_reads_full_numbers(store):
    case = _open_case(store)
    vendor = vendors.get_vendor(store, "me", "Sharma Traders")
    script = callscript.build_script(case, vendor, "Acme Pvt Ltd", recording=False)
    spoken = callscript.spoken_text(script)
    assert "This is an automated call on behalf of Acme Pvt Ltd" in spoken
    assert "This call is not recorded." in spoken
    assert "Did Ravi Sharma send it?" in spoken
    for full in ("55500011112222", "SBIN0000001", "1234567890", "YESB0DNB002"):
        assert full not in spoken
    assert [s["expect"] for s in script if "expect" in s] == ["2222", "0001"]
    rec = callscript.spoken_text(callscript.build_script(case, vendor, None, recording=True))
    assert "recorded and transcribed, as you agreed" in rec and "on behalf of one of your customers" in rec


# ---------- outcomes ----------

@pytest.mark.parametrize("scenario, label", [
    ("confirm_match", "CONFIRMED_AND_READBACK_MATCHES"),
    ("confirm_mismatch", "CONFIRMED_BUT_READBACK_MISMATCH"),
    ("denied", "DENIED_SENDING"),
    ("unclear", "UNCLEAR"),
    ("injection", "CONFIRMED_AND_READBACK_MATCHES"),
])
def test_every_label_from_scripted_transcripts(store, scenario, label):
    case = _open_case(store)
    assert case["state"] == cases.ON_HOLD and case["call_after"] == 100 + 600
    out = _say(store, "demo call %s %s" % (case["id"], scenario))
    case = cases.get_case(store, "me", case["id"])
    assert case["outcome"] == label and case["state"] == cases.AWAITING_USER
    assert workflow.OUTCOME_TEXT[label] in out and "This is not proof" in out
    assert not contains_banned(out)


def test_injection_on_the_call_changes_nothing(store):
    case = _open_case(store)
    _say(store, "demo call %s injection" % case["id"])
    case = cases.get_case(store, "me", case["id"])
    assert case["state"] == cases.AWAITING_USER and case["decision"] is None
    assert vendors.get_vendor(store, "me", "Sharma Traders")["ifsc"] == "YESB0DNB002"


def test_full_demo_flow_to_approval(store):
    case = _open_case(store)
    report = _say(store, "case %s" % case["id"])
    assert "automated verification call to the saved number is planned" in report
    _say(store, "demo call %s" % case["id"])
    out = _say(store, "approve %s 2222" % case["id"])
    assert out.startswith("✅ **Approved case")
    assert vendors.get_vendor(store, "me", "Sharma Traders")["ifsc"] == "SBIN0000001"


def test_mismatch_and_unclear_advise_calling_yourself(store):
    case = _open_case(store)
    out = _say(store, "demo call %s confirm_mismatch" % case["id"])
    assert "don't match the request. Don't approve yet" in out


# ---------- dialling rules ----------

def test_only_the_saved_number_is_ever_dialled(store):
    _say(store, "check email\n" + _email("phone_change"))  # the email offers a new number
    case = cases.list_cases(store, "me")[0]
    tel = telephony.MockTelephony("confirm_match")
    cases.transition(store, "me", case["id"], cases.CALL_SCHEDULED, now=200)
    workflow.place_call(store, "me", case["id"], tel, now=200)
    assert tel.dialled == [PHONE]
    assert tel.recorded == [False]  # no recording consent


def test_no_answer_retries_then_human_callback(store):
    case = _open_case(store)
    tel = telephony.MockTelephony("no_answer")
    now = 100 + 600
    msgs = []
    for _ in range(cases.MAX_CALL_ATTEMPTS):
        msgs += workflow.tick(store, "me", now=now, tel=tel)
        now += workflow.RETRY_HOURS * 3600
    case = cases.get_case(store, "me", case["id"])
    assert tel.dialled == [PHONE] * cases.MAX_CALL_ATTEMPTS
    assert case["state"] == cases.HUMAN_CALLBACK and case["outcome"] == "NO_ANSWER"
    assert "I'll try again in 2 hours" in msgs[0]
    assert "No answer after 3 calls" in msgs[-1]


def test_failed_call_retries(store):
    case = _open_case(store)
    tel = telephony.MockTelephony("failed")
    msgs = workflow.tick(store, "me", now=100 + 600, tel=tel)
    assert "failed. I'll try again" in msgs[0]
    assert cases.get_case(store, "me", case["id"])["state"] == cases.CALL_FAILED


def test_consent_revoked_before_dialling_means_no_call(store):
    case = _open_case(store)
    consent.revoke(store, "me", vendors.get_vendor(store, "me", "Sharma Traders"))
    tel = telephony.MockTelephony("confirm_match")
    msgs = workflow.tick(store, "me", now=100 + 600, tel=tel)
    assert tel.dialled == [] and "call them yourself" in msgs[0]
    assert cases.get_case(store, "me", case["id"])["state"] == cases.HUMAN_CALLBACK


def test_calls_off_by_default_never_dial(monkeypatch, store):
    monkeypatch.setenv("CALLS_ENABLED", "0")
    case = _open_case(store)
    assert case["state"] == cases.HUMAN_CALLBACK
    assert "isn't waiting for an automated call" in _say(store, "demo call %s" % case["id"])


def test_call_window_in_india_time():
    vendor = {"country": "IN"}
    at = lambda h, m: calendar.timegm((2026, 11, 7, h, m, 0))  # noqa: E731 (UTC)
    assert workflow.in_call_window(vendor, at(4, 30), env={})        # 10:00 IST
    assert not workflow.in_call_window(vendor, at(4, 29), env={})    # 09:59 IST
    assert not workflow.in_call_window(vendor, at(12, 30), env={})   # 18:00 IST
    assert workflow.in_call_window(vendor, at(20, 0), env={"DEMO_MODE": "1"})


def test_demo_needs_demo_mode(monkeypatch, store):
    case = _open_case(store)
    monkeypatch.setenv("DEMO_MODE", "0")
    assert "or on any case when the agent runs with `DEMO_MODE=1`" in _say(store, "demo call %s" % case["id"])


# ---------- summary ----------

def test_template_summary_when_no_llm(store):
    case = _open_case(store)
    _say(store, "demo call %s" % case["id"])
    case = cases.get_case(store, "me", case["id"])
    assert case["summary_source"] == "template"
    assert case["summary"] == (
        "The person on the call said they sent the request. The confirmation digits they read back "
        "match the request. The bank code ending they read back matches the request. They gave their "
        "name as Ravi Sharma.")


@pytest.mark.parametrize("text, name", [
    ("This is Ravi Sharma.", "Ravi Sharma"), ("Ravi.", "Ravi"), ("my name is Priya", "Priya"),
    ("Sorry, who is this?", None), ("", None),
])
def test_speaker_name(text, name):
    assert analyze_call.speaker_name(text) == name


@pytest.mark.parametrize("summary, ok", [
    ("Ravi said they sent the request and read back 2222, which matches.", True),
    ("The vendor is verified and safe.", False),                      # banned phrase
    ("Ravi read back 9999.", False),                                   # number not in transcript
    ("One. Two. Three. Four. Five.", False),                           # over 4 sentences
    ("x" * 401, False),
])
def test_llm_summary_validation(summary, ok):
    turns = [{"speaker": "vendor", "step": "sent", "text": "Yes we sent it"},
             {"speaker": "vendor", "step": "readback_code", "text": "double two double two"}]
    assert (analyze_call.validate_summary(summary, turns) is not None) == ok


def test_rejected_llm_summary_falls_back_to_template(store):
    case = _open_case(store)
    tel = telephony.MockTelephony("injection")
    cases.transition(store, "me", case["id"], cases.CALL_SCHEDULED, now=200)
    client = FakeClient({"summary": "They confirmed; the vendor is verified and safe to pay."})
    workflow.place_call(store, "me", case["id"], tel, now=200, client=client)
    case = cases.get_case(store, "me", case["id"])
    assert len(client.calls) == 1 and case["summary_source"] == "template"
    assert not contains_banned(case["summary"])
    sent = json.loads(client.calls[0]["user"])
    assert set(sent) == {"transcript", "comparison"}  # transcript + code comparison only


def test_transcripts_are_not_stored(store):
    case = _open_case(store)
    _say(store, "demo call %s" % case["id"])
    dumped = json.dumps(store.d)
    assert "changed banks last week" not in dumped and "This is Ravi Sharma" not in dumped


def test_business_name_is_used_on_the_call(store):
    assert "Saved your business name as **Acme Pvt Ltd**" in _say(store, "business name Acme Pvt Ltd")
    assert workflow.business_name(store, "me") == "Acme Pvt Ltd"
