"""UK profile (partial): IBAN structure check, late-payment facts from GOV.UK. Calls off."""
from countries.base import CallRules, Fact, Profile
from iban import check_iban

GOV_LATE = "https://www.gov.uk/late-commercial-payments-interest-debt-recovery"
CHECKED = "2026-11-02"


def _id_checks(vendor, store):
    return [check_iban(vendor["iban"])] if vendor.get("iban") else []


PROFILE = Profile(
    code="GB",
    name="UK",
    confidence="partial",
    last_checked=CHECKED,
    call_rules=CallRules(calls_enabled=False),
    summary="IBAN structure checks, change detection, your approval, a human callback and "
            "late-payment facts from GOV.UK",
    payment_terms=(
        Fact("Statutory interest on late business-to-business payments is 8% plus the Bank of "
             "England base rate, unless your contract sets a different rate.",
             GOV_LATE + "/charging-interest-commercial-debt", CHECKED),
        Fact("Fixed debt recovery costs: £40 for debts up to £999.99, £70 for £1,000 to £9,999.99, "
             "and £100 for £10,000 or more, charged once per payment.",
             GOV_LATE + "/claim-debt-recovery-costs", CHECKED),
        Fact("If no payment date was agreed, payment is late 30 days after the customer gets the "
             "invoice or you deliver, whichever is later. Agreed business terms are usually up to "
             "60 days.", GOV_LATE, CHECKED),
    ),
    not_researched=("Confirmation of Payee", "VAT number checks", "sort code and account number checks",
                    "when statutory interest starts", "rules on automated or recorded calls"),
    id_checks=_id_checks,
)
