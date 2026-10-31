# VendorCheck

Check an Indian vendor's **GSTIN, PAN, IFSC and PIN code** before you pay them. VendorCheck cross-checks the IDs against each other, suggests fixes for likely typos, flags risky "our bank has changed" wording, and warns you when a saved vendor's bank details change.

## Try it
- `Check this vendor: GSTIN 27AAPFU0939F1ZV, PAN AAPFU0939F, IFSC YESB0DNB002`
- `GSTIN 27AAPFU0939F1ZX` (typo: shows the likely fix)
- `Hi, we've changed our bank account, please pay today to IFSC SBIN0000001`
- `check 27 aapfu 0939 f1zv pan aapfu0939f` (loosely typed IDs work)
- `pincode 560001`
- Several vendors, one per line, for a batch table
- `help` or `sample`

## Remember a vendor, catch bank changes
1. Check a vendor, then say `save vendor Sharma Traders`.
2. Next time, say `check Sharma Traders` with the IDs from the new invoice.
3. If the IFSC, GSTIN or account number differs, you get a ❌ **Bank details changed** warning.

Also: `my vendors`, `forget <name>`, `forget all`, `details <n>` (after a batch).

## What it checks
- **GSTIN**: format, checksum, state code, and the PAN inside it
- **PAN**: format and holder type
- **IFSC**: bank, branch, state, NEFT/RTGS/IMPS/UPI (live lookup)
- **PIN**: district and state (live lookup)
- **Cross-checks**: PAN vs GSTIN; GSTIN state vs PIN and bank branch state
- **Heads-up** for risky wording: changed bank details, pressure to pay today, "don't call", personal accounts, a different payee name

## How AI is used
Every result comes from fixed rules and public lookups, never from the AI model. This agent uses ASI:One only to read IDs out of messy messages that its built-in parser can't read. Any ID the model returns must appear in your message, and the reply tells you which IDs it read.

## What it does not do
These are format, checksum and consistency checks. They don't prove identity, confirm that a GSTIN is active, or show that a payment carries no risk. Always check the GSTIN on gst.gov.in and call the vendor on a number you already have.

## Privacy
- Messages are not logged or stored.
- When the built-in parser can't read a message, up to its first 500 characters are sent to ASI:One to read the IDs.
- IFSC and PIN codes (only the codes) are looked up at ifsc.razorpay.com and postalpincode.in.
- Saved vendors are stored only when you ask, hold a name, GSTIN, PAN, IFSC and the last 4 digits of an account number (never the full number), and are deleted with `forget all`.
- Your most recent check and batch are remembered for 30 minutes so `save vendor` and `details` work.

## Source
https://github.com/sree-dhana/vendor-check
