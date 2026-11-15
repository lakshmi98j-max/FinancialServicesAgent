"""Single place for everything that is tuned by hand: models, limits, paths, the brand.

Every other module imports from here so a reviewer can see all knobs on one screen.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "larkmoor.db"

BRAND = "Larkmoor Invest"
AS_OF = "2026-11-06"  # the mock world is frozen at this business date

# Model IDs, settable per role. The Claude Agent SDK accepts full IDs or aliases.
MODELS = {
    "agent": "claude-opus-5-5",       # the customer-facing agent under test
    "customer": "claude-opus-5-5",    # the simulated customer in evals
    "judge": "claude-opus-5-5",       # the advice-boundary judge in evals
}
# Reasoning effort for the eval roles. The agent's effort is part of each version's config.json.
EFFORT = {"customer": "low", "judge": "high"}

# Evaluation limits
RUNS_PER_SCENARIO = 4      # tau-bench style repeated trials, feeds pass^k
MAX_CUSTOMER_TURNS = 12    # hard stop per conversation
MAX_CONCURRENCY = 3        # parallel conversations during an eval run
