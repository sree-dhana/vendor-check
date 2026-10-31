import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import format as fmt  # noqa: E402
import lookups  # noqa: E402
from checks import check_gstin, extract_ids, gstin_check_char  # noqa: E402


class MemStore:
    def __init__(self):
        self.d = {}

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value):
        self.d[key] = value


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


IFSC_OK = {
    "BANK": "Yes Bank", "BRANCH": "Delhi Nagrik Sehkari Bank", "CITY": "Delhi",
    "DISTRICT": "Delhi", "STATE": "Maharashtra",
    "NEFT": True, "RTGS": True, "IMPS": True, "UPI": True,
}
PIN_OK = [{
    "Status": "Success", "Message": "Number of pincode(s) found:1",
    "PostOffice": [{"Name": "Bangalore GPO", "District": "Bangalore", "State": "Karnataka"}],
}]
PIN_NONE = [{"Status": "Error", "Message": "No records found", "PostOffice": None}]


@pytest.fixture
def fake_net(monkeypatch):
    calls = []

    def fake_get(url, timeout=None, headers=None):
        calls.append(url)
        if "razorpay" in url:
            return FakeResp(200, IFSC_OK) if url.endswith("YESB0DNB002") else FakeResp(404, "Not Found")
        if url.endswith("560001"):
            return FakeResp(200, PIN_OK)
        return FakeResp(200, PIN_NONE)

    monkeypatch.setattr(lookups.requests, "get", fake_get)
    return calls


def test_gstin_checksum_valid_and_invalid():
    assert gstin_check_char("27AAPFU0939F1Z") == "V"
    assert check_gstin("27AAPFU0939F1ZV")["level"] == "pass"
    assert check_gstin("27AAPFU0939F1ZX")["level"] == "fail"


def _gstin(state_code, rest="AAPFU0939F1Z"):
    first14 = state_code + rest
    return first14 + gstin_check_char(first14)


def test_retired_and_unknown_state_codes_warn():
    for code in ("25", "28"):
        r = check_gstin(_gstin(code))
        assert r["level"] == "warn"
        assert "no longer issued" in r["text"]
        assert r["state"] is None  # no cross-check against a retired state
    assert "not in my list" in check_gstin(_gstin("40"))["text"]
    assert check_gstin(_gstin("37"))["state"] == "Andhra Pradesh"


def test_extract_ids_mixed_and_malformed():
    found, bad = extract_ids("GSTIN 27AAPFU0939F1ZV, PAN AAPFU0939F, IFSC YESB0DNB002, GST 12345")
    assert found["gstin"] == ["27AAPFU0939F1ZV"]
    assert found["pan"] == ["AAPFU0939F"]
    assert found["ifsc"] == ["YESB0DNB002"]
    assert bad["gstin"] == ["12345"]


def test_bare_six_digits_only_count_as_pin_alone_or_after_keyword():
    assert extract_ids("560001")[0]["pin"] == ["560001"]
    assert extract_ids("pincode 560001")[0]["pin"] == ["560001"]
    assert extract_ids("invoice 560001 amount")[0]["pin"] == []


def test_full_vendor_check_all_pass(fake_net):
    out = fmt.reply_for_text(
        "Check this vendor: GSTIN 27AAPFU0939F1ZV, PAN AAPFU0939F, IFSC YESB0DNB002", MemStore()
    )
    assert "0 problems" in out
    assert "PAN AAPFU0939F matches" in out
    assert "bank branch state (Maharashtra)" in out


def test_pan_mismatch_is_a_problem(fake_net):
    out = fmt.reply_for_text("GSTIN 27AAPFU0939F1ZV PAN ABCDE1234F", MemStore())
    assert "PAN mismatch" in out
    assert "1 problem**" in out


def test_pin_lookup_and_not_found(fake_net):
    assert "Bangalore, Karnataka" in fmt.reply_for_text("pincode 560001", MemStore())
    assert "no area with this PIN" in fmt.reply_for_text("pincode 999999", MemStore())


def test_ifsc_not_found_and_cache(fake_net):
    store = MemStore()
    assert "no bank branch" in fmt.reply_for_text("IFSC ABCD0XXXXX1", store)
    fmt.reply_for_text("IFSC YESB0DNB002", store)
    fmt.reply_for_text("IFSC YESB0DNB002", store)
    assert sum("YESB0DNB002" in u for u in fake_net) == 1


def test_greetings_sample_and_nonsense(monkeypatch):
    def no_net(*a, **k):
        raise AssertionError("no network expected")

    monkeypatch.setattr(lookups.requests, "get", no_net)
    assert fmt.reply_for_text("hello", MemStore()) == fmt.HELP_TEXT
    assert fmt.reply_for_text("  ", MemStore()) == fmt.HELP_TEXT
    assert fmt.reply_for_text("sample", MemStore()) == fmt.SAMPLE_TEXT
    assert fmt.reply_for_text("what's the weather", MemStore()) == fmt.NO_ID_TEXT


def test_leading_mentions_are_ignored(fake_net):
    addr = "@agent1qfgjfnaffngvss45953n0x362p0hyfj7xpag073lppuqnnsdruyzg6750aq"
    assert fmt.reply_for_text(addr + " help", MemStore()) == fmt.HELP_TEXT
    assert fmt.reply_for_text("@vendor-check sample", MemStore()) == fmt.SAMPLE_TEXT
    assert fmt.reply_for_text(addr, MemStore()) == fmt.HELP_TEXT
    assert "Bangalore, Karnataka" in fmt.reply_for_text("@vendor-check pincode 560001", MemStore())


def test_lookups_send_non_default_user_agent(monkeypatch):
    seen = []

    def fake_get(url, timeout=None, headers=None):
        seen.append((headers or {}).get("User-Agent", ""))
        return FakeResp(200, PIN_OK)

    monkeypatch.setattr(lookups.requests, "get", fake_get)
    lookups.lookup_pin("560001", MemStore())
    lookups.lookup_ifsc("YESB0DNB002", MemStore())
    assert len(seen) == 2
    assert all(ua and "python-requests" not in ua for ua in seen)


def test_lookup_unavailable_degrades_to_warning(monkeypatch):
    def down(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(lookups.requests, "get", down)
    out = fmt.reply_for_text("IFSC YESB0DNB002", MemStore())
    assert "couldn't confirm the branch" in out
