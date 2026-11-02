"""`start` questionnaire and `settings`: a small per-user state machine in plain code.
No LLM is involved; answers are parsed with fixed word lists and the country registry."""
import re
import time

import countries

STEPS = ("consent", "country", "role", "vendor_countries", "monitoring", "first_vendor")
EDITABLE = {"country": "country", "role": "role", "vendors": "vendor_countries",
            "vendor countries": "vendor_countries", "monitoring": "monitoring", "email": "monitoring"}

LEGAL_NOTICE = (
    "**Legal notice.** The legal side of this product is not settled and needs careful study "
    "before real use: automated or AI-voice calls, recording and transcribing calls, consent from "
    "vendors, data protection, and country-specific rules. Nothing here is legal advice. Until a "
    "qualified person reviews it, automated verification calls are **off by default**, run only "
    "in demo mode or with your own consent and test numbers, and are not enabled for any country "
    "except India."
)

INTRO = (
    "**Welcome to VendorCheck setup.**\n\n"
    "I check your vendors' tax and bank details, watch for requests to change them, and hold "
    "the payment until you approve. You stay in charge: I never approve a bank change or "
    "release a payment.\n\n"
    + LEGAL_NOTICE + "\n\n"
    "Reply `yes` to continue, or `no` to stop."
)

QUESTIONS = {
    "country": "**1/5 Which country is your business in?** (for example India, UK, Germany, US)",
    "role": ("**2/5 Do you pay vendors, get paid by customers, or both?** "
             "Reply `pay`, `get paid` or `both`."),
    "vendor_countries": ("**3/5 Which countries are your vendors in?** One or more, separated by "
                         "commas (for example `India, UK`), or `same` for your own country."),
    "monitoring": ("**4/5 Do you want vendor email monitoring and verification calls?** "
                   "Reply `yes` or `no`. You can change this later."),
    "first_vendor": ("**5/5 Add your first vendor.** Send `save vendor <name>` with their IDs, for "
                     "example `save vendor Sharma Traders GSTIN 27AAPFU0939F1ZV IFSC YESB0DNB002`, "
                     "or `skip`."),
}

YES = {"yes", "y", "ok", "okay", "sure", "agree", "i agree", "continue", "yes please", "yep", "haan", "ha"}
NO = {"no", "n", "nope", "stop", "nahi"}
SKIP = {"skip", "later", "not now"}
CANCEL = {"cancel", "stop setup", "quit setup", "exit setup"}
PASS_THROUGH = {"help", "sample", "samples", "example", "examples", "selftest", "my vendors"}
ROLES = {
    "pay": ("pay", "pay vendors", "i pay vendors", "buyer", "vendors", "1"),
    "get_paid": ("get paid", "paid", "get paid by customers", "seller", "customers", "2"),
    "both": ("both", "3"),
}
ROLE_TEXT = {"pay": "you pay vendors", "get_paid": "you get paid by customers",
             "both": "you pay vendors and get paid by customers"}
_ROLE_BY_WORD = {w: role for role, words in ROLES.items() for w in words}
LIST_SPLIT = re.compile(r"\s*(?:,|/|&|;|\band\b|\+)\s*", re.I)


def _key(sender):
    return "profile:%s" % sender


def load(store, sender):
    try:
        p = store.get(_key(sender), {})
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


def _save(store, sender, p):
    store.set(_key(sender), p)


def is_active(store, sender):
    step = load(store, sender).get("step")
    return step in STEPS


def has_profile(store, sender):
    return bool(load(store, sender))


def begin(store, sender, now=None):
    p = load(store, sender)
    p.update({"step": "consent", "editing": False,
              "started_at": time.time() if now is None else now})
    _save(store, sender, p)
    return INTRO


def edit(store, sender, field):
    step = EDITABLE.get(field.strip().lower())
    p = load(store, sender)
    if not p.get("completed"):
        return "You haven't finished setup yet. Say `start` to begin."
    if not step:
        return _settings_menu(p)
    p.update({"step": step, "editing": True})
    _save(store, sender, p)
    return QUESTIONS[step]


def settings_reply(store, sender):
    p = load(store, sender)
    if not p.get("completed"):
        return "You haven't set up VendorCheck yet. Say `start` to begin."
    return _settings_menu(p)


def _settings_menu(p):
    return "\n".join([
        summary(p, title="**Your settings**"),
        "",
        "To change one, say `settings country`, `settings role`, `settings vendors` or "
        "`settings monitoring`. Say `start` to redo everything.",
    ])


def _norm(text):
    return " ".join(re.sub(r"[^\w\s/&,+;]", " ", (text or "").lower()).split())


def _looks_like_something_else(text):
    """A message that is clearly not an answer (IDs, a pasted invoice) goes to the normal
    checks instead, and setup waits for the next answer."""
    return any(c.isdigit() for c in text) or len(text) > 80


