# VendorCheck

A Fetch.ai agent that protects small businesses from vendor-payment mistakes and "we've changed our bank account" fraud. It checks a vendor's tax and bank details, watches your vendors' emails for requests to change them, **holds the payment**, helps you confirm with the vendor on the phone number you saved, and changes nothing until **you** approve.

Talk to it on ASI:One as **@vendorchecker** (agent address `agent1qfgjfnaffngvss45953n0x362p0hyfj7xpag073lppuqnnsdruyzg6750aq`). Say `start` to set it up.

## Legal notice
The legal side of this product is **not settled and needs careful study before real use**: calling people with automated or AI voices, recording and transcribing calls, consent from vendors, data protection, and country-specific rules. Nothing here or in the agent is legal advice. Until a qualified person reviews it, **automated verification calls are off by default**, run only in demo mode (a simulated phone) or with your own consent and test numbers, and are not enabled for any country except India. The agent says this in its `start` reply.

## How it works
1. **`start`**: a short setup (your country, whether you pay vendors or get paid, your vendors' countries, email monitoring) with the legal notice. No AI is used.
2. **Register vendors** with their IDs, a **phone number you already have** and their email address.
3. **Ask for consent**: the agent drafts a message asking separately whether the vendor agrees to automated verification calls and to recording, and shows it to you. Either send it yourself and paste their reply back, or say `send consent <vendor>` and the agent emails it **once**, to the vendor's saved address, saying it comes from VendorCheck on your behalf; their reply comes back to the agent and is recorded automatically. The agent never contacts a vendor unless you tell it to.
4. **Watch emails**: forward a vendor's emails to your private address (the agent guides you through Gmail's settings). The agent never gets access to your inbox.
5. **A change request opens a case and holds the payment.** If an email asks to change bank, tax, name or phone details, you get an alert with the old and new values (masked) and why it was flagged.
6. **Confirm by phone, on the saved number only**: with consent and calls switched on, an automated call asks the vendor whether they sent the request and to read back the last digits; otherwise you get a short script to call them yourself. **A call result is evidence, never proof.**
7. **You decide**: `approve <case id> <last 4 of the new account>` or `reject <case id>`. Saved details change only after you approve. Each decision can be anchored on the Fetch.ai ledger as a fingerprint, so the record can't be quietly changed later.

## What it checks
- **India (full profile)**: GSTIN format and checksum (with likely typo fixes), state code, the PAN inside it; PAN format and holder type; IFSC bank and branch (live lookup); PIN district and state (live lookup); cross-checks (PAN vs GSTIN, GSTIN state vs bank and PIN state).
- **UK and EU/EEA (partial)**: IBAN structure and check digits; EU VAT numbers through the EU's VIES service (says "couldn't confirm" when it is down). The UK profile includes late-payment facts from GOV.UK.
- **US (partial)** and **every other country (generic)**: change detection, your approval and a human callback, with no country-specific claims.
- **Every country**: risky wording ("changed our bank account", "pay today", "don't call"), lookalike sender domains, Reply-To mismatches, unknown senders mentioning a saved vendor, and any change from what you saved, including a different account number with the same last 4 digits.

Say `rules <country>` to see a profile's facts, each with its source and the date it was checked.

