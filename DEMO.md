# VendorCheck demo (60 to 90 seconds)

## Before the demo
1. In `agent/.env`: `CALLS_ENABLED=1`, `DEMO_MODE=1`, `ANCHOR_ENABLED=1`, `ANCHOR_NETWORK=testnet`, `AGENT_NETWORK=mainnet`.
2. Run `python agent.py` and leave it running; wait for `Successfully registered as mailbox agent`.
3. In a fresh ASI:One chat, send `@vendorchecker forget all` so the demo starts clean, then rehearse once.
4. Keep this file open to copy the messages. Replace `YOUR@EMAIL` with an address you control.

## Script

**1. Set-up (10 s).** Send, one at a time:
```
@vendorchecker start
@vendorchecker yes
@vendorchecker India
@vendorchecker pay
@vendorchecker India
@vendorchecker yes
@vendorchecker skip
```
> Say: "Set-up is plain code, no AI. It shows the legal notice first: automated calls are off by default until the legal side is reviewed."

**2. Register a vendor and ask for consent (15 s).**
```
@vendorchecker save vendor Sharma Traders GSTIN 27AAPFU0939F1ZV IFSC YESB0DNB002 account 1234567890 phone +91 98765 43210 email YOUR@EMAIL contact Ravi Sharma
```
> Say: "The phone number is one I already trust. The agent drafts a consent request for me to send; it never contacts the vendor itself."

Then record the vendor's reply:
```
@vendorchecker consent reply Sharma Traders
From: YOUR@EMAIL
CALLS: YES
RECORDING: NO
```

**3. The scam email arrives (15 s).** Paste:
```
@vendorchecker check email
From: Sharma Traders <YOUR@EMAIL>
Subject: Updated bank details
Message-ID: <demo-1@example.com>

We have changed our bank account. Please pay today.
IFSC SBIN0000001
Account number 55500011112222
```
> Say: "Every ID is valid, but the bank details changed and the wording pushes me to pay today. The case is open and the payment is on hold."

**4. The verification call (15 s).** Use the case id from the reply:
```
@vendorchecker demo call <case id>
```
> Say: "This is a simulated call to the saved number only, never one from the email. The vendor read back the last four digits and they match. That's evidence, not proof."

**5. My decision (10 s).**
```
@vendorchecker approve <case id> 2222
```
> Say: "Only I can approve, by quoting the case and the new account's last four digits. A fingerprint of this decision goes on the Fetch.ai ledger, so the record can't be quietly changed."

**6. Optional (10 s), a minute later:**
```
@vendorchecker verify case <case id>
```

## Say aloud before you finish
- "A call result is **evidence, not proof**; I always decide."
- "The **legal side is still to be studied**: automated calls, recording and consent. That's why calls are off by default and only simulated here."

## If something goes wrong
- **No reply:** check the agent terminal is still running; refresh the chat.
- **"please wait"**: the 20-messages-per-10-minutes limit; pause, or use a second account for rehearsals.
- **Show other call results:** `demo call <case id> denied`, `confirm_mismatch` or `unclear`.
