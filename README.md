# Larkmoor Invest support agent

A customer service chat agent for a fictional consumer investing app, built to handle regulated financial
conversations: verify identity, explain account facts and performance, never give investment advice, and hand off
to a licensed advisor with a clean summary. A tau-bench style evaluation harness measures it.

> Work in progress. Milestones M1 (mock world, tools, tests) and M2 (policy, agent v1, chat interfaces) are done.
> The evaluation harness, later agent versions, results, and the full write-up follow.

**Fictional company. Illustrative policy. Not financial advice.** All customers, accounts, and tickers are invented.

## What is here so far

| Path | What it is |
|---|---|
| `policy.md` | The rules the agent follows, R1 to R12, with what code enforces and what the model must judge |
| `mockworld/` | SQLite schema and a deterministic seed: 20 customers, accounts, holdings, withdrawals, transfers, fees |
| `agent/tools.py` | The 10 tools. Code-enforced rules live here, such as no account data before verification |
| `agent/llm.py` | The one place that talks to Claude, through the Claude Agent SDK |
| `agent/agent.py` | One conversation: Claude session, tool wiring, trace of every tool call |
| `agent/versions/v1/` | The baseline agent: one plain system prompt plus the tools |
| `agent/chat.py` | Command-line chat |
| `ui/app.py` | Streamlit demo with a side panel of tool calls and policy rules |
| `tests/` | Unit tests for the seed, every tool, and the agent wiring. No model calls |

## Run it

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m mockworld.seed        # builds data/larkmoor.db, which is not committed
pytest                          # 77 tests, no model calls
```

The agent calls Claude through the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview), which
runs on your own Claude Code login. Sign in once with Claude Code before chatting. Alternatively, set
`ANTHROPIC_API_KEY` (see `.env.example`) and the SDK uses the API key instead. Model IDs for the agent, the simulated
customer, and the judge are set in `config.py`.

```bash
python -m agent.chat            # command-line chat
streamlit run ui/app.py         # demo UI at http://localhost:8501
```

The demo sidebar lists three demo customers with their verification details.
