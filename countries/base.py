"""Shapes shared by every country profile. Profiles are plain data and code: no LLM output,
and every factual claim carries a source URL and the date it was last checked."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Fact:
    text: str
    source: str  # URL
    last_checked: str  # YYYY-MM-DD


@dataclass(frozen=True)
class CallRules:
    calls_enabled: bool
    consent_required: tuple = ("vendor_call", "vendor_recording")
    recording_allowed_by_default: bool = False
    notes: tuple = ()  # Facts
    window: tuple = (10, 18)  # local hours, start inclusive, end exclusive
    language: str = "en"


@dataclass(frozen=True)
class Profile:
    code: str  # ISO 3166-1 alpha-2, or a group id such as "EU" / "GENERIC"
    name: str
    confidence: str  # "full", "partial" or "generic"
    last_checked: str
    call_rules: CallRules
    summary: str  # one line for the onboarding summary: what checks apply
    payment_terms: tuple = ()  # Facts
    lookups: tuple = ()  # names of optional official lookups, each with its own fallback
    not_researched: tuple = ()  # topics deliberately left out
    id_checks: object = field(default=None, compare=False)  # callable(vendor, store) -> [result]

    def check_ids(self, vendor, store):
        return self.id_checks(vendor, store) if self.id_checks else []
