"""Local demo UI for screen recording.

    streamlit run ui/app.py

Left: the chat. Right: every tool call the agent made, whether code allowed or refused it, and the policy rules
involved. Sidebar: agent version, three demo customers with their verification details, and a reset button.
"""
import html
import re
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # lets `streamlit run ui/app.py` import the project packages

from agent.agent import AgentError, BlockingAgent, list_versions, load_version  # noqa: E402
from config import AS_OF, BRAND, DB_PATH, MODELS  # noqa: E402
from mockworld.db import connect  # noqa: E402
from mockworld.seed import reset_db  # noqa: E402

DEMO_CUSTOMERS = [
    {"name": "Priya Raman", "story": "Her mortgage payment goes up $201 in December",
     "details": ["Email: priya.raman@example.com", "Date of birth: March 12, 1984", "Property ZIP: 80302"],
     "try": ["Why is my mortgage payment going up?", "I can't afford that. Can you refinance me at a lower rate?"]},
    {"name": "Marcus Oyelaran", "story": "Paid his November installment online and wants autopay",
     "details": ["Loan ending 2915", "Date of birth: August 23, 1979", "SSN last 4: 5527"],
     "try": ["Did you get my November payment?", "Can you set up autopay from my checking account?"]},
    {"name": "Elena Vasquez-Torres", "story": "Her home equity line application has stalled",
     "details": ["Email: elena.vt@example.com", "Date of birth: November 2, 1991", "Property ZIP: 78704"],
     "try": ["What's happening with my HELOC application?", "Can you just approve it today?"]},
]
WRITE_TOOLS = ("create_handoff", "escalate_to_human", "make_payment", "set_autopay")
TEAM_NAMES = {"loan_officer": "Licensed loan officer", "hardship_specialist": "Hardship specialist"}
PANE_HEIGHT = 640   # chat and side panel scroll independently so both stay in frame while recording

CSS = """
<style>
.block-container {padding-top: 1.2rem; max-width: 1500px;}
[data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li, [data-testid="stChatMessage"] td {font-size: 1.05rem; line-height: 1.55;}
.banner {background: #fff4d6; border: 1px solid #e3c770; border-radius: 10px; padding: 0.6rem 1rem;
         font-size: 1rem; margin-bottom: 0.8rem;}
.chip {display: inline-block; background: #e3ede8; color: #1f5c4a; border-radius: 999px; padding: 0.1rem 0.6rem;
       margin: 0.15rem 0.3rem 0.15rem 0; font-size: 0.82rem; font-weight: 600;}
.chip-block {background: #fbe3e0; color: #8a2a1d;}
.call {font-size: 1rem; font-weight: 600; margin-top: 0.5rem;}
.muted {color: #5b6661; font-size: 0.9rem;}
</style>
"""


@st.cache_data
def rule_titles() -> dict[str, str]:
    text = (ROOT / "policy.md").read_text()
    return dict(re.findall(r"^## (R\d+)\. (.+)$", text, flags=re.M))


def md(text: str) -> str:
    """Escape dollar signs so Streamlit does not render amounts as LaTeX."""
    return text.replace("$", "\\$")


def start_conversation(version: str) -> None:
    old = st.session_state.get("agent")
    if old is not None:
        old.close()
        old.agent.conn.close()
    if not DB_PATH.exists():
        reset_db()
    st.session_state.messages = []
    st.session_state.turns = []
    st.session_state.start_error = None
    with st.spinner("Starting the agent..."):
        try:
            st.session_state.agent = BlockingAgent(version, connect())
        except Exception as exc:   # shown on the page instead of a stack trace
            st.session_state.pop("agent", None)
            st.session_state.start_error = f"{type(exc).__name__}: {exc}"


def reset_demo() -> None:
    old = st.session_state.pop("agent", None)
    if old is not None:
        old.close()
        old.agent.conn.close()
    reset_db()
    start_conversation(st.session_state.version)


def sidebar() -> None:
    with st.sidebar:
        st.markdown(f"## {BRAND}")
        st.caption("Support agent demo")
        versions = list_versions()
        st.selectbox("Agent version", versions, index=len(versions) - 1, key="version",
                     on_change=lambda: start_conversation(st.session_state.version))
        st.caption(load_version(st.session_state.version)["description"])
        st.button("Reset demo", on_click=reset_demo, type="primary", use_container_width=True,
                  help="Rebuild the database from the seed and start a new conversation.")
        st.markdown("### Try a demo customer")
        for c in DEMO_CUSTOMERS:
            with st.expander(f"{c['name']}: {c['story']}"):
                st.markdown("\n".join(f"- {md(d)}" for d in c["details"]))
                st.markdown("**Try asking**")
                st.markdown("\n".join(f"- {md(t)}" for t in c["try"]))
        st.divider()
        usage = st.session_state.agent.agent.usage
        st.caption(f"Agent model: {MODELS['agent']}. Business date: {AS_OF}.")
        st.caption(f"This conversation: {md(usage.line())}.")


