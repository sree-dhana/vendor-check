import re
import time

from checks import (
    MAX_PER_TYPE,
    LABELS,
    extract_ids,
    check_gstin,
    check_pan,
    check_malformed,
    cross_checks,
)
from flags import detect_flags, heads_up_lines
from limits import RATE_LIMITED_TEXT, TOO_LONG_TEXT, allow_llm, allow_message, too_long
from llm import OFF, classify_flags_llm, extract_ids_llm, flags_inconclusive, should_extract, summarize
from lookups import ifsc_result, lookup_ifsc, lookup_pin, pin_result
from suggest import gstin_typo_fixes
import cases
import consent
import countries
import detect
import mailwatch
import telephony
import workflow
from iban import check_iban
import onboarding
import vendors

ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
DOT = " · "

SAMPLE_GSTIN = "27AAPFU0939F1ZV"
SAMPLE_PAN = "AAPFU0939F"
SAMPLE_IFSC = "YESB0DNB002"

HELP_TEXT = (
    "Hi! I'm **VendorCheck**. Paste an Indian vendor's GSTIN, PAN, IFSC or PIN code "
    "(any mix) and I'll check each one and cross-check them against each other.\n\n"
    "Try:\n"
    "- `Check this vendor: GSTIN %s, PAN %s, IFSC %s`\n"
    "- `Is GSTIN %s valid?`\n"
    "- `Which bank is IFSC %s?`\n"
    "- `pincode 560001`\n\n"
    "Write each ID without spaces. Say `sample` for a ready-to-copy example.\n"
    "I check format, checksum and consistency. I can't prove identity or that a GSTIN is active.\n\n"
    "New here? Say `start` to set up your business and vendors."
) % (SAMPLE_GSTIN, SAMPLE_PAN, SAMPLE_IFSC, SAMPLE_GSTIN, SAMPLE_IFSC)

SAMPLE_TEXT = (
    "Copy and send this to see a full check:\n\n"
    "`Check this vendor: GSTIN %s, PAN %s, IFSC %s`"
) % (SAMPLE_GSTIN, SAMPLE_PAN, SAMPLE_IFSC)

NO_ID_TEXT = (
    "I check Indian **GSTIN, PAN, IFSC and PIN codes**, and I couldn't find one in your message.\n\n"
    "Example: `Check this vendor: GSTIN %s, PAN %s`\n"
    "Say `help` for more examples."
) % (SAMPLE_GSTIN, SAMPLE_PAN)

DISCLAIMER = (
    "_These are format, checksum and consistency checks. They don't prove identity, "
    "registration status, or that a payment carries no risk._"
)

GREETINGS = {"hi", "hii", "hello", "hey", "help", "menu", "?", "namaste"}
START_WORDS = {"start", "setup", "/start", "get started"}
SETTINGS_RE = re.compile(r"^settings?(?:\s+(.+))?$", re.I)
RULES_RE = re.compile(r"^(?:rules|country rules|rules for)\s+(.+)$", re.I)
EMAIL_CMD_RE = re.compile(r"^(?:check\s+)?e-?mail\s*:?\s*\n?(.+)$", re.I | re.S)
CONSENT_REQ_RE = re.compile(r"^(?:consent request|request consent|ask consent)\s+(?:for\s+)?(.+)$", re.I)
CONSENT_REPLY_RE = re.compile(
    r"^consent reply\s+(?:for\s+)?(.+?)(?:\s*\n|\s*:\s+|\s+(?=calls?\b|recording\b|from\s*:|1\s*[.)]))(.*)$",
    re.I | re.S)
CONFIRM_CONSENT_RE = re.compile(r"^confirm consent\s+(?:for\s+)?(.+)$", re.I)
REVOKE_RE = re.compile(r"^(?:revoke|withdraw) consent\s+(?:for\s+)?(.+)$", re.I)
CASE_ID = r"(C[A-Za-z0-9]{5})"
MY_CASES = {"my cases", "cases", "open cases", "all cases"}
CASE_RE = re.compile(r"^case\s+%s$" % CASE_ID, re.I)
APPROVE_RE = re.compile(r"^approve\s+%s(?:\s+(\S+))?$" % CASE_ID, re.I)
REJECT_RE = re.compile(r"^reject\s+%s$" % CASE_ID, re.I)
CALLBACK_RE = re.compile(r"^(confirmed|not confirmed|no answer)(?:\s+%s)?$" % CASE_ID, re.I)
CANCEL_CALL_RE = re.compile(r"^(?:i'?ll call|i will call|cancel call|call myself)\s+%s$" % CASE_ID, re.I)
DEMO_CALL_RE = re.compile(r"^demo call\s+%s(?:\s+([a-z_]+))?$" % CASE_ID, re.I)
BUSINESS_RE = re.compile(r"^(?:business name|my business is)\s+(.+)$", re.I)
WATCH_RE = re.compile(r"^(?:watch|monitor)\s+(?!status\b)(.+)$", re.I)
STOP_WATCH_RE = re.compile(r"^(?:stop watching|stop monitoring|unwatch)\s+(.+)$", re.I)
WATCH_STATUS = {"watch status", "watching", "email status"}
STATE_TEXT = {
    "ON_HOLD": "payment on hold", "CALL_SCHEDULED": "automated call scheduled", "CALLING": "calling the vendor",
    "CALL_DONE": "call finished", "NO_ANSWER": "the vendor didn't answer", "CALL_FAILED": "the call failed",
    "HUMAN_CALLBACK": "waiting for you to call the vendor", "AWAITING_USER": "waiting for your decision",
    "APPROVED": "approved", "REJECTED": "rejected", "EXPIRED": "expired, nothing changed", "RECEIVED": "received",
}
FLAG_SHORT = {
    "lookalike_domain": "the sender's domain only looks like the vendor's",
    "reply_to_mismatch": "replies would go to a different address",
    "unknown_sender": "sent from an address you didn't save for this vendor",
    "bank_change": "says the bank details changed", "urgency": "pushes you to pay quickly",
    "no_callback": "discourages a phone call", "send_to_account": "asks you to pay into a specific account",
    "personal_account": "mentions a personal account", "different_payee": "mentions a different payee name",
}
EMAIL_MAX_CHARS = 8000

# ASI:One prefixes messages with the agent mention, e.g. "@agent1q... help" or "@vendor-check help".
LEADING_MENTIONS = re.compile(r"^(?:\s*@\S+)+")

