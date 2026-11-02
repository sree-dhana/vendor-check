"""v3 step 4: case state machine, audit trail, approval and expiry."""
import json

import pytest

import cases
import vendors
from cases import (APPROVED, AWAITING_USER, CALL_DONE, CALL_FAILED, CALL_SCHEDULED, CALLING,
                   EXPIRED, HUMAN_CALLBACK, NO_ANSWER, ON_HOLD, RECEIVED, REJECTED, InvalidTransition)
from conftest import MemStore

GST = "27AAPFU0939F1ZV"


@pytest.fixture
def setup():
    store = MemStore()
    vendors.save_vendor(store, "alice", "Sharma Traders",
                        {"gstin": GST, "ifsc": "YESB0DNB002", "acct_digits": "1234567890",
                         "phone": "+919123456789"}, now=0)
    vendor = vendors.get_vendor(store, "alice", "Sharma Traders")
    changes = [{"field": "ifsc", "old": "YESB0DNB002", "new": "SBIN0000001"},
               vendors.account_change(store, "alice", vendor["id"], "9876543210")]
    case = cases.open_case(store, "alice", vendor, changes,
                           trigger={"type": "email", "ref": "msg-1"}, flags=["urgency"], now=100)
    return store, vendor, case


def _walk(store, case_id, *states):
    for s in states:
        cases.transition(store, "alice", case_id, s, now=200)


def test_open_case_puts_payment_on_hold_and_logs(setup):
    store, vendor, case = setup
    assert case["state"] == ON_HOLD
    assert [(a["from"], a["to"]) for a in case["audit"]] == [(None, RECEIVED), (RECEIVED, ON_HOLD)]
    assert case["priority"] == "high" and case["flags"] == ["urgency"]
    assert case["id"].startswith("C") and len(case["id"]) == 6
    assert cases.get_case(store, "alice", case["id"].lower())["id"] == case["id"]


def test_full_call_path_to_approval_updates_vendor(setup):
    store, vendor, case = setup
    _walk(store, case["id"], CALL_SCHEDULED, CALLING, CALL_DONE, AWAITING_USER)
    status, c = cases.approve(store, "alice", case["id"], "3210", now=300)
    assert status == "approved" and c["state"] == APPROVED
    rec = vendors.get_vendor(store, "alice", "Sharma Traders")
    assert rec["ifsc"] == "SBIN0000001" and rec["acct_last4"] == "3210"
    # the new fingerprint matches the new number and not the old one
    assert vendors.compare(rec, {"acct_digits": "9876543210", "acct_last4": "3210"})[0] == []
    assert vendors.compare(rec, {"acct_digits": "1234567890", "acct_last4": "7890"})[0]
    assert [a["to"] for a in c["audit"]][-1] == APPROVED and c["audit"][-1]["by"] == "user"


def test_human_callback_path(setup):
    store, vendor, case = setup
    _walk(store, case["id"], HUMAN_CALLBACK, AWAITING_USER)
    assert cases.get_case(store, "alice", case["id"])["state"] == AWAITING_USER


@pytest.mark.parametrize("path, bad", [
    ([], CALLING),                       # ON_HOLD cannot jump to CALLING
    ([], CALL_DONE),
    ([CALL_SCHEDULED, CALLING], AWAITING_USER),  # a call must finish first
    ([HUMAN_CALLBACK], CALL_DONE),
    ([HUMAN_CALLBACK, AWAITING_USER], CALLING),
])
def test_invalid_transitions_raise(setup, path, bad):
    store, vendor, case = setup
    _walk(store, case["id"], *path)
    with pytest.raises(InvalidTransition):
        cases.transition(store, "alice", case["id"], bad)


def test_only_the_user_can_approve_or_reject(setup):
    store, vendor, case = setup
    _walk(store, case["id"], HUMAN_CALLBACK, AWAITING_USER)
    for state in (APPROVED, REJECTED):
        with pytest.raises(InvalidTransition):
            cases.transition(store, "alice", case["id"], state, by="system")
    with pytest.raises(InvalidTransition):
        cases.transition(store, "alice", case["id"], APPROVED, by="user")  # must go through approve()


def test_wrong_code_fails_logs_and_changes_nothing(setup):
    store, vendor, case = setup
    _walk(store, case["id"], HUMAN_CALLBACK, AWAITING_USER)
    status, c = cases.approve(store, "alice", case["id"], "7890")
    assert status == "wrong_code" and c["state"] == AWAITING_USER and c["failed_approvals"] == 1
    assert c["audit"][-1]["note"] == "approval failed: wrong code"
    assert vendors.get_vendor(store, "alice", "Sharma Traders")["ifsc"] == "YESB0DNB002"


