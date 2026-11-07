import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv
from uagents import Context, Protocol, Agent
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)

from format import first_contact, reply_for_text
from format import email_alert, email_alert_short
from llm import PROVIDERS, load_config
import anchor
import mailwatch
import notify
import workflow

load_dotenv()

SEED = os.environ.get("AGENT_SEED")
if not SEED:
    raise SystemExit("AGENT_SEED is not set")

LLM = load_config()


class Store:
    """Adapter so other modules only need get/set."""

    def __init__(self, storage):
        self.s = storage

    def get(self, key, default=None):
        v = self.s.get(key)
        return default if v is None else v

    def set(self, key, value):
        self.s.set(key, value)


def create_text_chat(text: str, end_session: bool = False) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    if end_session:
        content.append(EndSessionContent(type="end-session"))
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=content)


# The agent stays on mainnet: on testnet its address becomes "test-agent1..." and ASI:One,
# which knows it as "agent1...", can no longer reach it. Decision fingerprints are anchored
# separately on ANCHOR_NETWORK (testnet by default, free) using the same wallet key.
NETWORK = anchor.agent_network()
ANCHOR_NETWORK = anchor.network()

agent = Agent(
    name=os.environ.get("AGENT_NAME", "vendor-check"),
    seed=SEED,
    port=int(os.environ.get("AGENT_PORT", "8000")),
    mailbox=True,
    network=NETWORK,
)
protocol = Protocol(spec=chat_protocol_spec)


@protocol.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(
        sender,
        ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id),
    )

    text = msg.text()
    if not text:
        if any(isinstance(c, StartSessionContent) for c in msg.content):
            await ctx.send(sender, create_text_chat(first_contact(Store(ctx.storage), sender)))
        return

    try:
        reply = reply_for_text(text, Store(ctx.storage), sender=sender, llm=LLM)
    except Exception:
        ctx.logger.exception("VendorCheck failed")  # never log the user's text
        reply = (
            "Sorry, something went wrong on my side. Please try again, "
            "or say `help` to see what I can check."
        )

    await ctx.send(sender, create_text_chat(reply))


@protocol.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


agent.include(protocol, publish_manifest=True)


@agent.on_event("startup")
async def report_llm(ctx: Context):
    if LLM.client:
        ctx.logger.info(
            "LLM: %s (extract=%s, flags=%s, summary=%s)"
            % (LLM.provider_name, LLM.extract, LLM.flags, LLM.summary)
        )
    else:
        provider = os.environ.get("LLM_PROVIDER", "").strip()
        if provider and provider.lower() not in PROVIDERS:
            ctx.logger.warning(
                "LLM: off, unknown LLM_PROVIDER '%s' (use one of: %s; the model goes in LLM_MODEL)"
                % (provider, ", ".join(PROVIDERS))
            )
        else:
            ctx.logger.info("LLM: off (set LLM_PROVIDER and LLM_API_KEY in .env to enable)")


@agent.on_interval(period=300.0)
async def case_tick(ctx: Context):
    """Every 5 minutes: expire old cases, move cases past their cancel window on, and send
    reminders for cases waiting on the user. Whether ASI:One shows these unprompted messages
    is still to be tested."""
    store = Store(ctx.storage)
    for owner in workflow.owners(store):
        try:
            for message in workflow.tick(store, owner, client=LLM.client):
                await deliver(ctx, store, owner, message)
        except Exception:
            ctx.logger.exception("case tick failed")  # no message text or IDs logged
    for owner in set(mailwatch.watching_owners(store)):
        for message in mailwatch.silence_notices(store, owner):
            await deliver(ctx, store, owner, message)


