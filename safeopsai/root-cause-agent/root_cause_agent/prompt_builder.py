"""
Root Cause Agent — Prompt Builder
=====================================
Constructs the exact prompt string sent to Ollama.

Design principles:
  - The prompt always ends with a strict JSON-only instruction.
  - The expected output schema is shown inline so the LLM knows exactly
    what fields to return.
  - All numeric values are rounded to avoid floating-point noise in the prompt.
  - The prompt is deterministic given the same inputs (no random elements).
"""

from __future__ import annotations

import json
from typing import Any

from .models import Evidence, RCARequest


# ── Output schema shown to the LLM ───────────────────────────────────────

_OUTPUT_SCHEMA = {
    "root_cause_service": "<string: backend | database | frontend | unknown>",
    "cause_type": (
        "<string: one of: container_crash | database_overload | "
        "database_unavailable | slow_queries | high_load | memory_pressure | "
        "network_fault | configuration_error | cascading_failure | unknown>"
    ),
    "confidence": "<float: 0.0 to 1.0>",
    "evidence_summary": "<string: 1-3 sentences summarising the key evidence>",
    "reasoning": "<string: step-by-step reasoning explaining your conclusion>",
    "remediation_candidates": [
        {
            "action": (
                "<string: one of: restart_service | scale_up | rollback_config | "
                "clear_fault | investigate | restart_database | redeploy>"
            ),
            "target": "<string: service name>",
            "description": "<string: what this action does>",
            "priority": "<integer: 1=highest>",
            "estimated_downtime_seconds": "<integer>",
            "reversible": "<boolean>",
        }
    ],
}

_SYSTEM_PROMPT = """\
You are an expert Site Reliability Engineer analysing a cloud microservice incident.
You must respond ONLY with a single valid JSON object — no markdown, no explanation outside the JSON.
The JSON must exactly match the schema provided.
Do not add any fields not present in the schema.
Be concise in evidence_summary (1-3 sentences maximum).
"""


def build(request: RCARequest, evidence: Evidence) -> str:
    """
    Build the complete prompt string for Ollama.

    The prompt contains:
      1. System instruction (role + output format rule)
      2. Incident context
      3. Metrics snapshot at detection time
      4. Current live metrics
      5. Historical metric windows
      6. Service topology and dependency hints
      7. Cascade failure pattern hints
      8. Explicit JSON output schema
    """
    lines: list[str] = []

    # ── System role ───────────────────────────────────────────────────────
    lines.append(_SYSTEM_PROMPT.strip())
    lines.append("")

    # ── Incident context ──────────────────────────────────────────────────
    lines.append("=== INCIDENT ===")
    lines.append(f"Incident ID   : {request.incident_id}")
    lines.append(f"Type          : {request.incident_type}")
    lines.append(f"Affected service: {request.service}")
    lines.append(f"Severity      : {request.severity}")
    lines.append(f"Fault type    : {request.fault_type}")
    lines.append(f"Description   : {request.description}")
    lines.append(f"Detected at   : {request.detected_at}")
    lines.append("")

    # ── Metrics at detection time ──────────────────────────────────────────
    if request.metrics_snapshot:
        lines.append("=== METRICS AT DETECTION ===")
        for k, v in request.metrics_snapshot.items():
            if k == "sampled_at":
                continue
            display = _fmt(v)
            lines.append(f"  {k}: {display}")
        lines.append("")

    # ── Current live metrics ──────────────────────────────────────────────
    if evidence.current_metrics:
        lines.append("=== CURRENT METRICS (live) ===")
        for m in evidence.current_metrics:
            parts = [f"  {m.name}: {_fmt(m.value)}"]
            if m.unit:
                parts.append(m.unit)
            if m.note:
                parts.append(f"({m.note})")
            lines.append(" ".join(parts))
        lines.append("")

    # ── Historical windows ─────────────────────────────────────────────────
    if evidence.metric_history:
        lines.append(f"=== METRIC HISTORY (last {evidence.metric_history[0].window_minutes} minutes) ===")
        for h in evidence.metric_history:
            lines.append(f"  {h.to_prompt_str()}")
        lines.append("")

    # ── Topology ───────────────────────────────────────────────────────────
    if evidence.service_deps:
        lines.append("=== SERVICE DEPENDENCIES ===")
        lines.append(f"  {request.service} depends on: {', '.join(evidence.service_deps)}")
        lines.append("")

    if evidence.cascade_patterns:
        lines.append("=== KNOWN CASCADE FAILURE PATTERNS ===")
        for hint in evidence.cascade_patterns:
            lines.append(f"  {hint}")
        lines.append("")

    # ── System architecture summary ────────────────────────────────────────
    lines.append("=== SYSTEM ARCHITECTURE ===")
    lines.append("  frontend (port 3000, Nginx) → backend (port 8000, FastAPI) → database (port 5432, PostgreSQL)")
    lines.append("  Metrics source: Prometheus scraping backend /metrics endpoint")
    lines.append("  Key metrics: http_requests_total, application_errors_total,")
    lines.append("               http_request_duration_seconds (histogram), db_query_duration_seconds (histogram)")
    lines.append("")

    # ── Task instruction ───────────────────────────────────────────────────
    lines.append("=== TASK ===")
    lines.append(
        "Analyse the incident data above and determine the most likely root cause. "
        "Consider cascade failures: a database problem can cause backend errors and high latency. "
        "Provide 1-3 remediation candidates ordered by priority (1=most important)."
    )
    lines.append("")

    # ── Output schema ──────────────────────────────────────────────────────
    lines.append("=== REQUIRED OUTPUT FORMAT ===")
    lines.append("Respond ONLY with this JSON structure (fill in actual values, no placeholders):")
    lines.append(json.dumps(_OUTPUT_SCHEMA, indent=2))

    return "\n".join(lines)


def _fmt(v: Any) -> str:
    """Format a metric value for the prompt."""
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)