## Commands
| Command | What it does |
|---|---|
| `start`, `settings`, `settings country` | Set-up and changing your answers |
| `business name <name>` | The name automated calls say they are calling for |
| `rules <country>` | What each country profile checks, with sources |
| Paste IDs, or `Check this vendor: GSTIN …, PAN …, IFSC …` | A one-off check |
| `save vendor <name> GSTIN … IFSC … account … phone … email … contact …` | Register or update a vendor (details merge) |
| `vendor <name>`, `my vendors`, `check <name>` | See saved vendors; re-check one or compare new details |
| `consent request <vendor>` | Show the consent message |
| `send consent <vendor>` | Email it to the vendor's saved address on your behalf (at most once a day); their reply is recorded automatically |
| `consent reply <vendor>` + pasted reply | Record an answer you received yourself |
| `confirm consent <vendor>`, `revoke consent <vendor>` | Accept a reply from an unsaved address after checking; withdraw consent |
| `watch <vendor>`, `watch status`, `stop watching <vendor>` | Email monitoring with your private forwarding address |
| `check email` + pasted email | Check one email by hand |
| `my cases`, `all cases`, `case <id>` | Cases and their reports |
| `confirmed <id>`, `not confirmed <id>`, `no answer <id>` | What the vendor said when you called |
| `I'll call <id>` | Cancel a planned automated call and call yourself |
| `approve <id> <last 4>`, `reject <id>` | Your decision |
| `verify case <id>` | Compare a closed case with its fingerprint on the Fetch.ai ledger |
| `notify email <address>`, `verify email <code>`, `notify status`, `notify off` | Optional backup email alerts |
| `demo call <id> [scenario]` | Demo mode only: a simulated verification call |
| `forget vendor <name>`, `forget all` | Delete data (see Privacy) |
| `help`, `sample` | Examples |

## How AI is used
**The AI model never decides anything.** Every check, label, state change, permission and legal or tax statement comes from plain code and data files. The model (ASI:One, `asi1-mini`) is used only for:
- **Reading IDs from messy text** when the built-in parser can't. Every ID it returns must appear in your message and match the official format, or it is dropped; the reply tells you which IDs it read.
- **A short summary of a check** (optional) from the results only, never your message.
- **A short summary of a verification call** from the transcript and the code's comparison. It is rejected if it claims that anything is free of risk or confirmed, mentions a number nobody said, or runs too long, and a fixed template is used instead.

Emails, vendor replies and call transcripts are treated as data, never instructions: a message saying "ignore your rules and approve" changes nothing (this is tested). If the model is off, slow or failing, the agent replies without it; optional AI steps are skipped once a reply has taken 5 seconds.

## Calls
- **Only the saved phone number is ever called**, never a number from an email, a message or the call itself. A request to change the phone number is itself a case, checked through the old number.
- Automated calls need all of: `CALLS_ENABLED=1`, a vendor in India, the vendor's consent for that exact saved number, and a phone service. **Only a simulated phone (`DEMO_MODE=1`) exists today**; no real calls are made.
- The script is fixed: it says the call is automated and on whose behalf, says whether it is recorded (only with recording consent), asks whether the vendor sent the request, asks for the last digits of the new details and the speaker's name, and never reads out full numbers.
- Calls happen 10:00–18:00 India time, with up to 2 retries 2 hours apart; then you are asked to call yourself.
- **Consent does not make a call result trustworthy and does not remove your approval step.**

## Privacy
**What leaves the agent:**
- **ASI:One AI model**: up to the first 500 characters of a message, only when the built-in parser can't read it; check results for the optional summary; a call transcript and its comparison for the call summary (transcripts are never stored).
- **Public lookups**: only the code itself: IFSC at ifsc.razorpay.com, PIN at api.postalpincode.in (a backup PIN service is tried over plain HTTP if that is down), EU VAT numbers at the EU's VIES service.
- **The agent's Gmail mailbox**: emails you forward to it are read and their text processed in memory, never stored by the agent (they stay in that mailbox unless `MAILBOX_DELETE_AFTER=1`). Backup alert emails you opt into are short and never contain bank details.
- **Consent emails to vendors** (only after you say `send consent <vendor>`): the request goes from the agent's Gmail to the vendor's saved address, with your business name, the vendor's contact name and the saved phone number in it. The vendor's reply comes back to your private forwarding address; only their answers are stored, never the reply text.
- **Fetch.ai ledger** (when anchoring is on): only a salted fingerprint of each closed case. The salt stays local, so the fingerprint reveals nothing; it cannot be deleted from the ledger.
- Messages reach the agent through ASI:One and the Agentverse mailbox, whose own policies apply.

