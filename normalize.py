"""Loose input: turn messy text into clean tokens and find IDs written with spaces,
dashes, dots or lowercase (e.g. `27 aapfu 0939 f1zv`, `sbin-0000001`). Pure, no network."""
import re

GSTIN_RE = re.compile(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]")
PAN_RE = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]")
IFSC_RE = re.compile(r"[A-Z]{4}0[A-Z0-9]{6}")
PIN_RE = re.compile(r"[1-9][0-9]{5}")

ID_PATTERNS = (("gstin", GSTIN_RE), ("pan", PAN_RE), ("ifsc", IFSC_RE), ("pin", PIN_RE))

TOKEN_SPLIT = re.compile(r"[\s,;:.()\[\]{}<>\"'|/\\=#*]+")
FILLERS = {"NO", "NUMBER", "NUM", "CODE", "ID", "IS", "OF", "THE"}
KEYS = {
    "gstin": {"GSTIN", "GST", "GSTN"},
    "pan": {"PAN"},
    "ifsc": {"IFSC", "IFS"},
    "pin": {"PIN", "PINCODE", "POSTAL", "ZIP", "ZIPCODE"},
}
KEYWORDS = set().union(*KEYS.values())

MAX_RUN = 5  # an ID may be split over at most this many tokens


def tokenize(text):
    out = []
    for t in TOKEN_SPLIT.split((text or "").upper()):
        t = t.strip("-_").replace("-", "")
        if t:
            out.append(t)
    return out


def squash(text):
    """Uppercase and drop everything that is not a letter or digit."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def id_kind(s):
    for kind, rx in ID_PATTERNS:
        if rx.fullmatch(s):
            return kind
    return None


def keyword_before(tokens, i):
    """Which ID type does the closest keyword before position i name (skipping filler words)?"""
    j = i - 1
    steps = 0
    while j >= 0 and steps < 3:
        t = tokens[j]
        if t in FILLERS:
            j -= 1
            steps += 1
            continue
        for kind, words in KEYS.items():
            if t in words:
                return kind
        return None
    return None


def kinds_mentioned(tokens):
    return {kind for kind, words in KEYS.items() if any(t in words for t in tokens)}


def match_at(tokens, i):
    """Longest run of tokens starting at i that forms an ID: (kind, id, n_tokens) or None.

    A single token follows the v1 rules (a bare 6-digit number is a PIN only after a PIN
    keyword or when it is the whole message). A run of several tokens counts as a GSTIN
    anywhere, since 15 structured characters rarely line up by accident; any other kind
    needs its own keyword just before it, or the run must be the whole message."""
    hint = keyword_before(tokens, i)
    for n in range(min(MAX_RUN, len(tokens) - i), 0, -1):
        parts = tokens[i:i + n]
        if n > 1 and any(p in KEYWORDS or p in FILLERS for p in parts):
            continue
        s = "".join(parts)
        kind = id_kind(s)
        if not kind:
            continue
        whole = i == 0 and n == len(tokens)
        if kind == "pin" and not (whole or hint == "pin"):
            continue
        if n > 1 and kind != "gstin" and not (whole or hint == kind):
            continue
        return kind, s, n
    return None