SAVE_RE = re.compile(r"^save\s+vendor\b\s*(.*)$", re.I | re.S)
FORGET_RE = re.compile(r"^forget\b\s*(.*)$", re.I | re.S)
CHECK_NAME_RE = re.compile(r"^check\s+(.+)$", re.I | re.S)
MY_VENDORS = {"my vendors", "vendors", "list vendors", "saved vendors"}
VENDOR_RE = re.compile(r"^(?:show\s+)?vendor\s+(?!details?\b)(.+)$", re.I)
DETAILS_RE = re.compile(r"^(?:details?|show)\s+(?:of\s+|for\s+)?(?:vendor\s+)?#?(\d{1,2})$", re.I)

BATCH_MAX = 5
BLANK_SPLIT = re.compile(r"\n\s*\n")
PIECE_SPLIT = re.compile(r"[;\n]")
BULLET = re.compile(r"^(?:[-*•]|\d{1,2}[.)])\s*")


def _typo_lines(r):
    if r.get("fixes"):
        return ["  - ↳ Possible typo fixes, confirm with the vendor: %s"
                % ", ".join("`%s`" % f for f in r["fixes"])]
    if r.get("too_many_fixes"):
        return ["  - ↳ Several valid GSTINs are one typo away, too many to guess. Ask the vendor to resend it."]
    return []


def _selftest(store):
    ifsc = lookup_ifsc(SAMPLE_IFSC, store)["status"]
    pin = lookup_pin("560001", store)["status"]
    return "selftest: ifsc=%s, pin=%s" % (ifsc, pin)


def _ids_line(rec):
    parts = ["%s `%s`" % (vendors.FIELD_LABELS[f], vendors.mask(f, rec[f]) if f == "acct_last4" else rec[f])
             for f in vendors.FIELDS if rec.get(f)]
    if rec.get("phone"):
        parts.append("Phone `%s`" % vendors.mask("phone", rec["phone"]))
    return ", ".join(parts)


def _vendor_card(rec):
    """Everything saved for one vendor, masked where it matters."""
    lines = ["**%s**" % rec["name"], ""]
    if rec.get("legal_name"):
        lines.append("- **Legal name:** %s" % rec["legal_name"])
    if rec.get("country"):
        prof = countries.get_profile(rec["country"])
        lines.append("- **Country:** %s (%s profile)" % (countries.display_name(rec["country"]), prof.confidence))
    ids = _ids_line({k: rec[k] for k in vendors.FIELDS if rec.get(k)})
    if ids:
        lines.append("- **IDs:** %s" % ids)
    lines.append("- **Saved phone:** %s" % (
        "`%s` (the only number I'll ever suggest calling)" % rec["phone"] if rec.get("phone") else "none yet"))
    if rec.get("contact"):
        lines.append("- **Contact person:** %s" % rec["contact"])
    if rec.get("emails") or rec.get("domains"):
        lines.append("- **Expected emails from:** %s" % ", ".join(rec.get("emails", []) + [
            "any address @" + d for d in rec.get("domains", [])
            if not any(e.endswith("@" + d) for e in rec.get("emails", []))]))
    lines.append("- **Consent for verification calls:** %s" % consent.describe(rec.get("consent")))
    lines.append("- **Vendor id:** `%s`" % rec["id"])
    return lines


def _missing_hints(rec):
    hints = []
    if not rec.get("phone"):
        hints.append("**Add a phone number** you already trust, so I know which number to suggest "
                     "calling if their details change: `save vendor %s phone +91 98765 43210`." % rec["name"])
    if not rec.get("emails"):
        hints.append("**Add their email address** so I can recognise messages from them: "
                     "`save vendor %s email accounts@example.com`." % rec["name"])
    return hints


def _vendor_command(t, low, store, sender, now):
    """Handle the saved-vendor commands. Returns a reply, or None if `t` is not one."""
    if low in MY_VENDORS:
        recs = vendors.list_vendors(store, sender)
        if not recs:
            return "You haven't saved any vendors yet. After a check, say `save vendor <name>`."
        lines = ["**Your saved vendors** (%d)" % len(recs), ""]
        lines.extend("- **%s**%s: %s" % (
            r["name"], " (%s)" % countries.display_name(r["country"]) if r.get("country") else "",
            _ids_line(r) or "no IDs yet") for r in recs)
        lines.append("")
        lines.append("Say `vendor <name>` for details, `check <name>` to check one again, or "
                     "`forget vendor <name>` to delete it.")
        return "\n".join(lines)

    m = VENDOR_RE.match(t)
    if m:
        rec = vendors.get_vendor(store, sender, m.group(1))
        if not rec:
            return ("I don't have a saved vendor called **%s**. Say `my vendors` to see the list."
                    % vendors.clean_name(m.group(1)))
        return "\n".join(_vendor_card(rec) + [""] + _missing_hints(rec))

    m = SAVE_RE.match(t)
    if m:
        return _save_vendor_reply(m.group(1), store, sender, now)

    m = FORGET_RE.match(t)
    if m:
        arg = re.sub(r"^vendor\s+", "", m.group(1).strip(), flags=re.I)
        if not arg:
            return "Say `forget <name>` to delete one saved vendor, or `forget all` to delete everything."
        if arg.lower() == "all":
            mailwatch.forget_all(store, sender)
            n = vendors.forget_all(store, sender)
            return ("Deleted %d saved vendor%s, your cases, your recent check and your setup answers."
                    % (n, "" if n == 1 else "s"))
        if vendors.forget(store, sender, arg):
            return "Deleted **%s**." % vendors.clean_name(arg)
        return ("I don't have a saved vendor called **%s**. Say `my vendors` to see the list."
                % vendors.clean_name(arg))
    return None


