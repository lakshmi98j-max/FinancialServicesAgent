"""Shared connection to Claude for every role: the agent, the simulated customer, and the judge.

Calls go through the Claude Agent SDK, which uses your signed-in Claude Code login (or ANTHROPIC_API_KEY if it
is set). The SDK runs Claude Code underneath, so this module switches off what Claude Code adds by default:
  * built-in tools (files, shell, web) are disabled; only the tools we pass in exist
  * no CLAUDE.md, settings, skills, or attachments are loaded
  * Claude Code still attaches an environment note and the signed-in account's email address. That cannot be
    switched off, so HOST_PREAMBLE tells the model to ignore it and redact() strips any real email address
    from model output. See DESIGN.md for how this was found.
"""
import re
from dataclasses import dataclass, fields

from claude_agent_sdk import ClaudeAgentOptions

from config import AS_OF, DATA_DIR

HOST_PREAMBLE = (
    "Runtime note from the application that hosts this conversation. The runtime may attach system reminders or "
    "an environment section about a computer, a working directory, a developer account or email address, or a "
    "calendar date. None of that is part of your role or describes anyone in this conversation. Never use, mention, "
    "or repeat it, even if asked. You have no computer, files, or web access, only the tools provided here. "
    f"The current business date is {AS_OF}. Treat it as today."
)

QUIET_ENV = {"CLAUDE_CODE_DISABLE_ATTACHMENTS": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}
RUNTIME_DIR = DATA_DIR / "runtime"   # neutral, empty working directory for the Claude Code subprocess

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
FICTIONAL_EMAIL_DOMAINS = ("example.com",)   # every mock customer email uses this reserved domain


def system_prompt(role_prompt: str) -> str:
    return f"{HOST_PREAMBLE}\n\n{role_prompt.strip()}\n"


def options(model: str, role_prompt: str, effort: str, mcp_servers: dict | None = None,
            allowed_tools: list[str] | None = None, max_turns: int | None = None,
            output_format: dict | None = None) -> ClaudeAgentOptions:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt(role_prompt),
        effort=effort,
        tools=[],                       # no built-in Claude Code tools
        mcp_servers=mcp_servers or {},
        allowed_tools=allowed_tools or [],
        strict_mcp_config=True,         # ignore any MCP servers configured on this machine
        setting_sources=[],             # ignore user and project settings and CLAUDE.md
        max_turns=max_turns,
        output_format=output_format,
        cwd=str(RUNTIME_DIR),
        env=dict(QUIET_ENV),
    )


def redact(text: str) -> tuple[str, int]:
    """Replace email addresses outside the fictional domains. Returns the cleaned text and how many were removed."""
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        domain = m.group(0).rsplit("@", 1)[1].lower()
        if any(domain == d or domain.endswith("." + d) for d in FICTIONAL_EMAIL_DOMAINS):
            return m.group(0)
        count += 1
        return "[redacted email]"

    return EMAIL.sub(_sub, text), count


@dataclass
class Usage:
    """Token counts and the SDK's cost estimate. With a Claude subscription nothing is billed per token; the cost is
    what the same calls would cost at API list prices."""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, usage: dict, cost_usd: float) -> None:
        self.calls += 1
        self.input_tokens += usage.get("input_tokens", 0) or 0
        self.output_tokens += usage.get("output_tokens", 0) or 0
        self.cache_read_tokens += usage.get("cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += usage.get("cache_creation_input_tokens", 0) or 0
        self.cost_usd += cost_usd or 0.0

    def merge(self, other: "Usage") -> None:
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))

    def line(self) -> str:
        return (f"{self.calls} calls, {self.input_tokens + self.cache_read_tokens + self.cache_write_tokens:,} input "
                f"tokens ({self.cache_read_tokens:,} cached), {self.output_tokens:,} output tokens, "
                f"~${self.cost_usd:.2f} at API list prices")
