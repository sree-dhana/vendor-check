"""Country profile registry. `resolve` turns a typed country into a code; `get_profile`
returns its profile, falling back to the generic profile for anything unsupported."""
import os
import re

from countries import eu, generic, india, uk, us

PROFILES = {p.code: p for p in (india.PROFILE, uk.PROFILE, eu.PROFILE, us.PROFILE, generic.PROFILE)}

# Names people type -> ISO 3166-1 alpha-2. Countries without a profile still resolve,
# so the summary can say "uses the generic profile" instead of "unknown".
COUNTRY_NAMES = {
    "IN": ("india", "bharat", "ind"),
    "GB": ("uk", "united kingdom", "great britain", "britain", "england", "scotland", "wales",
           "northern ireland", "gbr"),
    "US": ("us", "usa", "united states", "united states of america", "america", "california"),
    "AT": ("austria",), "BE": ("belgium",), "BG": ("bulgaria",), "HR": ("croatia",),
    "CY": ("cyprus",), "CZ": ("czechia", "czech republic"), "DK": ("denmark",),
    "EE": ("estonia",), "FI": ("finland",), "FR": ("france",), "DE": ("germany", "deutschland"),
    "GR": ("greece",), "HU": ("hungary",), "IE": ("ireland",), "IT": ("italy",),
    "LV": ("latvia",), "LT": ("lithuania",), "LU": ("luxembourg",), "MT": ("malta",),
    "NL": ("netherlands", "holland"), "PL": ("poland",), "PT": ("portugal",),
    "RO": ("romania",), "SK": ("slovakia",), "SI": ("slovenia",), "ES": ("spain",),
    "SE": ("sweden",), "IS": ("iceland",), "LI": ("liechtenstein",), "NO": ("norway",),
    "CH": ("switzerland",), "CA": ("canada",), "AU": ("australia",), "NZ": ("new zealand",),
    "SG": ("singapore",), "AE": ("uae", "united arab emirates", "dubai"), "SA": ("saudi arabia",),
    "JP": ("japan",), "CN": ("china",), "HK": ("hong kong",), "MY": ("malaysia",),
    "ID": ("indonesia",), "TH": ("thailand",), "VN": ("vietnam",), "PH": ("philippines",),
    "BD": ("bangladesh",), "LK": ("sri lanka",), "NP": ("nepal",), "PK": ("pakistan",),
    "ZA": ("south africa",), "NG": ("nigeria",), "KE": ("kenya",), "BR": ("brazil",),
    "MX": ("mexico",),
}
DISPLAY = {
    "IN": "India", "GB": "UK", "US": "US", "AE": "UAE",
}
EU_EEA = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
          "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
          "IS", "LI", "NO"}

_BY_NAME = {name: code for code, names in COUNTRY_NAMES.items() for name in names}


def resolve(text):
    """Return the ISO code for a typed country name or code, or None."""
    t = " ".join(re.sub(r"[^a-z ]", " ", (text or "").lower()).split())
    if not t:
        return None
    if t in _BY_NAME:
        return _BY_NAME[t]
    if len(t) == 2 and t.upper() in COUNTRY_NAMES:
        return t.upper()
    return None


def display_name(code):
    if code in DISPLAY:
        return DISPLAY[code]
    names = COUNTRY_NAMES.get(code)
    return names[0].title() if names else code


def profile_code(code):
    if code in PROFILES:
        return code
    if code in EU_EEA:
        return "EU"
    return "GENERIC"


def get_profile(code):
    return PROFILES[profile_code(code)]


def describe(code):
    """Everything a profile claims, with sources, for the `rules <country>` command."""
    p = get_profile(code)
    name = display_name(code)
    head = "**Rules for %s**" % name
    if p.code not in (code, "GENERIC"):
        head += " (uses the %s profile)" % p.name
    lines = [head, "",
             "- **Profile:** %s (%s), last checked %s." % (p.name, p.confidence, p.last_checked),
             "- **What I check:** %s." % p.summary]
    if p.lookups:
        lines.append("- **Lookups:** %s." % ", ".join(p.lookups))
    lines.append("- **Automated calls:** %s." % (
        "can be enabled (demo or with consent)" if p.call_rules.calls_enabled else "not enabled"))
    facts = list(p.payment_terms) + list(p.call_rules.notes)
    if facts:
        lines.extend(["", "**Facts and sources**", ""])
        lines.extend("- %s Source: %s (checked %s)" % (f.text, f.source, f.last_checked) for f in facts)
    if p.not_researched:
        lines.extend(["", "**Not researched yet:** %s." % ", ".join(p.not_researched)])
    lines.extend(["", "_This is general information, not legal or tax advice._"])
    return "\n".join(lines)


def calls_globally_enabled(env=None):
    """The global kill switch. Off unless CALLS_ENABLED=1."""
    env = os.environ if env is None else env
    return str(env.get("CALLS_ENABLED", "0")).strip() == "1"


def demo_mode(env=None):
    env = os.environ if env is None else env
    return str(env.get("DEMO_MODE", "0")).strip() == "1"
