"""F5: opt-in vendor memory and bank-change detection."""
import json

import format as fmt
import vendors
from conftest import MemStore

GST = "27AAPFU0939F1ZV"
FIRST = "GSTIN %s PAN AAPFU0939F IFSC SBIN0000001 acct 1234567890" % GST


def _say(store, text, sender="alice", now=1000):
    return fmt.reply_for_text(text, store, sender=sender, now=now)


def test_save_after_a_check_and_recall(fake_net):
    store = MemStore()
    _say(store, FIRST)
    out = _say(store, "save vendor Sharma Traders")
    assert "Saved **Sharma Traders**" in out
    rec = vendors.get_vendor(store, "alice", "sharma traders")
    assert rec == {"name": "Sharma Traders", "gstin": GST, "pan": "AAPFU0939F",
                   "ifsc": "SBIN0000001", "acct_last4": "7890"}
    listing = _say(store, "my vendors")
    assert "**Sharma Traders**" in listing and "••••7890" in listing


def test_save_with_ids_in_the_same_message(fake_net):
    store = MemStore()
    out = _say(store, "save vendor Mehta & Sons, GSTIN %s, IFSC SBIN0000001" % GST)
    assert "Saved **Mehta & Sons**" in out
    assert vendors.get_vendor(store, "alice", "Mehta & Sons")["ifsc"] == "SBIN0000001"


def test_full_account_number_is_never_stored(fake_net):
    store = MemStore()
    _say(store, FIRST)
    _say(store, "save vendor Sharma Traders")
    assert "1234567890" not in json.dumps(store.d)


def test_detect_ifsc_change(fake_net):
    store = MemStore()
    _say(store, FIRST)
    _say(store, "save vendor Sharma Traders")
    out = _say(store, "check Sharma Traders GSTIN %s IFSC HDFC0000001" % GST, now=2000)
    assert "❌ **Bank details changed since you saved Sharma Traders**" in out
    assert "IFSC: was `SBIN•••••01`, now `HDFC•••••01`" in out
    assert out.index("Bank details changed") < out.index("**Checks**")
    assert "1 problem**" in out.splitlines()[0]


def test_change_detected_by_gstin_without_naming_the_vendor(fake_net):
    store = MemStore()
    _say(store, "save vendor Sharma Traders GSTIN %s acct 1234567890" % GST)
    out = _say(store, "GSTIN %s please pay to acct 99887766554433" % GST)
    assert "Account: was `••••7890`, now `••••4433`" in out


def test_unchanged_details_pass(fake_net):
    store = MemStore()
    _say(store, FIRST)
    _say(store, "save vendor Sharma Traders")
    out = _say(store, "check Sharma Traders IFSC SBIN0000001 GSTIN %s" % GST)
    assert "Same GSTIN and IFSC as you saved for Sharma Traders." in out
    assert "Bank details changed" not in out


def test_check_name_alone_uses_saved_ids(fake_net):
    store = MemStore()
    _say(store, FIRST)
    _say(store, "save vendor Sharma Traders")
    out = _say(store, "check sharma traders")
    assert "**Checks** (the details you saved for Sharma Traders)" in out
    assert GST in out and "SBIN0000001" in out
    assert "check this vendor" not in _say(store, "check Unknown Co").lower()
    assert "I don't have a saved vendor called **Unknown Co**" in _say(store, "check Unknown Co")


def test_check_this_vendor_still_works(fake_net):
    store = MemStore()
    _say(store, "save vendor Sharma Traders GSTIN %s" % GST)
    out = _say(store, "Check this vendor: GSTIN %s, PAN AAPFU0939F" % GST)
    assert "**Checks**" in out and "PAN AAPFU0939F matches" in out


def test_forget_removes_data(fake_net):
    store = MemStore()
    _say(store, FIRST)
    _say(store, "save vendor A")
    _say(store, "save vendor B GSTIN %s" % GST)
    assert _say(store, "forget a") == "Deleted **a**."
    assert vendors.get_vendor(store, "alice", "A") is None
    assert "Deleted 1 saved vendor and your recent check." == _say(store, "forget all")
    assert vendors.list_vendors(store, "alice") == []
    assert vendors.last_ids(store, "alice", 1000) == {}
    assert "I didn't find any IDs" in _say(store, "save vendor C")


def test_caps_and_name_cleaning(fake_net):
    store = MemStore()
    for i in range(vendors.MAX_VENDORS):
        assert vendors.save_vendor(store, "alice", "V%d" % i, {"gstin": GST}) == "saved"
    assert vendors.save_vendor(store, "alice", "one more", {"gstin": GST}) == "full"
    assert vendors.save_vendor(store, "alice", "V1", {"gstin": GST}) == "updated"
    assert vendors.clean_name("Bad\x00Name\x1b[31m" + "x" * 60) == ("BadName[31m" + "x" * 60)[:40]
    assert len(vendors.clean_name("y" * 100)) == vendors.MAX_NAME


def test_per_sender_isolation(fake_net):
    store = MemStore()
    _say(store, "save vendor Sharma Traders GSTIN %s" % GST, sender="alice")
    assert vendors.get_vendor(store, "bob", "Sharma Traders") is None
    assert "haven't saved any vendors" in _say(store, "my vendors", sender="bob")
    _say(store, "forget all", sender="bob")
    assert vendors.get_vendor(store, "alice", "Sharma Traders") is not None


def test_last_check_expires(fake_net):
    store = MemStore()
    _say(store, FIRST, now=1000)
    assert vendors.last_ids(store, "alice", 1000 + vendors.LAST_CHECK_TTL + 1) == {}
    assert "I didn't find any IDs" in _say(store, "save vendor Late", now=1000 + vendors.LAST_CHECK_TTL + 1)


def test_account_last4_parsing():
    assert vendors.account_last4("acct 1234567890") == "7890"
    assert vendors.account_last4("A/C No. 1234 5678 9012") == "9012"
    assert vendors.account_last4("account number: 000123456789") == "6789"
    assert vendors.account_last4("account 12345") is None
    assert vendors.account_last4("invoice 1234567890") is None
