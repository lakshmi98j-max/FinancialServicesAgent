"""Command-line chat with the Larkmoor agent.

    python -m agent.chat                 # newest version
    python -m agent.chat --version v1

Commands: /trace shows this conversation's tool calls, /reset rebuilds the database and starts over, /quit exits.
"""
import argparse
import json

from agent.agent import AgentError, BlockingAgent, list_versions
from config import AS_OF, BRAND, DB_PATH, MODELS
from mockworld.db import connect
from mockworld.seed import reset_db


def show_calls(calls) -> None:
    for c in calls:
        status = "ok" if c.ok else c.result.get("error", "error")
        print(f"   [tool] {c.name}({json.dumps(c.args)}) -> {status}   rules: {' '.join(c.rules)}")


def start(version: str) -> BlockingAgent:
    if not DB_PATH.exists():
        reset_db()
    return BlockingAgent(version, connect())


def stop(agent: BlockingAgent) -> None:
    agent.close()
    agent.agent.conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default=list_versions()[-1], choices=list_versions())
    args = parser.parse_args()

    print(f"{BRAND} support chat, agent {args.version} on {MODELS['agent']}. Business date {AS_OF}.")
    print("Fictional company, illustrative policy, not financial advice. Type /quit to exit.")
    agent = start(args.version)
    try:
        while True:
            try:
                text = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text == "/quit":
                break
            if text == "/trace":
                show_calls(agent.agent.session.trace)
                continue
            if text == "/reset":
                stop(agent)
                reset_db()
                agent = start(args.version)
                print("Database rebuilt and conversation restarted.")
                continue
            try:
                turn = agent.send(text)
            except AgentError as exc:
                print(f"\n[error] {exc}")
                continue
            show_calls(turn.tool_calls)
            print(f"\nlarkmoor> {turn.reply}")
    finally:
        print(f"\nUsage: {agent.agent.usage.line()}")
        stop(agent)


if __name__ == "__main__":
    main()
