"""India profile (full): GSTIN, PAN, IFSC and PIN checks from v1/v2.

Automated calls are possible only in demo mode or with the vendor's consent and the
user's own test numbers, and only when the global CALLS_ENABLED switch is on. The legal
side has not been reviewed (see the legal notice), so no legal claims are made here."""
from checks import check_gstin, check_pan
from countries.base import CallRules, Profile
from lookups import ifsc_result, pin_result


def _id_checks(vendor, store):
    out = []
    if vendor.get("gstin"):
        out.append(check_gstin(vendor["gstin"]))
    if vendor.get("pan"):
        out.append(check_pan(vendor["pan"]))
    if vendor.get("ifsc"):
        out.append(ifsc_result(vendor["ifsc"], store))
    if vendor.get("pin"):
        out.append(pin_result(vendor["pin"], store))
    return out


PROFILE = Profile(
    code="IN",
    name="India",
    confidence="full",
    last_checked="2026-11-02",
    call_rules=CallRules(calls_enabled=True, window=(10, 18), language="en", utc_offset_min=330),  # IST
    summary="GSTIN, PAN, IFSC and PIN checks, cross-checks, change detection and your approval",
    lookups=("ifsc.razorpay.com (IFSC)", "api.postalpincode.in (PIN)"),
    not_researched=("payment terms", "rules on automated or recorded calls"),
    id_checks=_id_checks,
)
