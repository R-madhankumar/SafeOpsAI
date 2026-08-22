-- SafeOpsAI — Step 5 Migration
-- Adds scoring support for the Cost / Reliability / Security agents.
-- All statements are idempotent.

-- scoring_at — timestamp when the scoring agents started scoring an incident
-- (prevents duplicate scoring; same pattern as diagnosing_at for the RCA)
ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS scoring_at TIMESTAMPTZ;

-- View: scoring_queue
-- Incidents that already have a root_cause decision but have NOT been scored yet.
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

-- View: scoring_results
-- Per-incident scoring summary (one row per incident with all three criteria).
CREATE OR REPLACE VIEW scoring_results AS
SELECT
    i.id                                   AS incident_id,
    i.incident_type,
    i.service                              AS affected_service,
    i.severity,
    i.status,
    i.detected_at,
    i.fault_type,
    MAX(ad_cost.score)::float              AS cost_score,
    MAX(ad_rel.score)::float               AS reliability_score,
    MAX(ad_sec.score)::float               AS security_score,
    MIN(ad_cost.reasoning)                 AS cost_reasoning,
    MIN(ad_rel.reasoning)                  AS reliability_reasoning,
    MIN(ad_sec.reasoning)                  AS security_reasoning,
    MIN(ad_created.created_at)             AS scored_at
FROM incidents i
LEFT JOIN agent_decisions ad_cost
       ON ad_cost.incident_id = i.id AND ad_cost.agent_name = 'cost'
LEFT JOIN agent_decisions ad_rel
       ON ad_rel.incident_id = i.id AND ad_rel.agent_name = 'reliability'
LEFT JOIN agent_decisions ad_sec
       ON ad_sec.incident_id = i.id AND ad_sec.agent_name = 'security'
LEFT JOIN agent_decisions ad_created
       ON ad_created.incident_id = i.id AND ad_created.agent_name = 'cost'
GROUP BY i.id
ORDER BY i.detected_at DESC;