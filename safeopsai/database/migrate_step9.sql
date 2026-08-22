-- SafeOpsAI — Step 9 Migration
-- Adds Experimental Evaluation & Research Harness database tables.
-- All statements are idempotent.

-- Main experiment campaign table
CREATE TABLE IF NOT EXISTS experiments (
    id          VARCHAR(100) PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    config      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Individual experiment run records
CREATE TABLE IF NOT EXISTS experiment_runs (
    experiment_run_id           VARCHAR(100) PRIMARY KEY,
    experiment_id               VARCHAR(100) REFERENCES experiments(id) ON DELETE CASCADE,
    scenario_id                 VARCHAR(100) NOT NULL,
    strategy                    VARCHAR(100) NOT NULL,
    repetition                  INTEGER      NOT NULL DEFAULT 1,
    is_warmup                   BOOLEAN      NOT NULL DEFAULT FALSE,
    status                      VARCHAR(50)  NOT NULL DEFAULT 'COMPLETED', -- COMPLETED / ERROR / FAILED
    started_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    fault_injected_at           TIMESTAMPTZ,
    incident_detected_at        TIMESTAMPTZ,
    decision_at                 TIMESTAMPTZ,
    remediation_started_at      TIMESTAMPTZ,
    recovered_at                TIMESTAMPTZ,
    mttr_seconds                NUMERIC(8,2),
    downtime_seconds            NUMERIC(8,2),
    detection_latency_seconds   NUMERIC(8,2),
    decision_latency_seconds    NUMERIC(8,2),
    remediation_latency_seconds NUMERIC(8,2),
    rollback                    BOOLEAN      NOT NULL DEFAULT FALSE,
    success                     BOOLEAN      NOT NULL DEFAULT FALSE,
    escalated                   BOOLEAN      NOT NULL DEFAULT FALSE,
    selected_remediation        VARCHAR(100),
    top_ranked_candidate        VARCHAR(100),
    sandbox_pass                BOOLEAN,
    candidate_fallback_count    INTEGER      DEFAULT 0,
    remediation_attempts        INTEGER      DEFAULT 1,
    recovery_score              NUMERIC(4,2),
    final_outcome               VARCHAR(50),
    error_message               TEXT,
    raw_logs                    JSONB        DEFAULT '{}'::jsonb,
    system_config               JSONB        DEFAULT '{}'::jsonb,
    random_seed                 INTEGER,
    created_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exp_runs_experiment_id ON experiment_runs(experiment_id);
CREATE INDEX IF NOT EXISTS idx_exp_runs_scenario_strategy ON experiment_runs(scenario_id, strategy);

-- Raw metric key-value observations
CREATE TABLE IF NOT EXISTS experiment_metrics (
    id                  SERIAL PRIMARY KEY,
    experiment_run_id   VARCHAR(100) REFERENCES experiment_runs(experiment_run_id) ON DELETE CASCADE,
    metric_name         VARCHAR(100) NOT NULL,
    metric_value        NUMERIC(10,4) NOT NULL,
    timestamp           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exp_metrics_run_id ON experiment_metrics(experiment_run_id);