def _save_vendor_reply(rest, store, sender, now):
    name = vendors.name_part(rest)
    found, _ = extract_ids(rest)
    default_country = onboarding.load(store, sender).get("country")
    ids, problems = vendors.parse_details(rest, default_country)
    ids.update({k: found[k][0] for k in ("gstin", "pan", "ifsc") if found[k]})
    if not any(ids.get(k) for k in vendors.FIELDS + vendors.DETAILS + ("emails",)
               if k != "country"):
        ids.update(vendors.last_ids(store, sender, now))
    if ids.get("gstin") or ids.get("ifsc"):
        ids["country"] = "IN"  # Indian IDs decide the country over a setup default
    old = dict(vendors.get_vendor(store, sender, name) or {})
    status = vendors.save_vendor(store, sender, name, ids, now)
    if status == "no_name":
        return "Please give the vendor a name, for example `save vendor Sharma Traders`."
    if status == "no_ids":
        return "\n".join(problems + [
            "I didn't find any details to save. Send `save vendor <name>` right after a check, or "
            "together with details, for example `save vendor Sharma Traders GSTIN 27AAPFU0939F1ZV "
            "IFSC YESB0DNB002 phone +91 98765 43210 email accounts@sharmatraders.in`."])
    if status == "full":
        return "You already have %d saved vendors. Say `forget vendor <name>` to make room." % vendors.MAX_VENDORS
    rec = vendors.get_vendor(store, sender, name)
    verb = "Updated" if status == "updated" else "Saved"
    lines = ["%s **%s**%s: %s." % (
        verb, rec["name"], " (%s)" % countries.display_name(rec["country"]) if rec.get("country") else "",
        _ids_line(rec) or "details saved")]
    warnings = list(problems) + vendors.id_warnings(ids)
    if old.get("phone") and ids.get("phone") and old["phone"] != rec["phone"]:
        warnings.append("The saved phone changed from `%s` to `%s`. Only do this with a number you got "
                        "from a source you already trust, never from an email asking for a change."
                        % (old["phone"], rec["phone"]))
    if warnings:
        lines.extend([""] + ["⚠️ %s" % w for w in warnings])
    hints = _missing_hints(rec)
    if hints:
        lines.extend([""] + ["- %s" % h for h in hints])
    lines.extend(["", "Say `vendor %s` to see everything saved, `check %s` with new invoice details to "
                      "spot changes, or `forget vendor %s` to delete it. I never store full account "
                      "numbers: only the last 4 digits and a one-way fingerprint used to notice if "
                      "the number changes." % (rec["name"], rec["name"], rec["name"])])
    wants_monitoring = onboarding.load(store, sender).get("monitoring")
    if wants_monitoring and rec.get("emails") and rec["id"] not in mailwatch.watch_state(store, sender).get("vendors", []):
        lines.extend(["", "Want me to watch for emails from %s and flag bank-detail changes? Say `watch %s`."
                          % (", ".join(rec["emails"]), rec["name"])])
    if status == "saved" and rec.get("phone") and wants_monitoring:
        consent.mark_requested(store, sender, rec, now)
        lines.extend(["", _consent_draft_reply(rec)])
    elif rec.get("phone") and (rec.get("consent") or {}).get("status") == "not_requested":
        lines.extend(["", "To allow automated verification calls later, say `consent request %s` and I'll "
                          "draft a message for you to send them." % rec["name"]])
    return "\n".join(lines)


def _change_lines(name, changed):
    lines = ["❌ **Bank details changed since you saved %s**" % name, ""]
    for f, old, new in changed:
        lines.append("- %s: was `%s`, now `%s`" % (vendors.FIELD_LABELS[f], vendors.mask(f, old), vendors.mask(f, new)))
    lines.append("")
    lines.append(
        "Call the vendor on a number you already have before paying. If the change is genuine, "
        "say `save vendor %s` with the new details to update it." % name
    )
    return lines


def _llm_read_line(llm_read, provider_name):
    new, vendor_name = llm_read
    ids = ", ".join("%s `%s`" % (LABELS[k], v) for k, v in new)
    who = " for **%s**" % vendor_name if vendor_name else ""
    return ("_I read these IDs from your message%s with help from an AI model (%s): %s. "
            "Check they match what you sent._" % (who, provider_name, ids))


def _consent_command(t, store, sender, now):
    """Consent commands. Returns a reply, or None if `t` is not one."""
    for regex, kind in ((CONSENT_REPLY_RE, "reply"), (CONSENT_REQ_RE, "request"),
                        (CONFIRM_CONSENT_RE, "confirm"), (REVOKE_RE, "revoke")):
        m = regex.match(t)
        if m:
            break
    else:
        return None
    name = vendors.clean_name(m.group(1))
    vendor = vendors.get_vendor(store, sender, name)
    if not vendor:
        return "I don't have a saved vendor called **%s**. Say `my vendors` to see the list." % name

    if kind == "request":
        if not vendor.get("phone"):
            return ("Save a phone number for **%s** first, so the request names the number we'd call: "
                    "`save vendor %s phone +91 98765 43210`." % (vendor["name"], vendor["name"]))
        consent.mark_requested(store, sender, vendor, now)
        return _consent_draft_reply(vendor)

    if kind == "reply":
        status, record, warnings = consent.record_reply(store, sender, vendor, m.group(2), now)
        lines = []
        if status == "granted":
            lines.append("✅ Recorded: **%s** agreed to automated verification calls to `%s`. Recording: %s."
                         % (vendor["name"], record["phone"], "yes" if record["recording"] else "no"))
        elif status == "declined":
            lines.append("Recorded: **%s** declined automated calls. If their details change, you'll get "
                         "a script to call them yourself." % vendor["name"])
        elif status == "pending":
            lines.append("Not accepted yet: consent for **%s** stays pending." % vendor["name"])
        lines.extend("⚠️ %s" % w for w in warnings)
        lines.extend(["", "Consent never makes a call result proof, and you still approve every change yourself. "
                          "Say `revoke consent %s` at any time." % vendor["name"]])
        return "\n".join(lines).strip()

    if kind == "confirm":
        status, record = consent.confirm(store, sender, vendor, now)
        if status == "nothing_to_confirm":
            return "There's no reply waiting for confirmation for **%s**." % vendor["name"]
        if status == "no_phone":
            return "Save a phone number for **%s** first." % vendor["name"]
        return "Recorded after your check: consent for **%s** is now %s." % (vendor["name"], consent.describe(record))

    consent.revoke(store, sender, vendor, now)
    return ("Consent for **%s** is revoked. No automated calls will be made to them; you'll get a script "
            "to call them yourself instead." % vendor["name"])


