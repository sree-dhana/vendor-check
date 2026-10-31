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

from format import reply_for_text, HELP_TEXT
from llm import load_config

load_dotenv()

SEED = os.environ.get("AGENT_SEED")
if not SEED:
    raise SystemExit("AGENT_SEED is not set. Copy .env.example to .env and set it.")

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


# Runs locally; Agentverse is used only as a mailbox so ASI:One can reach it.
agent = Agent(
    name=os.environ.get("AGENT_NAME", "vendor-check"),
    seed=SEED,
    port=int(os.environ.get("AGENT_PORT", "8000")),
    mailbox=True,
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
            await ctx.send(sender, create_text_chat(HELP_TEXT))
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
        ctx.logger.info("LLM: off (set LLM_PROVIDER and LLM_API_KEY in .env to enable)")


if __name__ == "__main__":
    agent.run()
