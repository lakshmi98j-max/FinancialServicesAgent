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
| `.devcontainer/` | GitHub Codespaces setup |

## Run it in GitHub Codespaces

The repository includes a dev container, so a codespace sets itself up: Python, dependencies, the mock database,
and a `claude` command for signing in.

1. On GitHub, open the repository and select the branch you want to run.
2. Click **Code**, open the **Codespaces** tab, and create a codespace on that branch. Setup takes about two minutes.
3. In the codespace terminal, run `claude`, sign in with your Claude account, then type `/exit`.
4. Open the **Ports** tab and click the globe icon on port 8501, labelled "Larkmoor demo". The address looks like
   `https://<codespace-name>-8501.app.github.dev`. The localhost, network and external URLs that Streamlit prints
   do not work from your browser. If the page was already open before you signed in, click **Reset demo**.

If `claude` is not found, setup has not finished or did not run. Run `bash .devcontainer/setup.sh`, wait for
"Setup complete", then start the demo with `bash .devcontainer/demo.sh`, which prints the address to open.

Keep port 8501 **Private**. A public port would let anyone chat with the agent on your Claude login. Stop the
codespace when you finish so it does not use your free monthly hours. For the command-line chat, run
`python3 -m agent.chat` in the codespace terminal.

## Run it locally

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m mockworld.seed        # builds data/larkmoor.db, which is not committed
pytest                          # 80 tests, no model calls
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