def _watch_command(t, low, store, sender, now):
    """Email watching commands. Returns a reply, or None if `t` is not one."""
    if low in WATCH_STATUS:
        state = mailwatch.watch_state(store, sender)
        names = [r["name"] for r in vendors.list_vendors(store, sender) if r["id"] in state.get("vendors", [])]
        if not names:
            return "I'm not watching any vendor emails for you. Say `watch <vendor>` to start."
        lines = ["**Email watching**", "",
                 "- **Vendors:** %s" % ", ".join(names),
                 "- **Your forwarding address:** `%s`" % mailwatch.forwarding_address(
                     mailwatch.config()["address"], state["token"]) if mailwatch.configured() else
                 "- **Mailbox:** not configured on this agent",
                 "- **Forwarding:** %s" % ("working (first email received)" if state.get("confirmed_at")
                                           else "waiting for your first forwarded email")]
        return "\n".join(lines)

    m = STOP_WATCH_RE.match(t)
    if m:
        vendor = vendors.get_vendor(store, sender, m.group(1))
        if not vendor:
            return "I don't have a saved vendor called **%s**." % vendors.clean_name(m.group(1))
        if not mailwatch.stop_watching(store, sender, vendor):
            return "I wasn't watching emails for **%s**." % vendor["name"]
        return ("Stopped watching emails for **%s**. Emails about them that still arrive are ignored. You can "
                "also delete the forwarding filter in your email settings." % vendor["name"])

    m = WATCH_RE.match(t)
    if not m:
        return None
    vendor = vendors.get_vendor(store, sender, m.group(1))
    if not vendor:
        return None  # not a vendor name: let other handlers try
    status, address = mailwatch.start_watching(store, sender, vendor, now=now)
    if status == "no_email":
        return ("Save an email address for **%s** first, so I know whose emails to watch: "
                "`save vendor %s email accounts@example.com`." % (vendor["name"], vendor["name"]))
    if status == "no_mailbox":
        return ("Email watching isn't switched on for this agent yet (its mailbox isn't configured). "
                "You can still paste a vendor's email here with `check email`.")
    senders = ", ".join(vendor.get("emails") or ["@" + d for d in vendor.get("domains", [])])
    return "\n".join([
        "OK: I'll watch for emails from **%s** (%s) and flag any change to their bank or tax details. "
        "Say `stop watching %s` at any time." % (vendor["name"], senders, vendor["name"]),
        "",
        "**Set up forwarding once.** I never get access to your inbox, only what you forward to "
        "your private address `%s`." % address,
        "",
        "In Gmail:",
    ] + mailwatch.gmail_steps(address, vendor) + [
        "",
        "Using another email provider? Create a rule that forwards only %s's emails to `%s`."
        % (vendor["name"], address),
    ])


def email_alert(res):
    """Message for an email that arrived by forwarding (sent without the user asking)."""
    return "📩 **New forwarded email**\n\n" + _email_reply(res)


def _consent_draft_reply(vendor):
    return "\n".join([
        "Here's a consent request for **%s**. **Send it yourself** from your usual email (or read it "
        "out when you call them). I never contact vendors." % vendor["name"],
        "",
        "```",
        consent.draft_request(vendor),
        "```",
        "",
        "When they answer, paste their reply here starting with `consent reply %s` on the first line."
        % vendor["name"],
    ])


def _email_text(t):
    """The pasted email if this message is one (`check email ...`, a forwarded email, or text
    starting with From: and Subject: headers), else None."""
    m = EMAIL_CMD_RE.match(t)
    if m:
        return m.group(1)
    if detect.FORWARD_RE.search(t):
        return t
    head = [line.strip().lower() for line in t.split("\n")[:12] if line.strip()]
    if any(h.startswith("from:") for h in head) and any(h.startswith("subject:") for h in head):
        return t
    return None


def _show(field, value):
    if value is None:
        return "not on file"
    if field in ("legal_name", "vat"):
        return value
    return vendors.mask(field, value)


MATCHED_BY = {"sender": "a saved email address", "domain": "their saved domain",
              "lookalike": "a lookalike domain", "ids": "IDs in the email", "name": "their name in the email"}


def _email_reply(res):
    """Plain report of what an email contained. The email's own words are never repeated
    as instructions; only fixed text, IDs and masked values appear."""
    if res["status"] == "duplicate":
        return "I've already processed this email, so I didn't open another case."
    who = res["email"]["from_addr"] or "an unknown sender"
    vendor = res["vendor"]
    if vendor is None:
        lines = ["**Email from %s**" % who, "",
                 "I couldn't match it to a vendor you saved, so there's nothing to compare. Save the "
                 "vendor first, including their email address: `save vendor <name> email <address> ...`."]
    else:
        lines = ["**Email from %s** about **%s** (matched by %s)." % (who, vendor["name"], MATCHED_BY[res["matched_by"]])]
    warnings = detect.sender_flag_lines(res["sender_flags"])
    if warnings:
        lines.extend(["", "**Sender warnings**", ""] + ["- ⚠️ %s" % w for w in warnings])
    if res["wording_flags"]:
        lines.extend([""] + heads_up_lines(res["wording_flags"]))
    if res["status"] == "no_change":
        lines.extend(["", "✅ The details in this email match what you saved for %s. No case opened." % vendor["name"]])
    elif res["status"] == "case_opened":
        case = res["case"]
        label = dict(vendors.FIELD_LABELS, legal_name="Legal name")
        lines.extend(["", "❌ **Changes compared with what you saved for %s**" % vendor["name"], ""])
        lines.extend("- %s: was `%s`, now `%s`" % (label.get(c["field"], c["field"]), _show(c["field"], c["old"]),
                                                   _show(c["field"], c["new"])) for c in case["changes"])
        lines.extend(["", "**Case `%s` opened. The payment is ON HOLD** until you decide. Your saved "
                          "details stay as they are unless you approve." % case["id"]])
        lines.extend([""] + _next_step_lines(case, vendor))
    return "\n".join(lines)


def _change_list(case):
    label = dict(vendors.FIELD_LABELS, legal_name="Legal name")
    return ["- %s: was `%s`, now `%s`" % (label.get(c["field"], c["field"]), _show(c["field"], c["old"]),
                                         _show(c["field"], c["new"])) for c in case["changes"]]


