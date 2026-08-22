"""
SafeOpsAI — Remediation Agent: Prometheus Metrics
==================================================
Exposes Prometheus metrics for remediation lifecycle, rollback, and recovery scores.
"""

from prometheus_client import Counter, Histogram, Gauge

REMEDIATION_ATTEMPTS_TOTAL = Counter(
    "remediation_attempts_total",
    "Total production remediation execution attempts",
    ["service", "action"],
)

REMEDIATION_SUCCESS_TOTAL = Counter(
    "remediation_success_total",
    "Total successful production remediations",
    ["service", "action"],
)

REMEDIATION_FAILURE_TOTAL = Counter(
    "remediation_failure_total",
    "Total failed production remediations",
    ["service", "action", "reason"],
)

REMEDIATION_DURATION_SECONDS = Histogram(
    "remediation_duration_seconds",
    "Wall-clock duration of production remediation executions in seconds",
    ["service", "action"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

ROLLBACK_ATTEMPTS_TOTAL = Counter(
    "rollback_attempts_total",
    "Total rollback execution attempts",
    ["service"],
)

ROLLBACK_SUCCESS_TOTAL = Counter(
    "rollback_success_total",
    "Total successful rollbacks",
    ["service"],
)

ROLLBACK_FAILURE_TOTAL = Counter(
    "rollback_failure_total",
    "Total failed rollbacks",
    ["service", "reason"],
)

RECOVERY_SCORE_GAUGE = Gauge(
    "recovery_score",
    "Post-remediation Recovery Health Score (0.0 - 1.0)",
    ["service", "incident_id"],
)

ACTIVE_REMEDIATION_GAUGE = Gauge(
    "active_remediation",
    "Active remediation executions currently running",
    ["service"],
)

REMEDIATION_ESCALATIONS_TOTAL = Counter(
    "remediation_escalations_total",
    "Total human escalations triggered due to failed remediations or exceeded limits",
    ["service", "reason"],
)
