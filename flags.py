"""Deterministic red-flag detector for the wording of a message, plus the banned-phrase
list every reply is held to. Pure, no network. Flags never change pass/fail counts."""
import re

_I = re.IGNORECASE

# (label, patterns, fixed message). Messages are fixed text, never model output.
FLAG_RULES = (
    ("bank_change", (
        r"\b(?:chang\w*|new|updat\w*|different|revised|modif\w*)\s+(?:\w+\s+){0,3}?(?:bank|account|a/c|acct|ifsc)\b",
        r"\b(?:bank|account|a/c|ifsc)\s+(?:details?\s+|number\s+|info\w*\s+)?(?:has|have|is|are|was|were)\s+(?:been\s+)?(?:chang|updat|revis|modif)\w*",
    ), "The message says the bank details have changed. This is a common pattern in payment fraud."),
    ("urgency", (
        r"\b(?:urgent\w*|immediately|asap|right away|today itself|by today|by eod|end of day)\b",
        r"\bpay\s+(?:\w+\s+){0,2}?today\b",
        r"\bwithin\s+(?:the\s+)?(?:hour|\d+\s*(?:hours?|hrs?|mins?|minutes?))\b",
    ), "The message pushes you to pay quickly. Pressure to skip checks is a warning sign."),
    ("no_callback", (
        r"\b(?:do not|don't|dont|no need to|cannot|can't|won't be able to)\s+(?:\w+\s+){0,2}?(?:call|phone|ring)\b",
        r"\b(?:not reachable|unreachable)\s+(?:on|by)\s+(?:phone|call)",
        r"\bonly\s+(?:on|via|by|through)\s+(?:e-?mail|whatsapp|chat|message)",
    ), "The message discourages a phone call. A call to a number you already have is the best check."),
    ("send_to_account", (
        r"\b(?:send|transfer|pay|remit|deposit|credit)\b(?:\s+\w+){0,4}?\s+(?:to|in|into)\s+(?:this|the following|below|above|my|our new|the new|new)\s+(?:bank\s+)?(?:account|a/c|acct)\b",
    ), "The message asks you to pay into a specific account. Check it matches the account you already have on file."),
    ("personal_account", (
        r"\b(?:personal|individual|private|own|proprietor'?s?|director'?s?)\s+(?:bank\s+)?(?:account|a/c|acct)\b",
    ), "The payment would go to a personal account rather than the business. Confirm why."),
    ("different_payee", (
        r"\b(?:different|another|other|alternate|new|changed)\s+(?:payee|beneficiary|account holder|account name|company name)\b",
        r"\b(?:payee|beneficiary|account holder)(?:'s)?\s+name\s+(?:is|will be|may be)\s+different\b",
    ), "The payee name may not match the vendor. Confirm the account holder's name with the vendor."),
)

FLAG_TEXT = {label: text for label, _, text in FLAG_RULES}
_COMPILED = tuple((label, tuple(re.compile(p, _I) for p in pats)) for label, pats, _ in FLAG_RULES)

CALLBACK_ADVICE = (
    "Before paying, call the vendor on a number you already have and confirm the bank details."
)

BANNED_RE = re.compile(r"\b(?:safe|verified vendor|legit|trusted|guaranteed?)\b", _I)


def detect_flags(text):
    """Return flag labels found in the text, in FLAG_RULES order."""
    t = " ".join((text or "").split())
    return [label for label, pats in _COMPILED if any(p.search(t) for p in pats)]


def heads_up_lines(labels):
    if not labels:
        return []
    lines = ["**Heads-up**"]
    lines.extend("⚠️ %s" % FLAG_TEXT[label] for label in labels)
    lines.append(CALLBACK_ADVICE)
    return lines


def contains_banned(text):
    return BANNED_RE.search(text or "") is not None
