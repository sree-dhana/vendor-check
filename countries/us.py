"""US profile (partial), including California notes. No ID format checks yet: the routing
number checksum and EIN format are added only after they are confirmed from an
authoritative source. Calls off."""
from countries.base import CallRules, Fact, Profile

CHECKED = "2026-11-02"

PROFILE = Profile(
    code="US",
    name="US",
    confidence="partial",
    last_checked=CHECKED,
    call_rules=CallRules(
        calls_enabled=False,
        notes=(
            Fact("The FCC treats AI-generated voices as 'artificial' voices under the TCPA (Declaratory "
                 "Ruling, February 2024), so such calls carry the same consent and identification "
                 "requirements as other artificial or prerecorded calls.",
                 "https://orrick.com/Insights/2024/02/Did-the-FCC-Really-Ban-AI-Generated-Voice-Calls-No-But-Restrictions-Apply",
                 CHECKED),
            Fact("California requires the consent of all parties to record a confidential communication "
                 "(Penal Code 632), and to record calls involving cellular or cordless phones "
                 "(Penal Code 632.7). Summary from a secondary source.",
                 "https://www.rcfp.org/reporters-recording-guide/california/", CHECKED),
            Fact("How these rules apply to business-to-business verification calls has not been "
                 "worked out here, so automated calls stay off.",
                 "https://orrick.com/Insights/2024/02/Did-the-FCC-Really-Ban-AI-Generated-Voice-Calls-No-But-Restrictions-Apply",
                 CHECKED),
        ),
    ),
    summary="change detection, your approval and a human callback (US tax and bank ID formats "
            "not checked yet)",
    not_researched=("routing number checksum", "EIN format", "late-payment rules",
                    "recording rules in states other than California"),
)
