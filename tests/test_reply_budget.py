"""Tester feedback: "just needs to respond" and "if a lookup or model call fails, return the
deterministic checks that are available". Slow services must never hold a reply back."""
import format as fmt
from conftest import MemStore
from test_llm import EMPTY, FakeClient, _cfg

GST = "27AAPFU0939F1ZV"


class SlowClock:
    """Each read moves time forward, as if every step were slow."""

    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def test_summary_skipped_when_reply_is_already_slow(monkeypatch, no_net):
    monkeypatch.setattr(fmt, "_clock", SlowClock(fmt.REPLY_BUDGET_S))
    client = FakeClient({"summary": "Two checks passed."})
    out = fmt.reply_for_text("GSTIN %s PAN ABCDE1234F" % GST, MemStore(), sender="a",
                             llm=_cfg(client, extract=False, summary=True))
    assert client.calls == []                  # the slow optional step was skipped
    assert "PAN mismatch" in out               # the code-based checks still came back
    assert "**In short**" not in out


def test_extraction_skipped_when_over_budget_but_reply_still_sent(monkeypatch, no_net):
    monkeypatch.setattr(fmt, "_clock", SlowClock(fmt.REPLY_BUDGET_S))
    client = FakeClient(dict(EMPTY, ifsc=["SBIN0000001"]))
    out = fmt.reply_for_text("Bank branch code SBIN 0000001 please", MemStore(), sender="a",
                             llm=_cfg(client))
    assert client.calls == [] and out == fmt.NO_ID_TEXT


def test_fast_replies_still_use_the_ai_steps(no_net):
    client = FakeClient({"summary": "The PAN does not match the one inside the GSTIN."})
    out = fmt.reply_for_text("GSTIN %s PAN ABCDE1234F" % GST, MemStore(), sender="a",
                             llm=_cfg(client, extract=False, summary=True))
    assert len(client.calls) == 1 and "**In short**" in out


def test_no_id_message_from_tester_gets_an_answer(no_net):
    out = fmt.reply_for_text("this bank is saying i havent paid this store", MemStore(), sender="a")
    assert out == fmt.NO_ID_TEXT