def _next_step_lines(case, vendor):
    cid, state = case["id"], case["state"]
    if state == cases.ON_HOLD and case.get("call_after"):
        return ["An automated verification call to the saved number is planned in about %d minutes. "
                "To phone them yourself instead, say `I'll call %s`." % (workflow.cancel_window_min(), cid)]
    if state in (cases.CALL_SCHEDULED, cases.CALLING):
        return ["An automated verification call is in progress. I'll report back."]
    if state == cases.HUMAN_CALLBACK:
        return workflow.callback_script(case, vendor)
    if state == cases.AWAITING_USER:
        lines = []
        outcome = case.get("outcome")
        if outcome in ("VENDOR_DENIED", "DENIED_SENDING"):
            lines.append("The vendor said they did not send this. I suggest `reject %s`, not paying to "
                         "the new details, and reporting the email to your bank." % cid)
        elif outcome == "NO_ANSWER":
            lines.append("Nobody answered, so nothing is confirmed. Don't approve until you've reached "
                         "them; then reply `confirmed %s` or `not confirmed %s`." % (cid, cid))
        elif outcome == "CONFIRMED_BUT_READBACK_MISMATCH":
            lines.append("The digits read back on the call don't match the request. Don't approve yet: "
                         "call them yourself on the saved number, then reply `confirmed %s` or "
                         "`not confirmed %s`." % (cid, cid))
        elif outcome == "UNCLEAR":
            lines.append("The call was unclear. Call them yourself on the saved number before deciding, "
                         "then reply `confirmed %s` or `not confirmed %s`." % (cid, cid))
        lines.append("To approve, quote the case id and %s: `approve %s %s`. To keep your saved details, "
                     "say `reject %s`." % (cases.confirm_label(case), cid, cases.confirm_code(case), cid))
        return lines
    return []


def _case_report(store, owner, case):
    vendor = workflow.vendor_for(store, owner, case)
    lines = ["**Case `%s`: %s** (%s)" % (case["id"], case["vendor_name"], STATE_TEXT[case["state"]]), ""]
    lines.extend(["**Proposed changes**", ""] + _change_list(case))
    if case["flags"]:
        lines.extend(["", "**Why it was flagged:** %s." % "; ".join(FLAG_SHORT.get(f, f) for f in case["flags"])])
    outcome = workflow.outcome_line(case)
    if outcome:
        lines.extend(["", "**Call result:** %s" % outcome])
    if case.get("summary"):
        lines.extend(["", "**Call summary** _(%s)_: %s" % (
            "written by an AI model from the transcript, checked" if case.get("summary_source") == "llm"
            else "from the call results", case["summary"])])
    if case.get("decision"):
        lines.extend(["", "**Your decision:** %s." % case["decision"]["decision"]])
    nxt = _next_step_lines(case, vendor)
    if nxt:
        lines.extend([""] + nxt)
    return "\n".join(lines)


def _case_command(t, low, store, sender, now, llm=OFF):
    """Case commands. Returns a reply, or None if `t` is not one."""
    if low in MY_CASES or CASE_RE.match(t) or APPROVE_RE.match(t) or REJECT_RE.match(t) \
            or CALLBACK_RE.match(t) or CANCEL_CALL_RE.match(t) or DEMO_CALL_RE.match(t):
        notices = workflow.tick(store, sender, now, client=llm.client)
    else:
        return None
    pre = ["\n".join(notices), ""] if notices else []

    m = DEMO_CALL_RE.match(t)
    if m:
        if not countries.demo_mode():
            return "Demo calls only work with `DEMO_MODE=1` in the agent's `.env`."
        case = cases.get_case(store, sender, m.group(1))
        if not case:
            return "I don't have a case `%s`." % m.group(1).upper()
        scenario = (m.group(2) or "confirm_match").lower()
        if scenario not in telephony.SCENARIOS:
            return "Unknown scenario. Try one of: %s." % ", ".join("`%s`" % s for s in telephony.SCENARIOS)
        if case["state"] == cases.ON_HOLD and case.get("call_after"):
            case = cases.transition(store, sender, case["id"], cases.CALL_SCHEDULED, note="demo: call now", now=now)
        if case["state"] != cases.CALL_SCHEDULED:
            return ("Case `%s` isn't waiting for an automated call (%s). Automated calls need "
                    "`CALLS_ENABLED=1`, `DEMO_MODE=1`, a vendor in India, and the vendor's consent for the "
                    "saved number (`consent request <vendor>`)." % (case["id"], STATE_TEXT[case["state"]]))
        msg = workflow.place_call(store, sender, case["id"], telephony.MockTelephony(scenario), now, llm.client)
        case = cases.get_case(store, sender, case["id"])
        return "\n".join(pre + ["_Demo call (mock phone, scenario `%s`)._ %s" % (scenario, msg), "",
                                _case_report(store, sender, case)])

    if low in MY_CASES:
        open_cases = cases.list_cases(store, sender, open_only=low != "all cases")
        if not open_cases:
            return "\n".join(pre + ["You have no open cases."])
        lines = pre + ["**Your %scases** (%d)" % ("" if low == "all cases" else "open ", len(open_cases)), ""]
        lines.extend("- `%s` %s: %s" % (c["id"], c["vendor_name"], STATE_TEXT[c["state"]]) for c in open_cases)
        lines.extend(["", "Say `case <id>` for details."])
        return "\n".join(lines)

    m = CASE_RE.match(t)
    if m:
        case = cases.get_case(store, sender, m.group(1))
        return "\n".join(pre + [_case_report(store, sender, case) if case else "I don't have a case `%s`." % m.group(1).upper()])

    m = CANCEL_CALL_RE.match(t)
    if m:
        status, case = workflow.cancel_call(store, sender, m.group(1), now)
        if status == "not_found":
            return "I don't have a case `%s`." % m.group(1).upper()
        if status == "too_late":
            return "Case `%s` is already past that point (%s)." % (case["id"], STATE_TEXT[case["state"]])
        return "\n".join(["OK, no automated call."] + [""] + workflow.callback_script(case, workflow.vendor_for(store, sender, case)))

    m = CALLBACK_RE.match(t)
    if m:
        answer, cid = m.group(1).lower(), m.group(2)
        if not cid:
            waiting = [c for c in cases.list_cases(store, sender) if c["state"] == cases.HUMAN_CALLBACK]
            if len(waiting) != 1:
                return ("Which case? Say `%s <case id>`. Your cases waiting for a call: %s."
                        % (answer, ", ".join("`%s`" % c["id"] for c in waiting) or "none"))
            cid = waiting[0]["id"]
        status, case = workflow.record_callback(store, sender, cid, answer, now)
        if status == "not_found":
            return "I don't have a case `%s`." % cid.upper()
        if status == "wrong_state":
            return "Case `%s` isn't waiting for a call result (%s)." % (case["id"], STATE_TEXT[case["state"]])
        return "Noted.\n\n" + _case_report(store, sender, case)

    m = APPROVE_RE.match(t)
    if m:
        cid, code = m.group(1).upper(), m.group(2)
        case = cases.get_case(store, sender, cid)
        if case and not code:
            return "To approve, quote the case id and %s: `approve %s <code>`." % (cases.confirm_label(case), cid)
        status, case = cases.approve(store, sender, cid, code, now)
        if status == "not_found":
            return "I don't have a case `%s`." % cid
        if status == "approved":
            return ("✅ **Approved case `%s`.** I've updated the saved details for **%s**:\n\n%s\n\nThis was your "
                    "decision; the checks and any call were evidence, not proof."
                    % (cid, case["vendor_name"], "\n".join(_change_list(case))))
        if status == "already_approved":
            return "Case `%s` is already approved. Nothing more to do." % cid
        if status == "wrong_code":
            left = cases.MAX_FAILED_APPROVALS - case["failed_approvals"]
            return ("That code doesn't match, so nothing changed. Quote %s. %d attempt%s left."
                    % (cases.confirm_label(case), left, "" if left == 1 else "s"))
        if status == "locked":
            return ("Too many wrong codes for case `%s`, so approval is locked. Say `reject %s` and ask the "
                    "vendor to resend their details." % (cid, cid))
        if status == "not_ready":
            return "Case `%s` isn't ready to approve yet.\n\n%s" % (cid, "\n".join(_next_step_lines(case, workflow.vendor_for(store, sender, case))))
        return "Case `%s` is closed (%s)." % (cid, STATE_TEXT[case["state"]])

    m = REJECT_RE.match(t)
    if m:
        cid = m.group(1).upper()
        status, case = cases.reject(store, sender, cid, now)
        if status == "not_found":
            return "I don't have a case `%s`." % cid
        if status == "already_rejected":
            return "Case `%s` is already rejected." % cid
        if status == "closed":
            return "Case `%s` is closed (%s)." % (cid, STATE_TEXT[case["state"]])
        lines = ["**Rejected case `%s`.** Your saved details for **%s** stay as they were. Don't pay to the "
                 "details in that message." % (cid, case["vendor_name"])]
        if case.get("outcome") == "VENDOR_DENIED" or set(case["flags"]) & {"lookalike_domain", "unknown_sender", "reply_to_mismatch"}:
            lines.extend(["", "Consider reporting the email to your bank and your email provider as "
                              "suspected fraud, and don't reply to it."])
        return "\n".join(lines)
    return None


