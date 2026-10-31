import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lookups  # noqa: E402


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


def _ifsc(bank, state):
    return {
        "BANK": bank, "BRANCH": bank + " Main", "CITY": state, "DISTRICT": state, "STATE": state,
        "NEFT": True, "RTGS": True, "IMPS": True, "UPI": True,
    }


IFSC_DATA = {
    "YESB0DNB002": {
        "BANK": "Yes Bank", "BRANCH": "Delhi Nagrik Sehkari Bank", "CITY": "Delhi",
        "DISTRICT": "Delhi", "STATE": "Maharashtra",
        "NEFT": True, "RTGS": True, "IMPS": True, "UPI": True,
    },
    "SBIN0000001": _ifsc("State Bank of India", "West Bengal"),
    "HDFC0000001": _ifsc("HDFC Bank", "Maharashtra"),
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
            code = url.rsplit("/", 1)[-1]
            return FakeResp(200, IFSC_DATA[code]) if code in IFSC_DATA else FakeResp(404, "Not Found")
        if url.endswith("560001"):
            return FakeResp(200, PIN_OK)
        return FakeResp(200, PIN_NONE)

    monkeypatch.setattr(lookups.requests, "get", fake_get)
    return calls


@pytest.fixture
def no_net(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("no network expected")

    monkeypatch.setattr(lookups.requests, "get", fail)
