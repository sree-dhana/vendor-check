"""Tamper-evident decision log on the Fetch.ai ledger.

When a case is closed by the user (approved or rejected), a SHA-256 fingerprint of the case
record (changes, outcome, decision and the full audit trail) is written to the ledger as the
memo of a minimal transfer from the agent's wallet to itself. The fingerprint includes a
random per-case salt that is kept only in local storage, so the on-chain value reveals
nothing about the vendor or the bank details. `verify case <id>` recomputes the fingerprint
and compares it with the memo read back from the chain: a mismatch means the local record
changed after the decision.

No names, IDs, bank details or message text ever go on-chain. Ledger calls are made off the
event loop (see agent.py); this module only prepares, records and compares.

Checked against the installed libraries (2026-11-07): cosmpy LedgerClient.send_tokens(...,
memo=...) and the raw GetTx response (tx.body.memo); uagents.network.get_ledger(network)."""
import hashlib
import json
import os
import secrets
import time

import cases
import workflow

MEMO_PREFIX = "vendorcheck:v1:"
ANCHORED_STATES = (cases.APPROVED, cases.REJECTED)


def enabled(env=None):
    env = os.environ if env is None else env
    return str(env.get("ANCHOR_ENABLED", "0")).strip() == "1"


def network(env=None):
    """The ledger fingerprints are written to: ANCHOR_NETWORK, testnet unless set to mainnet.
    Separate from the agent's own network: a testnet agent gets a 'test-agent' address that
    ASI:One cannot reach, but the same wallet can anchor on testnet while the agent stays on
    mainnet."""
    env = os.environ if env is None else env
    return "mainnet" if str(env.get("ANCHOR_NETWORK", "testnet")).strip().lower() == "mainnet" else "testnet"


def agent_network(env=None):
    """The agent's own network. Keep mainnet: ASI:One reaches mainnet ('agent1...') addresses."""
    env = os.environ if env is None else env
    return "testnet" if str(env.get("AGENT_NETWORK", "mainnet")).strip().lower() == "testnet" else "mainnet"


def fingerprint(case):
    """SHA-256 over a canonical form of everything that matters about the decision."""
    record = {
        "v": 1,
        "salt": case["anchor_salt"],
        "case_id": case["id"],
        "vendor_id": case["vendor_id"],
        "changes": case["changes"],
        "flags": case["flags"],
        "outcome": case.get("outcome"),
        "decision": case.get("decision"),
        "audit": case["audit"],
    }
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def memo_for(case):
    return MEMO_PREFIX + fingerprint(case)


# ---------- choosing what to anchor (runs on the event loop) ----------

def mark_for_anchoring(store, owner, case_id):
    """Called when the user closes a case. Adds the local salt and queues the anchor."""
    data = cases._load(store, owner)
    case = data.get(case_id)
    if not case or case["state"] not in ANCHORED_STATES or case.get("anchor"):
        return False
    case["anchor_salt"] = secrets.token_hex(16)
    case["anchor"] = {"status": "pending"}
    cases._save(store, owner, data)
    return True


def pending(store, owner):
    """[(case_id, memo)] for closed cases waiting to be anchored."""
    return [(c["id"], memo_for(c)) for c in cases.list_cases(store, owner, open_only=False)
            if (c.get("anchor") or {}).get("status") == "pending"]


def record_anchor(store, owner, case_id, tx_hash, net, now=None):
    data = cases._load(store, owner)
    case = data[case_id]
    case["anchor"] = {"status": "anchored", "tx": tx_hash, "network": net,
                      "fingerprint": fingerprint(case), "at": time.time() if now is None else now}
    cases._save(store, owner, data)


def record_failure(store, owner, case_id, reason):
    data = cases._load(store, owner)
    anchor = data[case_id].setdefault("anchor", {"status": "pending"})
    anchor["attempts"] = anchor.get("attempts", 0) + 1
    anchor["last_error"] = reason[:80]
    if anchor["attempts"] >= 5:
        anchor["status"] = "failed"
    cases._save(store, owner, data)


def owners_with_pending(store):
    return [o for o in workflow.owners(store) if pending(store, o)]


# ---------- the ledger (network: call these off the event loop) ----------

class FetchLedger:
    """Minimal wrapper over the agent's own wallet and the Fetch.ai ledger client."""

    def __init__(self, wallet, ledger, net):
        self.wallet, self.ledger, self.network = wallet, ledger, net

    def balance(self):
        denom = self.ledger.network_config.fee_denomination
        return self.ledger.query_bank_balance(self.wallet.address(), denom)

    def submit(self, memo):
        """A 1-unit transfer to ourselves carrying the memo. Returns the transaction hash."""
        denom = self.ledger.network_config.fee_denomination
        tx = self.ledger.send_tokens(self.wallet.address(), 1, denom, self.wallet, memo=memo)
        tx.wait_to_complete()
        return tx.tx_hash

    def memo_of(self, tx_hash):
        from cosmpy.protos.cosmos.tx.v1beta1.service_pb2 import GetTxRequest
        resp = self.ledger.txs.GetTx(GetTxRequest(hash=tx_hash))
        return resp.tx.body.memo


def ledger_for_verify(net):
    """Read-only ledger access for `verify case`; the memo is read straight from the chain."""
    from uagents.network import get_ledger
    return FetchLedger(None, get_ledger(net), net)


# ---------- verification ----------

def verify(case, ledger):
    """'intact', 'changed', 'not_anchored', 'pending' or 'unavailable'. `ledger` needs memo_of()."""
    anchor = case.get("anchor") or {}
    if anchor.get("status") == "pending":
        return "pending"
    if anchor.get("status") != "anchored":
        return "not_anchored"
    try:
        on_chain = ledger.memo_of(anchor["tx"])
    except Exception:
        return "unavailable"
    return "intact" if on_chain == memo_for(case) else "changed"
