-- SafeOpsAI — Step 4 Migration
-- Extends agent_decisions and incidents tables for the Root Cause Agent.
-- All statements are idempotent (IF NOT EXISTS / DO NOTHING guards).

-- ── agent_decisions additions ─────────────────────────────────────────────
-- The existing columns are: id, incident_id, agent_name, score, reasoning,
-- raw_output (JSONB), created_at.
-- root_cause_output stores the full structured JSON from the RCA agent.
ALTER TABLE agent_decisions
    ADD COLUMN IF NOT EXISTS root_cause_output JSONB;

-- execution_time_ms — how long the LLM call took (useful for latency tracking)
ALTER TABLE agent_decisions
    ADD COLUMN IF NOT EXISTS execution_time_ms INTEGER;

-- llm_model — which Ollama model produced this diagnosis
ALTER TABLE agent_decisions
    ADD COLUMN IF NOT EXISTS llm_model VARCHAR(100);

-- ── incidents additions ────────────────────────────────────────────────────
-- diagnosing_at — timestamp when RCA was started (set by root-cause-agent)
ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS diagnosing_at TIMESTAMPTZ;

-- ── View: rca_queue ───────────────────────────────────────────────────────
-- Incidents that need Root Cause Analysis:
-- status='open' AND no existing root_cause decision AND detected at least 5s ago.
-- The root-cause-agent polls this view instead of writing complex WHERE clauses.
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
  AND i.diagnosing_at IS NULL          -- not yet picked up by RCA agent
  AND i.detected_at < NOW() - INTERVAL '5 seconds'  -- allow incident to settle
  AND NOT EXISTS (
      SELECT 1
      FROM agent_decisions ad
      WHERE ad.incident_id = i.id
        AND ad.agent_name  = 'root_cause'
  )
ORDER BY i.detected_at ASC;           -- oldest first

-- ── View: rca_results ────────────────────────────────────────────────────
-- Join incidents with their root_cause diagnosis for easy querying.
CREATE OR REPLACE VIEW rca_results AS
SELECT
    i.id                           AS incident_id,
    i.incident_type,
    i.service                      AS affected_service,
    i.severity,
    i.status,
    i.detected_at,
    i.resolved_at,
    i.mttr_seconds,
    i.fingerprint,
    i.metrics_snapshot,
    ad.id                          AS decision_id,
    ad.score                       AS confidence_score,
    ad.reasoning,
    ad.root_cause_output,
    ad.llm_model,
    ad.execution_time_ms,
    ad.created_at                  AS diagnosed_at
FROM incidents i
LEFT JOIN agent_decisions ad
       ON ad.incident_id = i.id
      AND ad.agent_name  = 'root_cause'
ORDER BY i.detected_at DESC;
