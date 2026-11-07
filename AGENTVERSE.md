# VendorCheck

Stops vendor-payment mistakes and "we've changed our bank account" fraud. I check a vendor's tax and bank details, watch their emails for change requests, **hold the payment**, and change nothing until **you** approve.

**Say `demo` for a 1-minute guided tour** on sample data (no set-up, no real emails or calls), or `start` to set up for real.

## Try it
- `Check this vendor: GSTIN 27AAPFU0939F1ZV, PAN AAPFU0939F, IFSC YESB0DNB002`
- `GSTIN 27AAPFU0939F1ZX` (shows the likely typo fix)
- `Hi, we've changed our bank account, please pay today to IFSC SBIN0000001`

## How it protects you
1. `save vendor <name> … phone … email …` with a phone number you already have.
2. `send consent <name>` asks them (only when you say so) to agree to verification calls.
3. `watch <name>`: forward their emails to me. A bank-detail change opens a **case and holds the payment**.
4. Confirm on the **saved** number. A call result is evidence, not proof.
5. `approve <case> <last 4>` or `reject <case>`. Only you can approve.

Checks: India GSTIN, PAN, IFSC, PIN; UK/EU IBAN and EU VAT; lookalike sender domains and risky wording everywhere. The AI only reads messy text and writes summaries; every decision is plain code.

**Legal:** automated calls, recording and consent need legal review before real use. Calls are off by default and simulated here. Not legal advice.

**Privacy:** message and email text aren't stored; only the last 4 digits of account numbers are kept. `forget all` deletes your data.

Source: https://github.com/sree-dhana/vendor-check
