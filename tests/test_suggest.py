"""F2: GSTIN typo suggestions."""
import pytest

import format as fmt
from checks import gstin_check_char
from conftest import MemStore
from suggest import MAX_SUGGESTIONS, gstin_typo_fixes

REAL = "27AAPFU0939F1ZV"


def _swap(g, i):
    return g[:i] + g[i + 1] + g[i] + g[i + 2:]


@pytest.mark.parametrize("typo", [
    "27AAPFU0939F1ZX",       # wrong check character
    "27AAPFU0939FIZV",       # look-alike: 1 typed as I
    _swap(REAL, 4),          # PF -> FP
    _swap(REAL, 8),          # 93 -> 39
])
def test_one_typo_returns_the_original(typo):
    assert gstin_check_char(typo[:14]) != typo[14]
    fixes, too_many = gstin_typo_fixes(typo)
    assert not too_many
    assert REAL in fixes
    assert len(fixes) <= MAX_SUGGESTIONS


def test_check_character_fix_is_ranked_first_and_alone():
    assert gstin_typo_fixes("27AAPFU0939F1ZX") == ([REAL], False)


def test_pan_from_message_narrows_candidates():
    typo = _swap(REAL, 3)  # AP -> PA: ambiguous on its own
    assert gstin_typo_fixes(typo) == ([], True)
    assert gstin_typo_fixes(typo, ["AAPFU0939F"]) == ([REAL], False)


def test_garbage_returns_none():
    assert gstin_typo_fixes("99ZZZZZ9999Z9ZZ") == ([], False)


def test_too_many_candidates_shows_none(monkeypatch):
    import suggest
    monkeypatch.setattr(suggest, "MAX_SUGGESTIONS", 0)
    assert suggest.gstin_typo_fixes("27AAPFU0939F1ZX") == ([], True)


def test_reply_shows_suggestion_under_the_failed_line(no_net):
    out = fmt.reply_for_text("GSTIN 27AAPFU0939F1ZX", MemStore())
    lines = out.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith("❌ GSTIN"))
    assert "Possible typo fixes, confirm with the vendor" in lines[i + 1]
    assert REAL in lines[i + 1]
    assert "the correct GSTIN" not in out
