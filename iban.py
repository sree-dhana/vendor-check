"""IBAN structure and check-digit validation (ISO 13616, mod 97). Pure, no network.

Lengths per country are from the IBAN formats table at
https://en.wikipedia.org/wiki/International_Bank_Account_Number (checked 2026-11-02).
A passing IBAN only has a valid structure: it does not show the account exists or who owns it."""
import re

IBAN_LENGTHS = {
    "AT": 20, "BE": 16, "BG": 22, "HR": 21, "CY": 28, "CZ": 24, "DK": 18, "EE": 20,
    "FI": 18, "FR": 27, "DE": 22, "GR": 27, "HU": 28, "IE": 22, "IT": 27, "LV": 21,
    "LT": 20, "LU": 20, "MT": 31, "NL": 18, "PL": 28, "PT": 25, "RO": 24, "SK": 24,
    "SI": 19, "ES": 24, "SE": 24, "IS": 26, "LI": 21, "NO": 15, "GB": 22, "CH": 21,
}
IBAN_RE = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}")


def normalize(raw):
    return re.sub(r"[\s\-.]", "", (raw or "")).upper()


def mod97(iban):
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)  # A=10 ... Z=35
    return int(digits) % 97


def check_iban(raw):
    iban = normalize(raw)
    r = {"kind": "iban", "id": iban, "country": iban[:2]}
    if not IBAN_RE.fullmatch(iban):
        r.update(level="fail", text="wrong format. Expected 2 letters, 2 check digits, then 11 to 30 letters or digits.")
        return r
    expected = IBAN_LENGTHS.get(iban[:2])
    if expected and len(iban) != expected:
        r.update(level="fail", text="wrong length for %s: expected %d characters, got %d." % (
            iban[:2], expected, len(iban)))
        return r
    if mod97(iban) != 1:
        r.update(level="fail", text="check digits don't match, so at least one character is probably wrong.")
        return r
    if expected is None:
        r.update(level="warn", text="check digits are valid, but %s is not in my IBAN length table." % iban[:2])
        return r
    r.update(level="pass", text="valid structure and check digits for %s. This doesn't show that the "
                                "account exists or who owns it." % iban[:2])
    return r
