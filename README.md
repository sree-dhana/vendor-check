# VendorCheck

Check an Indian vendor's **GSTIN, PAN, IFSC and PIN code** before you pay them, and cross-check the IDs against each other.

## What it checks
- **GSTIN**: format, checksum, state code, and the PAN embedded in it
- **PAN**: format and holder type (individual, company, firm, trust, ...)
- **IFSC**: bank, branch, city/state, and NEFT/RTGS/IMPS/UPI support (live lookup)
- **PIN**: district and state (live lookup)
- **Cross-checks**: PAN vs the PAN inside the GSTIN; GSTIN state vs PIN state and bank branch state

## Try it
- `Check this vendor: GSTIN 27AAPFU0939F1ZV, PAN AAPFU0939F, IFSC YESB0DNB002`
- `Is GSTIN 27AAPFU0939F1ZV valid?`
- `Which bank is IFSC YESB0DNB002?`
- `pincode 560001`
- `sample` or `help`

## What it does not do
These are format, checksum and consistency checks. They don't prove identity, confirm that a GSTIN is active, or show that a payment is safe. Always confirm the GSTIN on gst.gov.in and call the vendor on a number you already have.

## Privacy
Message text is never logged or stored. Only public IFSC and PIN lookup results are cached.

## Run locally
```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp; then set AGENT_SEED
pytest -q
python agent.py
```
Open the Agent inspector link from the log, click **Connect**, and choose **Mailbox** to make the agent reachable from Agentverse and ASI:One.
