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
from llm import OFF, classify_flags_llm, extract_ids_llm, flags_inconclusive, should_extract
from lookups import lookup_ifsc, lookup_pin
from suggest import gstin_typo_fixes
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

SAVE_RE = re.compile(r"^save\s+vendor\b\s*(.*)$", re.I | re.S)
FORGET_RE = re.compile(r"^forget\b\s*(.*)$", re.I | re.S)
CHECK_NAME_RE = re.compile(r"^check\s+(.+)$", re.I | re.S)
MY_VENDORS = {"my vendors", "vendors", "list vendors", "saved vendors"}
DETAILS_RE = re.compile(r"^(?:details?|show)\s+(?:of\s+|for\s+)?(?:vendor\s+)?#?(\d{1,2})$", re.I)

BATCH_MAX = 5
BLANK_SPLIT = re.compile(r"\n\s*\n")
PIECE_SPLIT = re.compile(r"[;\n]")
BULLET = re.compile(r"^(?:[-*•]|\d{1,2}[.)])\s*")


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
        r["bank"] = d["bank"]
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


def _ids_line(rec):
    parts = ["%s `%s`" % (vendors.FIELD_LABELS[f], vendors.mask(f, rec[f]) if f == "acct_last4" else rec[f])
             for f in vendors.FIELDS if rec.get(f)]
    return ", ".join(parts)


def _vendor_command(t, low, store, sender, now):
    """Handle the saved-vendor commands. Returns a reply, or None if `t` is not one."""
    if low in MY_VENDORS:
        recs = vendors.list_vendors(store, sender)
        if not recs:
            return "You haven't saved any vendors yet. After a check, say `save vendor <name>`."
        lines = ["**Your saved vendors** (%d)" % len(recs)]
        lines.extend("- **%s**: %s" % (r["name"], _ids_line(r)) for r in recs)
        lines.append("")
        lines.append("Say `check <name>` to check one again, or `forget <name>` to delete it.")
        return "\n".join(lines)

    m = SAVE_RE.match(t)
    if m:
        rest = m.group(1)
        name = vendors.name_part(rest)
        found, _ = extract_ids(rest)
        ids = {k: found[k][0] for k in ("gstin", "pan", "ifsc") if found[k]}
        last4 = vendors.account_last4(rest)
        if last4:
            ids["acct_last4"] = last4
        if not ids:
            ids = vendors.last_ids(store, sender, now)
        status = vendors.save_vendor(store, sender, name, ids)
        if status == "no_name":
            return "Please give the vendor a name, for example `save vendor Sharma Traders`."
        if status == "no_ids":
            return ("I didn't find any IDs to save. Send `save vendor <name>` right after a check, "
                    "or together with the vendor's GSTIN, PAN or IFSC.")
        if status == "full":
            return "You already have %d saved vendors. Say `forget <name>` to make room." % vendors.MAX_VENDORS
        rec = vendors.get_vendor(store, sender, name)
        verb = "Updated" if status == "updated" else "Saved"
        return (
            "%s **%s**: %s.\n\n"
            "Next time, say `check %s` with the IDs from the new invoice and I'll tell you if the "
            "bank details changed. Say `forget %s` to delete it. I keep only the name and these IDs, "
            "never full account numbers." % (verb, rec["name"], _ids_line(rec), rec["name"], rec["name"])
        )

    m = FORGET_RE.match(t)
    if m:
        arg = m.group(1).strip()
        if not arg:
            return "Say `forget <name>` to delete one saved vendor, or `forget all` to delete everything."
        if arg.lower() == "all":
            n = vendors.forget_all(store, sender)
            return "Deleted %d saved vendor%s and your recent check." % (n, "" if n == 1 else "s")
        if vendors.forget(store, sender, arg):
            return "Deleted **%s**." % vendors.clean_name(arg)
        return ("I don't have a saved vendor called **%s**. Say `my vendors` to see the list."
                % vendors.clean_name(arg))
    return None


def _change_lines(name, changed):
    lines = ["❌ **Bank details changed since you saved %s**" % name]
    for f, old, new in changed:
        lines.append("- %s: was `%s`, now `%s`" % (vendors.FIELD_LABELS[f], vendors.mask(f, old), vendors.mask(f, new)))
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


def reply_for_text(text, store, sender="local", now=None, llm=OFF):
    if not allow_message(store, sender, now):
        return RATE_LIMITED_TEXT
    t = LEADING_MENTIONS.sub("", text or "").strip()
    if too_long(t):
        return TOO_LONG_TEXT % len(t)
    low = t.lower()

    if low == "selftest":
        return _selftest(store)

    reply = _vendor_command(t, low, store, sender, now)
    if reply is not None:
        return reply
    m = DETAILS_RE.match(t)
    if m:
        return _details_reply(int(m.group(1)), store, sender, now)

    found, bad = extract_ids(t)
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
        if vendor:
            changed, same = vendors.compare(vendor, ids)

    ev = _evaluate(found, bad, store, vendor, changed, same)
    return _check_reply(
        ev, flag_labels=flag_labels, vendor=vendor, changed=changed, from_saved=from_saved,
        llm_read=llm_read, provider_name=llm.provider_name,
    )


def _message_ids(found, text):
    ids = {k: found[k][0] for k in ("gstin", "pan", "ifsc") if found[k]}
    last4 = vendors.account_last4(text)
    if last4:
        ids["acct_last4"] = last4
    return ids


def _evaluate(found, bad, store, vendor=None, changed=(), same=()):
    """Run every check on the IDs found. All pass/warn/fail levels come from here."""
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
                 llm_read=None, provider_name=None, title=None):
    items, cross, counts, notes = ev["items"], ev["cross"], ev["counts"], ev["notes"]
    lines = [title, ""] if title else []
    lines.append(
        "**%d passed%s%d to confirm%s%d problem%s**"
        % (counts["pass"], DOT, counts["warn"], DOT, counts["fail"], "" if counts["fail"] == 1 else "s")
    )
    if counts["fail"]:
        lines.append("Resolve the problems before paying.")
    elif not counts["warn"]:
        lines.append("No problems found in these checks.")
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
    for r in items:
        lines.append("%s %s `%s`: %s" % (ICON[r["level"]], LABELS[r["kind"]], r["id"], r["text"]))
        lines.extend(_typo_lines(r))
    if cross:
        lines.append("")
        lines.append("**Cross-checks**")
        for r in cross:
            lines.append("%s %s" % (ICON[r["level"]], r["text"]))

    lines.extend(_before_you_pay(bool(ev["gst_res"])))
    for n in notes:
        lines.append("")
        lines.append(n)
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def _before_you_pay(has_gstin):
    lines = ["", "**Before you pay**"]
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
