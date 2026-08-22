-- SafeOpsAI — Step 6 Migration
-- Adds coordinator (deterministic MCDM) support.
-- All statements are idempotent.

-- coordinated_at — timestamp when the coordinator decided on an incident
ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS coordinated_at TIMESTAMPTZ;

-- Runtime-swappable coordinator weights.
-- The coordinator reads the LATEST row on every decision, so the evaluation
-- harness can change weights between incidents for a weight-sensitivity sweep.
CREATE TABLE IF NOT EXISTS coordinator_config (
    id                  SERIAL PRIMARY KEY,
    cost_weight         NUMERIC(4,3) NOT NULL DEFAULT 0.300,
    reliability_weight  NUMERIC(4,3) NOT NULL DEFAULT 0.500,
    security_weight     NUMERIC(4,3) NOT NULL DEFAULT 0.200,
    method              VARCHAR(20)  NOT NULL DEFAULT 'weighted_sum', -- weighted_sum | topsis
    note                TEXT,
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Seed the default weight set (cost 0.3, reliability 0.5, security 0.2)
INSERT INTO coordinator_config (cost_weight, reliability_weight, security_weight, method, note)
SELECT 0.300, 0.500, 0.200, 'weighted_sum', 'default (reliability-favouring)'
WHERE NOT EXISTS (SELECT 1 FROM coordinator_config);

-- View: coordinator_queue
-- Incidents that have scoring decisions but no coordinator decision yet.
CREATE OR REPLACE VIEW coordinator_queue AS
SELECT
    i.id,
    i.incident_type,
    i.service,
    i.severity,
    i.fault_type,
    i.detected_at
FROM incidents i
WHERE i.status = 'open'
  AND i.coordinated_at IS NULL
  AND EXISTS (
      SELECT 1 FROM agent_decisions ad
      WHERE ad.incident_id = i.id AND ad.agent_name = 'cost'
  )
  AND NOT EXISTS (
      SELECT 1 FROM agent_decisions ad
      WHERE ad.incident_id = i.id AND ad.agent_name = 'coordinator'
  )
ORDER BY i.detected_at ASC;