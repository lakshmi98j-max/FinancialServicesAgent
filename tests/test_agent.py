"""Agent wiring that can be tested without calling a model."""
import asyncio
import json

from agent import llm
from agent.agent import load_version, list_versions, mcp_tools
from agent.tools import TOOL_SPECS
from config import AS_OF


def test_every_version_folder_is_complete():
    assert "v1" in list_versions()
    for name in list_versions():
        v = load_version(name)
        assert len(v["system_prompt"]) > 200
        assert v["effort"] in ("low", "medium", "high", "xhigh", "max")
        assert isinstance(v["max_tool_rounds"], int) and v["description"]


def test_options_switch_off_claude_code_defaults():
    opts = llm.options(model="m", role_prompt="Be helpful.", effort="low")
    assert opts.tools == [] and opts.setting_sources == [] and opts.strict_mcp_config is True
    assert opts.env["CLAUDE_CODE_DISABLE_ATTACHMENTS"] == "1"
    assert opts.system_prompt.startswith(llm.HOST_PREAMBLE) and opts.system_prompt.rstrip().endswith("Be helpful.")
    assert AS_OF in llm.HOST_PREAMBLE


def test_redact_keeps_fictional_emails_only():
    # .test is a reserved domain, so these stand in for a real developer address without being one
    text, n = llm.redact("Reach dev.person@mail.test, ops@corp.dev.test or priya.raman@example.com")
    assert n == 2 and "mail.test" not in text and "corp.dev.test" not in text and "priya.raman@example.com" in text
    assert llm.redact("no emails here") == ("no emails here", 0)


def test_usage_accumulates():
    u = llm.Usage()
    u.add({"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 50}, 0.01)
    u.add({"input_tokens": 1, "output_tokens": 1}, 0.02)
    assert (u.calls, u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens) == (2, 11, 6, 100, 50)
    assert round(u.cost_usd, 4) == 0.03 and "161 input tokens" in u.line()


def test_mcp_wrappers_route_through_run_tool(conn, session):
    wrapped = {t.name: t for t in mcp_tools(session, conn)}
    assert set(wrapped) == {s["name"] for s in TOOL_SPECS}

    blocked = asyncio.run(wrapped["get_account_summary"].handler({}))
    assert blocked["is_error"] is True
    assert json.loads(blocked["content"][0]["text"])["error"] == "SESSION_NOT_VERIFIED"

    ok = asyncio.run(wrapped["verify_identity"].handler(
        {"locator": "priya.raman@example.com", "date_of_birth": "1984-03-12", "security_word": "saffron"}))
    assert ok["is_error"] is False and session.verified_customer_id == 1
    assert [c.name for c in session.trace] == ["get_account_summary", "verify_identity"]


def test_claude_errors_become_actionable_messages():
    from agent.agent import error_message
    assert "Run `claude`" in error_message("authentication_failed")
    assert "usage limit" in error_message("rate_limit")
    assert error_message("something_new") == "Claude returned an error: something_new"
