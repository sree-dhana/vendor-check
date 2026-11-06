"""What happens after a case opens: the call-or-callback decision, the human callback
script, recording what the vendor said, reminders and expiry. Plain code only.

Automated calls need all three: the global CALLS_ENABLED switch, a country profile with
calls enabled, and the vendor's consent for the saved number. Otherwise (the default) the
user calls the vendor themselves on the saved number. A call result is evidence, never
proof: the user always decides."""
import os
import time

import analyze_call
import callscript
import cases
import countries
import telephony
import vendors

OUTCOME_TEXT = {
    # human callback (the user called)
    "VENDOR_SAID_CONFIRM": "you called and the vendor said they confirm the change",
    "VENDOR_DENIED": "you called and the vendor said they did not send this",
    "NO_ANSWER": "nobody answered the call",
    # automated call results (step 8)
    "CONFIRMED_AND_READBACK_MATCHES": "the vendor said they confirm, and the digits they read back match",
    "CONFIRMED_BUT_READBACK_MISMATCH": "the vendor said they confirm, but the digits they read back do not match",
    "DENIED_SENDING": "the vendor said they did not send this",
    "UNCLEAR": "the call was unclear",
}
NOT_PROOF = "This is not proof; confirm yourself before approving."
CALLBACK_ANSWERS = {"confirmed": "VENDOR_SAID_CONFIRM", "not confirmed": "VENDOR_DENIED",
                    "no answer": "NO_ANSWER"}
REMINDER_HOURS = 12
RETRY_HOURS = 2


def _int_env(name, default, env=None):
    env = os.environ if env is None else env
    try:
        return max(0, int(env.get(name, default)))
    except ValueError:
        return default


def cancel_window_min(env=None):
    return _int_env("CANCEL_WINDOW_MIN", 10, env)


def call_permission(vendor, env=None):
    """(allowed, reason). When unsure, the answer is no: human callback."""
    if not countries.calls_globally_enabled(env):
        return False, "automated calls are switched off"
    profile = countries.get_profile(vendor.get("country") or "GENERIC")
    if not profile.call_rules.calls_enabled:
        return False, "automated calls are not enabled for %s" % profile.name
    if not vendor.get("phone"):
        return False, "there is no saved phone number"
    consent = vendor.get("consent") or {}
    if consent.get("status") != "granted" or consent.get("phone") != vendor["phone"]:
        return False, "the vendor hasn't agreed to automated calls on the saved number"
    if telephony.get_telephony(env) is None:
        return False, "no phone service is set up (only the demo mock exists so far)"
    return True, ""


def in_call_window(vendor, now, env=None):
    """Inside the country profile's calling hours, in the vendor's local time. Demo mode
    ignores the hours because no real phone rings."""
    if countries.demo_mode(env):
        return True
    rules = countries.get_profile(vendor.get("country") or "GENERIC").call_rules
    hour = time.gmtime(now + rules.utc_offset_min * 60).tm_hour
    return rules.window[0] <= hour < rules.window[1]


def business_name(store, owner):
    try:
        return (store.get("profile:%s" % owner, {}) or {}).get("business_name")
    except Exception:
        return None