def handle(text, store, sender, save_vendor):
    """Handle a reply while setup is running. Returns the reply, or None when the message
    is not a setup answer and should be processed normally.
    `save_vendor(text)` runs the normal `save vendor` command and returns (ok, reply)."""
    p = load(store, sender)
    step = p.get("step")
    if step not in STEPS:
        return None
    t = _norm(text)
    if t in PASS_THROUGH:
        return None

    if t in CANCEL:
        p["step"] = "done" if p.get("completed") else None
        _save(store, sender, p)
        return "Setup paused. Say `start` to begin again or `settings` to see your answers."

    if step == "first_vendor":
        if t in SKIP:
            return _finish(store, sender, p)
        if t.startswith("save vendor"):
            ok, reply = save_vendor(text)
            if not ok:
                return reply
            return reply + "\n\n" + _finish(store, sender, p)
        return None if _looks_like_something_else(text) else QUESTIONS[step]

    if step == "consent":
        if t in YES:
            p["accepted_notice_at"] = time.time()
            return _next(store, sender, p, "country")
        if t in NO:
            store.set(_key(sender), {})
            return ("OK, I've stopped setup and kept nothing from it. You can still paste a "
                    "vendor's IDs to check them, or say `start` any time.")
        return None if _looks_like_something_else(text) else "Reply `yes` to continue, or `no` to stop."

    if step == "country":
        code = countries.resolve(t)
        if not code:
            return None if _looks_like_something_else(text) else (
                "I don't recognise that country. Reply with its name or 2-letter code, "
                "for example `India`, `UK`, `Germany` or `US`.")
        p["country"] = code
        return _next(store, sender, p, "role")

    if step == "role":
        role = _ROLE_BY_WORD.get(t)
        if not role:
            return None if _looks_like_something_else(text) else QUESTIONS["role"]
        p["role"] = role
        return _next(store, sender, p, "vendor_countries")

    if step == "vendor_countries":
        if t in ("same", "same as mine", "same country", "mine"):
            codes = [p.get("country")] if p.get("country") else []
        else:
            parts = [x for x in LIST_SPLIT.split(t) if x]
            codes, unknown = [], []
            for part in parts:
                code = countries.resolve(part)
                if code:
                    if code not in codes:
                        codes.append(code)
                else:
                    unknown.append(part)
            if unknown or not codes:
                if _looks_like_something_else(text):
                    return None
                return ("I didn't recognise: %s. Reply with country names or 2-letter codes, "
                        "separated by commas." % ", ".join(unknown or [text.strip()]))
        p["vendor_countries"] = codes[:10]
        return _next(store, sender, p, "monitoring")

    if step == "monitoring":
        if t in YES or t in NO:
            p["monitoring"] = t in YES
            return _next(store, sender, p, "first_vendor")
        return None if _looks_like_something_else(text) else QUESTIONS["monitoring"]
    return None


def _next(store, sender, p, step):
    if p.get("editing"):
        p.update({"step": "done", "editing": False})
        _save(store, sender, p)
        return summary(p, title="**Updated.**")
    p["step"] = step
    _save(store, sender, p)
    return QUESTIONS[step]


def _finish(store, sender, p):
    p.update({"step": "done", "editing": False, "completed": True})
    _save(store, sender, p)
    return summary(p, title="**Setup done.**") + "\n\nSay `settings` to change any answer."


def calls_line(vendor_codes, env=None):
    if not countries.calls_globally_enabled(env):
        return ("**Calls: off.** Automated verification calls are switched off by default until "
                "the legal side is reviewed. If a vendor asks to change bank details, I'll give you "
                "a short script to call them yourself on the number you saved.")
    allowed = [c for c in vendor_codes if countries.get_profile(c).call_rules.calls_enabled]
    blocked = [c for c in vendor_codes if c not in allowed]
    if not allowed:
        return ("**Calls: not enabled** for your vendors' countries. You'll get a short script to "
                "call vendors yourself on the number you saved.")
    line = ("**Calls: possible only for vendors in %s**, and only in demo mode or with the "
            "vendor's consent and your own test numbers."
            % ", ".join(countries.display_name(c) for c in allowed))
    if blocked:
        line += " Not enabled for %s: you'll get a script for a human callback instead." % ", ".join(
            countries.display_name(c) for c in blocked)
    return line


def _profile_text(code):
    prof = countries.get_profile(code)
    if prof.code == "GENERIC":
        return "%s: generic profile, %s" % (countries.display_name(code), prof.summary)
    return "%s: %s profile, %s" % (countries.display_name(code), prof.confidence, prof.summary)


def summary(p, title="**Setup**", env=None):
    country = p.get("country")
    vendor_codes = p.get("vendor_countries") or []
    lines = [title, ""]
    if country:
        lines.append("- **You are in %s.** Rules in use: %s." % (
            countries.display_name(country), _profile_text(country)))
    if p.get("role"):
        lines.append("- **Role:** %s." % ROLE_TEXT[p["role"]])
    if vendor_codes:
        lines.append("- **Your vendors' countries:**")
        lines.extend("  - %s." % _profile_text(c) for c in vendor_codes)
    if "monitoring" in p:
        lines.append("- **Email monitoring:** %s." % (
            "yes. I'll guide you through forwarding a vendor's emails once you add their email address"
            if p["monitoring"] else "no"))
    lines.append("")
    lines.append(calls_line(vendor_codes or ([country] if country else []), env))
    return "\n".join(lines)
