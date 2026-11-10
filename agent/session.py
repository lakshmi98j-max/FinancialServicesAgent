"""Per-conversation state. The verification gate lives in code (here and in tools.py), not in the prompt."""
import uuid
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """One entry in the tool-call trace. Evals check this trace; the demo UI displays it."""
    name: str
    args: dict
    result: dict
    rules: list[str]           # policy rule IDs this tool enforces or informs (see policy.md)

    @property
    def ok(self) -> bool:
        return bool(self.result.get("ok"))


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    verified_customer_id: int | None = None
    verification_attempts: int = 0
    locked: bool = False
    escalated: bool = False
    trace: list[ToolCall] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.verified_customer_id is not None
