-- Mock world for a fictional mortgage and home equity lender that services its own loans.
-- Every table is rebuilt from seed.py. Nothing here is real.
PRAGMA foreign_keys = ON;

CREATE TABLE customers (
  customer_id    INTEGER PRIMARY KEY,
  first_name     TEXT NOT NULL,
  last_name      TEXT NOT NULL,
  email          TEXT NOT NULL UNIQUE,
  phone_last4    TEXT NOT NULL,
  date_of_birth  TEXT NOT NULL,           -- verification factor
  ssn_last4      TEXT NOT NULL,           -- verification factor (last 4 of the Social Security number)
  property_zip   TEXT NOT NULL,           -- verification factor (ZIP code of the primary property)
  customer_since TEXT NOT NULL
);

CREATE TABLE bank_accounts (              -- accounts already verified and linked; chat cannot add new ones
  customer_id  INTEGER NOT NULL REFERENCES customers(customer_id),
  bank_last4   TEXT NOT NULL,
  account_type TEXT NOT NULL,             -- checking | savings
  linked_on    TEXT NOT NULL,
  PRIMARY KEY (customer_id, bank_last4)
);

CREATE TABLE loans (
  loan_id                    INTEGER PRIMARY KEY,
  customer_id                INTEGER NOT NULL REFERENCES customers(customer_id),
  loan_number                TEXT NOT NULL UNIQUE,   -- 10 digits; last 4 are unique across all loans
  loan_type                  TEXT NOT NULL,          -- mortgage | heloc
  property_address           TEXT NOT NULL,
  interest_rate              REAL NOT NULL,          -- annual percentage rate on the note
  rate_type                  TEXT NOT NULL,          -- fixed | variable
  original_amount            REAL NOT NULL,          -- mortgage: amount borrowed; heloc: credit limit
  origination_date           TEXT NOT NULL,
  term_months                INTEGER NOT NULL,
  first_payment_date         TEXT NOT NULL,
  principal_balance          REAL NOT NULL,
  monthly_principal_interest REAL NOT NULL,          -- heloc: interest-only payment during the draw period
  monthly_escrow             REAL NOT NULL,          -- property tax and insurance collected monthly; 0 for heloc
  escrow_balance             REAL NOT NULL,
  next_due_date              TEXT NOT NULL,          -- oldest unpaid installment
  autopay_enabled            INTEGER NOT NULL,
  autopay_bank_last4         TEXT,
  autopay_updated_on         TEXT,
  credit_limit               REAL,                   -- heloc only
  draw_period_ends_on        TEXT,                   -- heloc only
  forbearance_start          TEXT,
  forbearance_end            TEXT,
  deferred_amount            REAL,                   -- installments skipped during forbearance
  special_handling           TEXT,                   -- NULL | bankruptcy
  special_handling_note      TEXT
);

CREATE TABLE payments (
  payment_id       TEXT PRIMARY KEY,
  loan_id          INTEGER NOT NULL REFERENCES loans(loan_id),
  for_due_date     TEXT NOT NULL,         -- installment the payment applies to
  received_on      TEXT NOT NULL,         -- or the scheduled date for future payments
  amount           REAL NOT NULL,
  applied_principal REAL,
  applied_interest REAL,
  applied_escrow   REAL,
  method           TEXT NOT NULL,         -- autopay | online | phone | bank_bill_pay | chat
  bank_last4       TEXT,
  status           TEXT NOT NULL,         -- posted | returned | pending | scheduled
  status_note      TEXT
);

CREATE TABLE heloc_draws (                -- money borrowed from a home equity line, sent to a linked bank account
  draw_id       TEXT PRIMARY KEY,
  loan_id       INTEGER NOT NULL REFERENCES loans(loan_id),
  drawn_on      TEXT NOT NULL,
  amount        REAL NOT NULL,
  to_bank_last4 TEXT NOT NULL,
  channel       TEXT NOT NULL             -- online | phone | branch
);

CREATE TABLE fees_charged (
  fee_id     INTEGER PRIMARY KEY,
  loan_id    INTEGER NOT NULL REFERENCES loans(loan_id),
  charged_on TEXT NOT NULL,
  fee_type   TEXT NOT NULL REFERENCES fee_schedule(fee_type),
  amount     REAL NOT NULL,
  status     TEXT NOT NULL,               -- unpaid | paid
  note       TEXT NOT NULL
);

CREATE TABLE escrow_analyses (
  loan_id                 INTEGER NOT NULL REFERENCES loans(loan_id),
  analysis_date           TEXT NOT NULL,
  effective_date          TEXT NOT NULL,  -- first installment at the new payment
  prior_annual_tax        REAL NOT NULL,
  new_annual_tax          REAL NOT NULL,
  prior_annual_insurance  REAL NOT NULL,
  new_annual_insurance    REAL NOT NULL,
  shortage_amount         REAL NOT NULL,
  surplus_amount          REAL NOT NULL,
  spread_months           INTEGER NOT NULL,
  PRIMARY KEY (loan_id, analysis_date)
);

CREATE TABLE applications (               -- home equity line of credit applications in progress
  application_id        TEXT PRIMARY KEY,
  customer_id           INTEGER NOT NULL REFERENCES customers(customer_id),
  product               TEXT NOT NULL,
  property_address      TEXT NOT NULL,
  requested_amount      REAL NOT NULL,
  submitted_on          TEXT NOT NULL,
  stage                 TEXT NOT NULL,    -- submitted | conditions_outstanding | underwriting | approved_closing_scheduled | withdrawn
  outstanding_items     TEXT NOT NULL,    -- JSON list of plain-language steps the applicant still owes
  loan_officer          TEXT NOT NULL,
  last_activity_on      TEXT NOT NULL,
  estimated_closing_on  TEXT,
  note                  TEXT
);

CREATE TABLE fee_schedule (
  fee_type     TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  amount       REAL NOT NULL,
  unit         TEXT NOT NULL,             -- per_item | per_year | pct_of_principal_and_interest
  description  TEXT NOT NULL,
  waiver       TEXT
);

-- Written by the agent's tools during a conversation. Evals compare these to the expected final state.
CREATE TABLE handoffs (
  handoff_id      TEXT PRIMARY KEY,
  customer_id     INTEGER NOT NULL REFERENCES customers(customer_id),
  loan_id         INTEGER REFERENCES loans(loan_id),
  created_on      TEXT NOT NULL,
  team            TEXT NOT NULL,          -- loan_officer | hardship_specialist
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
