-- Larkmoor Invest mock world. Every table is rebuilt from seed.py; nothing here is real.
PRAGMA foreign_keys = ON;

CREATE TABLE benchmarks (
  benchmark_id  TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  description   TEXT NOT NULL
);

CREATE TABLE benchmark_returns (
  benchmark_id  TEXT NOT NULL REFERENCES benchmarks(benchmark_id),
  period        TEXT NOT NULL,            -- 1M | 3M | YTD | 1Y
  return_pct    REAL NOT NULL,
  PRIMARY KEY (benchmark_id, period)
);

CREATE TABLE customers (
  customer_id    INTEGER PRIMARY KEY,
  first_name     TEXT NOT NULL,
  last_name      TEXT NOT NULL,
  email          TEXT NOT NULL UNIQUE,
  phone_last4    TEXT NOT NULL,
  date_of_birth  TEXT NOT NULL,           -- verification factor
  bank_last4     TEXT NOT NULL,           -- last 4 of linked bank account, verification factor
  security_word  TEXT NOT NULL,           -- verification factor
  customer_since TEXT NOT NULL,
  e_delivery     INTEGER NOT NULL,        -- 1 = electronic statements, 0 = paper
  risk_profile   TEXT NOT NULL            -- conservative | balanced | growth
);

CREATE TABLE accounts (
  account_id     INTEGER PRIMARY KEY,
  customer_id    INTEGER NOT NULL REFERENCES customers(customer_id),
  account_number TEXT NOT NULL UNIQUE,    -- 8 digits; last 4 are unique across all accounts
  account_type   TEXT NOT NULL,           -- individual | roth_ira | traditional_ira
  managed        INTEGER NOT NULL,        -- 1 = enrolled in managed portfolios (advisory fee applies)
  benchmark_id   TEXT NOT NULL REFERENCES benchmarks(benchmark_id),
  cash_balance   REAL NOT NULL,
  settled_cash   REAL NOT NULL,           -- cash available to withdraw today
  opened_on      TEXT NOT NULL
);

CREATE TABLE securities (
  symbol      TEXT PRIMARY KEY,           -- fictional tickers only
  name        TEXT NOT NULL,
  asset_class TEXT NOT NULL,              -- equity | equity_fund | bond_fund | reit
  sector      TEXT NOT NULL,
  price       REAL NOT NULL               -- closing price on the as-of date
);

CREATE TABLE security_returns (
  symbol     TEXT NOT NULL REFERENCES securities(symbol),
  period     TEXT NOT NULL,
  return_pct REAL NOT NULL,
  PRIMARY KEY (symbol, period)
);

CREATE TABLE holdings (
  account_id INTEGER NOT NULL REFERENCES accounts(account_id),
  symbol     TEXT NOT NULL REFERENCES securities(symbol),
  quantity   REAL NOT NULL,
  cost_basis REAL NOT NULL,               -- total amount paid
  PRIMARY KEY (account_id, symbol)
);

CREATE TABLE transactions (
  transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id     INTEGER NOT NULL REFERENCES accounts(account_id),
  posted_on      TEXT NOT NULL,
  type           TEXT NOT NULL,           -- deposit | withdrawal | buy | sell | dividend | fee | transfer_in | transfer_out
  symbol         TEXT,
  quantity       REAL,
  amount         REAL NOT NULL,           -- signed: cash in is positive, cash out is negative
  description    TEXT NOT NULL
);

CREATE TABLE withdrawals (
  withdrawal_id          TEXT PRIMARY KEY,
  account_id             INTEGER NOT NULL REFERENCES accounts(account_id),
  requested_on           TEXT NOT NULL,
  amount                 REAL NOT NULL,
  destination_bank_last4 TEXT NOT NULL,
  method                 TEXT NOT NULL,   -- ach | wire
  status                 TEXT NOT NULL,   -- pending | processing | completed | on_hold | rejected
  status_reason          TEXT,
  expected_arrival_on    TEXT,
  updated_on             TEXT NOT NULL
);

CREATE TABLE transfers (
  transfer_id             TEXT PRIMARY KEY,
  account_id              INTEGER NOT NULL REFERENCES accounts(account_id),
  direction               TEXT NOT NULL,  -- incoming | outgoing | internal
  counterparty            TEXT NOT NULL,  -- other institution, or the other Larkmoor account
  transfer_type           TEXT NOT NULL,  -- full_account | partial | cash
  amount                  REAL,
  status                  TEXT NOT NULL,  -- initiated | validating | in_transit | completed | rejected
  status_reason           TEXT,
  initiated_on            TEXT NOT NULL,
  estimated_completion_on TEXT,
  updated_on              TEXT NOT NULL
);

CREATE TABLE fee_schedule (
  fee_type     TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  amount       REAL NOT NULL,
  unit         TEXT NOT NULL,             -- per_item | per_statement | per_contract | per_trade | per_month | pct_annual
  description  TEXT NOT NULL,
  waiver       TEXT
);

-- Written by the agent's tools during a conversation. Evals compare these to the expected final state.
CREATE TABLE advisor_handoffs (
  handoff_id      TEXT PRIMARY KEY,
  customer_id     INTEGER NOT NULL REFERENCES customers(customer_id),
  account_id      INTEGER REFERENCES accounts(account_id),
  created_on      TEXT NOT NULL,
  reason_category TEXT NOT NULL,
  summary         TEXT NOT NULL,
  customer_goal   TEXT NOT NULL,
  open_questions  TEXT NOT NULL,          -- JSON list of strings
  urgency         TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE escalations (
  escalation_id   TEXT PRIMARY KEY,
  customer_id     INTEGER REFERENCES customers(customer_id),   -- NULL when the session was never verified
  created_on      TEXT NOT NULL,
  reason_category TEXT NOT NULL,
  details         TEXT NOT NULL,
  urgency         TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'open'
);