def first_contact(store, sender, now=None):
    """Reply to a new chat session: setup for a new user, help for a returning one."""
    if onboarding.has_profile(store, sender):
        return HELP_TEXT
    return onboarding.begin(store, sender, now)


def reply_for_text(text, store, sender="local", now=None, llm=OFF):
    if not allow_message(store, sender, now):
        return RATE_LIMITED_TEXT
    t = LEADING_MENTIONS.sub("", text or "").strip()
    if len(t) <= EMAIL_MAX_CHARS:
        reply = _consent_command(t, store, sender, now)
        if reply is not None:
            return reply
    email_text = _email_text(t)
    if email_text is not None:
        if len(t) > EMAIL_MAX_CHARS:
            return ("That email is too long for me (%d characters). Please paste up to %d characters: "
                    "the headers and the part about payment details." % (len(t), EMAIL_MAX_CHARS))
        return _email_reply(detect.process_email(store, sender, raw=email_text, now=now))
    if too_long(t):
        return TOO_LONG_TEXT % len(t)
    low = t.lower()

    if low in START_WORDS:
        return onboarding.begin(store, sender, now)
    m = SETTINGS_RE.match(t)
    if m:
        if m.group(1):
            return onboarding.edit(store, sender, m.group(1))
        return onboarding.settings_reply(store, sender)
    m = RULES_RE.match(t)
    if m:
        code = countries.resolve(m.group(1))
        if not code:
            return "I don't recognise that country. Try `rules India`, `rules UK`, `rules Germany` or `rules US`."
        return countries.describe(code)
    if onboarding.is_active(store, sender):
        def save_for_setup(txt):
            r = _vendor_command(txt, txt.lower(), store, sender, now) or ""
            return r.startswith(("Saved", "Updated")), r
        reply = onboarding.handle(t, store, sender, save_for_setup)
        if reply is not None:
            return reply

    if low == "selftest":
        return _selftest(store)

    reply = _case_command(t, low, store, sender, now, llm)
    if reply is not None:
        return reply
    reply = _watch_command(t, low, store, sender, now)
    if reply is not None:
        return reply
    m = BUSINESS_RE.match(t)
    if m:
        name = vendors.clean_name(m.group(1), vendors.MAX_TEXT)
        profile = onboarding.load(store, sender)
        profile["business_name"] = name
        store.set("profile:%s" % sender, profile)
        return ("Saved your business name as **%s**. Automated verification calls will say they're "
                "made on behalf of %s." % (name, name))
    reply = _vendor_command(t, low, store, sender, now)
    if reply is not None:
        return reply
    m = DETAILS_RE.match(t)
    if m:
        return _details_reply(int(m.group(1)), store, sender, now)

    found, bad = extract_ids(t)
    iban = vendors.find_iban(t)
    if iban:
        found["iban"] = [iban]
    flag_labels = detect_flags(t)
    nothing = not any(found.values()) and not any(bad.values())

    vendor, from_saved = None, False
    m = CHECK_NAME_RE.match(t)
    if m:
        vendor = vendors.get_vendor(store, sender, vendors.name_part(m.group(1)))
        if vendor and nothing:
            found = {k: [vendor[k]] if vendor.get(k) else [] for k in ("gstin", "pan", "ifsc", "pin")}
            nothing, from_saved = False, True
        elif nothing and vendors.list_vendors(store, sender):
            return ("I don't have a saved vendor called **%s**. Say `my vendors` to see the list."
                    % vendors.name_part(m.group(1)))

    if nothing:
        if not t or low in GREETINGS or low.startswith("help"):
            return HELP_TEXT
        if low in ("sample", "samples", "example", "examples"):
            return SAMPLE_TEXT

    if not nothing and not from_saved:
        chunks = split_vendors(t)
        if chunks:
            return _batch_reply(chunks, store, sender, now, flag_labels)

    llm_read = None
    if (llm.client and llm.extract and not from_saved and should_extract(t, found)
            and allow_llm(store, sender, now)):
        res = extract_ids_llm(t, llm.client)
        if res:
            new = [(k, v) for k in ("gstin", "pan", "ifsc", "pin") for v in res[k] if v not in found[k]]
            for k, v in new:
                found[k].append(v)
                # a glued fragment like "YESB0DNB002BRANCH" is not a separate problem once read
                bad[k] = [b for b in bad[k] if v not in b]
            if new:
                llm_read = (new, res["vendor_name"])
                nothing = False
    if (llm.client and llm.flags and flags_inconclusive(t, flag_labels)
            and allow_llm(store, sender, now)):
        flag_labels = classify_flags_llm(t, llm.client) or flag_labels

    if nothing:
        if flag_labels:
            return "\n".join(heads_up_lines(flag_labels) + ["", NO_ID_TEXT])
        return NO_ID_TEXT

    changed, same = [], []
    if not from_saved:
        ids = _message_ids(found, t)
        vendors.remember_last(store, sender, ids, now)
        if vendor is None and found["gstin"]:
            vendor = vendors.find_by_gstin(store, sender, found["gstin"][0])
        if vendor is None and ids.get("iban"):
            vendor = vendors.find_by_iban(store, sender, ids["iban"])
        if vendor:
            changed, same = vendors.compare(vendor, ids)

    ev = _evaluate(found, bad, store, vendor, changed, same)
    summary = None
    if llm.client and llm.summary and allow_llm(store, sender, now):
        summary = summarize(summary_input(ev, changed, flag_labels), llm.client)
    return _check_reply(
        ev, flag_labels=flag_labels, vendor=vendor, changed=changed, from_saved=from_saved,
        llm_read=llm_read, provider_name=llm.provider_name, summary=summary,
    )


