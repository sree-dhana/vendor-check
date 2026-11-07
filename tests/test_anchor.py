"""Blockchain: tamper-evident decision log anchored on the Fetch.ai ledger (with a fake ledger)."""
import json
import os
import re
from types import SimpleNamespace

import pytest

import anchor
import cases
import format as fmt
import vendors
from conftest import MemStore

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "emails")


class FakeLedger:
    def __init__(self):
        self.memos, self.down = {}, False

    def submit(self, memo):
        tx = "TX%04d" % (len(self.memos) + 1)
        self.memos[tx] = memo
        return tx

    def memo_of(self, tx):
        if self.down:
            raise ConnectionError("ledger down")
        return self.memos[tx]


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("ANCHOR_ENABLED", "1")
    monkeypatch.setenv("ANCHOR_NETWORK", "testnet")


@pytest.fixture
def store():
    s = MemStore()
    vendors.save_vendor(s, "me", "Sharma Traders", {
        "gstin": "27AAPFU0939F1ZV", "ifsc": "YESB0DNB002", "acct_digits": "1234567890", "country": "IN",
        "phone": "+919876543210", "emails": ["accounts@sharmatraders.in"], "domains": ["sharmatraders.in"]}, now=0)
    return s


def _say(store, text, now=100):
    return fmt.reply_for_text(text, store, sender="me", now=now)


def _approved_case(store):
    with open(os.path.join(FIXTURES, "bank_change.txt"), encoding="utf-8") as f:
        _say(store, "check email\n" + f.read())
    cid = cases.list_cases(store, "me")[0]["id"]
    _say(store, "confirmed %s" % cid)
    return cid, _say(store, "approve %s 2222" % cid)


def _run_job(store, ledger):
    """What the agent's anchor job does, without the event loop."""
    for case_id, memo in anchor.pending(store, "me"):
        anchor.record_anchor(store, "me", case_id, ledger.submit(memo), "testnet", now=200)


# ---------- the fingerprint ----------

def test_fingerprint_is_salted_deterministic_and_has_no_data(on, store):
    cid, _ = _approved_case(store)
    case = cases.get_case(store, "me", cid)
    memo = anchor.memo_for(case)
    assert re.fullmatch(r"vendorcheck:v1:[0-9a-f]{64}", memo)
    assert anchor.memo_for(case) == memo  # deterministic
    other_salt = dict(case, anchor_salt="00" * 16)
    assert anchor.fingerprint(other_salt) != anchor.fingerprint(case)  # the local salt hides the content
    for detail in ("SBIN", "YESB", "2222", "Sharma", "27AAPFU"):
        assert detail not in memo


def test_fingerprint_changes_with_any_edit(on, store):
    cid, _ = _approved_case(store)
    case = cases.get_case(store, "me", cid)
    base = anchor.fingerprint(case)
    edited = json.loads(json.dumps(case))
    edited["changes"][0]["new"] = "HDFC0000001"
    assert anchor.fingerprint(edited) != base
    edited = json.loads(json.dumps(case))
    edited["audit"][-1]["by"] = "system"
    assert anchor.fingerprint(edited) != base


# ---------- queueing ----------

def test_only_closed_cases_are_anchored_once(on, store):
    with open(os.path.join(FIXTURES, "bank_change.txt"), encoding="utf-8") as f:
        _say(store, "check email\n" + f.read())
    cid = cases.list_cases(store, "me")[0]["id"]
    assert not anchor.mark_for_anchoring(store, "me", cid)  # still open
    out = _say(store, "reject %s" % cid)
    assert "will be anchored on the Fetch.ai testnet" in out
    assert len(anchor.pending(store, "me")) == 1
    assert not anchor.mark_for_anchoring(store, "me", cid)  # not twice


def test_approval_message_mentions_anchoring_only_when_on(monkeypatch, store):
    monkeypatch.setenv("ANCHOR_ENABLED", "0")
    cid, out = _approved_case(store)
    assert "anchored" not in out
    assert anchor.pending(store, "me") == []
    assert "has no on-chain record" in _say(store, "verify case %s" % cid)


