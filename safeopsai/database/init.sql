-- SafeOpsAI demo database schema
-- Runs automatically when the PostgreSQL container starts for the first time.

-- Items table: the main business entity the demo app manages
CREATE TABLE IF NOT EXISTS items (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255)   NOT NULL,
    description TEXT           NOT NULL DEFAULT '',
    value       NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    created_at  TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

-- Index for time-based queries (used by stats endpoint)
CREATE INDEX IF NOT EXISTS idx_items_created_at ON items (created_at DESC);

-- Incident log: written by the Incident Agent (Step 4)
CREATE TABLE IF NOT EXISTS incidents (
    id           SERIAL PRIMARY KEY,
    detected_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at  TIMESTAMPTZ,
    service      VARCHAR(100) NOT NULL,
    fault_type   VARCHAR(100) NOT NULL,
    severity     VARCHAR(20)  NOT NULL DEFAULT 'medium', -- low / medium / high / critical
    status       VARCHAR(20)  NOT NULL DEFAULT 'open',   -- open / diagnosing / remediating / resolved / rolled_back
    description  TEXT,
    mttr_seconds INTEGER      -- calculated on resolution
);

-- Agent decisions: one row per agent evaluation per incident
CREATE TABLE IF NOT EXISTS agent_decisions (
    id            SERIAL PRIMARY KEY,
    incident_id   INTEGER REFERENCES incidents(id) ON DELETE CASCADE,
    agent_name    VARCHAR(50)  NOT NULL, -- cost / reliability / security / coordinator / root_cause
    score         NUMERIC(4,2),          -- 0–10
    reasoning     TEXT,
    raw_output    JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Remediation actions: what was actually done
CREATE TABLE IF NOT EXISTS remediation_actions (
    id              SERIAL PRIMARY KEY,
    incident_id     INTEGER REFERENCES incidents(id) ON DELETE CASCADE,
    action_type     VARCHAR(100) NOT NULL, -- restart / scale_up / config_rollback / redeploy
    target_service  VARCHAR(100) NOT NULL,
    final_score     NUMERIC(5,2),
    sandbox_passed  BOOLEAN,
    executed_at     TIMESTAMPTZ,
    outcome         VARCHAR(20),           -- success / rolled_back / failed
    notes           TEXT
);

-- Seed a few demo items so the UI is not empty on first launch
INSERT INTO items (name, description, value) VALUES
    ('Widget Alpha',   'First demo item',  42.00),
    ('Widget Beta',    'Second demo item', 17.50),
    ('Widget Gamma',   'Third demo item',  99.99),
    ('Service Config', 'Config blob item',  0.00);