def summary_input(ev, changed=(), flag_labels=()):
    """The structured results a summary may draw on. Never includes the user's message."""
    return {
        "counts": ev["counts"],
        "items": [{k: r[k] for k in ("kind", "id", "level", "text")} for r in ev["items"]],
        "cross": [{"level": r["level"], "text": r["text"]} for r in ev["cross"]],
        "bank_details_changed_since_saved": bool(changed),
        "wording_flags": list(flag_labels),
    }


def _message_ids(found, text):
    """IDs from a message for saving and comparing. acct_digits is used only for the
    salted-hash comparison and is never stored (remember_last drops it)."""
    ids = {k: found[k][0] for k in ("gstin", "pan", "ifsc", "iban") if found.get(k)}
    digits = vendors.account_digits(text)
    if digits:
        ids["acct_digits"] = digits
        ids["acct_last4"] = digits[-4:]
    return ids


def _evaluate(found, bad, store, vendor=None, changed=(), same=()):
    """Run every check on the IDs found. All pass/warn/fail levels come from here."""
    notes = []
    for kind in found:
        if len(found[kind]) > MAX_PER_TYPE or len(bad.get(kind, [])) > MAX_PER_TYPE:
            notes.append("I checked the first %d of each type." % MAX_PER_TYPE)
            break

    items = []
    gst_res, ifsc_res, pin_res = [], [], []
    pans_ok = []

    for g in found["gstin"][:MAX_PER_TYPE]:
        r = check_gstin(g)
        if r["level"] == "fail":
            r["fixes"], r["too_many_fixes"] = gstin_typo_fixes(g, found["pan"])
        gst_res.append(r)
        items.append(r)
    for p in found["pan"][:MAX_PER_TYPE]:
        r = check_pan(p)
        pans_ok.append(p)
        items.append(r)
    for c in found["ifsc"][:MAX_PER_TYPE]:
        r = ifsc_result(c, store)
        ifsc_res.append(r)
        items.append(r)
    for n in found["pin"][:MAX_PER_TYPE]:
        r = pin_result(n, store)
        pin_res.append(r)
        items.append(r)
    for n in found.get("iban", [])[:MAX_PER_TYPE]:
        items.append(check_iban(n))
    for kind in bad:
        for tok in bad[kind][:MAX_PER_TYPE]:
            items.append(check_malformed(kind, tok))

    cross = cross_checks(gst_res, pans_ok, ifsc_res, pin_res)
    if same and not changed:
        cross.append({
            "level": "pass",
            "text": "Same %s as you saved for %s." % (
                " and ".join(vendors.FIELD_LABELS[f] for f in same), vendor["name"]),
        })

    counts = {"pass": 0, "warn": 0, "fail": 0}
    for r in items + cross:
        counts[r["level"]] += 1
    if changed:
        counts["fail"] += 1
    return {"items": items, "cross": cross, "counts": counts, "notes": notes,
            "gst_res": gst_res, "ifsc_res": ifsc_res, "pans": pans_ok}


def _check_reply(ev, flag_labels=(), vendor=None, changed=(), from_saved=False,
                 llm_read=None, provider_name=None, title=None, summary=None):
    items, cross, counts, notes = ev["items"], ev["cross"], ev["counts"], ev["notes"]
    lines = [title, ""] if title else []
    lines.append(
        "**%d passed%s%d to confirm%s%d problem%s**"
        % (counts["pass"], DOT, counts["warn"], DOT, counts["fail"], "" if counts["fail"] == 1 else "s")
    )
    # ASI:One renders Markdown and joins lines separated by a single newline, so every
    # block below is a separate paragraph or a bullet list.
    if counts["fail"]:
        lines.extend(["", "Resolve the problems before paying."])
    elif not counts["warn"]:
        lines.extend(["", "No problems found in these checks."])
    if llm_read:
        lines.append("")
        lines.append(_llm_read_line(llm_read, provider_name))
    if flag_labels:
        lines.append("")
        lines.extend(heads_up_lines(flag_labels))
    if changed:
        lines.append("")
        lines.extend(_change_lines(vendor["name"], changed))
    lines.append("")
    if from_saved:
        lines.append("**Checks** (the details you saved for %s)" % vendor["name"])
    else:
        lines.append("**Checks**")
    lines.append("")
    for r in items:
        lines.append("- %s %s `%s`: %s" % (ICON[r["level"]], LABELS[r["kind"]], r["id"], r["text"]))
        lines.extend(_typo_lines(r))
    if cross:
        lines.extend(["", "**Cross-checks**", ""])
        for r in cross:
            lines.append("- %s %s" % (ICON[r["level"]], r["text"]))

    lines.extend(_before_you_pay(bool(ev["gst_res"])))
    for n in notes:
        lines.append("")
        lines.append(n)
    if summary:
        lines.append("")
        lines.append("**In short** _(written by an AI model (%s) from the checks above)_: %s"
                     % (provider_name, summary))
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def _before_you_pay(has_gstin):
    lines = ["", "**Before you pay**", ""]
    if has_gstin:
        lines.append("- Confirm the GSTIN is active on the official GST portal (gst.gov.in).")
    lines.append("- Call the vendor on a number you already have, not one from the invoice or message.")
    lines.append("- Treat any request to change bank details by WhatsApp or email as suspicious until confirmed.")
    return lines