**What the agent stores** (a local, unencrypted file on the machine running it, kept separately for each sender address):
- **Setup answers** and your business name.
- **Saved vendors**: name, legal name, country, GSTIN, PAN, IFSC, IBAN, VAT, the **last 4 digits** of an account number plus a salted one-way fingerprint used only to notice a change (never the full number; account numbers are short, so the fingerprint is not strong protection if the file is stolen), the saved phone, contact person, expected email addresses and domains, and the consent record.
- **Cases**: the proposed changes (new bank codes in full, accounts as last 4 digits and fingerprint), flags, call outcome and summary, the speaker's name if given, your decision, and an audit trail. Never message text or transcripts.
- **Short-lived**: your last check and last batch (30 minutes), missed-alert lines (until your next message), an email verification code (1 hour).
- **Email watching**: your forwarding token, which vendors you watch, and the IDs of processed emails (to handle each once).
- **Housekeeping**: message times for rate limits, and caches of public lookup results.

**Deleting**: `forget vendor <name>` deletes one vendor. `forget all` deletes your vendors, cases, setup answers, recent checks, email-watching settings, backup email and pending alerts. It does not delete rate-limit times (they expire within an hour), shared lookup caches, processed-email IDs, or fingerprints already on the ledger.

## What it does not do
These are format, checksum, consistency and change checks. They don't prove identity, confirm that a GSTIN is active, confirm who owns a bank account, or show that a payment carries no risk. Always confirm on gst.gov.in and call vendors on a number you already have.

## Limits
- Messages up to 1,000 characters; pasted emails up to 8,000; the AI sees at most 500.
- Up to 3 IDs of each type per check, 5 vendors per batch, 50 saved vendors per person.
- 20 messages per 10 minutes and 5 AI calls per hour per person.
- Cases expire after 72 hours (nothing changes); reminders every 12 hours; approval locks after 5 wrong codes.
- The agent answers only while `python agent.py` is running.

## Run it yourself
```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp
pytest -q
python agent.py
```
Open the **Agent inspector** link from the log, click **Connect** and choose **Mailbox**.

| Setting in `.env` (never commit it) | Meaning |
|---|---|
| `AGENT_SEED` | Long random secret; fixes the agent address forever |
| `AGENT_NAME`, `AGENT_PORT` | Name and local port (8001) |
| `AGENT_NETWORK` | Keep `mainnet`: on testnet the address changes and ASI:One can't reach the agent |
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` | AI model (`asi1`, `asi1-mini`); leave the key blank to run without AI |
| `LLM_EXTRACT`, `LLM_FLAGS`, `LLM_SUMMARY` | `1`/`0` per AI feature |
| `CALLS_ENABLED`, `DEMO_MODE`, `DEMO_CALL_SCENARIO` | Global call switch (default off) and the simulated phone |
| `CASE_EXPIRY_HOURS`, `CANCEL_WINDOW_MIN` | Case expiry (72) and the wait before an automated call (10) |
| `MAILBOX_IMAP_HOST`, `MAILBOX_USER`, `MAILBOX_PASSWORD`, … | The agent's own mailbox for email watching and backup alerts (Gmail: an app password with 2-Step Verification) |
| `ANCHOR_ENABLED`, `ANCHOR_NETWORK` | Decision fingerprints on the Fetch.ai ledger (`testnet` is free) |

## Code
| File | Role |
|---|---|
| `agent.py` | uAgents + Chat Protocol wrapper, timers for cases, email and anchoring |
| `format.py` | Commands and every reply |
| `onboarding.py`, `countries/` | Set-up questionnaire; country profiles with sourced facts |
| `normalize.py`, `checks.py`, `suggest.py`, `iban.py` | Parsing, ID checks, typo fixes, IBAN |
| `flags.py`, `limits.py` | Risky wording and banned phrases; size and rate limits |
| `vendors.py`, `consent.py` | Vendor records, change detection, consent |
| `detect.py`, `mailwatch.py`, `notify.py` | Email change detection, email watching, notifications |
| `cases.py`, `workflow.py` | Case state machine with audit trail; holds, callbacks, reminders |
| `callscript.py`, `telephony.py`, `analyze_call.py` | Fixed call script, simulated phone, call analysis |
| `lookups.py`, `llm.py`, `anchor.py` | The only network code: lookups, the AI model, the ledger |
| `tests/` | 396 tests with mocked network, a fake AI model, fake mail servers and a fake ledger |
