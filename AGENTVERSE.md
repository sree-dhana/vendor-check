# VendorCheck

Protects small businesses from vendor-payment mistakes and "we've changed our bank account" fraud. VendorCheck checks a vendor's tax and bank details, watches your vendors' emails for requests to change them, **holds the payment**, helps you confirm on the phone number you saved, and changes nothing until **you** approve.

**Say `start` to set it up.**

## Try it now
- `Check this vendor: GSTIN 27AAPFU0939F1ZV, PAN AAPFU0939F, IFSC YESB0DNB002`
- `GSTIN 27AAPFU0939F1ZX` (a typo: shows the likely fix)
- `Hi, we've changed our bank account, please pay today to IFSC SBIN0000001`
- `rules UK` (what each country profile checks, with sources)

## The protection workflow
1. `save vendor <name> GSTIN … IFSC … account … phone … email …` to register a vendor, with a phone number you already have.
2. `consent request <name>`: shows a message asking whether they agree to verification calls. Send it yourself, or say `send consent <name>` and I'll email it to their saved address on your behalf and record their reply automatically. I only contact a vendor when you tell me to.
3. `watch <name>`: forward their emails to your private address; I never get access to your inbox. Or paste one with `check email`.
4. If an email asks to change bank details, a **case opens and the payment is held**.
5. Confirm on the **saved** number: you call (I give you a script), or, with consent, an automated call does. **A call result is evidence, not proof.**
6. `approve <case> <last 4 digits>` or `reject <case>`. Only you can approve.

## What it checks
India: GSTIN, PAN, IFSC, PIN and cross-checks. UK/EU: IBAN check digits and EU VAT (VIES). Everywhere: risky wording, lookalike sender domains, Reply-To mismatches and any change from what you saved.

## How AI is used
The AI never decides anything. It only reads IDs from messy text and writes short summaries, which are checked before you see them. Everything else is plain code.

## Legal notice
The legal side (automated or AI-voice calls, recording, vendor consent, data protection, country rules) is **not settled and needs study before real use**. Nothing here is legal advice. Automated calls are **off by default** and only simulated in this version.

## Privacy
- Messages and email text are not stored. Saved vendors keep only the last 4 digits of an account number, never the full number.
- Only what's needed leaves the agent: codes for public lookups (bank, postcode, EU VAT), messy text the parser can't read (to ASI:One's AI), and a salted fingerprint of each decision on the Fetch.ai ledger.
- `forget all` deletes your vendors, cases and settings.

## Source
https://github.com/sree-dhana/vendor-check
