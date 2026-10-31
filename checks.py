import re

from normalize import (  # noqa: F401  (regexes re-exported for other modules)
    GSTIN_RE,
    PAN_RE,
    IFSC_RE,
    PIN_RE,
    tokenize,
    keyword_before,
    match_at,
)

CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

MAX_PER_TYPE = 3

STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman & Nicobar Islands",
    "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
    "96": "Other Country", "97": "Other Territory", "99": "Centre Jurisdiction",
}

RETIRED_STATE_CODES = {
    "25": ("Daman & Diu", "merged into code 26 (Dadra & Nagar Haveli and Daman & Diu) in 2020"),
    "28": ("Andhra Pradesh (before bifurcation)", "Andhra Pradesh now uses code 37"),
}

PAN_HOLDER_TYPES = {
    "P": "Individual", "C": "Company", "H": "Hindu Undivided Family",
    "F": "Firm / LLP", "A": "Association of Persons", "T": "Trust",
    "B": "Body of Individuals", "L": "Local Authority",
    "J": "Artificial Juridical Person", "G": "Government",
}

LABELS = {"gstin": "GSTIN", "pan": "PAN", "ifsc": "IFSC", "pin": "PIN"}

FORMAT_HELP = {
    "gstin": "15 characters: 2 digits, a 10-character PAN, 1 digit/letter, the letter Z, 1 check character",
    "pan": "10 characters: 5 letters, 4 digits, 1 letter",
    "ifsc": "11 characters: 4 letters, the digit 0, then 6 letters/digits",
    "pin": "6 digits, not starting with 0",
}

STATE_ALIASES = {
    "ORISSA": "ODISHA",
    "UTTARANCHAL": "UTTARAKHAND",
    "PONDICHERRY": "PUDUCHERRY",
}

def extract_ids(text):
    """Return (found, bad). found: well-formed IDs by type, including IDs written with
    spaces, dashes or dots. bad: tokens that follow a keyword (e.g. 'GSTIN') but are not
    well-formed."""
    tokens = tokenize(text)
    found = {"gstin": [], "pan": [], "ifsc": [], "pin": []}
    bad = {"gstin": [], "pan": [], "ifsc": [], "pin": []}
    i = 0
    while i < len(tokens):
        hit = match_at(tokens, i)
        if hit:
            kind, value, n = hit
            if value not in found[kind]:
                found[kind].append(value)
            i += n
            continue
        t = tokens[i]
        hint = keyword_before(tokens, i)
        if (
            hint
            and t.isalnum()
            and 4 <= len(t) <= 20
            and any(c.isdigit() for c in t)
        ):
            if t not in bad[hint]:
                bad[hint].append(t)
        i += 1
    return found, bad


def gstin_check_char(first14):
    total = 0
    for i, c in enumerate(first14):
        factor = 1 if i % 2 == 0 else 2
        p = CHARSET.index(c) * factor
        total += p // 36 + p % 36
    return CHARSET[(36 - total % 36) % 36]


def check_gstin(g):
    r = {"kind": "gstin", "id": g, "pan": g[2:12], "state": None}
    if gstin_check_char(g[:14]) != g[14]:
        r["level"] = "fail"
        r["text"] = "checksum does not match, so at least one character is probably wrong."
        return r
    state = STATE_CODES.get(g[:2])
    holder = PAN_HOLDER_TYPES.get(g[5])
    r["state"] = state
    if g[:2] in RETIRED_STATE_CODES:
        name, why = RETIRED_STATE_CODES[g[:2]]
        r["level"] = "warn"
        r["text"] = (
            "format and checksum are valid, but state code %s (%s) is no longer issued: %s. "
            "Ask the vendor for their current GSTIN." % (g[:2], name, why)
        )
        return r
    if state is None:
        r["level"] = "warn"
        r["text"] = "format and checksum are valid, but state code %s is not in my list." % g[:2]
        return r
    text = "valid format and checksum. State: %s. PAN inside it: %s" % (state, g[2:12])
    if holder:
        text += " (%s)" % holder
    text += ". Registration status is not checked."
    r["level"] = "pass" if holder else "warn"
    r["text"] = text
    return r


def check_pan(p):
    r = {"kind": "pan", "id": p}
    holder = PAN_HOLDER_TYPES.get(p[3])
    if holder is None:
        r["level"] = "warn"
        r["text"] = "format is valid, but the 4th letter '%s' is not a standard holder type." % p[3]
    else:
        r["level"] = "pass"
        r["text"] = "valid format. Holder type: %s. I can't confirm who owns it." % holder
    return r


def check_malformed(kind, token):
    return {
        "kind": kind,
        "id": token,
        "level": "fail",
        "text": "wrong format. Expected %s." % FORMAT_HELP[kind],
    }


# ---------- cross-checks ----------

def norm_state(s):
    s = (s or "").upper().replace("&", "AND")
    s = re.sub(r"[^A-Z ]", " ", s)
    s = " ".join(s.split())
    return STATE_ALIASES.get(s, s)


def same_state(a, b):
    a, b = norm_state(a), norm_state(b)
    if not a or not b:
        return True
    return a == b or a in b or b in a


def cross_checks(gst_results, pans, ifsc_results, pin_results):
    """gst_results/ifsc_results/pin_results: result dicts. pans: well-formed PAN strings."""
    out = []
    usable_gst = [g for g in gst_results if g.get("level") != "fail"]

    for g in usable_gst:
        if not pans:
            break
        if g["pan"] in pans:
            out.append({
                "level": "pass",
                "text": "PAN %s matches the PAN inside GSTIN %s." % (g["pan"], g["id"]),
            })
        else:
            out.append({
                "level": "fail",
                "text": "PAN mismatch: you gave %s, but GSTIN %s contains PAN %s."
                % (", ".join(pans), g["id"], g["pan"]),
            })

    gst_state = None
    for g in usable_gst:
        if g.get("state"):
            gst_state = g
            break
    if gst_state:
        for p in pin_results:
            if p.get("level") == "pass" and p.get("state"):
                if same_state(gst_state["state"], p["state"]):
                    out.append({
                        "level": "pass",
                        "text": "GSTIN state (%s) matches PIN %s (%s)." % (gst_state["state"], p["id"], p["state"]),
                    })
                else:
                    out.append({
                        "level": "warn",
                        "text": "GSTIN state is %s but PIN %s is in %s. This can be legitimate (another office), but confirm it."
                        % (gst_state["state"], p["id"], p["state"]),
                    })
        for b in ifsc_results:
            if b.get("level") == "pass" and b.get("state"):
                if same_state(gst_state["state"], b["state"]):
                    out.append({
                        "level": "pass",
                        "text": "GSTIN state (%s) matches the bank branch state (%s)." % (gst_state["state"], b["state"]),
                    })
                else:
                    out.append({
                        "level": "warn",
                        "text": "GSTIN state is %s but the bank branch %s is in %s. This can be legitimate (head office vs branch), but confirm it."
                        % (gst_state["state"], b["id"], b["state"]),
                    })
    return out