def place_call(store, owner, case_id, tel, now=None, client=None, env=None):
    """Place one automated verification call for a CALL_SCHEDULED case. Permission is
    checked again at dialling time, and only the saved number the consent covers is dialled.
    Returns a short message for the user."""
    now = time.time() if now is None else now
    data = cases._load(store, owner)
    case = data[case_id]
    vendor = vendors.get_vendor_by_id(store, owner, case["vendor_id"])
    allowed, reason = call_permission(vendor or {}, env) if vendor else (False, "the vendor was deleted")
    if not allowed or tel is None:
        cases.transition_case(case, cases.HUMAN_CALLBACK, "system", "human callback: " + (reason or "no phone service"), now)
        cases._save(store, owner, data)
        return "I couldn't place the call for case `%s` (%s). Please call them yourself: say `case %s`." % (
            case_id, reason or "no phone service", case_id)
    phone = vendor["phone"]
    if phone != vendor["consent"]["phone"]:  # never dial anything but the consented, saved number
        raise RuntimeError("refusing to dial a number other than the saved, consented one")
    recording = bool(vendor["consent"].get("recording"))
    script = callscript.build_script(case, vendor, business_name(store, owner), recording)
    cases.transition_case(case, cases.CALLING, "system", "calling the saved number", now)
    call_id = tel.place_call(phone, script, record=recording)
    status = tel.status(call_id)
    case["call_attempts"].append({"at": now, "status": status})
    if status == "completed":
        turns = tel.transcript(call_id)  # analysed in memory, never stored or logged
        expected = {s["step"]: s["expect"] for s in script if "expect" in s}
        result = analyze_call.analyze(turns, expected)
        cases.transition_case(case, cases.CALL_DONE, "system", "call finished", now)
        summary, source = analyze_call.summarize(turns, result, client)
        case.update(outcome=result["label"], summary=summary, summary_source=source,
                    readback=result["readback"], speaker_name=result["speaker_name"])
        cases.transition_case(case, cases.AWAITING_USER, "system", "call result: %s" % result["label"], now)
        msg = ("The verification call for case `%s` (%s) finished: %s. Say `case %s` for the report."
               % (case_id, case["vendor_name"], OUTCOME_TEXT[result["label"]], case_id))
    else:
        state = cases.NO_ANSWER if status == "no-answer" else cases.CALL_FAILED
        cases.transition_case(case, state, "system", "call attempt: %s" % status, now)
        if len(case["call_attempts"]) < cases.MAX_CALL_ATTEMPTS:
            case["next_call_at"] = now + RETRY_HOURS * 3600
            msg = ("No answer on the verification call for case `%s`. I'll try again in %d hours."
                   % (case_id, RETRY_HOURS) if state == cases.NO_ANSWER else
                   "The verification call for case `%s` failed. I'll try again in %d hours." % (case_id, RETRY_HOURS))
        else:
            case["outcome"] = "NO_ANSWER" if state == cases.NO_ANSWER else None
            cases.transition_case(case, cases.HUMAN_CALLBACK, "system",
                                  "no answer after %d calls" % cases.MAX_CALL_ATTEMPTS, now)
            msg = ("No answer after %d calls for case `%s`. Please call them yourself: say `case %s`."
                   % (cases.MAX_CALL_ATTEMPTS, case_id, case_id))
    cases._save(store, owner, data)
    return msg


def after_open(store, owner, case, vendor, now=None, env=None):
    """Route a newly opened case: a cancel window before an automated call, or straight to
    the human callback. Returns (route, reason)."""
    now = time.time() if now is None else now
    allowed, reason = call_permission(vendor, env)
    if allowed:
        data = cases._load(store, owner)
        data[case["id"]]["call_after"] = now + cancel_window_min(env) * 60
        cases._save(store, owner, data)
        case["call_after"] = data[case["id"]]["call_after"]
        return "call", ""
    cases.transition(store, owner, case["id"], cases.HUMAN_CALLBACK, note="human callback: " + reason, now=now)
    case["state"] = cases.HUMAN_CALLBACK
    return "callback", reason


def cancel_call(store, owner, case_id, now=None):
    """The user prefers to phone the vendor personally."""
    case = cases.get_case(store, owner, case_id)
    if not case:
        return "not_found", None
    if case["state"] not in (cases.ON_HOLD, cases.CALL_SCHEDULED):
        return "too_late", case
    case = cases.transition(store, owner, case["id"], cases.HUMAN_CALLBACK, by="user",
                            note="user will call personally", now=now)
    return "ok", case


def callback_script(case, vendor):
    """Fixed script for the user's own call. Never a number from the email."""
    phone = vendor.get("phone")
    code_label = cases.confirm_label(case)
    lines = ["**Call %s yourself**" % vendor["name"], ""]
    if phone:
        lines.append("Use the number you saved: `%s`. Never use a number from the email or message." % phone)
    else:
        lines.append("You have no saved phone for %s. Use a number from your own records (an old "
                     "invoice or contract), never one from the email." % vendor["name"])
    lines.extend([
        "",
        "Ask:",
        "1. \"Did you send us a request to change your bank details?\"",
        "2. If yes: \"Please read me %s.\" It should be `%s`." % (code_label, cases.confirm_code(case)),
        "3. \"Who am I speaking with?\"",
        "",
        "Then tell me: `confirmed %s`, `not confirmed %s` or `no answer %s`." % ((case["id"],) * 3),
    ])
    return lines


