-- SafeOpsAI — Step 7 Migration
-- Adds Adaptive Sandbox Validation Engine support.
-- All statements are idempotent.

-- sandbox_at — timestamp when the sandbox agent started validating an incident
ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS sandbox_at TIMESTAMPTZ;

-- Enhance remediation_actions table for detailed multi-signal sandbox validation tracking
ALTER TABLE remediation_actions
    ADD COLUMN IF NOT EXISTS candidate_rank INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS validation_score NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS sandbox_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS sandbox_ended_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS checks JSONB,
    ADD COLUMN IF NOT EXISTS baseline_metrics JSONB,
    ADD COLUMN IF NOT EXISTS after_metrics JSONB,
    ADD COLUMN IF NOT EXISTS failure_reason TEXT,
    ADD COLUMN IF NOT EXISTS selection_status VARCHAR(30) DEFAULT 'rejected',
    ADD COLUMN IF NOT EXISTS execution_authorized BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS rollback_available BOOLEAN DEFAULT TRUE;

-- View: sandbox_queue
-- Incidents that have been coordinated by MCDM but NOT yet validated in sandbox.
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
  AND i.coordinated_at IS NOT NULL
  AND i.sandbox_at IS NULL
  AND EXISTS (
      SELECT 1 FROM agent_decisions ad
      WHERE ad.incident_id = i.id AND ad.agent_name = 'coordinator'
  )
ORDER BY i.detected_at ASC;

-- View: sandbox_results
-- Summary of sandbox validation per incident.
CREATE OR REPLACE VIEW sandbox_results AS
SELECT
    ra.id                                  AS action_id,
    ra.incident_id,
    i.incident_type,
    ra.target_service,
    ra.action_type,
    ra.candidate_rank,
    ra.final_score,
    ra.validation_score,
    ra.sandbox_passed,
    ra.selection_status,
    ra.execution_authorized,
    ra.failure_reason,
    ra.checks,
    ra.baseline_metrics,
    ra.after_metrics,
    ra.sandbox_started_at,
    ra.sandbox_ended_at
FROM remediation_actions ra
JOIN incidents i ON i.id = ra.incident_id
ORDER BY ra.sandbox_started_at DESC;
