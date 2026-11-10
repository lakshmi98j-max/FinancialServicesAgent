# Larkmoor Invest support agent

Work in progress. A customer-service chat agent for a fictional consumer investing app, with a
tau-bench style evaluation harness for regulated financial conversations.

Milestone status: M1 (mock world, tools, tests) complete. Full README arrives with M5.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m mockworld.seed      # builds data/larkmoor.db (not committed)
pytest
```