def chat(prompt: str | None) -> None:
    st.markdown("### Chat")
    pane = st.container(height=PANE_HEIGHT, border=False)   # auto-scrolls to the newest message
    with pane:
        if not st.session_state.messages:
            st.markdown('<p class="muted">Say hello, or pick a demo customer in the sidebar.</p>', unsafe_allow_html=True)
        for m in st.session_state.messages:
            with st.chat_message(m["role"]):
                st.markdown(md(m["text"]))
    if prompt:
        with pane:
            send(prompt)


def send(prompt: str) -> None:
    with st.chat_message("user"):
        st.markdown(md(prompt))
    with st.chat_message("assistant"):
        with st.spinner("Working on it..."):
            try:
                turn = st.session_state.agent.send(prompt)
            except (AgentError, TimeoutError) as exc:
                st.error(f"The agent could not answer: {exc}")
                return
    st.session_state.messages += [{"role": "user", "text": prompt},
                                  {"role": "assistant", "text": turn.reply or "(no reply)"}]
    st.session_state.turns.append(turn)
    st.rerun()


def chips(rule_ids: list[str], blocked: bool = False) -> str:
    titles = rule_titles()
    cls = "chip chip-block" if blocked else "chip"
    return "".join(f'<span class="{cls}">{r} {titles.get(r, "")}</span>' for r in rule_ids)


def panel() -> None:
    st.markdown("### What the agent did")
    with st.container(height=PANE_HEIGHT, border=False, autoscroll=False):
        panel_body()


def panel_body() -> None:
    turns = st.session_state.turns
    if not turns:
        st.markdown('<p class="muted">Tool calls and the policy rules they enforce appear here.</p>',
                    unsafe_allow_html=True)
        return
    records = [c for t in turns for c in t.tool_calls if c.name in WRITE_TOOLS and c.ok]
    if records:
        st.markdown("**Records written**")
        for c in records:
            r = c.result
            if c.name == "create_handoff":
                rec = r["recorded"]
                with st.container(border=True):
                    st.markdown(f"**{TEAM_NAMES[r['team']]} handoff {r['handoff_id']}** · {rec['reason_category']} · "
                                f"{rec['urgency']} urgency")
                    st.markdown(md(f"**Summary:** {rec['summary']}"))
                    st.markdown(md(f"**Customer goal:** {rec['customer_goal']}"))
                    if rec["open_questions"]:
                        st.markdown("**Open questions**\n" + "\n".join(f"- {md(q)}" for q in rec["open_questions"]))
            elif c.name == "escalate_to_human":
                st.info(f"Escalation {r['escalation_id']}: {c.args.get('reason_category')}, {r['urgency']} urgency")
            elif c.name == "make_payment":
                st.info(md(f"Payment {r['payment_id']} ({r['status']}): {r['confirmed_details']}"))
            elif c.name == "set_autopay" and r.get("changed"):
                st.info(md(f"Autopay updated: {r['confirmed_details']}"))
    for i, turn in reversed(list(enumerate(turns, start=1))):
        st.markdown(f'<p class="muted">Message {i}: "{html.escape(turn.customer[:70])}"</p>', unsafe_allow_html=True)
        if not turn.tool_calls:
            st.markdown('<p class="muted">No tools used.</p>', unsafe_allow_html=True)
        for c in turn.tool_calls:
            waiting = c.result.get("error") == "CONFIRMATION_REQUIRED"   # the read-back step, not a refusal
            icon = "✅" if c.ok else "⏸️" if waiting else "⛔"
            outcome = "" if c.ok else " waiting for the customer's yes" if waiting else f" refused: {c.result.get('error')}"
            st.markdown(f'<div class="call">{icon} {c.name}{outcome}</div>'
                        f'{chips(c.rules, blocked=not (c.ok or waiting))}', unsafe_allow_html=True)
            with st.expander("Details"):
                st.json({"arguments": c.args, "result": c.result}, expanded=False)
        if turn.redactions:
            st.markdown(f'<div class="call">🛡️ Redacted {turn.redactions} email address(es)</div>{chips(["R11"])}',
                        unsafe_allow_html=True)
        st.divider()


def main() -> None:
    st.set_page_config(page_title=f"{BRAND} support", page_icon="💬", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    if "version" not in st.session_state:
        st.session_state.version = list_versions()[-1]
    if "agent" not in st.session_state and not st.session_state.get("start_error"):
        start_conversation(st.session_state.version)
    if st.session_state.get("start_error"):
        st.error(f"The agent could not start. Check that you are signed in to Claude (run `claude` in a terminal), "
                 f"then try again.\n\n{st.session_state.start_error}")
        if st.button("Try again"):
            start_conversation(st.session_state.version)
            st.rerun()
        st.stop()
    sidebar()
    st.markdown(f'<div class="banner"><b>Fictional company.</b> {BRAND} and all customers are invented. '
                '<b>Illustrative policy.</b> <b>Not financial or legal advice.</b></div>', unsafe_allow_html=True)
    prompt = st.chat_input(f"Message {BRAND} support")
    left, right = st.columns([3, 2], gap="large")
    with left:
        chat(prompt)
    with right:
        panel()


main()
