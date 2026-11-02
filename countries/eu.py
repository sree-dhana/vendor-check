"""EU/EEA profile (partial): IBAN structure and check digits, optional VIES VAT lookup, and a
note about the banks' Verification of Payee check. Calls off."""
from countries.base import CallRules, Fact, Profile
from iban import check_iban
from lookups import vies_result

CHECKED = "2026-11-02"
EU_VAT = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
          "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}  # VIES covers EU states


def _id_checks(vendor, store):
    out = []
    if vendor.get("iban"):
        out.append(check_iban(vendor["iban"]))
    country = vendor.get("country")
    if vendor.get("vat") and country in EU_VAT:
        out.append(vies_result(country, vendor["vat"], store))
    return out


PROFILE = Profile(
    code="EU",
    name="EU/EEA",
    confidence="partial",
    last_checked=CHECKED,
    call_rules=CallRules(calls_enabled=False),
    summary="IBAN structure and check-digit checks, an optional EU VIES VAT lookup, change "
            "detection, your approval and a human callback",
    payment_terms=(
        Fact("Banks in the euro area must offer a free Verification of Payee check (payee name "
             "against IBAN) before a transfer, from 9 October 2025 under the Instant Payments "
             "Regulation (EU) 2024/886. VendorCheck does not perform it; your bank may show the result.",
             "https://www.mccannfitzgerald.com/knowledge/financial-services-regulation/"
             "are-you-vop-to-go-prepare-for-verification-of-payee-by-october-2025", CHECKED),
        Fact("The VIES VAT lookup checks numbers for EU cross-border trade. It can be unavailable "
             "for some countries at times; VendorCheck then says it could not confirm.",
             "https://ec.europa.eu/assets/taxud/vow-information/swagger_publicVAT.yaml", CHECKED),
    ),
    lookups=("EU VIES VAT (ec.europa.eu), optional",),
    not_researched=("late-payment rules", "national tax ID formats", "rules on automated or recorded calls"),
    id_checks=_id_checks,
)
