"""v3 step 1: country registry and the `start` questionnaire."""
import pytest

import countries
import format as fmt
import onboarding
from conftest import MemStore
from flags import contains_banned


def _say(store, text, sender="me"):
    return fmt.reply_for_text(text, store, sender=sender)


def _run(store, *answers, sender="me"):
    out = None
    for a in answers:
        out = _say(store, a, sender)
    return out


# ---------- country registry ----------

@pytest.mark.parametrize("typed, code", [
    ("India", "IN"), ("in", "IN"), ("Bharat", "IN"), ("UK", "GB"), ("england", "GB"),
    ("United States", "US"), ("usa", "US"), ("Germany", "DE"), ("de", "DE"), ("singapore", "SG"),
])
def test_resolve_country(typed, code):
    assert countries.resolve(typed) == code


def test_unknown_country_is_none_and_unsupported_falls_back_to_generic():
    assert countries.resolve("atlantis") is None
    assert countries.get_profile("SG").code == "GENERIC"
    assert countries.get_profile("XX").code == "GENERIC"
    assert countries.get_profile("IN").code == "IN"


def test_profiles_have_confidence_and_last_checked():
    for p in countries.PROFILES.values():
        assert p.confidence in ("full", "partial", "generic")
        assert len(p.last_checked) == 10
        for fact in p.payment_terms + p.call_rules.notes:
            assert fact.source.startswith("https://") and fact.last_checked


def test_only_india_can_enable_calls_so_far():
    enabled = {c for c, p in countries.PROFILES.items() if p.call_rules.calls_enabled}
    assert enabled == {"IN"}


def test_calls_off_unless_kill_switch_on():
    assert not countries.calls_globally_enabled({})
    assert not countries.calls_globally_enabled({"CALLS_ENABLED": "0"})
    assert countries.calls_globally_enabled({"CALLS_ENABLED": "1"})


def test_india_profile_runs_v1_checks(fake_net):
    results = countries.get_profile("IN").check_ids(
        {"gstin": "27AAPFU0939F1ZV", "ifsc": "YESB0DNB002"}, MemStore())
    assert [(r["kind"], r["level"]) for r in results] == [("gstin", "pass"), ("ifsc", "pass")]
    assert countries.get_profile("SG").check_ids({"gstin": "x"}, MemStore()) == []


# ---------- questionnaire ----------

def test_start_shows_intro_with_legal_notice(no_net):
    out = _say(MemStore(), "start")
    assert "Welcome to VendorCheck setup" in out
    assert onboarding.LEGAL_NOTICE in out
    assert "Reply `yes` to continue" in out


def test_full_questionnaire(fake_net):
    store = MemStore()
    _run(store, "start", "yes")
    assert "Do you pay vendors" in _say(store, "India")
    assert "Which countries are your vendors in" in _say(store, "both")
    assert "vendor email monitoring" in _say(store, "India, UK and Singapore")
    assert "Add your first vendor" in _say(store, "yes")
    out = _say(store, "save vendor Sharma Traders GSTIN 27AAPFU0939F1ZV IFSC YESB0DNB002")
    assert out.startswith("Saved **Sharma Traders**")
    assert "**Setup done.**" in out
    assert "**You are in India.** Rules in use: India: full profile" in out
    assert "UK: generic profile" in out and "Singapore: generic profile" in out
    assert "**Calls: off.**" in out
    p = onboarding.load(store, "me")
    assert (p["country"], p["role"], p["vendor_countries"], p["monitoring"], p["completed"]) == (
        "IN", "both", ["IN", "GB", "SG"], True, True)


def test_skip_first_vendor_and_same_country(no_net):
    store = MemStore()
    _run(store, "start", "yes", "UK", "pay", "same", "no")
    out = _say(store, "skip")
    assert "**You are in UK.** Rules in use: UK: generic profile" in out
    assert "**Email monitoring:** no." in out
    assert onboarding.load(store, "me")["vendor_countries"] == ["GB"]


def test_bad_answers_reask(no_net):
    store = MemStore()
    _run(store, "start")
    assert _say(store, "maybe") == "Reply `yes` to continue, or `no` to stop."
    _say(store, "yes")
    assert "I don't recognise that country" in _say(store, "atlantis")
    _say(store, "India")
    assert "Reply `pay`, `get paid` or `both`" in _say(store, "dunno")
    _say(store, "pay")
    assert "I didn't recognise: narnia" in _say(store, "India, Narnia")


def test_no_at_consent_keeps_nothing(no_net):
    store = MemStore()
    _run(store, "start")
    assert "kept nothing" in _say(store, "no")
    assert not onboarding.has_profile(store, "me")


def test_ids_during_setup_still_get_checked(fake_net):
    store = MemStore()
    _run(store, "start", "yes")
    out = _say(store, "GSTIN 27AAPFU0939F1ZV")
    assert "valid format and checksum" in out
    assert "Do you pay vendors" in _say(store, "India")  # setup continues


def test_help_works_during_setup(no_net):
    store = MemStore()
    _run(store, "start", "yes")
    assert _say(store, "help") == fmt.HELP_TEXT


def test_cancel_pauses_setup(no_net):
    store = MemStore()
    _run(store, "start", "yes")
    assert "Setup paused" in _say(store, "cancel")
    assert _say(store, "hello") == fmt.HELP_TEXT


def test_settings_show_and_edit(no_net):
    store = MemStore()
    assert "Say `start` to begin" in _say(store, "settings")
    _run(store, "start", "yes", "India", "pay", "India", "no", "skip")
    menu = _say(store, "settings")
    assert "**Your settings**" in menu and "`settings country`" in menu
    assert "Which country is your business in" in _say(store, "settings country")
    out = _say(store, "Germany")
    assert out.startswith("**Updated.**") and "You are in Germany" in out
    assert onboarding.load(store, "me")["step"] == "done"


def test_calls_line_when_switch_is_on(monkeypatch):
    monkeypatch.setenv("CALLS_ENABLED", "1")
    line = onboarding.calls_line(["IN", "GB"])
    assert "possible only for vendors in India" in line and "Not enabled for UK" in line
    assert "not enabled" in onboarding.calls_line(["SG"])


def test_forget_all_clears_setup(no_net):
    store = MemStore()
    _run(store, "start", "yes", "India", "pay", "same", "no", "skip")
    _say(store, "forget all")
    assert not onboarding.has_profile(store, "me")


def test_first_contact(no_net):
    store = MemStore()
    assert "Welcome to VendorCheck setup" in fmt.first_contact(store, "new")
    _run(store, "yes", "India", "pay", "same", "no", "skip", sender="new")
    assert fmt.first_contact(store, "new") == fmt.HELP_TEXT


def test_setup_text_has_no_banned_phrases(monkeypatch):
    texts = [onboarding.INTRO, onboarding.LEGAL_NOTICE, *onboarding.QUESTIONS.values(),
             onboarding.summary({"country": "IN", "role": "both", "vendor_countries": ["IN", "US"],
                                 "monitoring": True})]
    monkeypatch.setenv("CALLS_ENABLED", "1")
    texts.append(onboarding.calls_line(["IN", "US"]))
    for t in texts:
        assert not contains_banned(t), t


def test_separate_users_have_separate_setup(no_net):
    store = MemStore()
    _run(store, "start", "yes", "India", sender="a")
    assert not onboarding.is_active(store, "b")
    assert _say(store, "pay", sender="b") != onboarding.QUESTIONS["vendor_countries"]