def record_callback(store, owner, case_id, answer, now=None):
    """Store what the vendor said on the user's own call and move the case on."""
    outcome = CALLBACK_ANSWERS[answer]
    data = cases._load(store, owner)
    case = data.get((case_id or "").upper())
    if not case:
        return "not_found", None
    if case["state"] not in (cases.HUMAN_CALLBACK, cases.AWAITING_USER, cases.NO_ANSWER, cases.CALL_FAILED):
        return "wrong_state", case
    now = time.time() if now is None else now
    case["outcome"] = outcome
    if case["state"] == cases.AWAITING_USER:
        cases._log(case, case["state"], case["state"], "user", "callback result updated: %s" % outcome, now)
    else:
        cases.transition_case(case, cases.AWAITING_USER, "user", "callback result: %s" % outcome, now)
    cases._save(store, owner, data)
    return "ok", case


def outcome_line(case):
    if not case.get("outcome"):
        return None
    return "%s. %s" % (OUTCOME_TEXT[case["outcome"]][0].upper() + OUTCOME_TEXT[case["outcome"]][1:], NOT_PROOF)


def tick(store, owner, now=None, env=None, tel=None, client=None):
    """Periodic work for one owner: expire old cases, move cases past their cancel window
    or retry delay to CALL_SCHEDULED, place due calls inside calling hours, and remind about
    cases waiting for the user. Returns messages for the user."""
    now = time.time() if now is None else now
    messages = []
    for case in cases.expire_due(store, owner, now):
        messages.append("Case `%s` for **%s** expired without a decision. Nothing was changed; your "
                        "saved details stay as they were." % (case["id"], case["vendor_name"]))
    data = cases._load(store, owner)
    dirty = False
    to_call = []
    for case in data.values():
        if case["state"] == cases.ON_HOLD and case.get("call_after") and now >= case["call_after"]:
            cases.transition_case(case, cases.CALL_SCHEDULED, "system", "cancel window passed", now)
            dirty = True
        elif case["state"] in (cases.NO_ANSWER, cases.CALL_FAILED) and now >= case.get("next_call_at", 0):
            cases.transition_case(case, cases.CALL_SCHEDULED, "system", "retrying the call", now)
            dirty = True
        if case["state"] == cases.CALL_SCHEDULED:
            vendor = vendors.get_vendor_by_id(store, owner, case["vendor_id"]) or {}
            if in_call_window(vendor, now, env):
                to_call.append(case["id"])
        elif case["state"] in (cases.HUMAN_CALLBACK, cases.AWAITING_USER):
            last = case.get("reminded_at") or case["created_at"]
            if now - last >= REMINDER_HOURS * 3600:
                case["reminded_at"] = now
                dirty = True
                todo = ("call the vendor and tell me the result" if case["state"] == cases.HUMAN_CALLBACK
                        else "approve or reject it")
                messages.append("Reminder: case `%s` for **%s** is still waiting. The payment stays on hold "
                                "until you %s. Say `case %s` for details."
                                % (case["id"], case["vendor_name"], todo, case["id"]))
    if dirty:
        cases._save(store, owner, data)
    for case_id in to_call:  # after saving, so each call works on fresh data
        messages.append(place_call(store, owner, case_id, tel if tel is not None else telephony.get_telephony(env),
                                   now, client, env))
    return messages


def owners(store):
    try:
        o = store.get("case_owners", [])
        return o if isinstance(o, list) else []
    except Exception:
        return []


def add_owner(store, owner):
    o = owners(store)
    if owner not in o:
        o.append(owner)
        store.set("case_owners", o[-1000:])


def open_and_route(store, owner, vendor, changes, trigger, flags, now=None, env=None):
    """Open a case, put the payment on hold, and route it. Returns (case, route, reason)."""
    case = cases.open_case(store, owner, vendor, changes, trigger=trigger, flags=flags, now=now)
    add_owner(store, owner)
    route, reason = after_open(store, owner, case, vendor, now, env)
    return cases.get_case(store, owner, case["id"]), route, reason


def vendor_for(store, owner, case):
    return vendors.get_vendor_by_id(store, owner, case["vendor_id"]) or {"name": case["vendor_name"]}