async def deliver(ctx: Context, store, owner, text, short=None):
    """Every unprompted notice: a chat message, a line for the next reply's digest (in case
    ASI:One doesn't show unprompted messages), and a short backup email if verified."""
    await ctx.send(owner, create_text_chat(text))
    short = short or notify.short_text(text)
    notify.record(store, owner, short)
    address = notify.backup_email_for(store, owner)
    if address:
        try:
            await asyncio.to_thread(notify.send_email, address, "VendorCheck: " + short[:80],
                                    notify.backup_body(short))
        except Exception as e:
            ctx.logger.warning("backup email failed: %s" % type(e).__name__)


if mailwatch.configured():
    @agent.on_interval(period=float(mailwatch.config()["poll_sec"]))
    async def poll_mailbox(ctx: Context):
        """Fetch forwarded emails off the event loop, then process them on it."""
        try:
            raws = await asyncio.to_thread(mailwatch.fetch_new)
        except Exception as e:
            ctx.logger.warning("mailbox check failed: %s" % type(e).__name__)  # never the password or text
            return
        store = Store(ctx.storage)
        for raw in raws:
            try:
                for owner, kind, payload in mailwatch.handle_message(store, raw):
                    if kind == "text":
                        await deliver(ctx, store, owner, payload)
                    else:
                        await deliver(ctx, store, owner, email_alert(payload), email_alert_short(payload))
            except Exception:
                ctx.logger.exception("processing a forwarded email failed")


MIN_ANCHOR_BALANCE = 10 ** 15  # in the network's smallest unit; a top-up is asked for below this


def anchor_ledger():
    from uagents.network import get_ledger
    return anchor.FetchLedger(agent.wallet, get_ledger(ANCHOR_NETWORK), ANCHOR_NETWORK)


@agent.on_event("startup")
async def report_ledger(ctx: Context):
    if NETWORK == "testnet":
        ctx.logger.warning("AGENT_NETWORK=testnet gives this agent a 'test-agent' address that ASI:One "
                           "can't reach. Use AGENT_NETWORK=mainnet; anchoring can still use testnet.")
    if not anchor.enabled():
        return
    ledger = anchor_ledger()
    try:
        balance = await asyncio.to_thread(ledger.balance)
    except Exception as e:
        ctx.logger.warning("Anchoring ledger: couldn't read the wallet balance (%s)" % type(e).__name__)
        return
    ctx.logger.info("Anchoring ledger: %s, wallet %s, balance %d" % (ANCHOR_NETWORK, agent.wallet.address(), balance))
    if ANCHOR_NETWORK == "testnet" and balance < MIN_ANCHOR_BALANCE:
        from uagents.network import add_testnet_funds
        await asyncio.to_thread(add_testnet_funds, str(agent.wallet.address()))
        ctx.logger.info("Anchoring ledger: asked the testnet faucet for funds")
    elif ANCHOR_NETWORK == "mainnet":
        ctx.logger.warning("Anchoring on mainnet spends real FET; ANCHOR_NETWORK=testnet is free")


if anchor.enabled():
    @agent.on_interval(period=60.0)
    async def anchor_decisions(ctx: Context):
        """Write fingerprints of closed cases to the ledger, off the event loop."""
        store = Store(ctx.storage)
        ledger = anchor_ledger()
        for owner in anchor.owners_with_pending(store):
            for case_id, memo in anchor.pending(store, owner):
                try:
                    tx_hash = await asyncio.to_thread(ledger.submit, memo)
                    anchor.record_anchor(store, owner, case_id, tx_hash, ANCHOR_NETWORK)
                    ctx.logger.info("Anchored a decision fingerprint: tx %s" % tx_hash)
                except Exception as e:
                    anchor.record_failure(store, owner, case_id, type(e).__name__)
                    ctx.logger.warning("Anchoring failed (%s); will retry" % type(e).__name__)


@agent.on_event("startup")
async def report_mailbox(ctx: Context):
    if mailwatch.configured():
        c = mailwatch.config()
        ctx.logger.info("Email watching: on (%s, every %ds)" % (c["host"], c["poll_sec"]))
    else:
        ctx.logger.info("Email watching: off (set MAILBOX_* in .env to enable)")


if __name__ == "__main__":
    agent.run()
