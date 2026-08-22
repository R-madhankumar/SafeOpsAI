"""
SafeOpsAI — Sandbox Agent: Prometheus Metrics
===============================================
Exposes Prometheus metrics for observability.
"""

from prometheus_client import Counter, Histogram, Gauge

# Total validation attempts
SANDBOX_VALIDATION_TOTAL = Counter(
    "sandbox_validation_total",
    "Total sandbox validation attempts",
    ["service"],
)

# Successful sandbox validations (PASS)
SANDBOX_VALIDATION_SUCCESS_TOTAL = Counter(
    "sandbox_validation_success_total",
    "Total successful sandbox validations (PASS)",
    ["service"],
)

# Failed sandbox validations (FAIL)
SANDBOX_VALIDATION_FAILURE_TOTAL = Counter(
    "sandbox_validation_failure_total",
    "Total failed sandbox validations (FAIL)",
    ["service", "reason"],
)

# Wall-clock duration of sandbox validations
SANDBOX_VALIDATION_DURATION_SECONDS = Histogram(
    "sandbox_validation_duration_seconds",
    "Wall-clock duration of sandbox validation runs in seconds",
    ["service"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

# Remediation candidate attempt counter (for candidate fallbacks)
REMEDIATION_CANDIDATE_ATTEMPTS_TOTAL = Counter(
    "remediation_candidate_attempts_total",
    "Total remediation candidate validation attempts",
    ["action", "target"],
)

# Remediation candidate rejection counter
REMEDIATION_CANDIDATE_REJECTIONS_TOTAL = Counter(
    "remediation_candidate_rejections_total",
    "Total remediation candidates rejected due to sandbox validation failure",
    ["action", "target", "reason"],
)

# Queue length gauge
SANDBOX_QUEUE_LENGTH = Gauge(
    "sandbox_queue_length",
    "Number of incidents waiting for sandbox validation",
)
