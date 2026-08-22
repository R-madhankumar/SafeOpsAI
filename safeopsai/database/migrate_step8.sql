-- SafeOpsAI — Step 8 Migration
-- Adds Risk-Aware Autonomous Remediation & Rollback support.
-- All statements are idempotent.

-- remediating_at — timestamp when the remediation controller started executing an incident
ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS remediating_at TIMESTAMPTZ;

-- Enhance remediation_actions table for detailed lifecycle, snapshot, recovery score, and rollback tracking
ALTER TABLE remediation_actions
    ADD COLUMN IF NOT EXISTS snapshot_id VARCHAR(100),
    ADD COLUMN IF NOT EXISTS state VARCHAR(50) DEFAULT 'PENDING',
    ADD COLUMN IF NOT EXISTS recovery_score NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS recovery_metrics JSONB,
    ADD COLUMN IF NOT EXISTS rollback_performed BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS rollback_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS rollback_ended_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS rollback_reason TEXT,
    ADD COLUMN IF NOT EXISTS attempt_number INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS max_attempts INTEGER DEFAULT 2,
    ADD COLUMN IF NOT EXISTS escalated BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS escalation_reason TEXT;

-- View: remediation_queue
-- Incidents that have passed Sandbox Validation (execution_authorized = True) but NOT yet remediated.
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
  AND i.sandbox_at IS NOT NULL
  AND i.remediating_at IS NULL
  AND ra.execution_authorized = TRUE
  AND ra.selection_status = 'selected'
ORDER BY i.detected_at ASC;

-- View: remediation_history
-- Full audit view for remediation actions and rollback outcomes.
CREATE OR REPLACE VIEW remediation_history AS
SELECT
    ra.id                                  AS remediation_id,
    ra.incident_id,
    i.incident_type,
    ra.target_service,
    ra.action_type,
    ra.state,
    ra.validation_score,
    ra.recovery_score,
    ra.snapshot_id,
    ra.rollback_performed,
    ra.rollback_reason,
    ra.attempt_number,
    ra.max_attempts,
    ra.escalated,
    ra.escalation_reason,
    ra.outcome,
    ra.executed_at,
    ra.rollback_ended_at
FROM remediation_actions ra
JOIN incidents i ON i.id = ra.incident_id
ORDER BY ra.id DESC;
