"""v3 step 2: UK, EU and US profiles, IBAN validation, VIES lookup, `rules <country>`."""
import pytest

import countries
import format as fmt
import lookups
from conftest import FakeResp, MemStore
from flags import contains_banned
from iban import IBAN_LENGTHS, check_iban, mod97

# Example IBANs from https://en.wikipedia.org/wiki/International_Bank_Account_Number
VALID = ["GB82WEST12345698765432", "IE64IRCE92050112345678", "GB82 WEST 1234 5698 7654 32"]


# ---------- IBAN ----------

def test_wikipedia_worked_example():
    assert int("3214282912345698765432161182") % 97 == 1
    assert mod97("GB82WEST12345698765432") == 1


@pytest.mark.parametrize("iban", VALID)
def test_valid_ibans_pass(iban):
    r = check_iban(iban)
    assert r["level"] == "pass", r
    assert "doesn't show that the account exists" in r["text"]


@pytest.mark.parametrize("iban, why", [
    ("GB82WEST12345698765433", "check digits"),  # last digit changed
    ("GB28WEST12345698765432", "check digits"),  # check digits swapped
    ("GB82WEST1234569876543", "wrong length"),    # 21 characters
    ("DE89 3704 0044 0532 0130", "wrong length"),
    ("12345", "wrong format"),
])
def test_bad_ibans_fail(iban, why):
    r = check_iban(iban)
    assert r["level"] == "fail" and why in r["text"]


def test_unlisted_country_with_good_digits_warns():
    body = "X" * 11
    for cd in range(100):
        cand = "ZZ%02d%s" % (cd, body)
        if mod97(cand) == 1:
            break
    r = check_iban(cand)
    assert r["level"] == "warn" and "not in my IBAN length table" in r["text"]


def test_length_table_covers_every_eu_eea_profile_country():
    assert countries.EU_EEA <= set(IBAN_LENGTHS)


# ---------- VIES ----------

@pytest.fixture
def vies(monkeypatch):
    seen = []
    replies = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen.append(json)
        reply = replies.get(json["vatNumber"], FakeResp(500, None))
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(lookups.requests, "post", fake_post)
    return seen, replies


def test_vies_valid_invalid_and_outages(vies):
    seen, replies = vies
    replies["100"] = FakeResp(200, {"valid": True, "name": "John Doe"})
    replies["200"] = FakeResp(200, {"valid": False, "name": "---"})
    replies["300"] = FakeResp(200, {"actionSucceed": False, "errorWrappers": [{"error": "MS_UNAVAILABLE"}]})
    replies["400"] = TimeoutError("slow")
    store = MemStore()
    assert lookups.vies_result("DE", "DE100", store)["level"] == "pass"
    assert "John Doe" in lookups.vies_result("DE", "100", store)["text"]
    assert len(seen) == 1  # second call came from the cache
    assert "does not list" in lookups.vies_result("DE", "200", store)["text"]
    for n in ("300", "400", "999"):
        r = lookups.vies_result("DE", n, store)
        assert r["level"] == "warn" and "couldn't confirm" in r["text"]


def test_vies_uses_el_for_greece(vies):
    seen, replies = vies
    replies["123"] = FakeResp(200, {"valid": True, "name": ""})
    lookups.vies_result("GR", "EL123", MemStore())
    assert seen[-1] == {"countryCode": "EL", "vatNumber": "123"}


# ---------- profiles ----------

def test_every_profile_loads_with_confidence_dates_and_sources():
    assert set(countries.PROFILES) == {"IN", "GB", "EU", "US", "GENERIC"}
    for p in countries.PROFILES.values():
        assert p.confidence in ("full", "partial", "generic")
        assert len(p.last_checked) == 10
        for f in p.payment_terms + p.call_rules.notes:
            assert f.source.startswith("https://") and len(f.last_checked) == 10


def test_routing():
    assert countries.get_profile("GB").code == "GB"
    assert countries.get_profile("DE").code == "EU"
    assert countries.get_profile("NO").code == "EU"
    assert countries.get_profile("US").code == "US"
    assert countries.get_profile("CH").code == "GENERIC"
    assert countries.get_profile("XX").code == "GENERIC"


def test_only_india_can_ever_enable_calls():
    assert [c for c, p in countries.PROFILES.items() if p.call_rules.calls_enabled] == ["IN"]


def test_uk_facts_match_gov_uk():
    text = " ".join(f.text for f in countries.PROFILES["GB"].payment_terms)
    assert "8% plus the Bank of England base rate" in text
    assert "£40" in text and "£70" in text and "£100" in text


def test_us_has_no_unverified_id_checks():
    p = countries.PROFILES["US"]
    assert p.check_ids({"routing": "011000015", "ein": "12-3456789"}, MemStore()) == []
    assert "routing number checksum" in p.not_researched and "EIN format" in p.not_researched


def test_eu_and_uk_id_checks(vies):
    seen, replies = vies
    replies["100"] = FakeResp(200, {"valid": True, "name": ""})
    res = countries.get_profile("DE").check_ids(
        {"country": "DE", "iban": "GB82WEST12345698765432", "vat": "100"}, MemStore())
    assert [(r["kind"], r["level"]) for r in res] == [("iban", "pass"), ("vat", "pass")]
    # Norway is EEA but not in VIES: IBAN only
    res = countries.get_profile("NO").check_ids({"country": "NO", "vat": "100"}, MemStore())
    assert res == []
    res = countries.get_profile("GB").check_ids({"iban": "GB82WEST12345698765432"}, MemStore())
    assert res[0]["level"] == "pass"


# ---------- `rules <country>` ----------

@pytest.mark.parametrize("country, expect", [
    ("UK", "8% plus the Bank of England base rate"),
    ("Germany", "(uses the EU/EEA profile)"),
    ("US", "Penal Code 632"),
    ("India", "GSTIN, PAN, IFSC and PIN"),
    ("Singapore", "Generic (generic)"),
])
def test_rules_command(no_net, country, expect):
    out = fmt.reply_for_text("rules " + country, MemStore())
    assert expect in out
    assert "not legal or tax advice" in out
    assert not contains_banned(out)


def test_rules_unknown_country(no_net):
    assert "I don't recognise that country" in fmt.reply_for_text("rules Narnia", MemStore())
