# VendorCheck

A chat agent that checks an Indian vendor's **GSTIN, PAN, IFSC and PIN code** before you pay them, cross-checks the IDs against each other, and warns you when a vendor's bank details change.

Talk to it on ASI:One as **@vendor-check** (agent address `agent1qfgjfnaffngvss45953n0x362p0hyfj7xpag073lppuqnnsdruyzg6750aq`).

## Why
"We've changed our bank account" messages and vendor impersonation are a leading form of payment fraud, and honest typos in GST numbers and bank codes are even more common. VendorCheck catches both kinds of mistake with fast, deterministic checks, and is clear about what it did not check.

## What it checks
- **GSTIN**: format, checksum, state code (flags retired codes 25 and 28), and the PAN inside it
- **PAN**: format and holder type (individual, company, firm/LLP, trust, ...)
- **IFSC**: bank, branch, city/state and NEFT/RTGS/IMPS/UPI support (live lookup)
- **PIN**: district and state (live lookup)
- **Cross-checks**: PAN vs the PAN inside the GSTIN; GSTIN state vs PIN state and bank branch state
- **Typo fixes**: when a GSTIN checksum fails, up to 3 likely fixes (wrong check character, look-alike characters such as 0/O or 1/I, or two swapped neighbours), labelled "confirm with the vendor"
- **Risky wording**: a Heads-up block for messages that say the bank details changed, push you to pay today, discourage a phone call, ask you to pay into a specific or personal account, or use a different payee name
- **Bank-change detection**: save a vendor once; if a later check shows a different IFSC, GSTIN or account number, you get a ❌ "Bank details changed since you saved this vendor" warning
- **Messy input**: IDs written with spaces, dashes, dots or lowercase (`27 aapfu 0939 f1zv`, `sbin-0000001`), and, when the built-in parser can't read a message, an AI model reads the IDs out of it
- **Batch**: up to 5 vendors in one message, answered with a compact table

## Try it
| Send | What you get |
|---|---|
| `Check this vendor: GSTIN 27AAPFU0939F1ZV, PAN AAPFU0939F, IFSC YESB0DNB002` | Full check, all passing |
| `check 27 aapfu 0939 f1zv pan aapfu0939f ifsc yesb0dnb002` | Same, from loosely typed IDs |
| `GSTIN 27AAPFU0939F1ZX` | Checksum fail plus the likely typo fix |
| `GSTIN 27AAPFU0939F1ZV PAN ABCDE1234F` | PAN mismatch |
| `Hi, we've changed our bank account, please pay today to IFSC SBIN0000001` | Heads-up block |
| `Sharma Traders here, kindly pay. Bank branch code SBIN 0000001. Thanks` | IDs read by the AI model, shown to you |
| `pincode 560001` | Bangalore, Karnataka |
| Three lines, each `<name> GSTIN ... IFSC ...` | Batch table |

### Commands
| Command | What it does |
|---|---|
| `save vendor <name>` | Saves the IDs from the same message, or from your last check (within 30 minutes) |
| `check <name>` | Re-checks a saved vendor; add new IDs to compare them with what you saved |
| `my vendors` | Lists your saved vendors |
| `forget <name>` / `forget all` | Deletes one saved vendor / everything stored for you |
| `details <n>` | Full check of vendor *n* from your last batch |
| `help`, `sample` | Examples |

## How AI is used
The AI model never decides a result. Every pass, warning and problem comes from fixed rules and public lookups.
- **Reading IDs from messy text** (on): used only when the built-in parser finds no ID in a message that contains digits, or when a long message names an ID type the parser couldn't read. Every ID it returns must appear in your message and match the official format, or it is dropped. The reply tells you which IDs it read.
- **Plain-language summary** (off by default): rewrites the computed results in up to two sentences. It sees only the results, never your message, and is discarded if it makes any claim beyond them.
- **Wording labels** (off by default): when the built-in wording rules find nothing in a payment-related message, the model may pick from the same fixed list of warnings.
- If the model is off, slow (over 4 seconds), errors or is rate-limited, VendorCheck behaves exactly as it does without it.

## What it does not do
These are format, checksum and consistency checks. They don't prove identity, confirm that a GSTIN is active, or show that a payment carries no risk. Always check the GSTIN on gst.gov.in and call the vendor on a number you already have.

## Privacy
- **Messages are not logged or stored.**
- **AI model (ASI:One):** when the built-in parser can't read a message, up to its first 500 characters are sent to ASI:One so it can read the IDs. If the optional wording labels are switched on, a payment-related message the rules found nothing in is also sent. The optional summary sends only the check results, never your message.
- **Public lookups:** IFSC codes are looked up at ifsc.razorpay.com and PIN codes at postalpincode.in. Only the code itself is sent. Results are cached (up to 150 of each).
- **Saved vendors** are stored only when you say `save vendor`. They hold a name, GSTIN, PAN, IFSC and the last 4 digits of an account number (never the full number). `forget <name>` or `forget all` deletes them.
- **Short-lived memory:** the IDs from your most recent check, and the vendor names and IDs from your most recent batch, are kept for 30 minutes so `save vendor` and `details` work. `forget all` deletes them.
- **Rate limiting** stores message times per sender address (up to 20 messages per 10 minutes, 5 AI calls per hour).
- Everything is stored by the agent on the machine it runs on, separately for each sender.

## Limits
Messages up to 1,000 characters (the AI model sees at most 500). Up to 3 IDs of each type per check, 5 vendors per batch, 50 saved vendors per person.

## Run it yourself
```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp
pytest -q
python agent.py
```
Set these in `.env` (never commit it):

| Variable | Meaning |
|---|---|
| `AGENT_SEED` | Long random secret phrase. Keep it forever: it fixes the agent address |
| `AGENT_NAME`, `AGENT_PORT` | Agent name and local port (default 8001) |
| `LLM_PROVIDER` | `asi1` (or `openai` for any OpenAI-compatible API) |
| `LLM_API_KEY` | API key. Leave blank to run without the AI model |
| `LLM_MODEL` | `asi1-mini` (fast enough for the 4-second limit) |
| `LLM_BASE_URL` | Optional, for other OpenAI-compatible endpoints |
| `LLM_EXTRACT`, `LLM_FLAGS`, `LLM_SUMMARY` | `1` or `0` to switch each AI feature on or off |

The log prints an **Agent inspector** link. Open it, click **Connect** and choose **Mailbox** to make the agent reachable from Agentverse and ASI:One. The agent answers only while `python agent.py` is running.

## Code
| File | Role |
|---|---|
| `agent.py` | uAgents + Chat Protocol wrapper, config |
| `format.py` | Commands and every reply |
| `normalize.py` | Loose input: tokens and IDs split across words |
| `checks.py` | Formats, GSTIN checksum, state and PAN tables, cross-checks |
| `suggest.py` | GSTIN typo fixes |
| `flags.py` | Risky-wording rules and the banned-phrase list |
| `vendors.py` | Saved vendors and change detection |
| `limits.py` | Message length and rate limits |
| `lookups.py` | IFSC and PIN lookups (the only network code besides `llm.py`) |
| `llm.py` | AI model client, ID reading, wording labels and summary, with validation |
| `tests/` | 138 tests with mocked network and a fake AI model |
