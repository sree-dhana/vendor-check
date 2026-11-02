"""Generic profile: used for any country without its own profile. Format checks, change
detection, user approval and human callback only. No country-specific claims."""
from countries.base import CallRules, Profile

PROFILE = Profile(
    code="GENERIC",
    name="Generic",
    confidence="generic",
    last_checked="2026-11-02",
    call_rules=CallRules(calls_enabled=False),
    summary="general checks only: change detection, your approval and a human callback "
            "(no country-specific tax, bank or legal rules)",
    not_researched=("tax ID formats", "bank ID formats", "payment terms", "call and recording rules"),
)