# ---------- the full loop ----------

def test_anchor_then_verify_intact(on, store, monkeypatch):
    ledger = FakeLedger()
    monkeypatch.setattr(anchor, "ledger_for_verify", lambda net: ledger)
    cid, out = _approved_case(store)
    assert "will be anchored" in out
    assert "waiting to be anchored" in _say(store, "verify case %s" % cid)
    _run_job(store, ledger)
    case = cases.get_case(store, "me", cid)
    assert case["anchor"]["status"] == "anchored" and case["anchor"]["tx"] == "TX0001"
    assert ledger.memos["TX0001"] == anchor.memo_for(case)
    report = _say(store, "case %s" % cid)
    assert "anchored on the Fetch.ai testnet (transaction `TX0001`)" in report
    assert "✅ Case `%s` matches its on-chain fingerprint" % cid in _say(store, "verify case %s" % cid)


def test_tampering_is_detected(on, store, monkeypatch):
    ledger = FakeLedger()
    monkeypatch.setattr(anchor, "ledger_for_verify", lambda net: ledger)
    cid, _ = _approved_case(store)
    _run_job(store, ledger)
    data = store.d["cases:me"]
    data[cid]["changes"][0]["new"] = "HDFC0000001"  # someone edits the local record afterwards
    assert "does **not** match its on-chain fingerprint" in _say(store, "verify case %s" % cid)


def test_ledger_unavailable(on, store, monkeypatch):
    ledger = FakeLedger()
    monkeypatch.setattr(anchor, "ledger_for_verify", lambda net: ledger)
    cid, _ = _approved_case(store)
    _run_job(store, ledger)
    ledger.down = True
    assert "couldn't reach the Fetch.ai ledger" in _say(store, "verify case %s" % cid)


def test_failures_retry_then_stop(on, store):
    cid, _ = _approved_case(store)
    for _ in range(5):
        anchor.record_failure(store, "me", cid, "InsufficientFundsError")
    assert cases.get_case(store, "me", cid)["anchor"]["status"] == "failed"
    assert anchor.pending(store, "me") == []


def test_unknown_case(store):
    assert "I don't have a case `CZZZZZ`" in _say(store, "verify case CZZZZZ")


# ---------- config and the ledger wrapper ----------

def test_network_settings_are_separate():
    # fingerprints: testnet unless explicitly mainnet (free by default)
    assert anchor.network({}) == "testnet"
    assert anchor.network({"ANCHOR_NETWORK": "Mainnet"}) == "mainnet"
    assert anchor.network({"AGENT_NETWORK": "mainnet"}) == "testnet"  # independent of the agent
    # the agent itself: mainnet unless explicitly testnet (ASI:One reaches mainnet addresses)
    assert anchor.agent_network({}) == "mainnet"
    assert anchor.agent_network({"AGENT_NETWORK": "testnet"}) == "testnet"
    assert anchor.agent_network({"ANCHOR_NETWORK": "testnet"}) == "mainnet"
    assert not anchor.enabled({}) and anchor.enabled({"ANCHOR_ENABLED": "1"})


def test_fetch_ledger_sends_one_unit_to_itself_with_the_memo():
    sent = {}

    class Tx:
        tx_hash = "ABC123"

        def wait_to_complete(self):
            sent["waited"] = True

    def send_tokens(dest, amount, denom, wallet, memo=None):
        sent.update(dest=dest, amount=amount, denom=denom, memo=memo)
        return Tx()

    wallet = SimpleNamespace(address=lambda: "fetch1self")
    ledger = SimpleNamespace(network_config=SimpleNamespace(fee_denomination="atestfet"), send_tokens=send_tokens)
    tx = anchor.FetchLedger(wallet, ledger, "testnet").submit("vendorcheck:v1:" + "a" * 64)
    assert tx == "ABC123"
    assert sent == {"dest": "fetch1self", "amount": 1, "denom": "atestfet",
                    "memo": "vendorcheck:v1:" + "a" * 64, "waited": True}
