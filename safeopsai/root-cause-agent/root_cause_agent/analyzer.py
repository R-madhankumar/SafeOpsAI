"""
Root Cause Agent — Analyzer
==============================
Orchestrates the full diagnosis pipeline for one incident:

  1. Collect evidence  (evidence.py)
  2. Build prompt      (prompt_builder.py)
  3. Call LLM          (llm_client.py)
  4. Parse + validate  (this module)
  5. Fallback          (rule-based, when LLM fails or is disabled)

Returns a RCAResult — always non-None, even on failure.
The caller (agent.py) decides whether to retry.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from . import evidence as evidence_module
from . import prompt_builder
from .config import cfg
from .llm_client import LLMClient, extract_json_from_response
from .models import (
    CauseType,
    DiagnosisOutput,
    RCARequest,
    RCAResult,
    RCAStatus,
    RemediationType,
)

log = logging.getLogger("rca.analyzer")

# ── Required fields in the LLM JSON output ─────────────────────────────────
_REQUIRED_FIELDS = {
    "root_cause_service",
    "cause_type",
    "confidence",
    "evidence_summary",
    "reasoning",
    "remediation_candidates",
}

# ── Valid enum values ────────────────────────────────────────────────────────
_VALID_CAUSE_TYPES    = {c.value for c in CauseType}
_VALID_REMEDIATION    = {r.value for r in RemediationType}
_VALID_SERVICES       = {"backend", "database", "frontend", "unknown"}


class Analyzer:
    """
    Stateless analyzer — safe to call concurrently from multiple tasks.
    Create one instance per agent process and reuse it.
    """

    def __init__(self) -> None:
        self._llm = LLMClient()

    async def run(self, request: RCARequest) -> RCAResult:
        """
        Full pipeline: evidence → prompt → LLM → parse → validate.

        Never raises — all errors are captured in RCAResult.
        """
        t0 = time.monotonic()

        # ── Step 1: Collect evidence ────────────────────────────────────
        try:
            ev = await evidence_module.collect(request)
        except Exception as exc:
            log.error("Evidence collection failed for incident %d: %s", request.incident_id, exc)
            # Still proceed — use empty evidence with stored snapshot
            from .models import Evidence
            ev = Evidence(
                incident_id=request.incident_id,
                incident_type=request.incident_type,
                service=request.service,
                detection_snapshot=request.metrics_snapshot,
            )

        # ── Step 2: Build prompt ────────────────────────────────────────
        prompt = prompt_builder.build(request, ev)
        log.debug("Prompt built: %d chars for incident %d", len(prompt), request.incident_id)

        # ── Step 3: Call LLM ────────────────────────────────────────────
        llm_error: str = ""
        raw_output: dict[str, Any] = {}
        diagnosis: DiagnosisOutput | None = None

        try:
            ollama_response = await self._llm.generate(prompt)
            raw_text = ollama_response.get("response", "")

            if not raw_text.strip():
                raise ValueError("Ollama returned an empty response body")

            raw_output = extract_json_from_response(raw_text)
            diagnosis  = self._validate_and_build(
                raw_output,
                request,
                llm_model=cfg.llm_model,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
            log.info(
                "LLM diagnosis: incident_id=%d root=%s cause=%s confidence=%.2f (%dms)",
                request.incident_id,
                diagnosis.root_cause_service,
                diagnosis.cause_type,
                diagnosis.confidence,
                diagnosis.execution_time_ms,
            )
            return RCAResult(request=request, status=RCAStatus.COMPLETED, output=diagnosis)

        except RuntimeError as exc:
            # LLM unavailable, timeout, HTTP error
            llm_error = str(exc)
            log.warning("LLM call failed for incident %d: %s", request.incident_id, exc)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            # Bad JSON or validation failure
            llm_error = str(exc)
            log.warning(
                "LLM output parse/validation failed for incident %d: %s",
                request.incident_id, exc,
            )

        # ── Step 4: Rule-based fallback ──────────────────────────────────
        if cfg.llm_fallback:
            log.info(
                "Using rule-based fallback for incident %d (llm_error=%s)",
                request.incident_id, llm_error[:80],
            )
            diagnosis = self._rule_based_fallback(
                request,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
            return RCAResult(request=request, status=RCAStatus.FALLBACK, output=diagnosis)

        return RCAResult(
            request=request,
            status=RCAStatus.FAILED,
            error=llm_error,
        )

    # ── Validation ────────────────────────────────────────────────────────

    def _validate_and_build(
        self,
        raw: dict[str, Any],
        request: RCARequest,
        llm_model: str,
        elapsed_ms: int,
    ) -> DiagnosisOutput:
        """
        Validate the LLM JSON against the expected schema and normalise values.
        Raises ValueError if required fields are missing.
        """
        missing = _REQUIRED_FIELDS - set(raw.keys())
        if missing:
            raise ValueError(f"LLM output missing required fields: {missing}")

        # Normalise root_cause_service
        svc = str(raw["root_cause_service"]).strip().lower()
        if svc not in _VALID_SERVICES:
            log.warning("Unknown root_cause_service %r — defaulting to 'unknown'", svc)
            svc = "unknown"

        # Normalise cause_type
        cause = str(raw["cause_type"]).strip().lower()
        if cause not in _VALID_CAUSE_TYPES:
            log.warning("Unknown cause_type %r — defaulting to 'unknown'", cause)
            cause = CauseType.UNKNOWN.value

        # Normalise confidence
        try:
            conf = float(raw["confidence"])
            conf = max(0.0, min(1.0, conf))
        except (TypeError, ValueError):
            conf = 0.5

        # Normalise remediation candidates
        candidates = self._normalise_candidates(raw.get("remediation_candidates", []))

        return DiagnosisOutput(
            incident_id            = request.incident_id,
            root_cause_service     = svc,
            cause_type             = cause,
            confidence             = conf,
            evidence_summary       = str(raw.get("evidence_summary", ""))[:500],
            reasoning              = str(raw.get("reasoning", ""))[:2000],
            remediation_candidates = candidates,
            diagnosis_method       = "llm",
            llm_model              = llm_model,
            execution_time_ms      = elapsed_ms,
        )

    @staticmethod
    def _normalise_candidates(raw_list: Any) -> list[dict[str, Any]]:
        """Sanitise the remediation_candidates list from LLM output."""
        if not isinstance(raw_list, list):
            return []
        result = []
        for i, item in enumerate(raw_list[:3]):          # max 3 candidates
            if not isinstance(item, dict):
                continue
            action = str(item.get("action", "investigate")).strip().lower()
            if action not in _VALID_REMEDIATION:
                action = RemediationType.INVESTIGATE.value
            result.append({
                "action":                     action,
                "target":                     str(item.get("target", "backend"))[:50],
                "description":                str(item.get("description", ""))[:300],
                "priority":                   max(1, int(item.get("priority", i + 1))),
                "estimated_downtime_seconds": max(0, int(item.get("estimated_downtime_seconds", 0))),
                "reversible":                 bool(item.get("reversible", True)),
            })
        return result

    # ── Rule-based fallback ───────────────────────────────────────────────

    @staticmethod
    def _rule_based_fallback(request: RCARequest, elapsed_ms: int) -> DiagnosisOutput:
        """
        Deterministic diagnosis when the LLM is unavailable.
        Maps incident_type → root cause + remediation, using the same
        cause_type vocabulary as the LLM output so downstream agents
        can process it identically.
        """
        inc_type = request.incident_type.upper()
        service  = request.service

        rules: dict[str, dict] = {
            "BACKEND_DOWN": {
                "root": "backend",
                "cause": CauseType.CONTAINER_CRASH.value,
                "confidence": 0.90,
                "summary": (
                    "Backend service is unreachable. "
                    "The container has crashed or been stopped."
                ),
                "reasoning": (
                    "Prometheus reports up{job='safeops-backend'}=0, indicating the backend "
                    "process is not responding to scrapes. This is consistent with a container "
                    "crash or OOM kill. No DB-level metrics are needed to confirm this."
                ),
                "remediations": [
                    {
                        "action": RemediationType.RESTART_SERVICE.value,
                        "target": "backend",
                        "description": "Restart the safeops-backend Docker container.",
                        "priority": 1,
                        "estimated_downtime_seconds": 30,
                        "reversible": True,
                    }
                ],
            },
            "DATABASE_DOWN": {
                "root": "database",
                "cause": CauseType.DATABASE_UNAVAILABLE.value,
                "confidence": 0.90,
                "summary": (
                    "PostgreSQL exporter is unreachable. "
                    "The database container is not running."
                ),
                "reasoning": (
                    "up{job='postgres'}=0 means the postgres-exporter cannot connect to "
                    "PostgreSQL. The database container has likely stopped or crashed. "
                    "Backend errors and latency are expected consequences."
                ),
                "remediations": [
                    {
                        "action": RemediationType.RESTART_DATABASE.value,
                        "target": "database",
                        "description": "Restart the safeops-database Docker container.",
                        "priority": 1,
                        "estimated_downtime_seconds": 20,
                        "reversible": True,
                    }
                ],
            },
            "HIGH_ERROR_RATE": {
                "root": "backend",
                "cause": CauseType.HIGH_LOAD.value,
                "confidence": 0.75,
                "summary": (
                    "Backend is returning errors at a high rate. "
                    "Could be a fault injection, database failure, or code issue."
                ),
                "reasoning": (
                    "rate(application_errors_total[1m]) exceeds threshold. "
                    "Without LLM reasoning, the most common cause in this environment "
                    "is an active fault injection (high_error_rate flag set via /admin/fault) "
                    "or a cascading database failure."
                ),
                "remediations": [
                    {
                        "action": RemediationType.CLEAR_FAULT.value,
                        "target": "backend",
                        "description": "Call POST /admin/fault/reset to clear any active fault injection.",
                        "priority": 1,
                        "estimated_downtime_seconds": 0,
                        "reversible": True,
                    },
                    {
                        "action": RemediationType.INVESTIGATE.value,
                        "target": "backend",
                        "description": "Check backend logs for unhandled exceptions.",
                        "priority": 2,
                        "estimated_downtime_seconds": 0,
                        "reversible": True,
                    },
                ],
            },
            "HIGH_5XX_RATIO": {
                "root": "backend",
                "cause": CauseType.HIGH_LOAD.value,
                "confidence": 0.70,
                "summary": "More than 10% of HTTP requests are returning 5xx status codes.",
                "reasoning": (
                    "High 5xx ratio typically indicates the backend cannot complete requests. "
                    "Common causes: DB unavailability, fault injection, or resource exhaustion."
                ),
                "remediations": [
                    {
                        "action": RemediationType.CLEAR_FAULT.value,
                        "target": "backend",
                        "description": "Clear any active fault injection via /admin/fault/reset.",
                        "priority": 1,
                        "estimated_downtime_seconds": 0,
                        "reversible": True,
                    }
                ],
            },
            "HIGH_LATENCY": {
                "root": "backend",
                "cause": CauseType.SLOW_QUERIES.value,
                "confidence": 0.70,
                "summary": "P95 request latency exceeds 2 seconds, suggesting downstream slowness.",
                "reasoning": (
                    "High request latency is often caused by slow database queries. "
                    "Check if db_query_duration_seconds is also elevated. "
                    "Could be slow_queries fault injection or genuine DB performance degradation."
                ),
                "remediations": [
                    {
                        "action": RemediationType.CLEAR_FAULT.value,
                        "target": "backend",
                        "description": "Clear slow_queries fault injection if active.",
                        "priority": 1,
                        "estimated_downtime_seconds": 0,
                        "reversible": True,
                    }
                ],
            },
            "SLOW_DATABASE": {
                "root": "database",
                "cause": CauseType.SLOW_QUERIES.value,
                "confidence": 0.85,
                "summary": "P95 database query latency exceeds 1 second.",
                "reasoning": (
                    "histogram_quantile(0.95, db_query_duration_seconds) > 1s confirms "
                    "the database is responding slowly. In this environment the most likely "
                    "cause is the slow_queries fault injection adding artificial delay "
                    "to all queries."
                ),
                "remediations": [
                    {
                        "action": RemediationType.CLEAR_FAULT.value,
                        "target": "backend",
                        "description": "Clear slow_queries fault via POST /admin/fault/reset.",
                        "priority": 1,
                        "estimated_downtime_seconds": 0,
                        "reversible": True,
                    }
                ],
            },
        }

        rule = rules.get(inc_type, {
            "root": service,
            "cause": CauseType.UNKNOWN.value,
            "confidence": 0.40,
            "summary": f"Incident of type {inc_type} on service {service}.",
            "reasoning": "No matching rule-based diagnosis available. Manual investigation required.",
            "remediations": [
                {
                    "action": RemediationType.INVESTIGATE.value,
                    "target": service,
                    "description": "Manual investigation required — no automated rule matches.",
                    "priority": 1,
                    "estimated_downtime_seconds": 0,
                    "reversible": True,
                }
            ],
        })

        return DiagnosisOutput(
            incident_id            = request.incident_id,
            root_cause_service     = rule["root"],
            cause_type             = rule["cause"],
            confidence             = rule["confidence"],
            evidence_summary       = rule["summary"],
            reasoning              = rule["reasoning"],
            remediation_candidates = rule["remediations"],
            diagnosis_method       = "fallback",
            llm_model              = "none",
            execution_time_ms      = elapsed_ms,
        )
