-- SafeOpsAI — Step 10 Migration
-- Adds Demo Seeding safety isolation and index optimizations.
-- Safe to run multiple times (all statements are idempotent).

-- ── 1. Demo Data Isolation Columns ──────────────────────────────────────
ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE agent_decisions
    ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE remediation_actions
    ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT FALSE;

-- ── 2. Performance Indexes for Incident Analytics ─────────────────────────
CREATE INDEX IF NOT EXISTS idx_incidents_is_demo ON incidents(is_demo);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_service ON incidents(service);
CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);
CREATE INDEX IF NOT EXISTS idx_incidents_fault_type ON incidents(fault_type);
CREATE INDEX IF NOT EXISTS idx_incidents_detected_at ON incidents(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_remediation_actions_state ON remediation_actions(state);
CREATE INDEX IF NOT EXISTS idx_remediation_actions_incident_id ON remediation_actions(incident_id);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_incident_id ON agent_decisions(incident_id);

-- ── 3. Update Polling Views to Exclude Demo Records ───────────────────────
-- This ensures live background agents (RCA, Scoring, Coordinator, Sandbox, Remediation)
-- will NEVER poll or execute actions on seeded demo/sample records.

-- View: rca_queue
CREATE OR REPLACE VIEW rca_queue AS
SELECT
    i.id,
    i.incident_type,
    i.service,
    i.severity,
    i.fault_type,
    i.fingerprint,
    i.description,
    i.detected_at,
    i.metrics_snapshot,
    i.diagnosing_at
FROM incidents i
WHERE i.status = 'open'
  AND (i.is_demo = FALSE)
  AND (i.detection_source != 'demo_seed')
  AND i.diagnosing_at IS NULL
  AND i.detected_at < NOW() - INTERVAL '5 seconds'
  AND NOT EXISTS (
      SELECT 1
      FROM agent_decisions ad
      WHERE ad.incident_id = i.id
        AND ad.agent_name  = 'root_cause'
  )
ORDER BY i.detected_at ASC;

-- View: scoring_queue
CREATE OR REPLACE VIEW scoring_queue AS
SELECT
    i.id,
    i.incident_type,
    i.service,
    i.severity,
    i.status,
    i.fault_type,
    i.detected_at,
    i.diagnosing_at,
    i.scoring_at
FROM incidents i
WHERE i.status = 'open'
  AND (i.is_demo = FALSE)
  AND (i.detection_source != 'demo_seed')
  AND i.scoring_at IS NULL
  AND EXISTS (
      SELECT 1 FROM agent_decisions ad
      WHERE ad.incident_id = i.id AND ad.agent_name = 'root_cause'
  )
  AND NOT EXISTS (
      SELECT 1 FROM agent_decisions ad
      WHERE ad.incident_id = i.id AND ad.agent_name = 'cost'
  )
ORDER BY i.detected_at ASC;

-- View: coordinator_queue
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
  AND (i.is_demo = FALSE)
  AND (i.detection_source != 'demo_seed')
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

-- View: sandbox_queue
CREATE OR REPLACE VIEW sandbox_queue AS
SELECT
    i.id,
    i.incident_type,
    i.service,
    i.severity,
    i.fault_type,
    i.detected_at,
    i.coordinated_at,
    i.sandbox_at
FROM incidents i
WHERE i.status = 'open'
  AND (i.is_demo = FALSE)
  AND (i.detection_source != 'demo_seed')
  AND i.coordinated_at IS NOT NULL
  AND i.sandbox_at IS NULL
  AND EXISTS (
      SELECT 1 FROM agent_decisions ad
      WHERE ad.incident_id = i.id AND ad.agent_name = 'coordinator'
  )
ORDER BY i.detected_at ASC;

-- View: remediation_queue
CREATE OR REPLACE VIEW remediation_queue AS
SELECT
    i.id                                   AS incident_id,
    i.incident_type,
    i.service,
    i.severity,
    i.fault_type,
    i.detected_at,
    i.coordinated_at,
    i.sandbox_at,
    i.remediating_at,
    ra.id                                  AS sandbox_action_id,
    ra.action_type,
    ra.target_service,
    ra.candidate_rank,
    ra.validation_score
FROM incidents i
JOIN remediation_actions ra ON ra.incident_id = i.id
WHERE i.status = 'open'
  AND (i.is_demo = FALSE)
  AND (i.detection_source != 'demo_seed')
  AND i.sandbox_at IS NOT NULL
  AND i.remediating_at IS NULL
  AND ra.execution_authorized = TRUE
  AND ra.selection_status = 'selected'
ORDER BY i.detected_at ASC;