# ---------- F7: batch check ----------

def split_vendors(text):
    """Split a message about several vendors into one chunk per vendor, on blank lines,
    or else on semicolons and lines that each hold a GSTIN. Returns [] for one vendor."""
    def has_gstin(s):
        return bool(extract_ids(s)[0]["gstin"])

    def has_ids(s):
        found, bad = extract_ids(s)
        return any(found.values()) or any(bad.values())

    blocks = [b for b in BLANK_SPLIT.split(text) if has_ids(b)]
    if sum(map(has_gstin, blocks)) >= 2:
        return blocks
    groups = []
    for piece in PIECE_SPLIT.split(text):
        if not piece.strip():
            continue
        if has_gstin(piece) or not groups:
            groups.append(piece)
        else:
            groups[-1] += "\n" + piece
    groups = [g for g in groups if has_ids(g)]
    return groups if sum(map(has_gstin, groups)) >= 2 else []


def _batch_vendor(chunk, n, store, sender):
    chunk = BULLET.sub("", chunk.strip())
    found, bad = extract_ids(chunk)
    name = vendors.name_part(chunk).replace("|", "/") or "Vendor %d" % n
    ids = _message_ids(found, chunk)
    vendor = vendors.find_by_gstin(store, sender, found["gstin"][0]) if found["gstin"] else None
    changed, same = vendors.compare(vendor, ids) if vendor else ([], [])
    return {"name": name, "found": found, "bad": bad, "ids": ids,
            "vendor": vendor, "changed": changed, "same": same}


def _batch_row(n, v, ev):
    g = ev["gst_res"][0] if ev["gst_res"] else None
    gstin = "%s `%s`" % (ICON[g["level"]], g["id"]) if g else "—"
    usable = g if g and g["level"] != "fail" else None
    if usable and ev["pans"]:
        pan = "✅" if usable["pan"] in ev["pans"] else "❌"
    else:
        pan = "—"
    b = ev["ifsc_res"][0] if ev["ifsc_res"] else None
    if not b:
        bank = "—"
    elif b["level"] == "pass":
        bank = b["bank"]
    elif b["level"] == "fail":
        bank = "❌ not found"
    else:
        bank = "⚠️ couldn't confirm"
    c = ev["counts"]
    if v["changed"]:
        result = "❌ bank details changed"
    elif c["fail"]:
        result = "❌ %d problem%s" % (c["fail"], "" if c["fail"] == 1 else "s")
    elif c["warn"]:
        result = "⚠️ %d to confirm" % c["warn"]
    else:
        result = "✅ no problems found"
    return "| %d | %s | %s | %s | %s | %s |" % (n, v["name"], gstin, pan, bank, result)


def _batch_reply(chunks, store, sender, now, flag_labels):
    over = len(chunks) > BATCH_MAX
    rows, saved, n_fail, n_warn, any_gstin = [], [], 0, 0, False
    for n, chunk in enumerate(chunks[:BATCH_MAX], 1):
        v = _batch_vendor(chunk, n, store, sender)
        ev = _evaluate(v["found"], v["bad"], store, v["vendor"], v["changed"], v["same"])
        rows.append(_batch_row(n, v, ev))
        n_fail += bool(ev["counts"]["fail"])
        n_warn += bool(not ev["counts"]["fail"] and ev["counts"]["warn"])
        any_gstin = any_gstin or bool(ev["gst_res"])
        saved.append({"name": v["name"], "found": v["found"], "bad": v["bad"], "ids": v["ids"]})
    store.set("batch:%s" % sender, {"t": time.time() if now is None else now, "vendors": saved})

    lines = ["**Checked %d vendors%s%d with problems%s%d to confirm**"
             % (len(rows), DOT, n_fail, DOT, n_warn)]
    if flag_labels:
        lines.append("")
        lines.extend(heads_up_lines(flag_labels))
    lines.append("")
    lines.append("| # | Vendor | GSTIN | PAN match | Bank | Result |")
    lines.append("|---|---|---|---|---|---|")
    lines.extend(rows)
    lines.append("")
    if over:
        lines.append("I can check up to %d vendors at once, so I checked the first %d. "
                     "Send the rest in another message." % (BATCH_MAX, BATCH_MAX))
    lines.append("Say `details 2` for the full check of vendor 2.")
    lines.extend(_before_you_pay(any_gstin))
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def _details_reply(n, store, sender, now):
    batch = store.get("batch:%s" % sender, {}) or {}
    now = time.time() if now is None else now
    if not batch.get("vendors") or now - batch.get("t", 0) > vendors.LAST_CHECK_TTL:
        return "I don't have a recent batch check. Paste several vendors in one message first."
    vlist = batch["vendors"]
    if not 1 <= n <= len(vlist):
        return "Your last batch had %d vendors. Say `details 1` to `details %d`." % (len(vlist), len(vlist))
    v = vlist[n - 1]
    vendor = vendors.find_by_gstin(store, sender, v["found"]["gstin"][0]) if v["found"]["gstin"] else None
    changed, same = vendors.compare(vendor, v["ids"]) if vendor else ([], [])
    ev = _evaluate(v["found"], v["bad"], store, vendor, changed, same)
    return _check_reply(ev, vendor=vendor, changed=changed,
                        title="**Vendor %d: %s**" % (n, v["name"]))
