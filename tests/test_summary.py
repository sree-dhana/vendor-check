"""F6: optional plain-language summary."""
import json

import pytest
import requests

import format as fmt
import llm
from conftest import MemStore
from flags import contains_banned
from test_llm import FakeClient, _cfg

GST = "27AAPFU0939F1ZV"
MSG = "Hello friend, secret note 4471. GSTIN %s PAN ABCDE1234F" % GST
GOOD = "The GSTIN checksum is valid but the PAN you gave does not match the one inside it. Registration status was not checked."


def _say(text, client, store=None, summary=True):
    return fmt.reply_for_text(text, store or MemStore(), sender="a",
                              llm=_cfg(client, extract=False, summary=summary))


def test_valid_summary_is_shown_before_the_disclaimer(no_net):
    out = _say(MSG, FakeClient({"summary": GOOD}))
    assert "**In short** _(written by an AI model (ASI:One) from the checks above)_: " + GOOD in out
    assert out.index("**Before you pay**") < out.index("**In short**") < out.index(fmt.DISCLAIMER)


def test_summary_never_receives_the_raw_message(no_net):
    client = FakeClient({"summary": GOOD})
    _say(MSG, client)
    sent = client.calls[0]["user"]
    assert "secret note" not in sent and "Hello friend" not in sent
    data = json.loads(sent)
    assert data["counts"]["fail"] == 1
    assert {i["id"] for i in data["items"]} == {GST, "ABCDE1234F"}


@pytest.mark.parametrize("reply", [
    {"summary": "All good, this vendor is safe to pay."},                   # banned phrase
    {"summary": "Looks trusted."},                                          # banned phrase
    {"summary": "The GSTIN 29ABCDE1234F1Z5 belongs to another state."},     # ID not in results
    {"summary": "Checked. " + "x" * 400},                                   # too long
    {"summary": "The bank account details are confirmed."},                 # overclaim
    {"summary": "The GSTIN is verified."},                                  # overclaim
    {"summary": "One. Two. Three."},                                        # over 2 sentences
    {"summary": ""},
    {"summary": 42},
    {"nope": "x"},
    "not json",
    requests.Timeout("slow"),
    llm.LLMError("HTTP 429"),
])
def test_rejected_or_failed_summary_sends_the_normal_reply(no_net, reply):
    out = _say(MSG, FakeClient(reply))
    assert out == fmt.reply_for_text(MSG, MemStore(), sender="a")
    assert "**In short**" not in out


def test_ids_mentioned_in_the_results_are_allowed(no_net):
    ok = "GSTIN %s contains PAN AAPFU0939F, which differs from ABCDE1234F." % GST
    assert ok in _say(MSG, FakeClient({"summary": ok}))


def test_summary_is_off_by_default(no_net):
    client = FakeClient({"summary": GOOD})
    out = _say(MSG, client, summary=False)
    assert client.calls == [] and "**In short**" not in out


def test_summary_counts_towards_the_llm_cap(no_net):
    client = FakeClient({"summary": GOOD})
    store = MemStore()
    for _ in range(7):
        _say(MSG, client, store)
    assert len(client.calls) == 5


def test_validate_summary_unit():
    results = {"items": [{"id": GST, "text": "PAN inside it: AAPFU0939F"}], "cross": []}
    assert llm.validate_summary("  Two   spaces. ", results) == "Two spaces."
    assert llm.validate_summary("It is guaranteed.", results) is None
    assert llm.validate_summary("PAN AAPFU0939F is inside it.", results) == "PAN AAPFU0939F is inside it."
    assert llm.validate_summary("There is one thing to confirm.", results) == "There is one thing to confirm."
    assert not contains_banned(GOOD)
