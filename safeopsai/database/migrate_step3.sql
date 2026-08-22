-- SafeOpsAI — Step 3 Migration
-- Adds columns required by the Incident Agent to the existing incidents table.
-- Safe to run multiple times (all statements are idempotent).
-- Run order: after init.sql has already executed.

-- fingerprint: deterministic key used for deduplication
--   format: "<INCIDENT_TYPE>:<service>"  e.g. "BACKEND_DOWN:backend"
ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS fingerprint VARCHAR(200);

-- incident_type: structured enum string, e.g. BACKEND_DOWN / HIGH_ERROR_RATE
--   Separate from fault_type (which mirrors the fault-injection vocabulary).
ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS incident_type VARCHAR(100);

-- detection_source: how the incident was detected, e.g. "prometheus_rule"
ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS detection_source VARCHAR(100)
        NOT NULL DEFAULT 'prometheus_rule';

-- metrics_snapshot: JSON blob of Prometheus values at detection time
--   e.g. {"error_rate": 1.2, "p95_latency": 4.8, "backend_up": 0}
ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS metrics_snapshot JSONB;

-- Unique index on fingerprint for active incidents — enforces deduplication
-- at the DB level as a second safety net (the agent also deduplicates in memory).
CREATE UNIQUE INDEX IF NOT EXISTS uq_incidents_active_fingerprint
    ON incidents (fingerprint)
    WHERE status NOT IN ('resolved', 'rolled_back');

-- Back-fill existing rows so NOT NULL constraints don't break old data
UPDATE incidents
SET
    fingerprint      = COALESCE(fingerprint, fault_type || ':' || service),
    incident_type    = COALESCE(incident_type, upper(fault_type)),
    detection_source = COALESCE(detection_source, 'prometheus_rule')
WHERE fingerprint IS NULL
   OR incident_type IS NULL;
