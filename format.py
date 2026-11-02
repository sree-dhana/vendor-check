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
import countries
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
    consent = (rec.get("consent") or {}).get("status", "not_requested").replace("_", " ")
    lines.append("- **Consent for verification calls:** %s" % consent)
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


def first_contact(store, sender, now=None):
    """Reply to a new chat session: setup for a new user, help for a returning one."""
    if onboarding.has_profile(store, sender):
        return HELP_TEXT
    return onboarding.begin(store, sender, now)


def reply_for_text(text, store, sender="local", now=None, llm=OFF):
    if not allow_message(store, sender, now):
        return RATE_LIMITED_TEXT
    t = LEADING_MENTIONS.sub("", text or "").strip()
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
