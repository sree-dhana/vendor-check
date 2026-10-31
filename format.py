import re

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
from limits import RATE_LIMITED_TEXT, TOO_LONG_TEXT, allow_message, too_long
from lookups import lookup_ifsc, lookup_pin
from suggest import gstin_typo_fixes

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
    "I check format, checksum and consistency. I can't prove identity or that a GSTIN is active."
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

GREETINGS = {"hi", "hii", "hello", "hey", "help", "start", "menu", "?", "namaste"}

# ASI:One prefixes messages with the agent mention, e.g. "@agent1q... help" or "@vendor-check help".
LEADING_MENTIONS = re.compile(r"^(?:\s*@\S+)+")


def _yn(v):
    return "yes" if v else "no"


def _ifsc_result(code, store):
    lk = lookup_ifsc(code, store)
    r = {"kind": "ifsc", "id": code}
    if lk["status"] == "ok":
        d = lk["data"]
        place = ", ".join([x for x in (d["city"], d["state"]) if x])
        r["level"] = "pass"
        r["state"] = d["state"]
        r["text"] = "%s, %s (%s). NEFT %s, RTGS %s, IMPS %s, UPI %s." % (
            d["bank"], d["branch"], place,
            _yn(d["neft"]), _yn(d["rtgs"]), _yn(d["imps"]), _yn(d["upi"]),
        )
    elif lk["status"] == "not_found":
        r["level"] = "fail"
        r["text"] = "well-formed, but no bank branch with this code was found."
    else:
        r["level"] = "warn"
        r["text"] = "format is valid, but the lookup service didn't respond, so I couldn't confirm the branch."
    return r


def _pin_result(pin, store):
    lk = lookup_pin(pin, store)
    r = {"kind": "pin", "id": pin}
    if lk["status"] == "ok":
        d = lk["data"]
        r["level"] = "pass"
        r["state"] = d["state"]
        r["text"] = "%s, %s." % (d["district"], d["state"])
    elif lk["status"] == "not_found":
        r["level"] = "fail"
        r["text"] = "well-formed, but no area with this PIN code was found."
    else:
        r["level"] = "warn"
        r["text"] = "format is valid, but the lookup service didn't respond, so I couldn't confirm the area."
    return r


def _typo_lines(r):
    if r.get("fixes"):
        return ["    ↳ Possible typo fixes, confirm with the vendor: %s"
                % ", ".join("`%s`" % f for f in r["fixes"])]
    if r.get("too_many_fixes"):
        return ["    ↳ Several valid GSTINs are one typo away, too many to guess. Ask the vendor to resend it."]
    return []


def _selftest(store):
    ifsc = lookup_ifsc(SAMPLE_IFSC, store)["status"]
    pin = lookup_pin("560001", store)["status"]
    return "selftest: ifsc=%s, pin=%s" % (ifsc, pin)


def reply_for_text(text, store, sender="local", now=None):
    if not allow_message(store, sender, now):
        return RATE_LIMITED_TEXT
    t = LEADING_MENTIONS.sub("", text or "").strip()
    if too_long(t):
        return TOO_LONG_TEXT % len(t)
    low = t.lower()

    if low == "selftest":
        return _selftest(store)

    found, bad = extract_ids(t)
    flag_labels = detect_flags(t)
    nothing = not any(found.values()) and not any(bad.values())
    if nothing:
        if not t or low in GREETINGS or low.startswith("help"):
            return HELP_TEXT
        if low in ("sample", "samples", "example", "examples"):
            return SAMPLE_TEXT
        if flag_labels:
            return "\n".join(heads_up_lines(flag_labels) + ["", NO_ID_TEXT])
        return NO_ID_TEXT

    notes = []
    for kind in found:
        if len(found[kind]) > MAX_PER_TYPE or len(bad[kind]) > MAX_PER_TYPE:
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
        r = _ifsc_result(c, store)
        ifsc_res.append(r)
        items.append(r)
    for n in found["pin"][:MAX_PER_TYPE]:
        r = _pin_result(n, store)
        pin_res.append(r)
        items.append(r)
    for kind in bad:
        for tok in bad[kind][:MAX_PER_TYPE]:
            items.append(check_malformed(kind, tok))

    cross = cross_checks(gst_res, pans_ok, ifsc_res, pin_res)

    counts = {"pass": 0, "warn": 0, "fail": 0}
    for r in items + cross:
        counts[r["level"]] += 1

    lines = []
    lines.append(
        "**%d passed%s%d to confirm%s%d problem%s**"
        % (counts["pass"], DOT, counts["warn"], DOT, counts["fail"], "" if counts["fail"] == 1 else "s")
    )
    if counts["fail"]:
        lines.append("Resolve the problems before paying.")
    elif not counts["warn"]:
        lines.append("No problems found in these checks.")
    if flag_labels:
        lines.append("")
        lines.extend(heads_up_lines(flag_labels))
    lines.append("")
    lines.append("**Checks**")
    for r in items:
        lines.append("%s %s `%s`: %s" % (ICON[r["level"]], LABELS[r["kind"]], r["id"], r["text"]))
        lines.extend(_typo_lines(r))
    if cross:
        lines.append("")
        lines.append("**Cross-checks**")
        for r in cross:
            lines.append("%s %s" % (ICON[r["level"]], r["text"]))

    lines.append("")
    lines.append("**Before you pay**")
    if gst_res:
        lines.append("- Confirm the GSTIN is active on the official GST portal (gst.gov.in).")
    lines.append("- Call the vendor on a number you already have, not one from the invoice or message.")
    lines.append("- Treat any request to change bank details by WhatsApp or email as suspicious until confirmed.")
    for n in notes:
        lines.append("")
        lines.append(n)
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
