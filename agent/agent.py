"""The Larkmoor support agent. One conversation is one Claude session, one Larkmoor Session, one database.

    agent = Agent("v1", conn)                    # async, used by the eval runner
    await agent.start(); turn = await agent.send("Hi"); await agent.close()

    agent = BlockingAgent("v1", conn)            # same thing for synchronous callers: the CLI and Streamlit
    turn = agent.send("Hi"); agent.close()

A version is a folder in agent/versions/ holding system_prompt.md and config.json. Model IDs live in config.py.
"""
import asyncio
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import (AssistantMessage, ClaudeSDKClient, RateLimitEvent, ResultMessage, TextBlock,
                              create_sdk_mcp_server, tool)

from agent import llm
from agent.session import Session, ToolCall
from agent.tools import TOOL_SPECS, run_tool
from config import MODELS
from mockworld.db import connect

VERSIONS_DIR = Path(__file__).parent / "versions"
SERVER = "larkmoor"   # the model sees tools as mcp__larkmoor__<name>


def list_versions() -> list[str]:
    return sorted(p.name for p in VERSIONS_DIR.iterdir() if (p / "system_prompt.md").exists())


def load_version(name: str) -> dict:
    folder = VERSIONS_DIR / name
    config = json.loads((folder / "config.json").read_text())
    return {"name": name, "system_prompt": (folder / "system_prompt.md").read_text(), **config}


def mcp_tools(session: Session, conn) -> list:
    """Wrap each Larkmoor tool for the SDK. Every call goes through run_tool, so it is gated and traced."""
    def make(spec: dict):
        async def handler(args: dict) -> dict:
            result = run_tool(session, conn, spec["name"], args)
            return {"content": [{"type": "text", "text": json.dumps(result, default=str)}],
                    "is_error": not result["ok"]}
        return tool(spec["name"], spec["description"], spec["input_schema"])(handler)
    return [make(spec) for spec in TOOL_SPECS]


@dataclass
class Turn:
    customer: str
    reply: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    redactions: int = 0          # email addresses removed from the reply (policy R11)


class AgentError(RuntimeError):
    pass


ERROR_HELP = {
    "authentication_failed": "Not signed in to Claude. Run `claude` in a terminal and sign in, then start a new "
                             "conversation (Reset demo in the UI, /reset in the CLI).",
    "rate_limit": "Your Claude usage limit has been reached. Wait for it to reset, then try again.",
    "billing_error": "Claude reported a billing problem with the account in use.",
}


def error_message(code: str) -> str:
    return ERROR_HELP.get(code, f"Claude returned an error: {code}")


class Agent:
    def __init__(self, version: str = "v1", conn=None):
        self.version = load_version(version)
        self.conn = conn if conn is not None else connect()
        self.session = Session()
        self.usage = llm.Usage()
        self.turns: list[Turn] = []
        self.rate_limit = None       # latest RateLimitInfo from the SDK, if any
        self._client: ClaudeSDKClient | None = None
        self._cumulative_cost = 0.0

    async def start(self) -> "Agent":
        server = create_sdk_mcp_server(name=SERVER, tools=mcp_tools(self.session, self.conn))
        opts = llm.options(
            model=MODELS["agent"], role_prompt=self.version["system_prompt"], effort=self.version["effort"],
            mcp_servers={SERVER: server}, allowed_tools=[f"mcp__{SERVER}__{s['name']}" for s in TOOL_SPECS],
            max_turns=self.version["max_tool_rounds"],
        )
        self._client = ClaudeSDKClient(options=opts)
        await self._client.connect()
        return self

    async def send(self, text: str) -> Turn:
        """Send one customer message and return the agent's reply plus the tool calls it made for it."""
        first_call = len(self.session.trace)
        await self._client.query(text)
        parts: list[str] = []
        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                if msg.error:
                    raise AgentError(error_message(msg.error))
                if msg.parent_tool_use_id is None:
                    parts += [b.text for b in msg.content if isinstance(b, TextBlock)]
            elif isinstance(msg, RateLimitEvent):
                self.rate_limit = msg.rate_limit_info
            elif isinstance(msg, ResultMessage):
                total = msg.total_cost_usd or self._cumulative_cost     # cumulative across the session
                self.usage.add(msg.usage or {}, total - self._cumulative_cost)
                self._cumulative_cost = total
                if msg.is_error:
                    raise AgentError(f"Turn ended with {msg.subtype}: {msg.result or msg.errors}")
        reply, redactions = llm.redact("\n\n".join(p.strip() for p in parts if p.strip()))
        turn = Turn(customer=text, reply=reply, tool_calls=self.session.trace[first_call:], redactions=redactions)
        self.turns.append(turn)
        return turn

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


class BlockingAgent:
    """Runs an Agent on a private event-loop thread so synchronous code can call it."""

    def __init__(self, version: str = "v1", conn=None):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="larkmoor-agent")
        self._thread.start()
        self.agent = Agent(version, conn)
        self._call(self.agent.start())

    def _call(self, coro, timeout: float = 600):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def send(self, text: str) -> Turn:
        return self._call(self.agent.send(text))

    def close(self) -> None:
        try:
            self._call(self.agent.close(), timeout=60)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=10)
