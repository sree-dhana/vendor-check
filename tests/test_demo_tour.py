"""Guided demo for judges: works from chat alone, on sample data, without touching real data."""
import pytest

import cases
import format as fmt
import notify
import telephony
import vendors
import workflow
from conftest import MemStore
from flags import contains_banned


@pytest.fixture(autouse=True)
def plain_env(monkeypatch, no_net):
    for k in ("CALLS_ENABLED", "DEMO_MODE", "ANCHOR_ENABLED", "MAILBOX_PASSWORD"):
        monkeypatch.delenv(k, raising=False)


def _say(store, text, now=100):
    return fmt.reply_for_text(text, store, sender="judge", now=now)


def test_full_tour_without_any_settings():
    s = MemStore()
    out = _say(s, "demo")
    assert "**VendorCheck guided demo**" in out and "sample data only" in out
    assert "Demo Traders (sample)" in out and "Say `next`" in out
    out = _say(s, "next")
    assert "**Step 2 of 4" in out and "ON HOLD" in out and "SBIN•••••01" in out
    out = _say(s, "next")
    assert "**Step 3 of 4" in out and "evidence, not proof" in out
    assert "the vendor said they confirm, and the digits they read back match" in out.lower()
    out = _say(s, "next")
    cid = cases.list_cases(s, "judge")[0]["id"]
    assert "**Step 4 of 4" in out and "`approve %s 2222`" % cid in out
    assert _say(s, "approve %s 2222" % cid).startswith("✅ **Approved case")
    out = _say(s, "next")
    assert "**That's the tour.**" in out and "approved" in out
    for o in (out,):
        assert not contains_banned(o)


def test_tour_texts_have_no_banned_phrases():
    s = MemStore()
    outs = [_say(s, "demo"), _say(s, "next"), _say(s, "next"), _say(s, "next")]
    for o in outs:
        assert not contains_banned(o), o


def test_only_the_simulated_phone_is_used(monkeypatch):
    dialled = []
    real = telephony.MockTelephony.place_call

    def spy(self, to, script, record=False):
        dialled.append(to)
        return real(self, to, script, record)

    monkeypatch.setattr(telephony.MockTelephony, "place_call", spy)
    monkeypatch.setattr(telephony, "get_telephony", lambda *a, **k: pytest.fail("real phone service used"))
    s = MemStore()
    _say(s, "demo"), _say(s, "next"), _say(s, "next")
    assert dialled == [fmt.DEMO_PHONE]


def test_background_timer_uses_the_simulated_phone_for_demo_cases(monkeypatch):
    monkeypatch.setattr(telephony, "get_telephony", lambda *a, **k: None)  # no real service
    s = MemStore()
    _say(s, "demo"), _say(s, "next")
    case = cases.list_cases(s, "judge")[0]
    msgs = workflow.tick(s, "judge", now=case["call_after"] + 1)  # cancel window passed, any hour
    case = cases.get_case(s, "judge", case["id"])
    assert case["state"] == cases.AWAITING_USER and case["outcome"] == "CONFIRMED_AND_READBACK_MATCHES"
    assert any("finished" in m for m in msgs)


def test_demo_vendor_is_never_emailed(monkeypatch):
    monkeypatch.setenv("MAILBOX_IMAP_HOST", "imap.gmail.com")
    monkeypatch.setenv("MAILBOX_USER", "agent@gmail.com")
    monkeypatch.setenv("MAILBOX_PASSWORD", "x")
    monkeypatch.setattr(notify, "send_email", lambda *a, **k: pytest.fail("emailed the demo vendor"))
    s = MemStore()
    _say(s, "demo")
    assert "I don't email it" in _say(s, "send consent Demo Traders (sample)")


def test_reset_leaves_real_data_alone():
    s = MemStore()
    vendors.save_vendor(s, "judge", "Real Vendor", {"gstin": fmt.SAMPLE_GSTIN, "phone": "+919876543210"}, now=0)
    _say(s, "demo"), _say(s, "next")
    out = _say(s, "demo reset")
    assert "Demo data deleted" in out
    names = [r["name"] for r in vendors.list_vendors(s, "judge")]
    assert names == ["Real Vendor"]
    assert cases.list_cases(s, "judge", open_only=False) == []
    assert _say(s, "next") == fmt.NO_ID_TEXT  # the tour is over


def test_restarting_the_tour_starts_clean():
    s = MemStore()
    _say(s, "demo"), _say(s, "next")
    _say(s, "demo")
    assert cases.list_cases(s, "judge", open_only=False) == []
    assert len([r for r in vendors.list_vendors(s, "judge") if r.get("demo")]) == 1


def test_other_call_results_on_demo_cases_without_demo_mode():
    s = MemStore()
    _say(s, "demo"), _say(s, "next")
    cid = cases.list_cases(s, "judge")[0]["id"]
    out = _say(s, "demo call %s denied" % cid)
    assert "the vendor said they did not send this" in out


def test_demo_call_on_a_real_case_still_needs_demo_mode():
    s = MemStore()
    vendors.save_vendor(s, "judge", "Real", {"gstin": fmt.SAMPLE_GSTIN, "emails": ["a@real.in"],
                                             "domains": ["real.in"], "ifsc": "YESB0DNB002"}, now=0)
    fmt.reply_for_text("check email\nFrom: a@real.in\nSubject: x\n\nNew IFSC SBIN0000001", s, sender="judge")
    cid = cases.list_cases(s, "judge")[0]["id"]
    assert "Simulated calls work on the guided demo's cases" in _say(s, "demo call %s" % cid)


def test_next_outside_the_tour_is_ignored():
    assert _say(MemStore(), "next") == fmt.NO_ID_TEXT


def test_help_mentions_the_demo():
    assert "`demo` for a 1-minute guided tour" in fmt.HELP_TEXT
