"""F7: several vendors in one message."""
import json

import pytest

import format as fmt
import vendors
from checks import gstin_check_char
from conftest import MemStore


def _gst(pan, state="27"):
    first14 = state + pan + "1Z"
    return first14 + gstin_check_char(first14)


A = _gst("AAPFU0939F")
B = _gst("ABCCD1234E", "29")
C = _gst("AAACM5678K", "07")
THREE = (
    "Sharma Traders GSTIN %s PAN AAPFU0939F IFSC YESB0DNB002\n"
    "Mehta & Sons GSTIN %s PAN ZZZPZ9999Z IFSC SBIN0000001\n"
    "Kumar Co GSTIN %s IFSC HDFC0000001" % (A, B, C)
)


def _say(text, store=None, **kw):
    return fmt.reply_for_text(text, store or MemStore(), sender="a", **kw)


def _rows(out):
    return [line for line in out.splitlines() if line.startswith("| ") and not line.startswith("| #")]


def test_three_vendors_one_with_a_pan_mismatch(fake_net):
    out = _say(THREE)
    # Kumar Co: Delhi GSTIN, Maharashtra bank -> to confirm. Mehta: problem (counted once).
    assert out.splitlines()[0] == "**Checked 3 vendors · 1 with problems · 1 to confirm**"
    rows = _rows(out)
    assert len(rows) == 3
    assert rows[0].startswith("| 1 | Sharma Traders | ✅ `%s` | ✅ | Yes Bank |" % A)
    assert rows[1].startswith("| 2 | Mehta & Sons | ✅ `%s` | ❌ |" % B)
    assert "❌ 1 problem" in rows[1]
    assert rows[2] == "| 3 | Kumar Co | ✅ `%s` | — | HDFC Bank | ⚠️ 1 to confirm |" % C
    assert rows[0].endswith("✅ no problems found |")
    assert "details 2" in out


@pytest.mark.parametrize("sep", ["\n\n", "; "])
def test_blank_lines_and_semicolons_also_split(fake_net, sep):
    text = sep.join(["Sharma Traders GSTIN %s" % A, "Kumar Co GSTIN %s" % C])
    rows = _rows(_say(text))
    assert [r.split(" | ")[1] for r in rows] == ["Sharma Traders", "Kumar Co"]


def test_one_vendor_with_semicolons_is_not_a_batch(fake_net):
    out = _say("GSTIN %s; PAN AAPFU0939F; IFSC YESB0DNB002" % A)
    assert "**Checks**" in out and "| # |" not in out


def test_over_cap_checks_first_five(fake_net):
    gsts = [_gst("AAPFU%04dF" % i) for i in range(7)]
    text = "\n".join("V%d GSTIN %s" % (i, g) for i, g in enumerate(gsts))
    out = _say(text)
    assert len(_rows(out)) == 5
    assert "I can check up to 5 vendors at once, so I checked the first 5." in out


def test_details_shows_the_full_check(fake_net):
    store = MemStore()
    _say(THREE, store)
    out = _say("details 2", store)
    assert out.startswith("**Vendor 2: Mehta & Sons**")
    assert "PAN mismatch" in out and "**Checks**" in out
    assert "Your last batch had 3 vendors" in _say("details 9", store)
    assert "I don't have a recent batch check" in _say("details 1")


def test_batch_detects_saved_vendor_bank_change(fake_net):
    store = MemStore()
    vendors.save_vendor(store, "a", "Kumar Co", {"gstin": C, "ifsc": "SBIN0000001"})
    rows = _rows(_say(THREE, store))
    assert "❌ bank details changed" in rows[2]


def test_batch_storage_holds_ids_not_text(fake_net):
    store = MemStore()
    _say(THREE, store)
    dumped = json.dumps(store.d)
    assert "Sharma Traders" in dumped  # the vendor name, for `details`
    assert THREE not in dumped and "Mehta & Sons GSTIN" not in dumped
    _say("forget all", store)
    assert "I don't have a recent batch check" in _say("details 1", store)