def test_approve_is_idempotent_and_needs_awaiting_user(setup):
    store, vendor, case = setup
    assert cases.approve(store, "alice", case["id"], "3210")[0] == "not_ready"
    _walk(store, case["id"], HUMAN_CALLBACK, AWAITING_USER)
    assert cases.approve(store, "alice", case["id"], "3210")[0] == "approved"
    n_audit = len(cases.get_case(store, "alice", case["id"])["audit"])
    assert cases.approve(store, "alice", case["id"], "3210")[0] == "already_approved"
    assert len(cases.get_case(store, "alice", case["id"])["audit"]) == n_audit


def test_only_the_owner_can_see_or_approve(setup):
    store, vendor, case = setup
    _walk(store, case["id"], HUMAN_CALLBACK, AWAITING_USER)
    assert cases.get_case(store, "mallory", case["id"]) is None
    assert cases.approve(store, "mallory", case["id"], "3210") == ("not_found", None)
    assert cases.get_case(store, "alice", case["id"])["state"] == AWAITING_USER


def test_reject_keeps_old_details(setup):
    store, vendor, case = setup
    status, c = cases.reject(store, "alice", case["id"])
    assert status == "rejected" and c["state"] == REJECTED
    assert vendors.get_vendor(store, "alice", "Sharma Traders")["ifsc"] == "YESB0DNB002"
    assert cases.reject(store, "alice", case["id"])[0] == "already_rejected"
    with pytest.raises(InvalidTransition):
        cases.transition(store, "alice", case["id"], ON_HOLD)


def test_expiry_changes_nothing(setup):
    store, vendor, case = setup
    assert cases.expire_due(store, "alice", now=case["expires_at"] - 1) == []
    expired = cases.expire_due(store, "alice", now=case["expires_at"])
    assert [c["id"] for c in expired] == [case["id"]]
    assert cases.get_case(store, "alice", case["id"])["state"] == EXPIRED
    assert vendors.get_vendor(store, "alice", "Sharma Traders")["ifsc"] == "YESB0DNB002"
    assert cases.approve(store, "alice", case["id"], "3210")[0] == "closed"


def test_expiry_hours_from_env():
    assert cases.expiry_hours({}) == 72
    assert cases.expiry_hours({"CASE_EXPIRY_HOURS": "24"}) == 24
    assert cases.expiry_hours({"CASE_EXPIRY_HOURS": "x"}) == 72


def test_call_retries_are_capped(setup):
    store, vendor, case = setup
    cid = case["id"]
    for _ in range(cases.MAX_CALL_ATTEMPTS):
        _walk(store, cid, CALL_SCHEDULED, CALLING)
        cases.record_call_attempt(store, "alice", cid, "no-answer")
        _walk(store, cid, NO_ANSWER)
    with pytest.raises(InvalidTransition):
        cases.transition(store, "alice", cid, CALL_SCHEDULED)
    _walk(store, cid, HUMAN_CALLBACK)  # the user calls instead


def test_failed_call_can_retry_or_fall_back(setup):
    store, vendor, case = setup
    _walk(store, case["id"], CALL_SCHEDULED, CALLING, CALL_FAILED, HUMAN_CALLBACK, AWAITING_USER)


def test_confirm_code_rules(setup):
    store, vendor, case = setup
    assert cases.confirm_code(case) == "3210"
    assert "account number" in cases.confirm_label(case)
    only_iban = dict(case, changes=[{"field": "iban", "old": "x", "new": "GB82WEST12345698765432"}])
    assert cases.confirm_code(only_iban) == "5432"
    only_phone = dict(case, changes=[{"field": "phone", "old": "+919876543210", "new": "+919123456789"}])
    assert cases.confirm_code(only_phone) == "6789"


def test_no_full_account_number_or_message_text_stored(setup):
    store, vendor, case = setup
    dumped = json.dumps(store.d)
    assert "9876543210" not in dumped and "1234567890" not in dumped
    assert case["trigger"] == {"type": "email", "ref": "msg-1"}


def test_forget_all_clears_cases(setup):
    store, vendor, case = setup
    vendors.forget_all(store, "alice")
    assert cases.list_cases(store, "alice", open_only=False) == []


def test_case_ids_are_unique_and_readable(setup):
    store, vendor, _ = setup
    ids = {cases.open_case(store, "alice", vendor, [{"field": "ifsc", "old": "a", "new": "b"}])["id"]
           for _ in range(50)}
    assert len(ids) == 50
    assert all(not set(i[1:]) & set("01OIL") for i in ids)
