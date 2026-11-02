"""v3 step 3: vendor registration with phone, emails, IBAN, salted account hash."""
import json

import pytest

import format as fmt
import vendors
from conftest import MemStore
from flags import contains_banned

GST = "27AAPFU0939F1ZV"
FULL = ("save vendor Sharma Traders GSTIN %s IFSC YESB0DNB002 account 1234567890 "
        "phone +91 98765 43210 email Accounts@SharmaTraders.in contact Ravi Sharma "
        "legal name Sharma Traders Pvt Ltd" % GST)


def _say(store, text, sender="me", now=1000):
    return fmt.reply_for_text(text, store, sender=sender, now=now)


def test_full_registration(no_net):
    store = MemStore()
    out = _say(store, FULL)
    assert out.startswith("Saved **Sharma Traders** (India):")
    rec = vendors.get_vendor(store, "me", "Sharma Traders")
    assert rec["phone"] == "+919876543210"
    assert rec["emails"] == ["accounts@sharmatraders.in"]
    assert rec["domains"] == ["sharmatraders.in"]
    assert rec["contact"] == "Ravi Sharma"
    assert rec["legal_name"] == "Sharma Traders Pvt Ltd"
    assert rec["acct_last4"] == "7890" and len(rec["acct_hash"]) == 32 and rec["acct_salt"]
    assert "Add a phone number" not in out and "Add their email" not in out


def test_full_account_number_never_stored(no_net):
    store = MemStore()
    _say(store, FULL)
    _say(store, "GSTIN %s acct 1234567890" % GST)
    assert "1234567890" not in json.dumps(store.d)


def test_missing_phone_and_email_hints(no_net):
    out = _say(MemStore(), "save vendor Mehta GSTIN %s" % GST)
    assert "Add a phone number" in out and "Add their email" in out


def test_merge_keeps_earlier_details(no_net):
    store = MemStore()
    _say(store, "save vendor Mehta GSTIN %s" % GST)
    out = _say(store, "save vendor Mehta phone 9876543210")
    assert out.startswith("Updated **Mehta**")
    rec = vendors.get_vendor(store, "me", "mehta")
    assert rec["gstin"] == GST and rec["phone"] == "+919876543210"


def test_phone_change_warns(no_net):
    store = MemStore()
    _say(store, "save vendor Mehta GSTIN %s phone +91 98765 43210" % GST)
    out = _say(store, "save vendor Mehta phone +91 91234 56789")
    assert "The saved phone changed from `+919876543210` to `+919123456789`" in out
    assert "never from an email asking for a change" in out


@pytest.mark.parametrize("raw, country, expect", [
    ("+44 20 7946 0000", None, "+442079460000"),
    ("0044 20 7946 0000", None, "+442079460000"),
    ("98765 43210", "IN", "+919876543210"),
    ("080 2345 6789", "IN", "+918023456789"),
    ("919876543210", None, "+919876543210"),
    ("020 7946 0000", "GB", None),    # no country code outside India
    ("+12", None, None),
])
def test_normalize_phone(raw, country, expect):
    assert vendors.normalize_phone(raw, country) == expect


def test_bad_phone_is_reported_not_saved(no_net):
    store = MemStore()
    out = _say(store, "save vendor UKCo country UK phone 020 7946 0000 email a@ukco.co.uk")
    assert "I couldn't read the phone number" in out
    assert "phone" not in vendors.get_vendor(store, "me", "UKCo")


def test_eu_vendor_with_iban(no_net):
    store = MemStore()
    out = _say(store, "save vendor Acme GmbH IBAN GB82 WEST 1234 5698 7654 32 email billing@acme.de")
    rec = vendors.get_vendor(store, "me", "Acme GmbH")
    assert rec["iban"] == "GB82WEST12345698765432" and rec["country"] == "GB"
    assert "IBAN `GB82WEST12345698765432`" in out


def test_invalid_ids_warn_on_save(no_net):
    out = _say(MemStore(), "save vendor Typo Co GSTIN 27AAPFU0939F1ZX IBAN GB82WEST12345698765433")
    assert "⚠️ GSTIN `27AAPFU0939F1ZX`: checksum does not match" in out
    assert "⚠️ IBAN `GB82WEST12345698765433`: check digits don't match" in out


def test_vendor_card_and_forget_vendor(no_net):
    store = MemStore()
    _say(store, FULL)
    card = _say(store, "vendor sharma traders")
    assert "**Legal name:** Sharma Traders Pvt Ltd" in card
    assert "**Saved phone:** `+919876543210` (the only number I'll ever suggest calling)" in card
    assert "**Expected emails from:** accounts@sharmatraders.in" in card
    assert "**Consent for verification calls:** not requested" in card
    assert not contains_banned(card)
    assert _say(store, "forget vendor Sharma Traders") == "Deleted **Sharma Traders**."
    assert vendors.get_vendor(store, "me", "Sharma Traders") is None


def test_same_last4_different_account_is_a_change(no_net):
    store = MemStore()
    _say(store, "save vendor Kumar GSTIN %s IFSC SBIN0000001 acct 111122227890" % GST)
    out = _say(store, "GSTIN %s acct 999988887890" % GST)
    assert "Bank details changed since you saved Kumar" in out
    assert "a different number with the same last 4 digits" in out
    same = _say(store, "GSTIN %s acct 111122227890" % GST)
    assert "Bank details changed" not in same


def test_iban_change_detected_by_iban_match(no_net):
    store = MemStore()
    _say(store, "save vendor Acme IBAN GB82WEST12345698765432 acct 12345678")
    out = _say(store, "IBAN GB82WEST12345698765432 please pay")
    assert "Same IBAN as you saved for Acme." in out


def test_old_records_get_a_stable_id(no_net):
    store = MemStore()
    store.set("vendors:me", {"old co": {"name": "Old Co", "gstin": GST}})
    a = vendors.get_vendor(store, "me", "Old Co")["id"]
    b = vendors.get_vendor(store, "me", "old co")["id"]
    assert a == b and a.startswith("v")


def test_name_stops_at_email_and_labels():
    assert vendors.name_part("Sharma Traders accounts@x.in") == "Sharma Traders"
    assert vendors.name_part("Acme GmbH IBAN GB82...") == "Acme GmbH"
    assert vendors.name_part("Kumar Co phone +91 98765") == "Kumar Co"


def test_default_country_from_setup(no_net):
    store = MemStore()
    for a in ("start", "yes", "UK", "pay", "same", "no", "skip"):
        _say(store, a)
    _say(store, "save vendor Brit Ltd email ap@brit.co.uk phone +44 20 7946 0000")
    assert vendors.get_vendor(store, "me", "Brit Ltd")["country"] == "GB"
