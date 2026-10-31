"""GSTIN typo suggestions for a well-formed GSTIN whose checksum fails. Pure, no network.

Only edits people actually make are tried: a wrong check character, a look-alike
character (0/O, 1/I, 5/S, 8/B, ...) or two neighbouring characters swapped. Trying
every possible substitution finds 7-8 valid GSTINs one edit away from almost any
typo, which is too many to be useful."""
from checks import GSTIN_RE, PAN_HOLDER_TYPES, STATE_CODES, gstin_check_char

MAX_SUGGESTIONS = 3

LOOKALIKE_GROUPS = ("0OQD", "1IL", "2Z", "4A", "5S", "6G", "7T", "8B", "CG", "MN", "UV")
LOOKALIKES = {}
for _group in LOOKALIKE_GROUPS:
    for _c in _group:
        LOOKALIKES.setdefault(_c, set()).update(set(_group) - {_c})


def _plausible(g):
    return (
        GSTIN_RE.fullmatch(g) is not None
        and gstin_check_char(g[:14]) == g[14]
        and g[:2] in STATE_CODES
        and g[5] in PAN_HOLDER_TYPES
    )


def gstin_typo_fixes(g, pans=()):
    """Return (candidates, too_many).

    candidates are ranked: check-character fix, look-alike substitutions, then swapped
    neighbours. If a PAN from the same message matches some candidates, only those are
    kept. More than MAX_SUGGESTIONS candidates means it is a guess, so none are returned."""
    out = []

    def add(x):
        if x != g and x not in out and _plausible(x):
            out.append(x)

    add(g[:14] + gstin_check_char(g[:14]))
    for i in range(14):
        for c in sorted(LOOKALIKES.get(g[i], ())):
            add(g[:i] + c + g[i + 1:])
    for i in range(14):
        add(g[:i] + g[i + 1] + g[i] + g[i + 2:])

    matching = [x for x in out if x[2:12] in pans]
    if matching:
        out = matching
    if len(out) > MAX_SUGGESTIONS:
        return [], True
    return out, False
